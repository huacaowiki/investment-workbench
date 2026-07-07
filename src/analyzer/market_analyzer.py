# -*- coding: utf-8 -*-
"""
market_analyzer.py — 市场日报分析引擎
输入 market_data.get_market_snapshot() 的快照，输出结构化分析结果（供报告生成填充）。
铁则约束：
  - 市场状态判定严格按 config/investment_system.yaml market_state_framework 条件逐条核对；
    程序无法计算的条件（股债利差、政策表述等）明确标注【待人工确认】，绝不臆断；
  - 仓位约束提示严格按 config/risk_control.yaml position_by_market_state 映射；
  - "情绪观察分"仅是报告可读性用的工程化描述指标（0-10，基于涨跌结构与成交），
    已在输出中显式标注"非铁则"，不参与任何规则判定。
"""
from __future__ import annotations

from datetime import datetime

from src.data.data_utils import fmt_pct, fmt_yi, safe_get, to_float
from src.utils.file_utils import DIRS, load_config, read_json

# 用户每周日人工判定的市场状态落盘位置（§1.3：每周更新，一周内不改判）
MARKET_STATE_FILE = DIRS["data_processed"] / "market_state.json"

STATE_KEYS = ["A_undervalued", "B_low", "B_neutral", "B_high", "C_overvalued"]
STATE_NAMES_CN = {"A_undervalued": "A区·低估", "B_low": "B偏低", "B_neutral": "B中性",
                  "B_high": "B偏高", "C_overvalued": "C区·高估"}


def _index_summary(snapshot: dict) -> list[dict]:
    """指数总览行（名称/收盘/涨跌幅/成交额）。"""
    rows = []
    for r in snapshot.get("index_spot") or []:
        rows.append({
            "名称": r.get("名称"),
            "收盘": to_float(r.get("最新价")),
            "涨跌幅_pct": to_float(r.get("涨跌幅")),
            "成交额": to_float(r.get("成交额")),
        })
    return rows


def _total_turnover(snapshot: dict) -> float | None:
    """沪深两市成交额合计（用上证+深成指成交额近似，口径在报告标注）。"""
    total = 0.0
    found = False
    for r in snapshot.get("index_spot") or []:
        if r.get("名称") in ("上证指数", "深证成指"):
            v = to_float(r.get("成交额"))
            if v:
                total += v
                found = True
    return total if found else None


def _sector_strength(snapshot: dict, top_n: int = 5) -> dict:
    """板块强弱排序：按涨跌幅取前/后N。"""
    boards = snapshot.get("board_ranks") or []
    valid = [b for b in boards if to_float(b.get("涨跌幅")) is not None]
    ordered = sorted(valid, key=lambda b: to_float(b.get("涨跌幅")), reverse=True)
    return {"top": ordered[:top_n], "bottom": ordered[-top_n:][::-1],
            "source_note": (ordered[0].get("数据源") if ordered and ordered[0].get("数据源") else "东方财富行业板块")}


def _sentiment(snapshot: dict) -> dict:
    """
    市场情绪观察（非铁则的工程化描述指标）：
    上涨占比、涨停/跌停、活跃度表数据罗列 + 0-10观察分。
    """
    act = {str(r.get("item", r.get("指标", ""))): r for r in (snapshot.get("market_activity") or [])}

    def act_value(*names):
        for n in names:
            if n in act:
                return to_float(act[n].get("value", act[n].get("数值")))
        return None

    up = act_value("上涨")
    down = act_value("下跌")
    limit = snapshot.get("limit_stats") or {}
    zt, dt = limit.get("涨停家数"), limit.get("跌停家数")
    up_ratio = up / (up + down) if (up and down) else None

    # 观察分：上涨占比60% + 涨跌停结构40%；仅用于报告展示
    score = None
    if up_ratio is not None:
        score = up_ratio * 6
        if zt is not None and dt is not None and (zt + dt) > 0:
            score += (zt / (zt + dt)) * 4
        else:
            score = score / 6 * 10
        score = round(score, 1)
    return {"上涨家数": up, "下跌家数": down, "上涨占比": up_ratio,
            "涨停家数": zt, "跌停家数": dt,
            "涨停代表": limit.get("涨停代表") or [], "跌停代表": limit.get("跌停代表") or [],
            "情绪观察分_0到10": score,
            "说明": "情绪观察分为报告展示用工程化指标（上涨占比60%+涨跌停结构40%），非config铁则，不参与规则判定"}


def check_market_state(snapshot: dict, config: dict) -> dict:
    """
    §1.3 市场状态条件逐条核对 + 程序初判（v4.2.0 auto_judgment）。
    量化条件（automated: true）自动判定；定性条件（政策/开户数/日光基/破净率）
    不参与判定，仅在报告中列示为参考。
    判定逻辑（investment_system.yaml v4.2.0 裁决）：
      A区/C区：各自可判定条件满足≥3条 → 进入该区；
      否则按股债利差落入B区细分带（利差 = 1/中证全指PE - 10年国债收益率）。
    生效状态：人工 set-state（7日内有效）优先于程序初判。
    """
    sh_close = next((to_float(r.get("最新价")) for r in (snapshot.get("index_spot") or [])
                     if r.get("名称") == "上证指数"), None)
    turnover = _total_turnover(snapshot)
    history = snapshot.get("turnover_history") or []
    cs_pe = safe_get(snapshot, "csindex_pe", "市盈率1")
    cn10y = snapshot.get("cn10y_yield")
    sh_high = safe_get(snapshot, "sh_index_high", "历史最高收盘")

    spread = (1 / cs_pe - cn10y) if (cs_pe and cn10y is not None) else None

    checks = []

    def add(zone, cond, verdict, value, note=""):
        checks.append({"区": zone, "条件": cond, "程序判定": verdict,
                       "当前值": value + (f"｜{note}" if note else "")})

    # ---- A区可判定条件 ----
    a_hits = 0
    v = (sh_close < 3000) if sh_close else None
    a_hits += 1 if v else 0
    add("A区", "上证跌破3000点（或PB历史5%分位以下）", v,
        f"上证收盘 {sh_close}" if sh_close else "数据缺失",
        "PB分位半边无公开数据源，按点位半边判定")
    v = (cs_pe < 22) if cs_pe else None
    a_hits += 1 if v else 0
    add("A区", "全A PE<22倍", v, f"中证全指PE1 = {cs_pe}" if cs_pe else "数据缺失",
        "替代口径：中证全指（含金融石化，偏保守）")
    v = (spread > 0.05) if spread is not None else None
    a_hits += 1 if v else 0
    add("A区", "股债利差>5%", v,
        f"利差 {fmt_pct(spread)}（1/PE {fmt_pct(1/cs_pe) if cs_pe else '—'} − 国债 {fmt_pct(cn10y)}）"
        if spread is not None else "数据缺失")
    recent10 = [h["turnover"] for h in history[-10:] if h.get("turnover")]
    if len(recent10) >= 10:
        v = all(t < 7e11 for t in recent10)
        low_note = "自建序列样本充足"
    elif recent10:
        v = all(t < 7e11 for t in recent10)
        low_note = f"自建序列仅{len(recent10)}日样本（<10日），判定随样本累积收敛"
    else:
        v, low_note = None, "无成交额历史样本"
    a_hits += 1 if v else 0
    add("A区", "日成交额连续10日<7000亿", v,
        f"近{len(recent10)}日成交额 {['%.0f亿' % (t/1e8) for t in recent10[-3:]]}…" if recent10 else "数据缺失",
        low_note + "；换手率半边无公开序列，按成交额半边判定")
    add("A区", "全市场破净率>10%", None, "不参与自动判定", "无可编程公开数据源（v4.2.0核实）")
    add("A区", "官方'提振资本市场'表述", None, "不参与自动判定", "定性条件（裁决#3）")

    # ---- C区可判定条件 ----
    c_hits = 0
    v = (sh_close >= sh_high * 0.95) if (sh_close and sh_high) else None
    c_hits += 1 if v else 0
    add("C区", "上证逼近/突破历史高点（≥历史最高×95%）", v,
        f"收盘 {sh_close} vs 历史最高 {sh_high}" if (sh_close and sh_high) else "数据缺失")
    recent5 = [h["turnover"] for h in history[-5:] if h.get("turnover")]
    v = (len(recent5) >= 5 and all(t > 1.5e12 for t in recent5)) if recent5 else None
    if recent5 and len(recent5) < 5:
        v = all(t > 1.5e12 for t in recent5)
    c_hits += 1 if v else 0
    add("C区", "日成交额连续5日>1.5万亿", v,
        f"近{len(recent5)}日样本" if recent5 else "数据缺失",
        "恐惧贪婪指数无可编程源，取原文并列可测部分（裁决#2）")
    v = (spread < 0.02) if spread is not None else None
    c_hits += 1 if v else 0
    add("C区", "股债利差<2%", v, f"利差 {fmt_pct(spread)}" if spread is not None else "数据缺失")
    add("C区", "新股民开户数同比历史高位", None, "不参与自动判定", "无可编程公开数据源")
    add("C区", "偏股基金日光基", None, "不参与自动判定", "定性条件")
    add("C区", "官方提示金融市场风险", None, "不参与自动判定", "定性条件（裁决#3）")

    # ---- 程序初判 ----
    if a_hits >= 3:
        auto_state, basis = "A_undervalued", f"A区可判定条件命中{a_hits}条（≥3）"
    elif c_hits >= 3:
        auto_state, basis = "C_overvalued", f"C区可判定条件命中{c_hits}条（≥3）"
    elif spread is not None:
        if spread >= 0.045:
            auto_state = "B_low"
        elif spread >= 0.035:
            auto_state = "B_neutral"
        else:
            auto_state = "B_high"   # 利差<3%时亦落此档（偏保守），C区另由≥3条判定
        basis = (f"A区命中{a_hits}条/C区命中{c_hits}条（均<3），按股债利差 {fmt_pct(spread)} 落入B区细分带")
    else:
        auto_state, basis = None, "股债利差数据缺失，无法初判"

    # ---- 生效状态：人工判定（7日内）优先 ----
    saved = read_json(MARKET_STATE_FILE) or {}
    manual_state = saved.get("state") if saved.get("source") == "manual" else None
    manual_date = saved.get("date")
    if manual_state and manual_date:
        try:
            age = (datetime.now() - datetime.strptime(manual_date, "%Y-%m-%d")).days
            if age > 7:   # §1.3：每周更新；过期的人工判定降级为参考
                manual_state = None
        except ValueError:
            pass
    effective = manual_state or auto_state
    return {
        "conditions": checks,
        "a_hits": a_hits, "c_hits": c_hits,
        "equity_bond_spread": spread,
        "auto_state": auto_state,
        "auto_basis": basis,
        "manual_state": manual_state,
        "manual_state_date": manual_date if manual_state else None,
        "effective_state": effective,
        "note": (f"生效状态来源：人工判定（{manual_date}，7日内有效，优先于程序初判）" if manual_state
                 else f"生效状态来源：程序初判（{basis}）；人工 set-state 可覆盖"),
    }


def judge_and_record_state(snapshot: dict | None = None, config: dict | None = None) -> dict:
    """
    执行市场状态程序初判并落盘（run.py judge-state 入口）。
    不覆盖7日内的人工判定；写入 source=auto 记录。
    """
    from src.data.market_data import get_market_snapshot
    from src.utils.file_utils import write_json
    config = config or load_config()
    snapshot = snapshot or get_market_snapshot()
    result = check_market_state(snapshot, config)
    if result["manual_state"]:
        result["recorded"] = False
        result["record_note"] = "存在7日内人工判定，程序初判仅供参考，未落盘覆盖"
        return result
    if result["auto_state"]:
        write_json(MARKET_STATE_FILE, {
            "state": result["auto_state"],
            "date": datetime.now().strftime("%Y-%m-%d"),
            "source": "auto",
            "basis": result["auto_basis"],
            "note": "程序初判（v4.2.0 auto_judgment）；人工 set-state 优先",
        })
        result["recorded"] = True
    else:
        result["recorded"] = False
        result["record_note"] = "数据不足未落盘"
    return result


def _position_constraints(state_check: dict, config: dict) -> dict:
    """按§1.4映射输出两组合仓位上限；无人工状态时输出全表供对照。"""
    mapping = safe_get(config, "risk_control", "position_by_market_state") or {}
    state = state_check.get("effective_state")
    table = {k: v for k, v in mapping.items() if k != "source"}
    current = table.get(state) if state else None
    strict = safe_get(config, "risk_control", "timing_strict_mode") or {}
    return {"当前状态": state, "当前约束": current, "全表": table,
            "严格风控模式": strict}


def _risk_alerts(snapshot: dict, state_check: dict, sentiment: dict) -> list[dict]:
    """
    风险提示（P0-P3 分级按 risk_control.yaml risk_alert_levels 的定义归类）。
    只报告体系内规则相关的风险，不做主观行情预测。
    """
    alerts = []
    vol = safe_get(snapshot, "volatility_gauge", "数值")
    vol_name = safe_get(snapshot, "volatility_gauge", "指标") or "波动率"
    vol_note = safe_get(snapshot, "volatility_gauge", "口径") or ""
    if vol is not None and vol > 0.35:
        alerts.append({"级别": "P1", "内容":
                       f"{vol_name} {fmt_pct(vol)} > 35%，触发择时组合严格风控模式"
                       f"（§1.5：仓位上限30%、单票15%、不开新仓）。{vol_note}"})
    elif vol is not None and vol > 0.30:
        alerts.append({"级别": "P2", "内容":
                       f"{vol_name} {fmt_pct(vol)} 接近35%严格风控触发线，需观察"})
    if state_check.get("effective_state") is None:
        alerts.append({"级别": "P2", "内容":
                       "市场状态无法判定（数据缺失且无人工记录）——请运行 judge-state 或人工 set-state"})
    elif state_check.get("effective_state") == "C_overvalued":
        alerts.append({"级别": "P1", "内容":
                       f"市场状态为C区·高估（{state_check.get('note')}）：择时只卖不买降至20%以下；"
                       "股息暂停买入并逐票执行估值卖出检查（v4.2.0裁决#10）"})
    dt = sentiment.get("跌停家数")
    if dt is not None and dt > 30:
        alerts.append({"级别": "P3", "内容": f"当日跌停 {int(dt)} 家，情绪偏弱，注意§3.5异常预案（大盘单日跌幅>5%不恐慌卖出）"})
    if snapshot.get("errors"):
        alerts.append({"级别": "P3", "内容": f"数据缺失项：{', '.join(snapshot['errors'])}（分析已按缺失处理）"})
    if not alerts:
        alerts.append({"级别": "P3", "内容": "无体系规则相关的风险触发"})
    return alerts


def analyze_market(snapshot: dict, config: dict | None = None) -> dict:
    """
    市场分析统一入口。返回结构化结果，供 report_writer 填充模板：
    {"date", "index_summary", "total_turnover", "sector", "sentiment",
     "state_check", "position", "alerts", "capital_flow", "errors"}
    """
    config = config or load_config()
    sentiment = _sentiment(snapshot)
    state_check = check_market_state(snapshot, config)
    return {
        "date": snapshot.get("date"),
        "index_summary": _index_summary(snapshot),
        "total_turnover": _total_turnover(snapshot),
        "sector": _sector_strength(snapshot),
        "sentiment": sentiment,
        "state_check": state_check,
        "position": _position_constraints(state_check, config),
        "alerts": _risk_alerts(snapshot, state_check, sentiment),
        "capital_flow": {"margin": snapshot.get("margin_summary") or {},
                         "lhb": (snapshot.get("lhb") or [])[:10]},
        "volatility": snapshot.get("volatility_gauge"),
        "csindex_pe": snapshot.get("csindex_pe"),
        "cn10y_yield": snapshot.get("cn10y_yield"),
        "errors": snapshot.get("errors") or {},
    }
