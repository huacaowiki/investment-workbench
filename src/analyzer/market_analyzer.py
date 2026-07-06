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

from src.data.data_utils import fmt_pct, fmt_yi, safe_get, to_float
from src.utils.file_utils import DIRS, load_config, read_json

# 用户每周日人工判定的市场状态落盘位置（§1.3：每周更新，一周内不改判）
MARKET_STATE_FILE = DIRS["data_processed"] / "market_state.json"

STATE_KEYS = ["A_undervalued", "B_low", "B_neutral", "B_high", "C_overvalued"]


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
    §1.3 市场状态条件逐条核对。
    返回：{"conditions": [...], "auto_verdict": ..., "manual_state": ..., "effective_state": ...}
    - 程序可判定：上证点位、成交额（当日）、沪深300波动率（严格模式条件）
    - 待人工：股债利差、破净率、Wind全A PE、政策表述、恐惧贪婪等
    - effective_state 优先取用户每周人工判定（data/processed/market_state.json），
      无记录时为 None（报告提示按§1.3流程周日判定），绝不由程序拍板。
    """
    framework = safe_get(config, "investment_system", "market_state_framework") or {}
    sh_close = next((to_float(r.get("最新价")) for r in (snapshot.get("index_spot") or [])
                     if r.get("名称") == "上证指数"), None)
    turnover = _total_turnover(snapshot)

    checks = []
    # A区条件① 上证跌破3000
    checks.append({"区": "A区", "条件": "上证跌破3000点或市净率历史5%分位以下",
                   "程序判定": (sh_close < 3000) if sh_close else None,
                   "当前值": f"上证收盘 {sh_close}" if sh_close else "数据缺失"})
    # A区条件⑤ 成交额（程序只有当日值，10日连续性待人工）
    checks.append({"区": "A区", "条件": "日成交额连续10日<7000亿且换手率<1.5%",
                   "程序判定": None,
                   "当前值": f"当日两市成交额约 {fmt_yi(turnover)}（连续10日口径需人工跟踪）" if turnover else "数据缺失"})
    # C区条件② 成交持续>1.5万亿
    checks.append({"区": "C区", "条件": "日成交持续>1.5万亿（恐惧贪婪极度贪婪）",
                   "程序判定": None,
                   "当前值": f"当日两市成交额约 {fmt_yi(turnover)}（持续性与情绪指数需人工确认）" if turnover else "数据缺失"})
    # 其余条件：股债利差/破净率/PE/开户数/日光基/政策表述 → 待人工
    for zone, cond in [("A区", "Wind全A非金融石化PE<22倍"), ("A区", "股债利差>5%"),
                       ("A区", "全市场破净率>10%"), ("A区", "官方明确'提振资本市场'表述"),
                       ("C区", "上证逼近或突破历史高点"), ("C区", "新股民开户数同比历史高位"),
                       ("C区", "偏股基金出现日光基"), ("C区", "官方提示金融市场风险"),
                       ("C区", "股债利差<2%")]:
        checks.append({"区": zone, "条件": cond, "程序判定": None, "当前值": "【待人工确认】"})

    manual = read_json(MARKET_STATE_FILE)   # 用户每周日判定后写入
    manual_state = (manual or {}).get("state")
    manual_date = (manual or {}).get("date")
    return {
        "conditions": checks,
        "manual_state": manual_state,
        "manual_state_date": manual_date,
        "effective_state": manual_state,   # 程序绝不自行拍板市场状态
        "note": ("当前生效状态为用户人工判定" if manual_state
                 else "尚无人工判定记录：请按§1.3于每周日核对条件后，将结果写入 data/processed/market_state.json（格式见使用手册）"),
    }


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
    vol = safe_get(snapshot, "hs300_volatility", "近20日年化波动率")
    if vol is not None and vol > 0.35:
        alerts.append({"级别": "P1", "内容":
                       f"沪深300近20日年化波动率 {fmt_pct(vol)} > 35%，触发择时组合严格风控模式"
                       f"（§1.5：仓位上限30%、单票15%、不开新仓）。口径：历史波动率近似，非官方VIX（待确认#2）"})
    elif vol is not None and vol > 0.30:
        alerts.append({"级别": "P2", "内容":
                       f"沪深300近20日年化波动率 {fmt_pct(vol)} 接近35%严格风控触发线，需观察"})
    if state_check.get("effective_state") is None:
        alerts.append({"级别": "P2", "内容":
                       "市场状态无人工判定记录，仓位上限约束无法生效核对——请按§1.3周日流程补判"})
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
        "capital_flow": {"hsgt": snapshot.get("hsgt_flow") or [],
                         "lhb": (snapshot.get("lhb") or [])[:10]},
        "volatility": snapshot.get("hs300_volatility"),
        "cn10y_yield": snapshot.get("cn10y_yield"),
        "errors": snapshot.get("errors") or {},
    }
