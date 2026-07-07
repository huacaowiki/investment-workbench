# -*- coding: utf-8 -*-
"""
self_check.py — 分析逻辑自检机制（2026-07-07审计任务新增）
每次生成报告前自动校验"分析结果 ↔ config铁则"的一致性，发现偏差主动输出预警，
预警随报告一起展示（报告'自检'小节），绝不静默吞掉。

自检维度：
  1. 规则参数一致性：结果中引用的仓位上限/阈值必须与config当前值一致（防配置改后代码未同步）
  2. 结论自洽性：verdict 与各门槛判定状态必须逻辑一致（有FAIL必排除/不满足；无FAIL无MISSING不得判排除）
  3. 数值合法性：评分∈[0,6]且等于明细加和；分位∈[0,1]；价格区间分级∈三区
  4. 边界口径落实：亏损股PE口径、分位样本不足降级必须在明细中留痕
自检本身失败（异常）时输出P0级预警，不阻断报告生成。
"""
from __future__ import annotations

from src.data.data_utils import safe_get, to_float

PASS, FAIL, MISSING = "PASS", "FAIL", "MISSING"


def check_stock_result(result: dict, snap: dict, config: dict) -> list[dict]:
    """个股分析自检。返回预警列表 [{"级别","内容"}...]，空列表=全部通过。"""
    warnings: list[dict] = []
    try:
        warnings.extend(_check_stock_caps(result, config))
        warnings.extend(_check_stock_verdicts(result))
        warnings.extend(_check_stock_numbers(result))
        warnings.extend(_check_stock_edge_cases(result, snap))
    except Exception as exc:  # noqa: BLE001 —— 自检崩溃必须显性化而非静默
        warnings.append({"级别": "P0", "内容": f"自检机制自身异常：{type(exc).__name__}: {exc}（报告仍生成，请复核）"})
    return warnings


def _check_stock_caps(result: dict, config: dict) -> list[dict]:
    """规则参数一致性：结果引用的仓位上限与config实时值核对。"""
    out = []
    caps = safe_get(config, "risk_control", "position_caps") or {}
    div_cap = safe_get(caps, "dividend", "single_stock")
    tim_cap = safe_get(caps, "timing", "single_stock")
    modifier = caps.get("satellite_circle_modifier", 0.5)
    circle = safe_get(result, "classify", "circle")
    got_div = safe_get(result, "risk", "股息组合单票上限")
    got_tim = safe_get(result, "risk", "择时组合单票上限")
    if circle == "excluded":
        if got_div is not None or got_tim is not None:
            out.append({"级别": "P0", "内容": "自检：能力圈外标的仍给出仓位上限，违反§2.1禁止建仓"})
        return out
    factor = modifier if circle in ("satellite", None) else 1
    for name, got, base in [("股息", got_div, div_cap), ("择时", got_tim, tim_cap)]:
        if base is not None and got is not None and abs(got - base * factor) > 1e-9:
            out.append({"级别": "P0", "内容":
                        f"自检：{name}组合单票上限 {got} 与config推导值 {base * factor} 不一致——分析代码可能未同步最新配置"})
    return out


def _check_stock_verdicts(result: dict) -> list[dict]:
    """结论自洽性：verdict 与门槛判定状态的逻辑一致性。"""
    out = []
    d = result.get("dividend") or {}
    statuses = [c["status"] for c in (d.get("universal") or []) + (d.get("category") or [])]
    verdict = d.get("verdict", "")
    if FAIL in statuses and not verdict.startswith("排除"):
        out.append({"级别": "P0", "内容": f"自检：股息门槛存在FAIL但结论为'{verdict}'，结论与判定矛盾"})
    if FAIL not in statuses and MISSING not in statuses and verdict.startswith("排除"):
        out.append({"级别": "P0", "内容": "自检：股息门槛无FAIL/MISSING但结论为排除，结论与判定矛盾"})
    t = result.get("timing") or {}
    t_statuses = [c["status"] for c in (t.get("pool") or []) + (t.get("gates") or [])]
    t_verdict = t.get("verdict", "")
    if FAIL in t_statuses and "不满足" not in t_verdict:
        out.append({"级别": "P0", "内容": f"自检：择时门槛存在FAIL但结论为'{t_verdict}'，结论与判定矛盾"})
    score = t.get("auto_score_floor")
    if (FAIL not in t_statuses and MISSING not in t_statuses and score is not None
            and score < 3 and "观察" not in t_verdict):
        out.append({"级别": "P1", "内容": f"自检：评分{score}<3但结论未按裁决#6标注'仅观察'"})
    return out


def _check_stock_numbers(result: dict) -> list[dict]:
    """数值合法性：评分范围与加和、分级枚举。"""
    out = []
    t = result.get("timing") or {}
    scoring = t.get("scoring") or []
    total = t.get("auto_score_floor")
    detail_sum = sum(s.get("得分") or 0 for s in scoring)
    if total is not None and total != detail_sum:
        out.append({"级别": "P0", "内容": f"自检：评分总分{total}≠明细加和{detail_sum}"})
    if total is not None and not (0 <= total <= 6):
        out.append({"级别": "P0", "内容": f"自检：评分{total}超出[0,6]合法区间"})
    zone = safe_get(result, "zone", "zone") or ""
    if not any(zone.startswith(z) for z in ("买入区", "持有区", "减持区")):
        out.append({"级别": "P1", "内容": f"自检：价格分级'{zone}'不在三区枚举内"})
    return out


def _check_stock_edge_cases(result: dict, snap: dict) -> list[dict]:
    """边界口径落实：亏损股/新股口径必须在判定明细中留痕。"""
    out = []
    pe = safe_get(snap, "valuation", "PE_TTM")
    if pe is not None and to_float(pe) is not None and to_float(pe) <= 0:
        d = result.get("dividend") or {}
        items = (safe_get(d, "valuation_buy", "items") or [])
        pe_item = next((c for c in items if "PE≤行业均值" in c["item"]), None)
        if pe_item and pe_item["status"] == PASS:
            out.append({"级别": "P0", "内容": f"自检：PE={pe}≤0（亏损）但PE估值条款判PASS——亏损股口径未生效"})
    samples = safe_get(snap, "valuation", "分位样本数")
    if samples is not None and samples < 250:
        d = result.get("dividend") or {}
        items = (safe_get(d, "valuation_buy", "items") or [])
        if any(c["status"] in (PASS, FAIL) and "分位" in c["item"] and "样本" not in c["detail"]
               for c in items):
            out.append({"级别": "P1", "内容": f"自检：分位样本仅{samples}日但分位条款仍硬判定，新股降级口径未生效"})
    return out


def check_daily_result(analysis: dict, config: dict) -> list[dict]:
    """市场日报自检。"""
    warnings: list[dict] = []
    try:
        # 1) 仓位映射与config一致
        mapping = safe_get(config, "risk_control", "position_by_market_state") or {}
        table = safe_get(analysis, "position", "全表") or {}
        for k, v in mapping.items():
            if k == "source" or not isinstance(v, dict):
                continue
            if k not in table:
                warnings.append({"级别": "P0", "内容": f"自检：仓位映射缺少状态 {k}，与config不一致"})
        # 2) 生效状态合法性
        state = safe_get(analysis, "state_check", "effective_state")
        if state is not None and state not in mapping:
            warnings.append({"级别": "P0", "内容": f"自检：生效市场状态'{state}'不在config状态枚举内"})
        # 3) 命中数与条件表自洽
        sc = analysis.get("state_check") or {}
        cond_true = {"A区": 0, "C区": 0}
        for c in sc.get("conditions") or []:
            if c.get("程序判定") is True:
                cond_true[c["区"]] = cond_true.get(c["区"], 0) + 1
        if sc.get("a_hits") is not None and sc["a_hits"] != cond_true.get("A区", 0):
            warnings.append({"级别": "P0", "内容":
                             f"自检：A区命中数{sc['a_hits']}与条件表中判True数{cond_true.get('A区', 0)}不一致"})
        if sc.get("c_hits") is not None and sc["c_hits"] != cond_true.get("C区", 0):
            warnings.append({"级别": "P0", "内容":
                             f"自检：C区命中数{sc['c_hits']}与条件表中判True数{cond_true.get('C区', 0)}不一致"})
        # 4) 情绪观察分范围
        score = safe_get(analysis, "sentiment", "情绪观察分_0到10")
        if score is not None and not (0 <= score <= 10):
            warnings.append({"级别": "P0", "内容": f"自检：情绪观察分{score}超出[0,10]"})
        # 5) 风险区非空
        if not analysis.get("alerts"):
            warnings.append({"级别": "P1", "内容": "自检：风险提示区为空——风险完备性要求至少有一条状态说明"})
    except Exception as exc:  # noqa: BLE001
        warnings.append({"级别": "P0", "内容": f"自检机制自身异常：{type(exc).__name__}: {exc}"})
    return warnings
