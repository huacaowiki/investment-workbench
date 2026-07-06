# -*- coding: utf-8 -*-
"""
stock_analyzer.py — 个股分析引擎（规则执行器）
输入 stock_data.get_stock_snapshot() 快照，严格对照 config/ 铁则层逐项判定：
  一票否决 → 股息组合门槛（通用+分类）→ 择时组合门槛与评分 → 估值分级 → 风控参数。
核心纪律：
  - 每一项判定的结果只有四种：PASS / FAIL / MISSING(数据缺失) / MANUAL(需人工)；
  - 数据缺失绝不按通过处理，结论中列出全部缺失与人工项；
  - 不新增任何 config 之外的标准；工程化辅助（如行业归类关键词）显式标注。
"""
from __future__ import annotations

from src.data.data_utils import fmt_pct, safe_get, to_float
from src.utils.file_utils import load_config

PASS, FAIL, MISSING, MANUAL = "PASS", "FAIL", "MISSING", "MANUAL"

# -----------------------------------------------------------------------------
# 行业归类（工程化辅助：仅用于选择"分类财务门槛"与能力圈提示，结果需人工复核）
# -----------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "A_financial": ["银行", "保险", "证券", "金融"],
    "B_cyclical": ["煤", "石油", "石化", "有色", "钢铁", "矿", "化学原料", "开采"],
    "C_stable": ["酒", "饮料", "食品", "家电", "电器", "乳", "电力", "水电", "燃气",
                 "铁路", "公路", "港口", "航运", "运输", "电信", "通信服务", "运营"],
}
CIRCLE_KEYWORDS = {
    "core": ["汽车", "新能源车", "保险", "储能", "电池", "消费电子"],
    "satellite": ["软件", "AI", "人工智能", "医疗器械", "银行", "煤", "运营商", "电信", "通信"],
    "excluded": ["医药", "生物制品", "创新药", "农", "军工", "航天", "兵器"],
}


def classify_industry(industry: str | None) -> dict:
    """行业→分类门槛类别 + 能力圈初判（关键词匹配，须人工复核）。"""
    if not industry:
        return {"category": None, "circle": None, "note": "行业数据缺失，需人工归类"}
    category = next((cat for cat, kws in CATEGORY_KEYWORDS.items()
                     if any(k in industry for k in kws)), None)
    circle = next((c for c, kws in CIRCLE_KEYWORDS.items()
                   if any(k in industry for k in kws)), None)
    return {"category": category, "circle": circle,
            "note": f"按行业名'{industry}'关键词初判，能力圈与分类归属需人工确认（工程化辅助，非铁则）"}


def _check(name: str, status: str, detail: str, source: str = "") -> dict:
    """统一判定记录结构。"""
    return {"item": name, "status": status, "detail": detail, "source": source}


# -----------------------------------------------------------------------------
# 一票否决
# -----------------------------------------------------------------------------
def evaluate_veto(snap: dict, config: dict) -> list[dict]:
    """§2.1红线 + §2.2合规门槛中程序可判定项；不可判定项列为 MANUAL。"""
    checks = []
    name = safe_get(snap, "basic", "名称") or ""
    if name:
        st = ("ST" in name.upper()) or ("退" in name)
        checks.append(_check("ST/退市风险股", FAIL if st else PASS, f"证券简称：{name}", "§2.1"))
    else:
        checks.append(_check("ST/退市风险股", MISSING, "名称数据缺失", "§2.1"))

    pledge = snap.get("pledge_ratio")
    if pledge is None:
        checks.append(_check("大股东质押率<30%", MISSING, "质押数据缺失，需人工核验", "§2.1/§2.2"))
    else:
        checks.append(_check("大股东质押率<30%", PASS if pledge < 0.30 else FAIL,
                             f"当前质押比例 {fmt_pct(pledge)}", "§2.1/§2.2"))

    for item, note in [("审计意见=标准无保留", "接口不可得"),
                       ("近3年无涉财务真实性监管函", "接口不可得"),
                       ("非纯概念股/无财务疑点/无重大诉讼", "定性判断")]:
        checks.append(_check(item, MANUAL, f"{note}，需人工核验后确认", "§2.1/§2.2"))
    return checks


# -----------------------------------------------------------------------------
# 股息组合视角
# -----------------------------------------------------------------------------
def evaluate_dividend_track(snap: dict, config: dict) -> dict:
    """§2.2：通用门槛逐项 → 分类财务门槛 → 加分条件 → 估值买入标准 → 分池结论。"""
    checks = []
    cn10y = snap.get("cn10y_yield")

    mv = safe_get(snap, "basic", "总市值")
    checks.append(_check("市值≥300亿", MISSING if mv is None else (PASS if mv >= 3e10 else FAIL),
                         f"总市值 {mv/1e8:.0f}亿" if mv else "缺失", "§2.2通用"))
    t20 = safe_get(snap, "quote", "近20日日均成交额")
    checks.append(_check("近20日日均成交额>1亿", MISSING if t20 is None else (PASS if t20 > 1e8 else FAIL),
                         f"{t20/1e8:.2f}亿/日" if t20 else "缺失", "§2.2通用"))
    dy_years = safe_get(snap, "dividends", "连续分红年数")
    checks.append(_check("连续分红≥5年", MISSING if dy_years is None else (PASS if dy_years >= 5 else FAIL),
                         f"连续分红 {dy_years} 年" if dy_years is not None else "缺失", "§2.2通用"))
    checks.append(_check("派现比例近3年均值≥30%", MANUAL,
                         "接口无派现比例直接字段，需人工用 每股分红/每股收益 核算", "§2.2通用"))
    dv, _note = effective_dividend_yield(snap)
    dv_note = f"（口径：{_note}）"
    if dv is None or cn10y is None:
        checks.append(_check("股息率≥max(1.5×10年国债, 3%)", MISSING, "股息率或国债收益率缺失", "§2.2通用"))
    else:
        floor = max(1.5 * cn10y, 0.03)
        checks.append(_check("股息率≥max(1.5×10年国债, 3%)", PASS if dv >= floor else FAIL,
                             f"股息率 {fmt_pct(dv)}{dv_note} vs 门槛 {fmt_pct(floor)}（10年国债 {fmt_pct(cn10y)}）", "§2.2通用"))
    checks.append(_check("派息率<80%", MANUAL, "需人工核算（分红/净利润）", "§2.2通用"))
    checks.append(_check("审计/质押/监管三项", MANUAL, "见一票否决检查表", "§2.2通用"))

    # 分类财务门槛
    cls = classify_industry(safe_get(snap, "basic", "行业"))
    cat = cls["category"]
    cat_checks = []
    fin = snap.get("financials") or {}
    roe_avg = fin.get("ROE近3年均值_pct")
    profit_pos_years = fin.get("净利润近3年为正年数")
    debt = fin.get("资产负债率_pct")
    cat_rules = safe_get(config, "stock_selection", "dividend_portfolio",
                         "category_thresholds") or {}
    if cat is None:
        cat_checks.append(_check("分类财务门槛", MANUAL, "行业无法自动归入A金融/B周期/C稳定，需人工选择适用门槛", "§2.2"))
    else:
        cat_name = safe_get(cat_rules, cat, "name") or cat
        roe_min = {"A_financial": 8, "B_cyclical": 10, "C_stable": 12}[cat]
        cat_checks.append(_check(f"[{cat_name}] ROE近3年均值≥{roe_min}%",
                                 MISSING if roe_avg is None else (PASS if roe_avg >= roe_min else FAIL),
                                 f"ROE近3年均值 {roe_avg:.2f}%" if roe_avg is not None else "缺失", "§2.2分类"))
        if cat == "A_financial":
            cat_checks.append(_check("[金融] 不良率/拨备/资本充足率", MANUAL, "银行专项指标需人工核验财报", "§2.2分类"))
            need_pos = 3
        elif cat == "B_cyclical":
            need_pos = 2
        else:
            need_pos = 3
        cat_checks.append(_check(f"净利润近3年至少{need_pos}年为正",
                                 MISSING if profit_pos_years is None else (PASS if profit_pos_years >= need_pos else FAIL),
                                 f"近3年为正 {profit_pos_years} 年" if profit_pos_years is not None else "缺失", "§2.2分类"))
        if cat != "A_financial":
            debt_max = 65 if cat == "B_cyclical" else 60
            cat_checks.append(_check(f"资产负债率≤{debt_max}%",
                                     MISSING if debt is None else (PASS if debt <= debt_max else FAIL),
                                     f"{debt:.1f}%" if debt is not None else "缺失", "§2.2分类"))
            cat_checks.append(_check("收现比/净现比门槛", MANUAL,
                                     f"接口口径不完整（收现比字段={fin.get('收现比_pct')}），需人工核验现金流量表", "§2.2分类"))

    # 估值买入标准（满足≥1条）
    val_result = evaluate_dividend_valuation(snap, config)

    # 分池结论（pool_thresholds 映射；加分条件为定性项→人工）
    statuses = [c["status"] for c in checks + cat_checks]
    if FAIL in statuses:
        verdict = "排除（存在门槛FAIL项）"
    elif MISSING in statuses or MANUAL in statuses:
        verdict = "暂列观察池（门槛存在缺失/人工项，补核后再定）"
    else:
        verdict = "候选池（通用+分类门槛全过；加分条件≥2条则优先，需人工确认）"
    return {"universal": checks, "category": cat_checks, "classify": cls,
            "valuation_buy": val_result, "verdict": verdict,
            "bonus_note": "加分条件5项（行业成熟/增速≥3%/龙头/壁垒/低颠覆风险）为定性判断，需人工勾选，满足≥2条优先入选"}


def effective_dividend_yield(snap: dict) -> tuple[float | None, str]:
    """股息率取值统一口径：优先TTM，缺失时回退最近年度分红记录（口径随值返回）。"""
    dv = safe_get(snap, "valuation", "股息率TTM")
    if dv is not None:
        return dv, "TTM"
    recs = safe_get(snap, "dividends", "近3年股息率记录_pct") or []
    dv = to_float(recs[-1]) if recs else None
    return dv, "最近年度分红记录（非TTM，需人工复核）" if dv is not None else "缺失"


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
        semi = pe_pct <= 0.30
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
# 择时组合视角
# -----------------------------------------------------------------------------
def evaluate_timing_track(snap: dict, config: dict, market_state: str | None) -> dict:
    """§2.3入池 + §2.4门槛与评分。评分中人工项按0分保守处理（待确认#5口径）。"""
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
    pool.append(_check("理解度（研究笔记）", MANUAL, "须能说清商业模式/竞争优势/风险/估值锚，并有研究笔记（附录A）", "§2.3"))
    pool.append(_check("审计/监管无重大问题", MANUAL, "见一票否决检查表", "§2.3"))

    # 门槛条件
    gates = []
    gates.append(_check("标的在备选池内", MANUAL, "以用户备选池清单为准（30-50只，季度维护）", "§2.4"))
    pe = safe_get(snap, "valuation", "PE_TTM")
    pe_pct = safe_get(snap, "valuation", "PE历史分位")
    pb = safe_get(snap, "valuation", "PB")
    dv, _dvn = effective_dividend_yield(snap)
    val_ok, val_detail = None, []
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
        ok = cond1 or cond2 or cond3
        gates.append(_check("估值三选一(PE分位≤30%且PE≤30 / PB≤2.5 / 股息率≥4%)",
                            PASS if ok else FAIL, "；".join(val_detail), "§2.4"))
    dd = safe_get(snap, "quote", "较1年内高点回撤")
    gates.append(_check("较1年内高点回撤≥25%", MISSING if dd is None else (PASS if dd >= 0.25 else FAIL),
                        fmt_pct(dd) if dd is not None else "缺失", "§2.4"))
    rev_yoy = fin.get("营收最新同比")
    np_yoy = fin.get("净利润最新同比")
    if rev_yoy is None and np_yoy is None:
        gates.append(_check("最近财报营收或净利同比不为负（或有明确拐点预期）", MISSING,
                            "增速数据缺失（拐点预期为人工判断）", "§2.4"))
    else:
        ok = (rev_yoy is not None and rev_yoy >= 0) or (np_yoy is not None and np_yoy >= 0)
        gates.append(_check("最近财报营收或净利同比不为负（或有明确拐点预期）",
                            PASS if ok else FAIL,
                            f"营收同比 {fmt_pct(rev_yoy)}，净利同比 {fmt_pct(np_yoy)}（若FAIL可由人工拐点预期覆盖）", "§2.4"))
    if market_state == "C_overvalued":
        gates.append(_check("市场非C区", FAIL, "当前人工判定为C区：只卖不买", "§2.4"))
    elif market_state is None:
        gates.append(_check("市场非C区", MANUAL, "市场状态无人工判定记录，需先完成周判", "§2.4"))
    else:
        gates.append(_check("市场非C区", PASS, f"当前判定 {market_state}", "§2.4"))

    # 评分（6项，人工项0分保守处理）
    scoring = []
    above60 = safe_get(snap, "quote", "站上60日线")
    scoring.append({"项": "技术底部（自编指标共振）", "得分": 0, "口径": "人工输入项，缺省0分（待确认#5）"})
    scoring.append({"项": "右侧确认", "得分": None,
                    "口径": f"个股站上60日线={above60}；原文要求'周线背离或行业指数站上60日线≥3日'，行业指数序列未取，需人工确认",})
    scoring.append({"项": "政策催化", "得分": 0, "口径": "人工判断项，缺省0分"})
    scoring.append({"项": "资金信号", "得分": 0, "口径": "北向个股数据停发，融资余额需人工核对，缺省0分"})
    forecast = snap.get("institution_forecast")
    scoring.append({"项": "机构预期(CAGR≥15%且上调居多)", "得分": None if forecast is None else 0,
                    "口径": "一致预期数据缺失，需人工核验" if forecast is None else f"东财预测数据：{forecast}，CAGR需人工核算"})
    scoring.append({"项": "预期差", "得分": 0, "口径": "人工判断项（决策卡片写明），缺省0分"})
    auto_score = sum(s["得分"] or 0 for s in scoring)

    gate_statuses = [g["status"] for g in gates]
    pool_statuses = [p["status"] for p in pool]
    if FAIL in pool_statuses or FAIL in gate_statuses:
        verdict = "不满足择时买入条件（存在FAIL项）"
    else:
        verdict = ("门槛项存在缺失/人工项，须补核；评分自动口径下限为 "
                   f"{auto_score} 分（人工项确认后重算）。按待确认#3临时口径：评分<3不给建仓建议，仅观察")
    return {"pool": pool, "gates": gates, "scoring": scoring,
            "auto_score_floor": auto_score, "verdict": verdict,
            "cooling_note": "如最终评分≥3且门槛全过：决策卡片定稿+24小时冷静期后方可首仓30%（§2.6/§3.2）"}


# -----------------------------------------------------------------------------
# 估值分级与风控参数
# -----------------------------------------------------------------------------
def price_zone(snap: dict, dividend_val: dict, config: dict) -> dict:
    """价格区间分级（valuation_model.yaml price_zone_definition）。"""
    pe_pct = safe_get(snap, "valuation", "PE历史分位")
    pb_pct = safe_get(snap, "valuation", "PB历史分位")
    dv, _ = effective_dividend_yield(snap)
    cn10y = snap.get("cn10y_yield")
    sell_trigger = ((pe_pct is not None and pe_pct >= 0.70) or
                    (pb_pct is not None and pb_pct >= 0.70) or
                    (dv is not None and cn10y is not None and dv < cn10y))
    if sell_trigger:
        zone = "减持区（触发估值卖出条件）"
    elif dividend_val.get("met"):
        zone = "买入区（满足估值买入标准；目标买入价区间需研究笔记估值锚确认）"
    else:
        zone = "持有区（未满足买入标准，亦未触发估值卖出条件）"
    return {"zone": zone,
            "note": "【估值锚缺失口径】无研究笔记时按体系默认相对估值口径分级，需补研究笔记（valuation_model.yaml industry_models.interim_rule）"}


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
                   None: "能力圈归属需人工确认，按保守口径先按卫星减半提示"}[circle]
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
            "说明": "另有PE70%分位触发与'高点回落≥8%清仓'移动止盈（§3.2）",
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
    manual_items = [c for c in veto + dividend["universal"] + dividend["category"] +
                    timing["pool"] + timing["gates"] if c["status"] in (MANUAL, MISSING)]
    if veto_fail:
        overall = f"排除：命中一票否决（{'；'.join(c['item'] for c in veto_fail)}）"
    elif cls["circle"] == "excluded":
        overall = "排除：行业初判属能力圈外（§2.1禁止建仓）——归类需人工最终确认"
    else:
        overall = f"股息组合：{dividend['verdict']} ｜ 择时组合：{timing['verdict']}"
    return {"code": snap.get("code"), "name": safe_get(snap, "basic", "名称"),
            "classify": cls, "veto": veto, "dividend": dividend, "timing": timing,
            "zone": zone, "risk": risk, "overall": overall,
            "manual_items": manual_items,
            "market_state": market_state,
            "errors": snap.get("errors") or {}}
