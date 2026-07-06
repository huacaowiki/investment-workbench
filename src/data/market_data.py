# -*- coding: utf-8 -*-
"""
market_data.py — A股市场级数据抓取（基于 akshare）
功能：大盘指数、板块涨跌幅、涨跌停统计、沪深港通资金、龙虎榜、市场情绪指标、
     沪深300波动率、10年国债收益率。
设计：
  - 每个数据项独立抓取、独立容错：单项失败不影响整体，失败项返回 None 并记录到 errors；
  - 本地缓存（data/raw/<日期>/）：当日重复调用直接读缓存；
  - get_market_snapshot() 是对外统一入口，输出结构化 dict。
数据口径备注：
  - 北向资金逐日净买入额自2024年8月起停止实时发布，本模块以沪深港通
    成交/额度类公开数据替代，报告中已标注口径。
"""
from __future__ import annotations

import traceback
from datetime import datetime, timedelta

from src.data.data_utils import (annualized_volatility, df_to_records, load_cache,
                                 save_cache, to_float)

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


def _hsgt_flow() -> list[dict]:
    """沪深港通资金概况（北向逐日净买入已停发，此为可得替代口径）。"""
    import akshare as ak
    df = ak.stock_hsgt_fund_flow_summary_em()
    return df_to_records(df)


def _lhb(day: str) -> list[dict]:
    """龙虎榜明细（前20条）。"""
    import akshare as ak
    ymd = f"{day[:4]}{day[4:6]}{day[6:]}"
    df = ak.stock_lhb_detail_em(start_date=ymd, end_date=ymd)
    cols = [c for c in ["代码", "名称", "收盘价", "涨跌幅", "龙虎榜净买额",
                        "上榜原因"] if c in df.columns]
    return df_to_records(df[cols], limit=20)


def _hs300_volatility() -> dict:
    """
    沪深300近20日年化历史波动率。
    口径说明：risk_control.yaml 严格风控条件③'VIX类指标'的近似实现
    （见其 pending_confirmation[2]），报告展示时必须带口径标注。
    """
    import akshare as ak
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    try:
        df = ak.index_zh_a_hist(symbol="000300", period="daily",
                                start_date=start, end_date=end)
        closes = [to_float(x) for x in df["收盘"].tolist()]
    except Exception:   # 备源：新浪指数日线
        df = ak.stock_zh_index_daily(symbol="sh000300")
        closes = [to_float(x) for x in df["close"].tolist()][-60:]
    vol = annualized_volatility(closes, window=20)
    return {"近20日年化波动率": vol, "口径": "历史波动率近似，非官方VIX"}


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
       "market_activity": [...], "hsgt_flow": [...], "lhb": [...],
       "hs300_volatility": {...}, "cn10y_yield": float, "errors": {...}}
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
        "hsgt_flow": (_hsgt_flow, {}),
        "lhb": (lambda: _lhb(day), {}),
        "hs300_volatility": (_hs300_volatility, {}),
        "cn10y_yield": (_cn10y_yield, {}),
    }
    for key, (func, opts) in jobs.items():
        data, err = _fetch(f"market_{key}", func, day=day, refresh=refresh, **opts)
        snapshot[key] = data
        if err:
            errors[key] = err
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
