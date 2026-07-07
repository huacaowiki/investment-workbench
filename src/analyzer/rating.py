# -*- coding: utf-8 -*-
"""
rating.py — 综合评分/评级/仓位推导引擎（v4.4.0 composite_scoring 执行器）
定位：把既有门槛判定结果映射为百分制得分与评级（展示层），不改变门槛制判定；
     全部权重/映射/封顶规则读取 config/stock_selection.yaml，代码零硬编码参数。
铁则约束（写死）：
  - 一票否决FAIL / 能力圈excluded / 新股 → 评级=排除，仓位=0，不进入映射；
  - 市场C区评级封顶"观望"、B偏高"买入"降级（config system_overrides）；
  - 建议仓位 = 评级系数 × risk_control单票上限 × 能力圈系数，绝不突破铁则。
"""
from __future__ import annotations

from src.data.data_utils import fmt_pct, safe_get, to_float

PASS, FAIL, MISSING = "PASS", "FAIL", "MISSING"

RATING_EXCLUDE = "排除"


def _find_check(checks: list[dict], keyword: str) -> dict | None:
    return next((c for c in checks if keyword in c["item"]), None)


def _item_score(checks: list[dict], keyword: str, points: float) -> dict:
    """按判定状态计分：PASS=满分，FAIL/MISSING=0（缺失不给分，偏保守）。"""
    c = _find_check(checks, keyword)
    if c is None:
        return {"得分": 0.0, "满分": points, "依据": f"未找到判定项'{keyword}'（计0分）"}
    got = points if c["status"] == PASS else 0.0
    tag = {"PASS": "达标", "FAIL": "不达标", "MISSING": "数据缺失（不给分）"}[c["status"]]
    return {"得分": got, "满分": points, "依据": f"{tag}：{c['detail'][:60]}"}


def valuation_state(result: dict, snap: dict) -> tuple[str, str]:
    """估值定性（config rating_mapping.valuation_state_source 口径）。返回 (定性, 口径说明)。"""
    mv = result.get("multi_valuation") or {}
    price = safe_get(snap, "quote", "最新收盘价")
    combined = mv.get("combined")
    if combined and price:
        low, high = combined["合理区间"]
        if price < combined["安全边际价"]:
            return "低估", f"现价 {price} < 多锚安全边际价 {combined['安全边际价']}"
        if price > combined["高估阈值"]:
            return "高估", f"现价 {price} > 多锚高估阈值 {combined['高估阈值']}"
        if price <= high:
            return "合理", f"现价 {price} 位于多锚综合区间 [{low}, {high}] 内或下方"
        return "合理", f"现价 {price} 介于合理区间上沿与高估阈之间（偏高的合理区）"
    # 回退：历史分位口径（多锚不可用/偏差未加权时）
    from src.analyzer.stock_analyzer import valuation_inputs
    vi = valuation_inputs(snap)
    pct = min(p for p in (vi["pe_pct"], vi["pb_pct"]) if p is not None) \
        if any(p is not None for p in (vi["pe_pct"], vi["pb_pct"])) else None
    if pct is None:
        return "合理", "多锚与分位均不可用，按'合理'中性口径处理并标注（不给低估加分）"
    state = "低估" if pct < 0.30 else ("高估" if pct >= 0.70 else "合理")
    return state, f"回退历史分位口径：PE/PB较低分位 {fmt_pct(pct)}（多锚未加权/不可用）"


def composite_rating(result: dict, snap: dict, config: dict,
                     market_state: str | None) -> dict:
    """综合评分+评级+仓位推导统一入口。"""
    sel = config.get("stock_selection") or {}
    cs = sel.get("composite_scoring") or {}
    rm = sel.get("rating_mapping") or {}
    d = result.get("dividend") or {}
    t = result.get("timing") or {}
    veto = result.get("veto") or []
    all_checks = (d.get("universal") or []) + (d.get("category") or []) + \
                 (t.get("pool") or []) + (t.get("gates") or [])
    cat = safe_get(result, "classify", "category")
    circle = safe_get(result, "classify", "circle")

    # ---- 前置排除门（不进入评分映射）----
    veto_fail = [c for c in veto if c["status"] == FAIL]
    new_stock = any("上市不满1年" in c["item"] and c["status"] == FAIL for c in veto)
    gate_reason = None
    if new_stock:
        gate_reason = "上市不满1年（新股否决，v4.4.0）：仅输出基础数据，不给投资评级"
    elif veto_fail:
        gate_reason = "命中一票否决（" + "；".join(c["item"] for c in veto_fail) + "）"
    elif circle == "excluded":
        gate_reason = "能力圈外行业（§2.1禁止建仓红线）"

    # ---- 分维度计分（权重全部来自config）----
    dims = {}
    fq = cs.get("dimensions", {}).get("financial_quality", {})
    fq_items = fq.get("items_financial" if cat == "A_financial" else "items_default", [])
    dims["财务质量"] = {"items": {}, "满分": fq.get("total", 40)}
    for it in fq_items:
        dims["财务质量"]["items"][it["name"]] = _item_score(all_checks, it["source_check"], it["points"])

    dr = cs.get("dimensions", {}).get("dividend_return", {})
    dims["分红与股东回报"] = {"items": {}, "满分": dr.get("total", 20)}
    for it in dr.get("items", []):
        dims["分红与股东回报"]["items"][it["name"]] = _item_score(all_checks, it["source_check"], it["points"])

    ls = cs.get("dimensions", {}).get("liquidity_scale", {})
    dims["流动性与规模"] = {"items": {}, "满分": ls.get("total", 10)}
    for it in ls.get("items", []):
        dims["流动性与规模"]["items"][it["name"]] = _item_score(all_checks, it["source_check"], it["points"])

    ts = cs.get("dimensions", {}).get("timing_signals", {})
    per = ts.get("per_signal_points", 3.75)
    dims["择时信号"] = {"items": {}, "满分": ts.get("total", 15)}
    for s in (t.get("scoring") or []):
        if "不参与自动评分" in s.get("口径", ""):
            continue
        dims["择时信号"]["items"][s["项"]] = {
            "得分": per * (s.get("得分") or 0), "满分": per,
            "依据": s.get("口径", "")[:60]}

    vp = cs.get("dimensions", {}).get("valuation_position", {})
    from src.analyzer.stock_analyzer import valuation_inputs
    vi = valuation_inputs(snap)
    pcts = [p for p in (vi["pe_pct"], vi["pb_pct"]) if p is not None]
    vp_total = vp.get("total", 15)
    if not pcts:
        vp_score, vp_basis = vp.get("insufficient_samples_points", 0), \
            "分位不可用（样本不足/亏损/数据缺失）→ 0分并标注"
    else:
        pct = min(pcts)
        vp_score, vp_basis = 0, f"PE/PB较低分位 {fmt_pct(pct)}"
        for band in vp.get("bands", []):
            if pct < band["below"]:
                vp_score = band["points"]
                vp_basis += f" → 落入<{fmt_pct(band['below'])}档，计{band['points']}分"
                break
    dims["估值位置"] = {"items": {"历史分位档位": {"得分": vp_score, "满分": vp_total,
                                              "依据": vp_basis}}, "满分": vp_total}

    total = round(sum(i["得分"] for d_ in dims.values() for i in d_["items"].values()), 1)

    # ---- 估值定性 + 评级映射 + 体系封顶 ----
    v_state, v_basis = valuation_state(result, snap)
    if gate_reason:
        rating = RATING_EXCLUDE
        rating_basis = gate_reason
    else:
        rating = "观望"
        for rule in rm.get("rules", []):
            if total >= rule["min_score"] and v_state in rule["valuation_state"]:
                rating = rule["rating"]
                break
        rating_basis = f"综合得分 {total} + 估值定性'{v_state}' → 映射'{rating}'"
        if market_state == "C_overvalued" and rating in ("买入", "逢低建仓"):
            rating, rating_basis = "观望", rating_basis + "；市场C区→封顶'观望'（§1.4）"
        elif market_state == "B_high" and rating == "买入":
            rating, rating_basis = "逢低建仓", rating_basis + "；市场B偏高→'买入'降级（§1.4暂停买入）"

    # ---- 建议仓位（恒不突破铁则）----
    caps = safe_get(config, "risk_control", "position_caps") or {}
    tim_cap = safe_get(caps, "timing", "single_stock") or 0.25
    div_cap = safe_get(caps, "dividend", "single_stock") or 0.15
    modifier = caps.get("satellite_circle_modifier")
    modifier = 0.5 if modifier is None else modifier
    circle_f = 0 if circle == "excluded" else (modifier if circle in ("satellite", None) else 1)
    rating_f = (rm.get("position_by_rating") or {}).get(rating, 0)
    pos_timing = round(tim_cap * circle_f * rating_f, 4)
    pos_div = round(div_cap * circle_f * rating_f, 4)

    # ---- 交易参数（多锚区间 + 体系止损档位）----
    price = safe_get(snap, "quote", "最新收盘价")
    mv = result.get("multi_valuation") or {}
    combined = mv.get("combined") or {}
    entry_low = combined.get("安全边际价")
    entry_high = combined["合理区间"][0] if combined.get("合理区间") else None
    target_low = combined["合理区间"][1] if combined.get("合理区间") else None
    target_high = combined.get("高估阈值")
    stop = round(price * 0.85, 2) if price else None          # §3.2硬止损-15%
    upside = (target_low / price - 1) if (price and target_low) else None
    rr = ((target_low - price) / (price - stop)) if (price and target_low and stop
                                                     and price > stop and target_low > price) else None
    usable = mv.get("usable_count") or 0
    missing_cnt = sum(1 for c in all_checks if c["status"] == MISSING)
    window_ok = "近10年" in str(safe_get(snap, "valuation", "分位窗口") or "")
    confidence = ("高" if (usable >= 3 and missing_cnt == 0 and window_ok) else
                  "中" if (usable >= 2 or missing_cnt <= 3) else "低")

    # 门槛一致性护栏：评级反映"质量+估值"维度；体系买入门槛（§2.2/§2.4）未全过时，
    # 评级不构成建仓许可——显著提示，防止评级与门槛制结论被误读为矛盾
    gates_ok = not any(c["status"] in (FAIL, MISSING) for c in all_checks)
    gate_note = (None if gate_reason else
                 ("体系买入门槛全部通过" if gates_ok else
                  "⚠️ 体系买入门槛未全过（详见选股校验/择时门槛表）——评级仅反映标的质量与估值维度，"
                  "实际建仓仍以门槛全过+决策卡片+冷静期为前提（铁则优先）"))

    return {
        "综合得分": total, "满分": 100,
        "维度得分": dims,
        "评级": rating, "评级依据": rating_basis,
        "门槛提示": gate_note,
        "估值定性": v_state, "估值定性依据": v_basis,
        "建议仓位上限": {"择时组合": pos_timing, "股息组合": pos_div,
                    "口径": f"评级系数{rating_f}×单票上限×能力圈系数{circle_f}（铁则封顶）"},
        "入场价区间": [entry_low, entry_high] if entry_low and entry_high else None,
        "目标价区间": [target_low, target_high] if target_low and target_high else None,
        "潜在上行空间": upside,
        "止损参考": stop,
        "盈亏比": round(rr, 2) if rr else None,
        "持有周期": "股息组合≥1年 / 择时组合1-6个月（§1.1，最长12个月强制重评）",
        "信心水平": confidence,
        "信心口径": f"数据完备度工程口径：可用估值方法{usable}种/缺失项{missing_cnt}个/分位窗口{'标准' if window_ok else '降级'}",
        "gate_reason": gate_reason,
        "pool_level": ("排除" if (gate_reason or rating == RATING_EXCLUDE) else
                       "核心池" if total >= 80 else "观察池" if total >= 60 else "排除"),
    }
