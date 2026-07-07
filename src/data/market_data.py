# -*- coding: utf-8 -*-
"""
market_data.py — A股市场级数据抓取（基于 akshare）
功能：大盘指数、板块涨跌幅、涨跌停统计、两融资金、龙虎榜、市场情绪指标、
     波动率（QVIX主口径）、中证全指PE、10年国债收益率、成交额历史序列。
设计：
  - 每个数据项独立抓取、独立容错：单项失败不影响整体，失败项返回 None 并记录到 errors；
  - 本地缓存（data/raw/<日期>/）：当日重复调用直接读缓存；
  - get_market_snapshot() 是对外统一入口，输出结构化 dict。
数据口径备注（v4.2.0）：
  - 北向资金数据已停发，按用户裁决删除，资金动向改用两融余额（沪市聚合序列+深市明细汇总）；
  - 波动率主口径=300ETF期权QVIX（裁决#11），备源=沪深300近20日年化历史波动率HV20；
  - 中证全指PE(市盈率1)为"Wind全A非金融石化PE"的替代口径（裁决：含金融石化，偏保守）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.data.data_utils import (annualized_volatility, df_to_records, load_cache,
                                 save_cache, to_float)
from src.utils.file_utils import DIRS, read_json, write_json

# 盘中快照类数据缓存1小时；收盘后统计类数据当日有效
INTRADAY_TTL = 3600
# 重试退避基数（秒）；单元测试置0加速
RETRY_BACKOFF = 1.5

# 关注的大盘指数（名称需与东财接口返回一致）
KEY_INDICES = ["上证指数", "深证成指", "创业板指", "沪深300", "科创50", "中证500"]


def _fetch(name: str, func, *args, ttl: int | None = None, day: str | None = None,
           refresh: bool = False, retries: int = 2, **kwargs):
    """
    统一抓取包装：缓存优先 → 调用akshare（瞬时断连自动重试） → 异常兜底。
    返回 (data, error)：成功 error=None；失败 data=None，error为简述字符串。
    """
    import time as _time

    # TTL只对"今天"的盘中数据有意义；历史日期的缓存是收盘定格值，永不过期、
    # 也绝不能被当下的实时数据覆写（否则历史报告会被盘中值污染）
    if day and day != datetime.now().strftime("%Y%m%d"):
        ttl = None
        if load_cache(name, day=day) is None and name in (
                "market_index_spot", "market_market_activity"):
            return None, "历史日期无收盘缓存，实时接口无法回溯该日快照（按缺失处理）"
    if not refresh:
        cached = load_cache(name, day=day, max_age_seconds=ttl)
        if cached is not None:
            return cached, None
    last_err = None
    for attempt in range(retries + 1):
        try:
            data = func(*args, **kwargs)
            save_cache(name, data, day=day)
            return data, None
        except Exception as exc:  # noqa: BLE001 —— 数据源异常必须兜底，不允许闪退
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                _time.sleep(RETRY_BACKOFF * (attempt + 1))   # 东财接口偶发断连，退避后重试
    return None, last_err


# =============================================================================
# 各数据项抓取函数（内部函数返回可JSON序列化结构）
# =============================================================================

def _index_spot() -> list[dict]:
    """大盘核心指数快照：收盘价、涨跌幅、成交额。主源东财，备源新浪。"""
    import akshare as ak
    try:
        df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    except Exception:   # 东财push2接口偶发拒绝，切新浪源（字段名一致）
        df = ak.stock_zh_index_spot_sina()
    rows = df[df["名称"].isin(KEY_INDICES)]
    rows = rows.drop_duplicates(subset=["名称"], keep="first")   # 新浪源沪深300有沪深两个代码
    cols = [c for c in ["名称", "最新价", "涨跌幅", "成交额", "成交量"] if c in rows.columns]
    return df_to_records(rows[cols])


def _board_ranks() -> list[dict]:
    """行业板块涨跌幅，全量返回，分析层再取强弱排序。主源东财，备源新浪。"""
    import akshare as ak
    try:
        df = ak.stock_board_industry_name_em()
        cols = [c for c in ["板块名称", "最新价", "涨跌幅", "总市值", "换手率",
                            "上涨家数", "下跌家数", "领涨股票"] if c in df.columns]
        return df_to_records(df[cols])
    except Exception:   # 备源：新浪行业板块（字段映射到统一口径）
        df = ak.stock_sector_spot(indicator="新浪行业")
        records = []
        for _, r in df.iterrows():
            records.append({
                "板块名称": r.get("板块"),
                "涨跌幅": to_float(r.get("涨跌幅")),
                "总成交额": to_float(r.get("总成交额")),
                "公司家数": r.get("公司家数"),
                "数据源": "新浪行业(备源)",
            })
        return records


def _limit_stats(day: str) -> dict:
    """涨停/跌停统计（涨停池、跌停池数量与代表个股）。"""
    import akshare as ak
    zt = ak.stock_zt_pool_em(date=day)
    dt = ak.stock_zt_pool_dtgc_em(date=day)
    return {
        "涨停家数": int(len(zt)),
        "跌停家数": int(len(dt)),
        "涨停代表": df_to_records(zt[["代码", "名称", "涨跌幅", "连板数"]], limit=10)
        if len(zt) else [],
        "跌停代表": df_to_records(dt[["代码", "名称", "涨跌幅"]], limit=10)
        if len(dt) else [],
    }


def _market_activity() -> list[dict]:
    """乐咕市场活跃度：上涨/下跌/涨停/跌停/停牌家数，情绪打分的基础输入。"""
    import akshare as ak
    df = ak.stock_market_activity_legu()
    return df_to_records(df)


def _margin_summary() -> dict:
    """
    两融资金概况（v4.2.0：替代已停发的北向数据）。
    沪市：交易所聚合序列（近15个交易日趋势）；深市：最新交易日明细汇总。
    """
    import akshare as ak
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=25)).strftime("%Y%m%d")
    sse = ak.stock_margin_sse(start_date=start, end_date=end)
    sse = sse.sort_values("信用交易日期")
    sse_series = [{"日期": str(r["信用交易日期"]), "融资余额": to_float(r["融资余额"])}
                  for _, r in sse.iterrows()]
    latest_day = str(sse["信用交易日期"].iloc[-1])
    szse_total = None
    try:
        szse = ak.stock_margin_detail_szse(date=latest_day)
        szse_total = float(sum(to_float(x) or 0 for x in szse["融资余额"]))
    except Exception:
        pass   # 深市明细偶发不可得，沪市序列足以观察趋势
    sse_latest = sse_series[-1]["融资余额"] if sse_series else None
    sse_prev10 = sse_series[-11]["融资余额"] if len(sse_series) >= 11 else (
        sse_series[0]["融资余额"] if sse_series else None)
    return {
        "沪市融资余额序列": sse_series[-11:],
        "沪市融资余额_最新": sse_latest,
        "沪市融资余额_10日前": sse_prev10,
        "沪市10日变化率": ((sse_latest - sse_prev10) / sse_prev10
                       if sse_latest and sse_prev10 else None),
        "深市融资余额_最新": szse_total,
        "两市融资余额_最新": (sse_latest + szse_total) if (sse_latest and szse_total) else None,
        "口径": "交易所官方两融数据（沪市聚合序列+深市明细汇总）；北向数据已停发按裁决删除",
    }


def _lhb(day: str) -> list[dict]:
    """龙虎榜明细（前20条）。"""
    import akshare as ak
    ymd = f"{day[:4]}{day[4:6]}{day[6:]}"
    df = ak.stock_lhb_detail_em(start_date=ymd, end_date=ymd)
    cols = [c for c in ["代码", "名称", "收盘价", "涨跌幅", "龙虎榜净买额",
                        "上榜原因"] if c in df.columns]
    return df_to_records(df[cols], limit=20)


def _volatility_gauge() -> dict:
    """
    波动率指标（risk_control 严格风控条件③，阈值35%）。
    v4.2.0裁决#11：主口径=300ETF期权QVIX收盘值；备源=沪深300 HV20（启用时标注）。
    """
    import akshare as ak
    try:
        df = ak.index_option_300etf_qvix()
        close = to_float(df["close"].iloc[-1])
        if close is None:
            raise ValueError("QVIX最新值为空")
        return {"数值": close / 100, "指标": "QVIX(300ETF期权隐含波动率)",
                "日期": str(df["date"].iloc[-1]), "口径": "主口径（v4.2.0裁决#11）"}
    except Exception:
        # 备源：HV20 历史波动率
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            df = ak.index_zh_a_hist(symbol="000300", period="daily",
                                    start_date=start, end_date=end)
            closes = [to_float(x) for x in df["收盘"].tolist()]
        except Exception:
            df = ak.stock_zh_index_daily(symbol="sh000300")
            closes = [to_float(x) for x in df["close"].tolist()][-60:]
        vol = annualized_volatility(closes, window=20)
        return {"数值": vol, "指标": "HV20(沪深300近20日年化历史波动率)",
                "日期": datetime.now().strftime("%Y-%m-%d"),
                "口径": "备源（QVIX不可得时启用，报告须标注）"}


def _csindex_pe() -> dict:
    """中证全指(000985)市盈率1 —— 'Wind全A非金融石化PE<22'条件的替代口径（v4.2.0裁决）。"""
    import akshare as ak
    df = ak.stock_zh_index_value_csindex(symbol="000985")
    row = df.iloc[-1]
    return {"市盈率1": to_float(row.get("市盈率1")),
            "日期": str(row.get("日期")),
            "口径": "中证指数官网000985中证全指·市盈率1（含金融石化，替代Wind全A非金融石化，偏保守）"}


def _sh_index_high() -> dict:
    """上证指数历史最高收盘价（C区条件'逼近或突破历史高点'：现价≥历史最高×95%）。"""
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol="sh000001")
    closes = [to_float(x) for x in df["close"].tolist()]
    closes = [x for x in closes if x]
    return {"历史最高收盘": max(closes), "样本起点": str(df["date"].iloc[0])}


# 成交额历史序列（市场状态判定的连续性条件用，由每日运行累积）
TURNOVER_HISTORY_FILE = DIRS["data_processed"] / "turnover_history.json"


def update_turnover_history(day: str, total_turnover: float | None) -> list[dict]:
    """
    把当日两市成交额追加进历史序列（同日覆盖），返回最近30条。
    防污染：当日15:05收盘前的盘中成交额是未完成值，不入库（已有的当日盘中记录一并清除），
    避免"连续10日<7000亿"等连续性条件被盘中部分值误判。
    """
    data = read_json(TURNOVER_HISTORY_FILE) or {"history": []}
    history = data["history"]
    now = datetime.now()
    is_today = (day == now.strftime("%Y%m%d"))
    market_closed = now.strftime("%H%M") >= "1505"
    if is_today and not market_closed:
        # 盘中：剔除可能已写入的当日部分值，只返回既有序列
        history = [h for h in history if h.get("date") != day]
        write_json(TURNOVER_HISTORY_FILE, {"history": history})
        return history[-30:]
    if total_turnover is None:
        return history[-30:]
    # 只允许两种写入：① 今天且已收盘 ② 补录历史缺口（该日尚无记录，且数据来自当日缓存）
    exists = any(h.get("date") == day for h in history)
    if not is_today and exists:
        return history[-30:]   # 历史日期已有定格值，绝不覆写
    history = [h for h in history if h.get("date") != day]
    history.append({"date": day, "turnover": total_turnover})
    history = sorted(history, key=lambda h: h["date"])[-250:]
    write_json(TURNOVER_HISTORY_FILE, {"history": history})
    return history[-30:]


def _cn10y_yield() -> float | None:
    """10年期国债收益率（%→小数），股息率门槛的锚。"""
    import akshare as ak
    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    df = ak.bond_zh_us_rate(start_date=start)
    col = "中国国债收益率10年"
    series = [to_float(x) for x in df[col].tolist() if to_float(x) is not None]
    return series[-1] / 100 if series else None


# =============================================================================
# 对外统一入口
# =============================================================================

def get_market_snapshot(day: str | None = None, refresh: bool = False) -> dict:
    """
    拉取指定日期（默认今天，格式YYYYMMDD）的市场全量快照。
    返回：
      {"date": ..., "index_spot": [...], "board_ranks": [...], "limit_stats": {...},
       "market_activity": [...], "margin_summary": {...}, "lhb": [...],
       "volatility_gauge": {...}, "csindex_pe": {...}, "sh_index_high": {...},
       "turnover_history": [...], "cn10y_yield": float, "errors": {...}}
    任何数据项失败→值为None并登记errors，调用方需按缺失处理，绝不抛异常。
    """
    day = day or datetime.now().strftime("%Y%m%d")
    snapshot: dict = {"date": day, "generated_at": datetime.now().isoformat()}
    errors: dict = {}

    jobs = {
        "index_spot": (_index_spot, {"ttl": INTRADAY_TTL}),
        "board_ranks": (_board_ranks, {"ttl": INTRADAY_TTL}),
        "limit_stats": (lambda: _limit_stats(day), {}),
        "market_activity": (_market_activity, {"ttl": INTRADAY_TTL}),
        "margin_summary": (_margin_summary, {}),
        "lhb": (lambda: _lhb(day), {}),
        "volatility_gauge": (_volatility_gauge, {}),
        "csindex_pe": (_csindex_pe, {}),
        "sh_index_high": (_sh_index_high, {}),
        "cn10y_yield": (_cn10y_yield, {}),
    }
    for key, (func, opts) in jobs.items():
        data, err = _fetch(f"market_{key}", func, day=day, refresh=refresh, **opts)
        snapshot[key] = data
        if err:
            errors[key] = err

    # 成交额历史序列（市场状态连续性条件的数据基础，随每次运行累积）
    total = 0.0
    found = False
    for r in snapshot.get("index_spot") or []:
        if r.get("名称") in ("上证指数", "深证成指"):
            v = to_float(r.get("成交额"))
            if v:
                total += v
                found = True
    snapshot["turnover_history"] = update_turnover_history(day, total if found else None)
    snapshot["errors"] = errors
    return snapshot


if __name__ == "__main__":
    # 直接运行本脚本 = 现场验证数据可用性（阶段二验收用）
    import json
    snap = get_market_snapshot()
    print(json.dumps({k: (v if k in ("date", "errors", "cn10y_yield") else
                          f"<{len(v) if isinstance(v, list) else 'dict'} 条>")
                      for k, v in snap.items() if k != "generated_at"},
                     ensure_ascii=False, indent=2))
