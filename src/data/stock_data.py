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

from src.data.data_utils import (avg, days_above_ma, df_to_records, drawdown_from_high,
                                 load_cache, moving_average, normalize_stock_code,
                                 percentile_rank, save_cache, tech_bottom_signal,
                                 to_float, volume_stall_signal, yoy_growth)
from src.data.market_data import _cn10y_yield, _fetch

# PE/PB历史分位窗口：近10年（v4.2.0裁决#4正式口径；数据源三级链：乐咕→自算→百度）
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
        "站上60日线连续天数": days_above_ma(closes, 60),        # 右侧确认：≥3日
        "技术底部信号": tech_bottom_signal(closes),             # v4.2.0裁决#5
        "放量滞涨信号": volume_stall_signal(closes, amounts),   # v4.2.0裁决#12
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
            "分位样本数": len([x for x in pe_series if x is not None]),
            "PE分位值": _series_quantiles(pe_series),   # 多锚估值用：30/50/70分位对应的PE水平
            "PB分位值": _series_quantiles(pb_series),
        }
    except Exception:
        pass
    # 二级源（v4.2.0裁决#4）：新浪价格 + 年报EPS/BPS 自算近10年分位（阶梯近似口径）
    try:
        return _valuation_selfcalc(code)
    except Exception:
        pass
    # 末级兜底：百度估值序列（约2年窗口，必须标注）。股息率由分红模块口径兜底
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
        "分位窗口": f"约{years}年（百度末级兜底，非近10年口径，报告须标注）",
        "分位样本数": len([x for x in pe_series if x is not None]),
        "PE分位值": _series_quantiles(pe_series),
        "PB分位值": _series_quantiles(pb_series),
    }


def _series_quantiles(series: list) -> dict | None:
    """序列的30/50/70分位水平值（多锚估值的历史分位法参数）。剔除非正值。"""
    clean = sorted(x for x in (to_float(v) for v in series) if x is not None and x > 0)
    if len(clean) < 30:
        return None

    def q(p: float) -> float:
        idx = min(len(clean) - 1, max(0, int(round(p * (len(clean) - 1)))))
        return round(clean[idx], 2)

    return {"p30": q(0.30), "p50": q(0.50), "p70": q(0.70)}


def _valuation_selfcalc(code: str) -> dict:
    """
    分位二级源（v4.2.0裁决#4）：新浪日线（近10年，不复权价）+ 年报EPS/BPS阶梯 →
    自算 PE/PB 序列并计算分位。口径：EPS/BPS按年报阶梯（用上一年度年报值），近似静态口径。
    """
    import akshare as ak
    daily = ak.stock_zh_a_daily(symbol=_sina_symbol(code))   # 不复权，与EPS同口径
    daily = daily.tail(252 * PERCENTILE_YEARS)
    fin = ak.stock_financial_analysis_indicator(
        symbol=code, start_year=str(datetime.now().year - PERCENTILE_YEARS - 1))
    fin["日期"] = fin["日期"].astype(str)
    annual = fin[fin["日期"].str.endswith("12-31")].sort_values("日期")

    def col(*names):
        for n in names:
            if n in annual.columns:
                return {str(r["日期"])[:4]: to_float(r[n]) for _, r in annual.iterrows()}
        return {}

    eps_by_year = col("摊薄每股收益(元)", "加权每股收益(元)", "每股收益_调整后(元)")
    bps_by_year = col("每股净资产_调整后(元)", "每股净资产(元)")

    pe_series, pb_series = [], []
    for _, r in daily.iterrows():
        close = to_float(r.get("close"))
        y = str(r.get("date"))[:4]
        prev_y = str(int(y) - 1)   # 阶梯：当年价格对上一年年报
        e, b = eps_by_year.get(prev_y), bps_by_year.get(prev_y)
        pe_series.append(close / e if (close and e and e > 0) else None)
        pb_series.append(close / b if (close and b and b > 0) else None)
    pe = next((x for x in reversed(pe_series) if x is not None), None)
    pb = next((x for x in reversed(pb_series) if x is not None), None)
    if pe is None and pb is None:
        raise ValueError("自算序列为空")
    return {
        "PE_TTM": pe,   # 注意：静态阶梯口径，非严格TTM
        "PB": pb,
        "股息率TTM": None,
        "PE历史分位": percentile_rank(pe_series, pe),
        "PB历史分位": percentile_rank(pb_series, pb),
        "分位窗口": f"近{PERCENTILE_YEARS}年（二级源自算：新浪价格×年报EPS/BPS阶梯，静态近似口径）",
        "分位样本数": len([x for x in pe_series if x is not None]),
        "PE分位值": _series_quantiles(pe_series),
        "PB分位值": _series_quantiles(pb_series),
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
    eps = col("摊薄每股收益(元)", "加权每股收益(元)", "每股收益_调整后(元)")
    bps = col("每股净资产_调整后(元)", "每股净资产(元)")
    years = annual["日期"].str[:4].tolist()

    return {
        "报告年度": years,
        "ROE各年_pct": roe,                       # 百分数口径
        "ROE近3年均值_pct": avg(roe[-3:]),
        "BPS最新": next((x for x in reversed(bps) if x), None),   # v4.3.0：PB法估值基数
        "营收各年": main_income,                                    # v4.3.0：PS法估值输入
        "净利润各年": net_profit,
        "净利润近3年为正年数": sum(1 for x in net_profit[-3:] if x is not None and x > 0),
        "净利润近3年全为负": (len(net_profit[-3:]) == 3 and
                        all(x is not None and x < 0 for x in net_profit[-3:])),
        "净利润最新同比": yoy_growth(net_profit[-1] if net_profit else None,
                                net_profit[-2] if len(net_profit) > 1 else None),
        "营收最新同比": yoy_growth(main_income[-1] if main_income else None,
                               main_income[-2] if len(main_income) > 1 else None),
        "资产负债率_pct": debt_ratio[-1] if debt_ratio else None,
        "EPS各年": eps,                            # v4.2.0：派现比例与机构预期CAGR计算基数
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
    # 每股现金分红按年度汇总（现金分红比例=每10股派X元 → /10 为每股；同年多次分红累加）
    dps_by_year: dict[int, float] = {}
    for y, v in zip(df["年度"], df["现金分红-现金分红比例"]):
        val = to_float(v)
        if val and val > 0:
            dps_by_year[int(y)] = dps_by_year.get(int(y), 0.0) + val / 10.0
    return {
        "连续分红年数": consecutive,
        "最近分红年度": paid_years[-1] if paid_years else None,
        "分红年度列表": paid_years[-8:],
        "近3年股息率记录_pct": payout,
        "每股分红按年度": {str(k): round(v, 4) for k, v in sorted(dps_by_year.items())},
        # v4.2.0：派现比例 = 每股分红/当年EPS，由 get_stock_snapshot 汇总计算
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
    """
    机构一致预测（v4.2.0：同花顺预测年报EPS，用于'机构预期CAGR≥15%'自动评分）。
    返回 {"预测": [{年度, 均值}...], "口径": ...}；CAGR由分析层结合最近实际EPS计算。
    """
    import akshare as ak
    df = ak.stock_profit_forecast_ths(symbol=code, indicator="预测年报每股收益")
    if df is None or len(df) == 0:
        return None
    rows = [{"年度": str(r.get("年度")), "均值": to_float(r.get("均值")),
             "预测机构数": r.get("预测机构数")} for _, r in df.iterrows()]
    return {"预测EPS": rows, "口径": "同花顺一致预测年报EPS均值（v4.2.0裁决）"}


# ---- v4.2.0 新增自动化数据项 --------------------------------------------------

# 公告标题关键词（stock_selection.yaml veto_rules auto_rule 的执行口径）
SCAN_KEYWORDS = {
    "audit": ["保留意见", "无法表示意见", "否定意见", "带强调事项段"],
    "regulatory": ["监管函", "警示函", "监管工作函", "年报问询函"],
    "lawsuit": ["重大诉讼", "重大仲裁"],
    "fraud": ["立案调查", "财务造假", "虚假记载", "重大会计差错"],
}


def _announcement_scan(code: str) -> dict:
    """
    近3年公告标题关键词扫描（分段拉取，巨潮限制单次跨度）。
    输出各类命中的公告标题列表 + 实际覆盖窗口（部分段失败时如实缩窗）。
    """
    import time as _t
    import akshare as ak
    hits = {k: [] for k in SCAN_KEYWORDS}
    covered_from, total = None, 0
    for i in range(6):   # 6段×181天 ≈ 3年
        seg_end = datetime.now() - timedelta(days=182 * i)
        seg_start = seg_end - timedelta(days=181)
        try:
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol=code, market="沪深京",
                start_date=seg_start.strftime("%Y%m%d"), end_date=seg_end.strftime("%Y%m%d"))
            total += len(df)
            covered_from = seg_start.strftime("%Y-%m-%d")
            for _, r in df.iterrows():
                title = str(r.get("公告标题") or "")
                when = str(r.get("公告时间") or "")
                for cat, kws in SCAN_KEYWORDS.items():
                    if any(k in title for k in kws):
                        hits[cat].append({"标题": title[:60], "时间": when[:10]})
        except Exception:
            break   # 分段失败即止：覆盖窗口如实缩短并标注
        _t.sleep(0.5)
    year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    return {
        "命中": hits,
        "近1年审计类命中": [h for h in hits["audit"] if h["时间"] >= year_ago],
        "近1年诉讼类命中": [h for h in hits["lawsuit"] if h["时间"] >= year_ago],
        "扫描公告数": total,
        "覆盖起点": covered_from,
        "口径": "公告标题关键词扫描（v4.2.0裁决#7）；无命中→推定通过，推定口径在报告标注",
    }


def _margin_position(code: str, day: datetime) -> float | None:
    """取指定日期附近（向前找5个交易日）该股融资余额；沪市走SSE明细，深市走SZSE明细。"""
    import akshare as ak
    sse = code.startswith(("6", "9"))
    for back in range(6):
        d = (day - timedelta(days=back)).strftime("%Y%m%d")
        try:
            if sse:
                df = ak.stock_margin_detail_sse(date=d)
                col_code, col_bal = "标的证券代码", "融资余额"
            else:
                df = ak.stock_margin_detail_szse(date=d)
                col_code, col_bal = "证券代码", "融资余额"
            if df is None or len(df) == 0:
                continue
            row = df[df[col_code].astype(str).str.zfill(6) == code]
            if len(row):
                return to_float(row.iloc[0][col_bal])
            return None   # 当日有数据但无此标的（非两融标的）
        except Exception:
            continue
    return None


def _margin_signal(code: str) -> dict:
    """择时评分'资金信号'（v4.2.0）：当前融资余额 > 10个交易日前 → 1分。"""
    now = datetime.now()
    latest = _margin_position(code, now - timedelta(days=1))
    prev = _margin_position(code, now - timedelta(days=15))   # ≈10个交易日前
    signal = (latest is not None and prev is not None and latest > prev)
    return {"最新融资余额": latest, "10日前融资余额": prev, "signal": signal,
            "口径": "交易所两融明细；北向数据已停发按裁决删除，采用原文并列选项'融资余额上升'"}


def _cashflow_ratios(code: str) -> dict:
    """
    收现比/净现比（B/C类分类门槛，v4.2.0自动化）：
    收现比 = 销售商品提供劳务收到的现金 / 营业收入；净现比 = 经营净现金流 / 净利润。
    数据源：新浪年度财务报表。
    """
    import akshare as ak
    sym = _sina_symbol(code)
    cf = ak.stock_financial_report_sina(stock=sym, symbol="现金流量表")
    pl = ak.stock_financial_report_sina(stock=sym, symbol="利润表")

    def annual_rows(df):
        df = df.copy()
        df["报告日"] = df["报告日"].astype(str)
        return df[df["报告日"].str.endswith("1231")].sort_values("报告日").tail(3)

    def col_val(row, *names):
        for n in names:
            if n in row.index:
                v = to_float(row[n])
                if v is not None:
                    return v
        return None

    cf3, pl3 = annual_rows(cf), annual_rows(pl)
    out = []
    for (_, c), (_, p) in zip(cf3.iterrows(), pl3.iterrows()):
        sales_cash = col_val(c, "销售商品、提供劳务收到的现金")
        op_cash = col_val(c, "经营活动产生的现金流量净额", "经营活动产生的现金流量")
        revenue = col_val(p, "营业收入", "一、营业总收入", "营业总收入")
        profit = col_val(p, "净利润", "五、净利润")
        out.append({
            "年度": str(c["报告日"])[:4],
            "收现比": (sales_cash / revenue) if (sales_cash and revenue) else None,
            "净现比": (op_cash / profit) if (op_cash and profit and profit > 0) else None,
        })
    latest = out[-1] if out else {}
    return {"各年": out, "收现比_最新": latest.get("收现比"), "净现比_最新": latest.get("净现比"),
            "口径": "新浪年度报表：销售收现/营业收入、经营净现流/净利润（金融行业不适用，分析层按类豁免）"}


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
        "announcement_scan": lambda: _announcement_scan(code),   # v4.2.0 合规扫描
        "cashflow_ratios": lambda: _cashflow_ratios(code),       # v4.2.0 收现比/净现比
        "margin_signal": lambda: _margin_signal(code),           # v4.2.0 资金信号
        "peers": lambda: _peers(code, industry),
        "institution_forecast": lambda: _institution_forecast(code),
        "cn10y_yield": _cn10y_yield,
    }
    for key, func in jobs.items():
        data, err = _fetch(f"stock_{code}_{key}", func, refresh=refresh)
        snapshot[key] = data
        if err:
            errors[key] = err

    # v4.2.0：派现比例自动计算（每股分红/当年EPS，逐年对齐后取近3年）
    snapshot["payout"] = _compute_payout(snapshot.get("dividends"), snapshot.get("financials"))
    snapshot["errors"] = errors
    return snapshot


def _compute_payout(dividends: dict | None, financials: dict | None) -> dict:
    """派现比例 = 每股现金分红(税前) / 当年EPS（v4.2.0裁决：巨潮/东财分红记录×新浪年报EPS）。"""
    if not dividends or not financials:
        return {"近3年派现比例": [], "近3年均值": None, "最新年度派息率": None,
                "口径": "分红或EPS数据缺失，无法计算"}
    dps = dividends.get("每股分红按年度") or {}
    years = financials.get("报告年度") or []
    eps = financials.get("EPS各年") or []
    eps_by_year = {str(y): e for y, e in zip(years, eps) if e}
    ratios = []
    for y in sorted(set(dps) & set(eps_by_year))[-3:]:
        d, e = to_float(dps[y]), to_float(eps_by_year[y])
        if d is not None and e:
            ratios.append({"年度": y, "派现比例": d / e})
    vals = [r["派现比例"] for r in ratios]
    return {"近3年派现比例": ratios,
            "近3年均值": sum(vals) / len(vals) if vals else None,
            "最新年度派息率": vals[-1] if vals else None,
            "口径": "每股现金分红(税前)/当年EPS（v4.2.0自动化口径）"}


if __name__ == "__main__":
    # 直接运行 = 现场验证：python -m src.data.stock_data 600519
    import json
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "600519"
    snap = get_stock_snapshot(target)
    print(json.dumps(snap, ensure_ascii=False, indent=2, default=str)[:3000])
