# -*- coding: utf-8 -*-
"""
stock_analyzer.py — 个股分析引擎（规则执行器，v4.2.0 全自动化口径）
输入 stock_data.get_stock_snapshot() 快照，严格对照 config/ 铁则层逐项判定：
  一票否决 → 股息组合门槛（通用+分类）→ 择时组合门槛与评分 → 估值分级 → 风控参数。
核心纪律：
  - 判定结果三态：PASS / FAIL / MISSING(数据缺失)；
  - 推定类判定（如公告扫描无命中→推定审计正常）必须在 detail 中标注推定口径；
  - 数据缺失绝不按通过处理；不新增 config 之外的标准；
  - v4.2.0：原"待人工"项全部按用户裁决替换为量化自动口径（见各 auto_rule）。
"""
from __future__ import annotations

from src.data.data_utils import fmt_pct, safe_get, to_float
from src.utils.file_utils import DIRS, load_config, read_json

PASS, FAIL, MISSING = "PASS", "FAIL", "MISSING"

WATCHLIST_FILE = DIRS["data_processed"] / "watchlist.json"

# -----------------------------------------------------------------------------
# 行业归类（v4.2.0自动口径：证监会行业名关键词映射；未命中A/B → C类兜底=最严ROE门槛，偏保守）
# -----------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "A_financial": ["银行", "保险", "证券", "金融"],
    "B_cyclical": ["煤", "石油", "石化", "有色", "钢铁", "矿", "化学原料", "开采"],
}
CIRCLE_KEYWORDS = {
    "core": ["汽车", "新能源车", "保险", "储能", "电池", "消费电子"],
    "excluded": ["医药", "生物制品", "创新药", "农", "军工", "航天", "兵器"],
    "satellite": ["软件", "AI", "人工智能", "医疗器械", "银行", "煤", "运营商", "电信", "通信"],
}


def classify_industry(industry: str | None) -> dict:
    """行业→分类门槛类别 + 能力圈归属（v4.2.0全自动：未映射按保守默认）。"""
    if not industry:
        return {"category": "C_stable", "circle": None,
                "note": "行业数据缺失：分类按C类兜底（最严ROE门槛），能力圈按卫星减半保守口径"}
    category = next((cat for cat, kws in CATEGORY_KEYWORDS.items()
                     if any(k in industry for k in kws)), "C_stable")
    circle = next((c for c in ("core", "excluded", "satellite")
                   if any(k in industry for k in CIRCLE_KEYWORDS[c])), None)
    cat_note = ("关键词命中" if category != "C_stable" or any(
        k in industry for k in ["酒", "饮料", "食品", "家电", "电力", "制造", "运输", "通信"])
        else "未命中A/B类关键词，按C类兜底（偏保守）")
    return {"category": category, "circle": circle,
            "note": f"行业'{industry}'自动归类：{cat_note}；能力圈={circle or '未映射→按卫星减半保守口径'}"}


def _check(name: str, status: str, detail: str, source: str = "") -> dict:
    """统一判定记录结构。"""
    return {"item": name, "status": status, "detail": detail, "source": source}


def _watchlist() -> list[dict]:
    """备选池清单（data/processed/watchlist.json，run.py watchlist 命令维护）。"""
    return (read_json(WATCHLIST_FILE) or {}).get("stocks", [])


def in_watchlist(code: str) -> bool:
    return any(str(s.get("code")).zfill(6) == code for s in _watchlist())


# -----------------------------------------------------------------------------
# 一票否决（v4.2.0：公告扫描+盈利结构全自动）
# -----------------------------------------------------------------------------
def evaluate_veto(snap: dict, config: dict) -> list[dict]:
    checks = []
    name = safe_get(snap, "basic", "名称") or ""
    if name:
        st = ("ST" in name.upper()) or ("退" in name)
        checks.append(_check("ST/退市风险股", FAIL if st else PASS, f"证券简称：{name}", "§2.1"))
    else:
        checks.append(_check("ST/退市风险股", MISSING, "名称数据缺失", "§2.1"))

    pledge = snap.get("pledge_ratio")
    if pledge is None:
        checks.append(_check("大股东质押率<30%", MISSING, "质押数据缺失", "§2.1/§2.2"))
    else:
        checks.append(_check("大股东质押率<30%", PASS if pledge < 0.30 else FAIL,
                             f"当前质押比例 {fmt_pct(pledge)}", "§2.1/§2.2"))

    scan = snap.get("announcement_scan")
    if not scan:
        checks.append(_check("公告合规扫描（审计/监管函/财务疑点/诉讼）", MISSING,
                             "公告扫描数据缺失，无法执行推定判定", "§2.1裁决#7"))
    else:
        cover = f"扫描{scan.get('扫描公告数')}条公告，覆盖自{scan.get('覆盖起点')}"
        audit_hits = scan.get("近1年审计类命中") or []
        checks.append(_check("审计意见=标准无保留", FAIL if audit_hits else PASS,
                             (f"近1年命中审计异常关键词：{audit_hits[:2]}" if audit_hits
                              else f"近1年无审计异常关键词命中→推定标准无保留（{cover}）"), "§2.1裁决#7"))
        reg_hits = safe_get(scan, "命中", "regulatory") or []
        checks.append(_check("近3年无涉财务真实性监管函", FAIL if reg_hits else PASS,
                             (f"命中：{[h['标题'] for h in reg_hits[:3]]}" if reg_hits
                              else f"3年窗口无监管函类关键词命中→推定通过（{cover}）"), "§2.2裁决#7"))
        fraud_hits = safe_get(scan, "命中", "fraud") or []
        checks.append(_check("无财务造假/立案调查记录", FAIL if fraud_hits else PASS,
                             (f"命中：{[h['标题'] for h in fraud_hits[:3]]}" if fraud_hits
                              else "无造假类关键词命中→推定通过"), "§2.1裁决#7"))
        law_hits = scan.get("近1年诉讼类命中") or []
        checks.append(_check("重大诉讼提示（命中不直接否决，报告P1预警）",
                             PASS if not law_hits else PASS,
                             (f"⚠️近1年命中重大诉讼/仲裁公告：{[h['标题'] for h in law_hits[:3]]}（P1预警，影响需结合金额）"
                              if law_hits else "近1年无重大诉讼/仲裁公告"), "§2.1裁决#7"))

    fin = snap.get("financials") or {}
    mv = safe_get(snap, "basic", "总市值")
    all_neg = fin.get("净利润近3年全为负")
    if all_neg is None or mv is None:
        checks.append(_check("纯概念股量化判定", MISSING, "盈利或市值数据缺失", "§2.1裁决#7"))
    else:
        concept = bool(all_neg) and mv > 2e10
        checks.append(_check("纯概念股量化判定", FAIL if concept else PASS,
                             ("近3年净利全负且市值>200亿→概念股嫌疑" if concept
                              else "盈利结构不符合概念股特征（近3年净利并非全负）"), "§2.1裁决#7"))
    return checks


# -----------------------------------------------------------------------------
# 股息组合视角（v4.2.0：派现比例/派息率/分红趋势/收现净现全自动）
# -----------------------------------------------------------------------------
def effective_dividend_yield(snap: dict) -> tuple[float | None, str]:
    """股息率取值统一口径：优先TTM，缺失时回退最近年度分红记录（口径随值返回）。"""
    dv = safe_get(snap, "valuation", "股息率TTM")
    if dv is not None:
        return dv, "TTM"
    recs = safe_get(snap, "dividends", "近3年股息率记录_pct") or []
    dv = to_float(recs[-1]) if recs else None
    return dv, "最近年度分红记录（非TTM，口径标注）" if dv is not None else "缺失"


def _dividend_trend(snap: dict, years_required: int, non_decreasing: bool) -> tuple[str, str]:
    """分红趋势自动判定：近N年不间断（可选：且每股分红非递减）。"""
    dps = safe_get(snap, "dividends", "每股分红按年度") or {}
    consecutive = safe_get(snap, "dividends", "连续分红年数")
    if consecutive is None:
        return MISSING, "分红记录缺失"
    if consecutive < years_required:
        return FAIL, f"连续分红仅{consecutive}年 < 要求{years_required}年"
    if non_decreasing and dps:
        recent = [v for _, v in sorted(dps.items())[-3:]]
        if len(recent) >= 2 and any(b < a for a, b in zip(recent, recent[1:])):
            return FAIL, f"近3年每股分红出现递减：{[round(x,3) for x in recent]}"
        return PASS, f"连续{consecutive}年且近3年每股分红非递减：{[round(x,3) for x in recent]}"
    return PASS, f"连续分红{consecutive}年（≥{years_required}年）"


def evaluate_dividend_track(snap: dict, config: dict) -> dict:
    checks = []
    cn10y = snap.get("cn10y_yield")
    payout = snap.get("payout") or {}

    mv = safe_get(snap, "basic", "总市值")
    checks.append(_check("市值≥300亿", MISSING if mv is None else (PASS if mv >= 3e10 else FAIL),
                         f"总市值 {mv/1e8:.0f}亿" if mv else "缺失", "§2.2通用"))
    t20 = safe_get(snap, "quote", "近20日日均成交额")
    checks.append(_check("近20日日均成交额>1亿", MISSING if t20 is None else (PASS if t20 > 1e8 else FAIL),
                         f"{t20/1e8:.2f}亿/日" if t20 else "缺失", "§2.2通用"))
    dy_years = safe_get(snap, "dividends", "连续分红年数")
    checks.append(_check("连续分红≥5年", MISSING if dy_years is None else (PASS if dy_years >= 5 else FAIL),
                         f"连续分红 {dy_years} 年" if dy_years is not None else "缺失", "§2.2通用"))

    p3 = payout.get("近3年均值")
    checks.append(_check("派现比例近3年均值≥30%",
                         MISSING if p3 is None else (PASS if p3 >= 0.30 else FAIL),
                         (f"{fmt_pct(p3)}（{payout.get('口径')}）" if p3 is not None
                          else "分红/EPS数据不足，无法计算"), "§2.2通用·裁决自动化"))
    dv, dv_note = effective_dividend_yield(snap)
    if dv is None or cn10y is None:
        checks.append(_check("股息率≥max(1.5×10年国债, 3%)", MISSING, "股息率或国债收益率缺失", "§2.2通用"))
    else:
        floor = max(1.5 * cn10y, 0.03)
        checks.append(_check("股息率≥max(1.5×10年国债, 3%)", PASS if dv >= floor else FAIL,
                             f"股息率 {fmt_pct(dv)}（口径：{dv_note}） vs 门槛 {fmt_pct(floor)}（10年国债 {fmt_pct(cn10y)}）", "§2.2通用"))
    p_latest = payout.get("最新年度派息率")
    checks.append(_check("派息率<80%",
                         MISSING if p_latest is None else (PASS if p_latest < 0.80 else FAIL),
                         f"最新年度 {fmt_pct(p_latest)}" if p_latest is not None else "数据不足", "§2.2通用·裁决自动化"))
    checks.append(_check("合规三项（审计/质押/监管）", PASS, "由一票否决检查表自动判定（推定口径见该表）", "§2.2通用"))

    # 分类财务门槛
    cls = classify_industry(safe_get(snap, "basic", "行业"))
    cat = cls["category"]
    cat_checks = []
    fin = snap.get("financials") or {}
    cash = snap.get("cashflow_ratios") or {}
    roe_avg = fin.get("ROE近3年均值_pct")
    profit_pos_years = fin.get("净利润近3年为正年数")
    debt = fin.get("资产负债率_pct")
    cat_rules = safe_get(config, "stock_selection", "dividend_portfolio", "category_thresholds") or {}
    cat_name = safe_get(cat_rules, cat, "name") or cat
    roe_min = {"A_financial": 8, "B_cyclical": 10, "C_stable": 12}[cat]
    cat_checks.append(_check(f"[{cat_name}] ROE近3年均值≥{roe_min}%",
                             MISSING if roe_avg is None else (PASS if roe_avg >= roe_min else FAIL),
                             f"ROE近3年均值 {roe_avg:.2f}%" if roe_avg is not None else "缺失", "§2.2分类"))
    need_pos = 2 if cat == "B_cyclical" else 3
    cat_checks.append(_check(f"净利润近3年至少{need_pos}年为正",
                             MISSING if profit_pos_years is None else (PASS if profit_pos_years >= need_pos else FAIL),
                             f"近3年为正 {profit_pos_years} 年" if profit_pos_years is not None else "缺失", "§2.2分类"))
    trend_status, trend_detail = _dividend_trend(
        snap, years_required=5 if cat == "B_cyclical" else 3,
        non_decreasing=(cat == "C_stable"))
    cat_checks.append(_check("分红趋势", trend_status, trend_detail, "§2.2分类·自动化"))
    if cat == "A_financial":
        cat_checks.append(_check("[金融] 不良率/拨备覆盖/资本充足率", PASS,
                                 "无公开可编程数据源→移出自动判定（按用户'没有就删掉'指示），"
                                 "命中A类时在报告风险区提示该三项未核", "§2.2分类·裁决"))
    else:
        debt_max = 65 if cat == "B_cyclical" else 60
        cat_checks.append(_check(f"资产负债率≤{debt_max}%",
                                 MISSING if debt is None else (PASS if debt <= debt_max else FAIL),
                                 f"{debt:.1f}%" if debt is not None else "缺失", "§2.2分类"))
        sx_min = 0.80 if cat == "B_cyclical" else 0.90
        jx_min = 0.70 if cat == "B_cyclical" else 0.80
        sx, jx = cash.get("收现比_最新"), cash.get("净现比_最新")
        cat_checks.append(_check(f"收现比≥{fmt_pct(sx_min, 0)}",
                                 MISSING if sx is None else (PASS if sx >= sx_min else FAIL),
                                 f"{fmt_pct(sx)}（新浪年报口径）" if sx is not None else "现金流数据缺失", "§2.2分类·自动化"))
        cat_checks.append(_check(f"净现比≥{fmt_pct(jx_min, 0)}",
                                 MISSING if jx is None else (PASS if jx >= jx_min else FAIL),
                                 f"{fmt_pct(jx)}（新浪年报口径）" if jx is not None else "现金流数据缺失", "§2.2分类·自动化"))

    val_result = evaluate_dividend_valuation(snap, config)

    statuses = [c["status"] for c in checks + cat_checks]
    if FAIL in statuses:
        verdict = "排除（存在门槛FAIL项）"
    elif MISSING in statuses:
        verdict = "观察池（存在数据缺失项，缺失不按通过处理；补数后自动重判）"
    else:
        verdict = ("候选池：通用+分类门槛全过" +
                   ("，且满足估值买入标准（可按§3.1建仓节奏跟踪）" if val_result["met"]
                    else "，但当前估值未达买入标准（持有观察）"))
    return {"universal": checks, "category": cat_checks, "classify": cls,
            "valuation_buy": val_result, "verdict": verdict,
            "bonus_note": "加分条件5项为定性判断，不参与自动判定；核心池/观察池归类以门槛+估值自动结果为准"}


def evaluate_dividend_valuation(snap: dict, config: dict) -> dict:
    """§2.2 估值买入标准：三选一。"""
    pe = safe_get(snap, "valuation", "PE_TTM")
    pb = safe_get(snap, "valuation", "PB")
    pe_pct = safe_get(snap, "valuation", "PE历史分位")
    pb_pct = safe_get(snap, "valuation", "PB历史分位")
    window = safe_get(snap, "valuation", "分位窗口")
    ind_pe = safe_get(snap, "peers", "行业均值PE")
    dv, dv_note = effective_dividend_yield(snap)
    cn10y = snap.get("cn10y_yield")

    items = []
    if pe is None or pe_pct is None:
        items.append(_check("PE≤行业均值 且 历史30%分位以下", MISSING, "PE或分位缺失", "§2.2"))
    elif ind_pe is None:
        items.append(_check("PE≤行业均值 且 历史30%分位以下", MISSING,
                            f"PE {pe}（分位 {fmt_pct(pe_pct)}，窗口{window}）；行业均值PE缺失，无法完整判定", "§2.2"))
    else:
        ok = (pe <= ind_pe) and (pe_pct <= 0.30)
        items.append(_check("PE≤行业均值 且 历史30%分位以下", PASS if ok else FAIL,
                            f"PE {pe} vs 行业均值 {ind_pe:.1f}；分位 {fmt_pct(pe_pct)}（窗口{window}）", "§2.2"))
    if pb is None or pb_pct is None:
        items.append(_check("PB≤2.5 且 历史30%分位以下", MISSING, "PB或分位缺失", "§2.2"))
    else:
        ok = (pb <= 2.5) and (pb_pct <= 0.30)
        items.append(_check("PB≤2.5 且 历史30%分位以下", PASS if ok else FAIL,
                            f"PB {pb}；分位 {fmt_pct(pb_pct)}（窗口{window}）", "§2.2"))
    if dv is None or cn10y is None:
        items.append(_check("股息率≥2×10年国债", MISSING, "股息率或国债收益率缺失", "§2.2"))
    else:
        items.append(_check("股息率≥2×10年国债", PASS if dv >= 2 * cn10y else FAIL,
                            f"{fmt_pct(dv)}（口径：{dv_note}） vs 门槛 {fmt_pct(2*cn10y)}", "§2.2"))
    met = any(c["status"] == PASS for c in items)
    return {"items": items, "met": met,
            "conclusion": "满足估值买入标准（≥1条PASS）" if met else "未满足/无法完整判定估值买入标准"}


# -----------------------------------------------------------------------------
# 择时组合视角（v4.2.0：备选池watchlist机制 + 4项自动评分）
# -----------------------------------------------------------------------------
def evaluate_timing_track(snap: dict, config: dict, market_state: str | None) -> dict:
    code = snap.get("code")
    pool = []
    mv = safe_get(snap, "basic", "总市值")
    pool.append(_check("市值≥200亿", MISSING if mv is None else (PASS if mv >= 2e10 else FAIL),
                       f"总市值 {mv/1e8:.0f}亿" if mv else "缺失", "§2.3"))
    t5 = safe_get(snap, "quote", "近5日日均成交额")
    pool.append(_check("近5日日均成交额>1亿", MISSING if t5 is None else (PASS if t5 > 1e8 else FAIL),
                       f"{t5/1e8:.2f}亿/日" if t5 else "缺失", "§2.3"))
    fin = snap.get("financials") or {}
    roe = fin.get("ROE近3年均值_pct")
    pool.append(_check("ROE近3年均值≥8%", MISSING if roe is None else (PASS if roe >= 8 else FAIL),
                       f"{roe:.2f}%" if roe is not None else "缺失", "§2.3"))
    pp = fin.get("净利润近3年为正年数")
    pool.append(_check("净利润近3年≥2年为正", MISSING if pp is None else (PASS if pp >= 2 else FAIL),
                       f"为正 {pp} 年" if pp is not None else "缺失", "§2.3"))
    pledge = snap.get("pledge_ratio")
    pool.append(_check("大股东质押率<30%", MISSING if pledge is None else (PASS if pledge < 0.30 else FAIL),
                       fmt_pct(pledge) if pledge is not None else "缺失", "§2.3"))
    wl = in_watchlist(code)
    pool.append(_check("理解度（备选池成员资格）", PASS if wl else FAIL,
                       ("已在备选池 watchlist（入池即视为完成研究笔记义务，v4.2.0裁决）" if wl
                        else "不在备选池：先 python run.py watchlist add " + str(code)), "§2.3裁决自动化"))
    pool.append(_check("审计/监管无重大问题", PASS, "由一票否决公告扫描自动判定（推定口径见该表）", "§2.3"))

    # 门槛条件
    gates = []
    gates.append(_check("标的在备选池内", PASS if wl else FAIL,
                        "watchlist.json 自动核对" + ("" if wl else "：未入池"), "§2.4裁决自动化"))
    pe = safe_get(snap, "valuation", "PE_TTM")
    pe_pct = safe_get(snap, "valuation", "PE历史分位")
    pb = safe_get(snap, "valuation", "PB")
    dv, _dvn = effective_dividend_yield(snap)
    val_detail = []
    if pe is not None and pe_pct is not None:
        val_detail.append(f"PE {pe}/分位 {fmt_pct(pe_pct)} → {'满足' if (pe_pct <= 0.30 and pe <= 30) else '不满足'}①")
    if pb is not None:
        val_detail.append(f"PB {pb} → {'满足' if pb <= 2.5 else '不满足'}②")
    if dv is not None:
        val_detail.append(f"股息率 {fmt_pct(dv)} → {'满足' if dv >= 0.04 else '不满足'}③")
    cond1 = (pe is not None and pe_pct is not None and pe_pct <= 0.30 and pe <= 30)
    cond2 = (pb is not None and pb <= 2.5)
    cond3 = (dv is not None and dv >= 0.04)
    if pe is None and pb is None and dv is None:
        gates.append(_check("估值三选一(PE分位≤30%且PE≤30 / PB≤2.5 / 股息率≥4%)", MISSING, "估值数据缺失", "§2.4"))
    else:
        gates.append(_check("估值三选一(PE分位≤30%且PE≤30 / PB≤2.5 / 股息率≥4%)",
                            PASS if (cond1 or cond2 or cond3) else FAIL, "；".join(val_detail), "§2.4"))
    dd = safe_get(snap, "quote", "较1年内高点回撤")
    gates.append(_check("较1年内高点回撤≥25%", MISSING if dd is None else (PASS if dd >= 0.25 else FAIL),
                        fmt_pct(dd) if dd is not None else "缺失", "§2.4"))
    rev_yoy = fin.get("营收最新同比")
    np_yoy = fin.get("净利润最新同比")
    if rev_yoy is None and np_yoy is None:
        gates.append(_check("最近财报营收或净利同比不为负", MISSING, "增速数据缺失", "§2.4"))
    else:
        ok = (rev_yoy is not None and rev_yoy >= 0) or (np_yoy is not None and np_yoy >= 0)
        gates.append(_check("最近财报营收或净利同比不为负",
                            PASS if ok else FAIL,
                            f"营收同比 {fmt_pct(rev_yoy)}，净利同比 {fmt_pct(np_yoy)}"
                            + ("" if ok else "（'业绩拐点预期'属决策卡片环节判断，此处按数据判FAIL）"), "§2.4"))
    if market_state == "C_overvalued":
        gates.append(_check("市场非C区", FAIL, "当前判定为C区：只卖不买", "§2.4"))
    elif market_state is None:
        gates.append(_check("市场非C区", MISSING, "市场状态无法判定（运行 judge-state 补数）", "§2.4"))
    else:
        gates.append(_check("市场非C区", PASS, f"当前判定 {market_state}", "§2.4"))

    # 评分（v4.2.0全自动4项 + 2项恒不计分）
    scoring = []
    tb = safe_get(snap, "quote", "技术底部信号") or {}
    scoring.append({"项": "技术底部", "得分": 1 if tb.get("signal") else 0,
                    "口径": f"RSI超卖近20日={tb.get('RSI超卖近20日')}，MACD零下金叉近15日={tb.get('MACD零下金叉近15日')}（裁决#5共振口径）"})
    days60 = safe_get(snap, "quote", "站上60日线连续天数")
    scoring.append({"项": "右侧确认", "得分": 1 if (days60 or 0) >= 3 else 0,
                    "口径": f"个股收盘站上60日线连续{days60}天（≥3天记1分，行业指数不可得以个股替代，偏严）"})
    scoring.append({"项": "政策催化", "得分": 0, "口径": "不参与自动评分（不可编程，裁决口径；映射阈值不变=偏保守）"})
    ms = snap.get("margin_signal") or {}
    scoring.append({"项": "资金信号", "得分": 1 if ms.get("signal") else 0,
                    "口径": (f"融资余额 {ms.get('最新融资余额')} vs 10日前 {ms.get('10日前融资余额')}"
                           f"（{ms.get('口径', '两融明细')}）" if ms.get("最新融资余额") is not None
                           else "两融数据缺失/非两融标的，记0分")})
    cagr = _forecast_cagr(snap)
    scoring.append({"项": "机构预期", "得分": 1 if (cagr is not None and cagr >= 0.15) else 0,
                    "口径": (f"一致预测EPS CAGR {fmt_pct(cagr)}（同花顺，裁决口径：≥15%记1分）"
                           if cagr is not None else "一致预测数据缺失，记0分")})
    scoring.append({"项": "预期差", "得分": 0, "口径": "不参与自动评分（主观判断，仅决策卡片环节使用）"})
    auto_score = sum(s["得分"] for s in scoring)

    gate_statuses = [g["status"] for g in gates]
    pool_statuses = [p["status"] for p in pool]
    all_pass = all(s == PASS for s in gate_statuses) and all(s == PASS for s in pool_statuses)
    if FAIL in pool_statuses or FAIL in gate_statuses:
        verdict = "不满足择时买入条件（存在FAIL项）"
    elif MISSING in pool_statuses or MISSING in gate_statuses:
        verdict = f"门槛存在数据缺失项（缺失不按通过处理）；当前自动评分 {auto_score} 分"
    elif all_pass and auto_score >= 3:
        verdict = f"满足门槛且评分 {auto_score} 分（≥3）：可进入决策卡片+24小时冷静期流程"
    else:
        verdict = f"门槛全过但评分 {auto_score} 分 <3：仅观察不建仓（裁决#6严格口径）"
    return {"pool": pool, "gates": gates, "scoring": scoring,
            "auto_score_floor": auto_score, "verdict": verdict,
            "cooling_note": "评分≥3且门槛全过时：决策卡片定稿+24小时冷静期后方可首仓30%（§2.6/§3.2）"}


def _forecast_cagr(snap: dict) -> float | None:
    """机构预期CAGR：同花顺最远预测年EPS均值 vs 最近实际EPS（v4.2.0裁决口径）。"""
    fc = snap.get("institution_forecast") or {}
    rows = fc.get("预测EPS") or []
    fin = snap.get("financials") or {}
    eps_list = [e for e in (fin.get("EPS各年") or []) if e]
    years = fin.get("报告年度") or []
    if not rows or not eps_list or not years:
        return None
    base_eps = eps_list[-1]
    base_year = int(years[-1])
    valid = [(int(r["年度"]), r["均值"]) for r in rows
             if r.get("均值") and str(r.get("年度", "")).isdigit()]
    if not valid or base_eps <= 0:
        return None
    far_year, far_eps = max(valid)
    n = far_year - base_year
    if n <= 0 or far_eps <= 0:
        return None
    return (far_eps / base_eps) ** (1 / n) - 1


# -----------------------------------------------------------------------------
# 估值分级与风控参数
# -----------------------------------------------------------------------------
def price_zone(snap: dict, dividend_val: dict, config: dict) -> dict:
    """价格区间分级（valuation_model.yaml price_zone_definition）。"""
    pe_pct = safe_get(snap, "valuation", "PE历史分位")
    pb_pct = safe_get(snap, "valuation", "PB历史分位")
    dv, _ = effective_dividend_yield(snap)
    cn10y = snap.get("cn10y_yield")
    yield_trigger = (dv is not None and cn10y is not None and dv < cn10y)
    pct_trigger = ((pe_pct is not None and pe_pct >= 0.70) or
                   (pb_pct is not None and pb_pct >= 0.70))
    if pct_trigger or yield_trigger:
        reason = "PE/PB≥历史70%分位" if pct_trigger else "股息率<10年国债（该触发为股息组合卖出规则，对非收息持仓仅供参考）"
        zone = f"减持区（触发估值卖出条件：{reason}）"
    elif dividend_val.get("met"):
        zone = "买入区（满足估值买入标准；目标买入价区间需研究笔记估值锚确认）"
    else:
        zone = "持有区（未满足买入标准，亦未触发估值卖出条件）"
    stall = safe_get(snap, "quote", "放量滞涨信号") or {}
    return {"zone": zone,
            "volume_stall": stall,
            "note": "估值锚缺失时按体系默认相对估值口径分级（valuation_model.yaml v4.2.0正式规则）"}


def risk_params(snap: dict, config: dict, circle: str | None) -> dict:
    """风控参数测算：仓位上限、建仓批次、止损止盈价位（严格按risk_control.yaml）。"""
    price = safe_get(snap, "quote", "最新收盘价")
    caps = safe_get(config, "risk_control", "position_caps") or {}
    div_cap = safe_get(caps, "dividend", "single_stock") or 0.15
    tim_cap = safe_get(caps, "timing", "single_stock") or 0.25
    modifier = caps.get("satellite_circle_modifier") or 0.5
    circle_note = {"core": "核心能力圈：单票可至组合上限",
                   "satellite": f"卫星能力圈：仓位减半（×{modifier}）",
                   "excluded": "能力圈外：禁止建仓（红线）",
                   None: f"能力圈未映射：按卫星减半保守口径（×{modifier}，自动口径）"}[circle]
    eff_div = div_cap * (modifier if circle in ("satellite", None) else 1)
    eff_tim = tim_cap * (modifier if circle in ("satellite", None) else 1)
    out = {
        "能力圈判定": circle_note,
        "股息组合单票上限": None if circle == "excluded" else eff_div,
        "择时组合单票上限": None if circle == "excluded" else eff_tim,
        "建仓批次": "首仓30% → 二仓30% → 三仓40%（触发条件见config §3.1/§3.2）",
    }
    if price:
        out["止损参考价"] = {
            "-10%减仓50%": round(price * 0.90, 2),
            "-15%无条件清仓": round(price * 0.85, 2),
            "说明": "以首仓成本计，此处按现价演示口径",
        }
        out["止盈参考价"] = {
            "+20%减持1/3": round(price * 1.20, 2),
            "+40%再减1/3": round(price * 1.40, 2),
            "说明": "另有PE70%分位触发、放量滞涨信号（裁决#12量化）与'高点回落≥8%清仓'移动止盈（§3.2）",
        }
        out["时间止损"] = "3个月未盈利减仓50%；6个月清仓；满12个月强制重评（§3.2）"
    return out


# -----------------------------------------------------------------------------
# 统一入口
# -----------------------------------------------------------------------------
def analyze_stock(snap: dict, config: dict | None = None,
                  market_state: str | None = None) -> dict:
    """个股分析统一入口：返回全部结构化判定，供报告生成。"""
    config = config or load_config()
    cls = classify_industry(safe_get(snap, "basic", "行业"))
    veto = evaluate_veto(snap, config)
    dividend = evaluate_dividend_track(snap, config)
    timing = evaluate_timing_track(snap, config, market_state)
    zone = price_zone(snap, dividend["valuation_buy"], config)
    risk = risk_params(snap, config, cls["circle"])

    veto_fail = [c for c in veto if c["status"] == FAIL]
    # 推定/口径说明清单（替代原"待人工核验清单"：全自动化后仅保留口径透明度记录）
    assumptions = [c for c in veto + dividend["universal"] + dividend["category"] +
                   timing["pool"] + timing["gates"]
                   if c["status"] == MISSING or "推定" in c["detail"] or "口径" in c["detail"]]
    if veto_fail:
        overall = f"排除：命中一票否决（{'；'.join(c['item'] for c in veto_fail)}）"
    elif cls["circle"] == "excluded":
        overall = "排除：行业自动归类属能力圈外（§2.1禁止建仓）"
    else:
        overall = f"股息组合：{dividend['verdict']} ｜ 择时组合：{timing['verdict']}"
    return {"code": snap.get("code"), "name": safe_get(snap, "basic", "名称"),
            "classify": cls, "veto": veto, "dividend": dividend, "timing": timing,
            "zone": zone, "risk": risk, "overall": overall,
            "assumptions": assumptions,
            "market_state": market_state,
            "errors": snap.get("errors") or {}}
