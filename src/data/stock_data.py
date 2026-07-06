# -*- coding: utf-8 -*-
"""
stock_data.py — 个股全量数据抓取（基于 akshare）
输入股票代码，输出覆盖选股/估值/风控计算所需字段的结构化快照：
  基本信息、行情摘要（52周高点回撤/均线/成交额）、估值及历史分位、财务指标、
  分红记录、质押率、最新公告、同业对比、机构盈利预测、10年国债收益率。
设计与 market_data.py 一致：逐项容错 + 本地缓存；不可得字段返回 None 并入 errors，
由分析层按"数据缺失"处理（涉及门槛判断时输出【数据缺失，需人工核验】）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.data.data_utils import (avg, df_to_records, drawdown_from_high, load_cache,
                                 moving_average, normalize_stock_code, percentile_rank,
                                 save_cache, to_float, yoy_growth)
from src.data.market_data import _cn10y_yield, _fetch

# PE/PB历史分位窗口：近10年（stock_selection.yaml pending_confirmation[1] 临时口径）
PERCENTILE_YEARS = 10


def _sina_symbol(code: str) -> str:
    """600519 → sh600519（新浪接口符号规则）。"""
    prefix = "sh" if code.startswith(("6", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")
    return f"{prefix}{code}"


def _basic_info(code: str) -> dict:
    """基本信息：名称、行业、总市值、上市时间。主源东财，备源巨潮+百度。"""
    import akshare as ak
    try:
        df = ak.stock_individual_info_em(symbol=code)
        info = {str(r["item"]): r["value"] for _, r in df.iterrows()}
        return {
            "代码": code,
            "名称": info.get("股票简称"),
            "行业": info.get("行业"),
            "行业口径": "东方财富板块",
            "总市值": to_float(info.get("总市值")),
            "流通市值": to_float(info.get("流通市值")),
            "上市时间": str(info.get("上市时间")),
        }
    except Exception:
        # 备源：巨潮公司概况（行业为证监会门类口径）+ 百度总市值序列
        profile = ak.stock_profile_cninfo(symbol=code).iloc[0].to_dict()
        total_mv = None
        try:
            mv = ak.stock_zh_valuation_baidu(symbol=code, indicator="总市值", period="近一年")
            total_mv = to_float(mv["value"].iloc[-1])
            total_mv = total_mv * 1e8 if total_mv is not None else None   # 百度单位：亿元
        except Exception:
            pass
        return {
            "代码": code,
            "名称": profile.get("A股简称"),
            "行业": profile.get("所属行业"),
            "行业口径": "证监会行业分类（巨潮备源）",
            "总市值": total_mv,
            "流通市值": None,
            "上市时间": str(profile.get("上市日期")),
        }


def _quote_summary(code: str) -> dict:
    """行情摘要：现价、52周高点回撤、60日线、近5/20日日均成交额。主源东财，备源新浪。"""
    import akshare as ak
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start, end_date=end, adjust="qfq")
        closes = [to_float(x) for x in df["收盘"].tolist()]
        amounts = [to_float(x) for x in df["成交额"].tolist()]
    except Exception:   # 备源：新浪日线（amount字段=成交额）
        df = ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="qfq")
        df = df.tail(270)
        closes = [to_float(x) for x in df["close"].tolist()]
        amounts = [to_float(x) for x in df["amount"].tolist()]
    year_closes = closes[-252:] if len(closes) > 252 else closes
    ma60 = moving_average(closes, 60)
    last = closes[-1] if closes else None
    return {
        "最新收盘价": last,
        "52周最高": max(year_closes) if year_closes else None,
        "较1年内高点回撤": drawdown_from_high(year_closes),   # 择时门槛：≥25%
        "MA60": ma60,
        "站上60日线": (last is not None and ma60 is not None and last > ma60),
        "近5日日均成交额": avg(amounts[-5:]),     # 择时池门槛：>1亿
        "近20日日均成交额": avg(amounts[-20:]),   # 股息池门槛：>1亿
    }


def _valuation(code: str) -> dict:
    """
    估值指标与历史分位。主源乐咕（PE-TTM/PB/股息率全历史序列）；
    备源百度估值（序列长度以接口实际返回为准，窗口口径写入结果）。
    """
    import akshare as ak
    try:
        df = ak.stock_a_indicator_lg(symbol=code)
        cutoff = (datetime.now() - timedelta(days=365 * PERCENTILE_YEARS)).date()
        df = df[df["trade_date"] >= cutoff] if len(df) else df
        pe_series = [to_float(x) for x in df["pe_ttm"].tolist()]
        pb_series = [to_float(x) for x in df["pb"].tolist()]
        pe = next((x for x in reversed(pe_series) if x is not None), None)
        pb = next((x for x in reversed(pb_series) if x is not None), None)
        dv = to_float(df["dv_ttm"].iloc[-1]) if len(df) else None
        return {
            "PE_TTM": pe,
            "PB": pb,
            "股息率TTM": dv / 100 if dv is not None else None,   # 乐咕为百分数
            "PE历史分位": percentile_rank(pe_series, pe),
            "PB历史分位": percentile_rank(pb_series, pb),
            "分位窗口": f"近{PERCENTILE_YEARS}年（乐咕）",
        }
    except Exception:
        # 备源：百度估值序列。股息率百度不提供，置None由分红模块口径兜底
        pe_df = ak.stock_zh_valuation_baidu(symbol=code, indicator="市盈率(TTM)", period="近十年")
        pb_df = ak.stock_zh_valuation_baidu(symbol=code, indicator="市净率", period="近十年")
        pe_series = [to_float(x) for x in pe_df["value"].tolist()]
        pb_series = [to_float(x) for x in pb_df["value"].tolist()]
        pe = next((x for x in reversed(pe_series) if x is not None), None)
        pb = next((x for x in reversed(pb_series) if x is not None), None)
        years = max(1, round(len(pe_series) / 365))
        return {
            "PE_TTM": pe,
            "PB": pb,
            "股息率TTM": None,
            "PE历史分位": percentile_rank(pe_series, pe),
            "PB历史分位": percentile_rank(pb_series, pb),
            "分位窗口": f"约{years}年（百度备源，非近10年口径，报告须标注）",
        }


def _financials(code: str) -> dict:
    """
    财务指标（近4年年报）：ROE、净利润、营收/净利同比、资产负债率、
    收现比/净现比（如接口可得）、派息率基础数据。
    """
    import akshare as ak
    start_year = str(datetime.now().year - 4)
    df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
    # 只取年报行（日期以12-31结尾）
    df["日期"] = df["日期"].astype(str)
    annual = df[df["日期"].str.endswith("12-31")].sort_values("日期")

    def col(*names):
        """按候选列名取列（接口列名历史上有变化，做模糊兜底）。"""
        for n in names:
            if n in annual.columns:
                return [to_float(x) for x in annual[n].tolist()]
        return [None] * len(annual)

    roe = col("净资产收益率(%)", "加权净资产收益率(%)")
    net_profit = col("扣除非经常性损益后的净利润(元)", "净利润(元)")
    debt_ratio = col("资产负债率(%)")
    main_income = col("主营业务收入(元)")
    cash_ratio = col("销售商品提供劳务收到的现金与主营业务收入比率(%)",
                     "经营现金净流量对销售收入比率(%)")
    years = annual["日期"].str[:4].tolist()

    return {
        "报告年度": years,
        "ROE各年_pct": roe,                       # 百分数口径
        "ROE近3年均值_pct": avg(roe[-3:]),
        "净利润各年": net_profit,
        "净利润近3年为正年数": sum(1 for x in net_profit[-3:] if x is not None and x > 0),
        "净利润最新同比": yoy_growth(net_profit[-1] if net_profit else None,
                                net_profit[-2] if len(net_profit) > 1 else None),
        "营收最新同比": yoy_growth(main_income[-1] if main_income else None,
                               main_income[-2] if len(main_income) > 1 else None),
        "资产负债率_pct": debt_ratio[-1] if debt_ratio else None,
        "收现比_pct": cash_ratio[-1] if cash_ratio else None,   # 接口无净现比，标缺失
        "净现比_pct": None,
    }


def _dividends(code: str) -> dict:
    """分红记录：连续分红年数、近3年派现比例均值（东财分红送配接口）。"""
    import akshare as ak
    df = ak.stock_fhps_detail_em(symbol=code)
    df = df.dropna(subset=["现金分红-现金分红比例"])
    df["年度"] = df["报告期"].astype(str).str[:4]
    # 每年度有现金分红即计一年
    paid_years = sorted(set(int(y) for y, v in zip(df["年度"], df["现金分红-现金分红比例"])
                            if to_float(v) and to_float(v) > 0))
    # 从最近有分红的年度向前数连续年数
    consecutive = 0
    if paid_years:
        y = paid_years[-1]
        while y in paid_years:
            consecutive += 1
            y -= 1
    payout = [to_float(x) for x in df["现金分红-股息率"].tolist()][-3:] \
        if "现金分红-股息率" in df.columns else []
    return {
        "连续分红年数": consecutive,
        "最近分红年度": paid_years[-1] if paid_years else None,
        "分红年度列表": paid_years[-8:],
        "近3年股息率记录_pct": payout,
        # 派现比例（分红/净利润）接口无直接字段，需分析层用 每股分红/每股收益 估算或人工核验
        "派现比例说明": "东财接口无派现比例直接字段，报告中如无法推算须标注人工核验",
    }


def _pledge_ratio(code: str) -> float | None:
    """大股东质押率（全市场质押比例表中检索该股票）。门槛：<30%。"""
    import akshare as ak
    df = ak.stock_gpzy_pledge_ratio_em()
    row = df[df["股票代码"].astype(str).str.zfill(6) == code]
    if len(row) == 0:
        return None
    v = to_float(row.iloc[0].get("质押比例"))
    return v / 100 if v is not None else None


def _announcements(code: str) -> list[dict]:
    """最新公告（巨潮，近30天，最多10条）。"""
    import akshare as ak
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol=code, market="沪深京", start_date=start, end_date=end)
    cols = [c for c in ["公告标题", "公告时间"] if c in df.columns]
    return df_to_records(df[cols], limit=10)


def _peers(code: str, industry: str | None) -> dict:
    """同业对比：行业成分股表现与行业均值PE（东财口径，见valuation_model待确认#2）。"""
    import akshare as ak
    if not industry:
        return {"行业": None, "行业均值PE": None, "成分股样本": []}
    df = ak.stock_board_industry_cons_em(symbol=industry)
    pe_col = next((c for c in df.columns if "市盈率" in c), None)
    pes = [to_float(x) for x in df[pe_col].tolist()] if pe_col else []
    pes = [x for x in pes if x is not None and 0 < x < 500]   # 剔除异常值
    cols = [c for c in ["代码", "名称", "最新价", "涨跌幅", pe_col] if c in df.columns]
    return {
        "行业": industry,
        "行业均值PE": (sum(pes) / len(pes)) if pes else None,
        "行业均值PE口径": "东方财富行业板块成分股市盈率简单平均（剔除负值与>500）",
        "成分股样本": df_to_records(df[cols], limit=15),
    }


def _institution_forecast(code: str) -> dict | None:
    """机构盈利预测（东财一致预期，用于择时评分'机构预期'项的参考输入）。"""
    import akshare as ak
    df = ak.stock_profit_forecast_em(symbol="全部板块")
    row = df[df["代码"].astype(str).str.zfill(6) == code]
    if len(row) == 0:
        return None
    return df_to_records(row)[0]


# =============================================================================
# 对外统一入口
# =============================================================================

def get_stock_snapshot(code: str, refresh: bool = False) -> dict:
    """
    拉取个股全量快照。code 支持 '600519' / 'sh600519' / '600519.SH'。
    返回结构化dict；失败项为None并登记 errors。
    """
    code = normalize_stock_code(code)
    snapshot: dict = {"code": code, "generated_at": datetime.now().isoformat()}
    errors: dict = {}

    basic, err = _fetch(f"stock_{code}_basic", lambda: _basic_info(code), refresh=refresh)
    snapshot["basic"] = basic
    if err:
        errors["basic"] = err
    industry = (basic or {}).get("行业")

    jobs = {
        "quote": lambda: _quote_summary(code),
        "valuation": lambda: _valuation(code),
        "financials": lambda: _financials(code),
        "dividends": lambda: _dividends(code),
        "pledge_ratio": lambda: _pledge_ratio(code),
        "announcements": lambda: _announcements(code),
        "peers": lambda: _peers(code, industry),
        "institution_forecast": lambda: _institution_forecast(code),
        "cn10y_yield": _cn10y_yield,
    }
    for key, func in jobs.items():
        data, err = _fetch(f"stock_{code}_{key}", func, refresh=refresh)
        snapshot[key] = data
        if err:
            errors[key] = err

    # 体系必需但公开接口不可得的字段：显式标注人工核验（禁止臆断）
    snapshot["manual_check_fields"] = {
        "审计意见": "接口不可得，需人工核验年报审计意见（门槛：标准无保留）",
        "近3年监管函": "接口不可得，需人工核验（门槛：无涉财务真实性监管函）",
    }
    snapshot["errors"] = errors
    return snapshot


if __name__ == "__main__":
    # 直接运行 = 现场验证：python -m src.data.stock_data 600519
    import json
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "600519"
    snap = get_stock_snapshot(target)
    print(json.dumps(snap, ensure_ascii=False, indent=2, default=str)[:3000])
