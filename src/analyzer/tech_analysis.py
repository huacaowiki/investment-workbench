# -*- coding: utf-8 -*-
"""
tech_analysis.py — 个股技术面分析引擎（v4.5.0 报告重构·模块四）
六维度量化描述（均线/MACD/量能/KDJ/BOLL/趋势综合）+ 分档关键价位表。
纪律边界：全部为对既有行情数据的量化描述与体系信号复述，不构成走势预测；
         输出模板化措辞，无自由发挥的主观判断。
"""
from __future__ import annotations

from src.data.data_utils import (avg, boll_bands, kdj_series, macd_lines,
                                 moving_average, safe_get, to_float)


def analyze_technicals(snap: dict) -> dict:
    """
    输入个股快照（quote.kline 序列），输出：
    {"available", "trend": [六维度研判...], "verdict": 偏多/偏空/纠缠,
     "supports": [...], "resistances": [...], "note"}
    """
    k = safe_get(snap, "quote", "kline") or {}
    closes = [to_float(x) for x in (k.get("closes") or [])]
    highs = [to_float(x) for x in (k.get("highs") or [])]
    lows = [to_float(x) for x in (k.get("lows") or [])]
    amounts = [to_float(x) for x in (k.get("amounts") or [])]
    last = closes[-1] if closes else None
    if not last or len([c for c in closes if c]) < 61:
        return {"available": False, "trend": [], "verdict": "样本不足",
                "supports": [], "resistances": [],
                "note": "K线样本不足61个交易日（新股/数据缺失），技术面模块降级为不可用"}

    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    dif, dea = macd_lines(closes)
    macd_bar = (dif[-1] - dea[-1]) * 2 if dif and dea else None
    kv, dv_, jv = kdj_series(highs, lows, closes)
    bl, bm, bu = boll_bands(closes)
    vol5, vol20 = avg(amounts[-5:]), avg(amounts[-20:])

    bull, bear = 0, 0
    trend = []

    # ① 均线系统
    if all(v for v in (ma5, ma20, ma60)):
        if ma5 > ma20 > ma60 and last > ma5:
            desc, s = f"多头排列（现价>{ma5:.2f}(MA5)>{ma20:.2f}(MA20)>{ma60:.2f}(MA60)）", 1
        elif ma5 < ma20 < ma60 and last < ma5:
            desc, s = f"空头排列（现价<{ma5:.2f}(MA5)<{ma20:.2f}(MA20)<{ma60:.2f}(MA60)）", -1
        else:
            desc, s = f"均线纠缠（MA5 {ma5:.2f} / MA20 {ma20:.2f} / MA60 {ma60:.2f}）", 0
        bull += s == 1
        bear += s == -1
        trend.append({"维度": "均线系统", "方向": "多" if s == 1 else "空" if s == -1 else "中性", "描述": desc})

    # ② MACD
    if dif and dea:
        above = dif[-1] > dea[-1]
        zero = "零轴上方" if dif[-1] > 0 else "零轴下方"
        s = 1 if (above and macd_bar and macd_bar > 0) else (-1 if not above else 0)
        bull += s == 1
        bear += s == -1
        trend.append({"维度": "MACD", "方向": "多" if s == 1 else "空" if s == -1 else "中性",
                      "描述": f"DIF {dif[-1]:.3f} {'>' if above else '<'} DEA {dea[-1]:.3f}（{zero}，柱{'扩张' if macd_bar and macd_bar > 0 else '收缩/为负'}）"})

    # ③ 量能
    if vol5 and vol20:
        ratio = vol5 / vol20
        s = 1 if ratio > 1.2 else (-1 if ratio < 0.8 else 0)
        bull += s == 1
        bear += s == -1
        trend.append({"维度": "量能", "方向": "放量" if s == 1 else "缩量" if s == -1 else "平量",
                      "描述": f"5日均额/20日均额 = {ratio:.2f}（{'放量' if s == 1 else '缩量' if s == -1 else '量能平稳'}）"})

    # ④ KDJ
    if kv is not None:
        s = 1 if (kv > dv_ and kv < 80) else (-1 if kv < dv_ else 0)
        state = "超买区" if kv > 80 else "超卖区" if kv < 20 else "中性区"
        bull += s == 1
        bear += s == -1
        trend.append({"维度": "KDJ", "方向": "多" if s == 1 else "空" if s == -1 else "中性",
                      "描述": f"K={kv} D={dv_} J={jv}（{state}，K{'上穿' if kv > dv_ else '下穿/低于'}D）"})

    # ⑤ BOLL
    if bl is not None:
        if last > bu:
            desc, s = f"突破上轨 {bu}（超强/超买并存）", 0
        elif last > bm:
            desc, s = f"运行于中轨 {bm} 与上轨 {bu} 之间（偏强区）", 1
        elif last > bl:
            desc, s = f"运行于下轨 {bl} 与中轨 {bm} 之间（偏弱区）", -1
        else:
            desc, s = f"跌破下轨 {bl}（超弱/超卖并存）", 0
        bull += s == 1
        bear += s == -1
        trend.append({"维度": "BOLL", "方向": "多" if s == 1 else "空" if s == -1 else "极端",
                      "描述": desc})

    # ⑥ 趋势综合
    verdict = "偏多" if bull - bear >= 2 else "偏空" if bear - bull >= 2 else "多空纠缠"
    trend.append({"维度": "趋势综合", "方向": verdict,
                  "描述": f"六维信号：多方 {bull} 项 vs 空方 {bear} 项 → {verdict}"})

    # 关键价位分档（按距现价由近及远）
    y_high = safe_get(snap, "quote", "52周最高")
    y_low = safe_get(snap, "quote", "52周最低")
    low60 = min(x for x in lows[-60:] if x) if any(lows[-60:]) else None
    high60 = max(x for x in highs[-60:] if x) if any(highs[-60:]) else None
    sup_pool = [(ma20, "MA20（20日成本线）"), (ma60, "MA60（趋势生命线）"),
                (bl, "BOLL下轨"), (low60, "近60日低点"), (y_low, "52周低点")]
    res_pool = [(bu, "BOLL上轨"), (high60, "近60日高点"), (y_high, "52周高点")]
    supports = sorted([{"价位": round(p, 2), "依据": w} for p, w in sup_pool if p and p < last],
                      key=lambda x: -x["价位"])[:3]
    resistances = sorted([{"价位": round(p, 2), "依据": w} for p, w in res_pool if p and p > last],
                         key=lambda x: x["价位"])[:3]
    for i, s in enumerate(supports):
        s["档位"] = f"第{i + 1}支撑"
    for i, r in enumerate(resistances):
        r["档位"] = f"第{i + 1}阻力"

    return {"available": True, "trend": trend, "verdict": verdict,
            "supports": supports, "resistances": resistances,
            "indicators": {"MA5": ma5, "MA20": ma20, "MA60": ma60,
                           "KDJ": (kv, dv_, jv), "BOLL": (bl, bm, bu),
                           "量比5/20": round(vol5 / vol20, 2) if (vol5 and vol20) else None},
            "note": "技术指标为对既有行情的量化描述，不构成走势预测（体系§3.2执行仍以预设止盈止损触发）"}
