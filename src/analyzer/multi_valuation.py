# -*- coding: utf-8 -*-
"""
multi_valuation.py — 分行业多锚点估值引擎（v4.3.0 任务二）
按 config/valuation_model.yaml industry_models 的行业方法集，对个股并行执行
≥3种估值方法（核心锚/辅助锚/验证锚三层），输出：
  - 每种方法的适用性、参数、测算过程、合理区间、减持阈值；
  - 加权综合合理区间、安全边际价、高估阈值；
  - 方法间偏差>30%时：不强行加权，标注偏差与原因，分别列示。

方法论边界（诚实原则）：
  - 全部参数取自config（用户2026-07-07授权的v4.3.0工程参数）与体系既有锚
    （股息率≥2×国债买入锚、股息率<国债卖出锚、PE≤30上限、历史30/50/70分位）；
  - 数据不可得的方法标记"不可用+原因"，不编造输入；可用方法不足2种时不输出综合区间；
  - EV/EBITDA因EBITDA无稳定公开接口未纳入（config中已注明），PS仅作展示性补充口径。
"""
from __future__ import annotations

from src.data.data_utils import fmt_pct, safe_get, to_float

# 成长类行业关键词（估值方法集选择用；门槛分类仍走 stock_analyzer.classify_industry）
GROWTH_KEYWORDS = ["汽车", "电子", "软件", "计算机", "通信设备", "半导体", "电池",
                   "电气机械", "仪器仪表", "互联网", "信息技术"]


def resolve_val_class(category: str | None, industry: str | None) -> str:
    """
    估值方法集归类：A金融/B周期沿用门槛分类；C类再分稳定/成长
    （成长股用DDM会系统性低估，需切换PEG口径——见config适用场景说明）。
    """
    if category == "A_financial":
        return "A_financial"
    if category == "B_cyclical":
        return "B_cyclical"
    if industry and any(k in industry for k in GROWTH_KEYWORDS):
        return "G_growth"
    return "C_stable"


def _method(name: str, layer: str, weight: float, usable: bool, params: str,
            process: str, low=None, high=None, sell=None, reason: str = "") -> dict:
    return {"方法": name, "层级": layer, "权重": weight, "可用": usable,
            "参数": params, "过程": process,
            "区间": [round(low, 2), round(high, 2)] if usable and low and high else None,
            "减持阈": round(sell, 2) if usable and sell else None,
            "不可用原因": reason}


# -----------------------------------------------------------------------------
# 各估值方法实现（输入统一从快照取，输出统一结构）
# -----------------------------------------------------------------------------

def _m_pe_quantile(snap, layer, weight):
    """PE历史分位法：合理区间=[EPS×PE_p30, EPS×PE_p50]，减持=EPS×PE_p70。"""
    eps = _latest_eps(snap)
    q = safe_get(snap, "valuation", "PE分位值")
    window = safe_get(snap, "valuation", "分位窗口")
    if not eps or eps <= 0:
        return _method("PE历史分位法", layer, weight, False, "—", "—",
                       reason="EPS缺失或≤0（亏损股PE法不适用）")
    if not q:
        return _method("PE历史分位法", layer, weight, False, "—", "—",
                       reason="PE历史分位序列不可得（样本<30或数据源缺失）")
    return _method("PE历史分位法", layer, weight, True,
                   f"EPS={eps}；PE分位值 p30={q['p30']}/p50={q['p50']}/p70={q['p70']}（窗口：{window}）",
                   f"区间=[{eps}×{q['p30']}, {eps}×{q['p50']}]；减持={eps}×{q['p70']}",
                   eps * q["p30"], eps * q["p50"], eps * q["p70"])


def _m_pb_quantile(snap, layer, weight):
    """PB历史分位法：合理区间=[BPS×PB_p30, BPS×PB_p50]，减持=BPS×PB_p70。"""
    bps = safe_get(snap, "financials", "BPS最新")
    q = safe_get(snap, "valuation", "PB分位值")
    window = safe_get(snap, "valuation", "分位窗口")
    if not bps or bps <= 0:
        return _method("PB历史分位法", layer, weight, False, "—", "—", reason="每股净资产缺失")
    if not q:
        return _method("PB历史分位法", layer, weight, False, "—", "—",
                       reason="PB历史分位序列不可得")
    return _method("PB历史分位法", layer, weight, True,
                   f"BPS={bps}；PB分位值 p30={q['p30']}/p50={q['p50']}/p70={q['p70']}（窗口：{window}）",
                   f"区间=[{bps}×{q['p30']}, {bps}×{q['p50']}]；减持={bps}×{q['p70']}",
                   bps * q["p30"], bps * q["p50"], bps * q["p70"])


def _m_dividend_anchor(snap, layer, weight):
    """
    股息率锚法（体系原生锚，§2.2/§3.1）：
    买入锚=股息率≥2×10年国债 → 价格上限=DPS/(2×国债)；
    合理下沿取更苛刻的2.5×国债；卖出锚=股息率<国债 → 减持价=DPS/国债。
    """
    dps = _latest_dps(snap)
    cn10y = snap.get("cn10y_yield")
    if not dps or dps <= 0:
        return _method("股息率锚法", layer, weight, False, "—", "—",
                       reason="无现金分红记录（不分红标的该法不适用）")
    if not cn10y or cn10y <= 0:
        return _method("股息率锚法", layer, weight, False, "—", "—", reason="10年国债收益率缺失")
    low, high, sell = dps / (2.5 * cn10y), dps / (2 * cn10y), dps / cn10y
    return _method("股息率锚法", layer, weight, True,
                   f"每股分红DPS={dps}；10年国债={fmt_pct(cn10y)}（体系锚：买入≥2×国债/卖出<国债）",
                   f"区间=[{dps}/(2.5×{cn10y:.4f}), {dps}/(2×{cn10y:.4f})]；减持={dps}/{cn10y:.4f}",
                   low, high, sell)


def _m_peg(snap, layer, weight):
    """
    PEG法（成长类辅助锚）：合理PE=CAGR×100×PEG系数[0.8,1.0]，受体系PE≤30上限约束（§2.4）；
    减持阈=PEG 1.2（同样受30倍上限约束）。CAGR取同花顺一致预测口径。
    """
    from src.analyzer.stock_analyzer import _forecast_cagr
    eps = _latest_eps(snap)
    cagr = _forecast_cagr(snap)
    if not eps or eps <= 0:
        return _method("PEG法", layer, weight, False, "—", "—", reason="EPS缺失或≤0")
    if cagr is None or cagr <= 0:
        return _method("PEG法", layer, weight, False, "—", "—",
                       reason="一致预测CAGR缺失或≤0（无机构覆盖/预期下滑标的不适用）")
    g100 = cagr * 100
    pe_low, pe_high, pe_sell = (min(0.8 * g100, 30), min(1.0 * g100, 30), min(1.2 * g100, 30))
    return _method("PEG法", layer, weight, True,
                   f"EPS={eps}；一致预测CAGR={fmt_pct(cagr)}；PEG系数[0.8,1.0]，减持1.2；PE上限30（§2.4）",
                   f"合理PE=[{pe_low:.1f}, {pe_high:.1f}]→区间=EPS×PE；减持PE={pe_sell:.1f}",
                   eps * pe_low, eps * pe_high, eps * pe_sell)


def _m_industry_pe(snap, layer, weight):
    """同业PE对比法（验证锚）：合理上沿=EPS×行业均值PE（§2.2'PE≤行业均值'口径），下沿×0.7。"""
    eps = _latest_eps(snap)
    ind_pe = safe_get(snap, "peers", "行业均值PE")
    src = safe_get(snap, "peers", "行业均值PE口径") or ""
    if not eps or eps <= 0:
        return _method("同业PE对比法", layer, weight, False, "—", "—", reason="EPS缺失或≤0")
    if not ind_pe or ind_pe <= 0:
        return _method("同业PE对比法", layer, weight, False, "—", "—", reason="行业均值PE数据缺失")
    return _method("同业PE对比法", layer, weight, True,
                   f"EPS={eps}；行业均值PE={ind_pe:.1f}（{src[:30]}）",
                   f"区间=[EPS×行业PE×0.7, EPS×行业PE]；减持=EPS×行业PE×1.3",
                   eps * ind_pe * 0.7, eps * ind_pe, eps * ind_pe * 1.3)


def _latest_eps(snap) -> float | None:
    eps_list = [to_float(e) for e in (safe_get(snap, "financials", "EPS各年") or [])]
    return next((e for e in reversed(eps_list) if e is not None), None)


def _latest_dps(snap) -> float | None:
    dps = safe_get(snap, "dividends", "每股分红按年度") or {}
    if not dps:
        return None
    return to_float(dps[max(dps.keys())])


# 各行业类的方法集（层级/权重从config读取；此处为方法名→实现的映射）
_IMPL = {"pe_quantile": _m_pe_quantile, "pb_quantile": _m_pb_quantile,
         "dividend_anchor": _m_dividend_anchor, "peg": _m_peg, "industry_pe": _m_industry_pe}


def multi_anchor_valuation(snap: dict, config: dict, category: str | None) -> dict:
    """多锚估值统一入口。方法集与参数由 valuation_model.yaml industry_models 定义。"""
    industry = safe_get(snap, "basic", "行业")
    val_class = resolve_val_class(category, industry)
    model_cfg = safe_get(config, "valuation_model", "industry_models", "classes", val_class) or {}
    method_specs = model_cfg.get("methods") or []
    if not method_specs:
        return {"ok": False, "error": f"config未定义 {val_class} 的估值方法集", "val_class": val_class}

    methods = []
    for spec in method_specs:
        impl = _IMPL.get(spec.get("id"))
        if impl is None:
            continue
        methods.append(impl(snap, spec.get("layer", "辅助"), float(spec.get("weight", 0))))

    usable = [m for m in methods if m["可用"]]
    price = safe_get(snap, "quote", "最新收盘价")
    result = {"ok": True, "val_class": val_class,
              "val_class_name": model_cfg.get("name", val_class),
              "rationale": model_cfg.get("rationale", ""),
              "methods": methods, "usable_count": len(usable),
              "price": price, "combined": None, "diverged": False,
              "divergence_pct": None, "notes": []}

    if len(usable) < 2:
        result["notes"].append(f"可用估值方法仅{len(usable)}种（<2），不输出综合区间，防止单一方法误导")
        return result

    # 偏差检查：各方法区间中值的极差/均值 > 30% → 不强行加权（config divergence_threshold）
    threshold = safe_get(config, "valuation_model", "industry_models",
                         "divergence_threshold") or 0.30
    mids = [(m["区间"][0] + m["区间"][1]) / 2 for m in usable]
    mid_avg = sum(mids) / len(mids)
    divergence = (max(mids) - min(mids)) / mid_avg if mid_avg else None
    result["divergence_pct"] = divergence

    if divergence is not None and divergence > threshold:
        result["diverged"] = True
        hi = usable[mids.index(max(mids))]["方法"]
        lo = usable[mids.index(min(mids))]["方法"]
        result["notes"].append(
            f"方法间偏差 {fmt_pct(divergence)} > {fmt_pct(threshold)}阈值，不强行加权——"
            f"偏差主要来自 {hi}（偏高）与 {lo}（偏低），通常意味着市场定价逻辑与其中一类锚不匹配"
            "（如成长转价值、周期位置分歧），请分别参考各方法区间并结合研究笔记判断")
        return result

    # 加权综合（可用方法权重归一化）
    w_sum = sum(m["权重"] for m in usable) or 1
    low = sum(m["区间"][0] * m["权重"] for m in usable) / w_sum
    high = sum(m["区间"][1] * m["权重"] for m in usable) / w_sum
    sells = [m["减持阈"] for m in usable if m["减持阈"]]
    sell = sum(s * m["权重"] for s, m in zip(sells, [u for u in usable if u["减持阈"]])) / \
        (sum(m["权重"] for m in usable if m["减持阈"]) or 1)
    margin_coef = safe_get(config, "valuation_model", "industry_models",
                           "safety_margin_coef") or 0.90
    result["combined"] = {
        "合理区间": [round(low, 2), round(high, 2)],
        "安全边际价": round(low * margin_coef, 2),
        "高估阈值": round(sell, 2),
        "权重口径": "、".join(f"{m['方法']}{m['权重'] / w_sum:.0%}" for m in usable),
    }
    if price and result["combined"]:
        pos = ("低于安全边际价" if price < result["combined"]["安全边际价"] else
               "位于合理区间内" if low <= price <= high else
               "高于高估阈值" if price > sell else
               "介于合理区间与高估阈值之间" if price > high else "低于合理区间下沿")
        result["combined"]["现价位置"] = f"现价 {price} → {pos}"
    return result
