# -*- coding: utf-8 -*-
"""
report_writer.py — 报告生成器
读取 templates/ 模板，把分析引擎的结构化结果渲染为 Markdown 报告。
同时输出机器可读的 meta JSON 摘要（供 system_iter 迭代模块统计使用）。
模板占位符使用 {name} 语法；缺失占位符渲染为提示文本而非报错。
"""
from __future__ import annotations

from datetime import datetime

from src.data.data_utils import fmt_pct, fmt_yi, safe_get
from src.utils.file_utils import DIRS, load_config, read_text

STATE_NAMES = {"A_undervalued": "A区·低估", "B_low": "B偏低", "B_neutral": "B中性",
               "B_high": "B偏高", "C_overvalued": "C区·高估"}
STATUS_ICON = {"PASS": "✅ 通过", "FAIL": "❌ 不满足", "MISSING": "⚠️ 数据缺失"}


class _SafeDict(dict):
    """format_map 安全字典：缺失键渲染为占位提示，保证渲染永不报错。"""
    def __missing__(self, key):
        return f"【模板占位符 {key} 无对应内容】"


def _table(headers: list[str], rows: list[list]) -> str:
    """生成Markdown表格。"""
    if not rows:
        return "_（无数据）_"
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join("—" if v is None else str(v) for v in r) + " |")
    return "\n".join(lines)


def _checks_table(checks: list[dict]) -> str:
    """判定项列表 → 表格。"""
    return _table(["检查项", "判定", "详情", "规则来源"],
                  [[c["item"], STATUS_ICON.get(c["status"], c["status"]),
                    c["detail"], c.get("source", "")] for c in checks])


def _config_version() -> str:
    try:
        return safe_get(load_config("investment_system"), "meta", "version") or "?"
    except Exception:
        return "?"


# =============================================================================
# 市场日报
# =============================================================================

def render_daily_report(analysis: dict) -> tuple[str, dict]:
    """渲染市场日报。返回 (markdown文本, meta摘要dict)。"""
    tpl = read_text(DIRS["templates"] / "daily_market_report.md")

    idx_rows = [[r["名称"], r["收盘"],
                 fmt_pct((r["涨跌幅_pct"] or 0) / 100, signed=True) if r["涨跌幅_pct"] is not None else "—",
                 fmt_yi(r["成交额"])] for r in analysis["index_summary"]]
    total = analysis.get("total_turnover")
    sent = analysis["sentiment"]
    sector = analysis["sector"]

    sector_rows = lambda items: [[b.get("板块名称"), fmt_pct((b.get("涨跌幅") or 0) / 100, signed=True),
                                  b.get("领涨股票") or b.get("公司家数")] for b in items]
    state = analysis["state_check"]
    cond_rows = [[c["区"], c["条件"],
                  ("✅ 是" if c["程序判定"] else "❌ 否") if c["程序判定"] is not None else "📝 待人工",
                  c["当前值"]] for c in state["conditions"]]

    pos = analysis["position"]
    pos_rows = []
    for k, v in (pos.get("全表") or {}).items():
        if not isinstance(v, dict):
            continue
        mark = " ◀ 当前" if k == pos.get("当前状态") else ""
        pos_rows.append([STATE_NAMES.get(k, k) + mark,
                         fmt_pct(v.get("dividend_cap")) if v.get("dividend_cap") is not None else v.get("dividend_note", "—"),
                         (fmt_pct(v.get("timing_cap")) if v.get("timing_cap") is not None else "—") +
                         (f"（{v['timing_note']}）" if v.get("timing_note") else "")])

    alerts_md = "\n".join(f"- **[{a['级别']}]** {a['内容']}" for a in analysis["alerts"])
    margin = analysis["capital_flow"]["margin"] or {}
    margin_lines = [
        f"- 沪市融资余额（最新）：{fmt_yi(margin.get('沪市融资余额_最新'))}，"
        f"较10个交易日前 {fmt_pct(margin.get('沪市10日变化率'), signed=True)}",
        f"- 深市融资余额（最新）：{fmt_yi(margin.get('深市融资余额_最新'))}",
        f"- 两市融资余额合计：{fmt_yi(margin.get('两市融资余额_最新'))}",
        f"> {margin.get('口径', '两融数据缺失')}",
    ]
    lhb_rows = [[r.get("代码"), r.get("名称"),
                 fmt_pct((r.get("涨跌幅") or 0) / 100) if r.get("涨跌幅") is not None else "—",
                 fmt_yi(r.get("龙虎榜净买额")), (r.get("上榜原因") or "")[:20]]
                for r in analysis["capital_flow"]["lhb"]]

    vol = safe_get(analysis, "volatility", "数值")
    vol_name = safe_get(analysis, "volatility", "指标") or "波动率"
    vol_note = safe_get(analysis, "volatility", "口径") or ""
    cs_pe = safe_get(analysis, "csindex_pe", "市盈率1")
    quality_lines = [f"- 10年期国债收益率：{fmt_pct(analysis.get('cn10y_yield'))}（股息率门槛锚定值）",
                     f"- 波动率：{vol_name} = {fmt_pct(vol)}（{vol_note}；严格风控触发线35%）",
                     f"- 中证全指PE1：{cs_pe}（'全A PE<22'与股债利差的替代口径，{safe_get(analysis, 'csindex_pe', '日期')}）",
                     f"- 板块数据源：{sector.get('source_note')}"]
    for k, v in (analysis.get("errors") or {}).items():
        quality_lines.append(f"- ⚠️ `{k}` 拉取失败：{v}（该项分析按缺失处理）")

    filled = tpl.format_map(_SafeDict(
        report_date=analysis["date"],
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        config_version=_config_version(),
        index_table=_table(["指数", "收盘", "涨跌幅", "成交额"], idx_rows),
        turnover_summary=f"两市成交额合计约 **{fmt_yi(total)}**（上证+深成口径）" if total else "_成交额数据缺失_",
        sentiment_section=(
            f"- 上涨 {sent.get('上涨家数') or '—'} 家 / 下跌 {sent.get('下跌家数') or '—'} 家"
            f"（上涨占比 {fmt_pct(sent.get('上涨占比'))}）\n"
            f"- 涨停 {sent.get('涨停家数') if sent.get('涨停家数') is not None else '—'} 家 / "
            f"跌停 {sent.get('跌停家数') if sent.get('跌停家数') is not None else '—'} 家\n"
            f"- 情绪观察分：**{sent.get('情绪观察分_0到10') if sent.get('情绪观察分_0到10') is not None else '—'} / 10**\n"
            f"> {sent['说明']}"),
        sector_top_table=_table(["板块", "涨跌幅", "领涨股/家数"], sector_rows(sector["top"])),
        sector_bottom_table=_table(["板块", "涨跌幅", "领涨股/家数"], sector_rows(sector["bottom"])),
        sector_conclusion="",
        capital_flow_section=("**两融资金（融资余额）**\n\n" + "\n".join(margin_lines) +
                              "\n\n**龙虎榜净买入前列**\n\n" +
                              _table(["代码", "名称", "涨跌幅", "净买额", "上榜原因"], lhb_rows)),
        market_state_section=(
            _table(["区", "条件", "程序判定", "当前值"], cond_rows) +
            f"\n\n**程序初判**：{STATE_NAMES.get(state.get('auto_state'), '无法判定')}"
            f"（A区命中 {state.get('a_hits')} 条 / C区命中 {state.get('c_hits')} 条 / "
            f"股债利差 {fmt_pct(state.get('equity_bond_spread'))}）"
            f"\n\n**当前生效状态**：{STATE_NAMES.get(state.get('effective_state'), '未判定')}"
            f"\n\n> {state['note']}"),
        position_constraint_section=_table(["市场状态", "股息组合仓位上限", "择时组合仓位上限"], pos_rows) +
            "\n\n> 严格风控模式（§1.5）触发条件：年度盈利>15% / 进入C区 / 沪深300波动率>35% → 择时仓位上限30%、单票15%、不开新仓",
        risk_alerts_section=alerts_md,
        data_quality_section="\n".join(quality_lines),
    ))

    meta = {
        "type": "daily_market", "date": analysis["date"],
        "generated_at": datetime.now().isoformat(),
        "index": {r["名称"]: {"close": r["收盘"], "pct": r["涨跌幅_pct"]} for r in analysis["index_summary"]},
        "total_turnover": total,
        "sentiment_score": sent.get("情绪观察分_0到10"),
        "top_sectors": [b.get("板块名称") for b in sector["top"]],
        "bottom_sectors": [b.get("板块名称") for b in sector["bottom"]],
        "effective_state": state.get("effective_state"),
        "auto_state": state.get("auto_state"),
        "equity_bond_spread": state.get("equity_bond_spread"),
        "volatility": {"指标": vol_name, "数值": vol},
        "alerts": analysis["alerts"],
        "data_errors": list((analysis.get("errors") or {}).keys()),
    }
    return filled, meta


# =============================================================================
# 个股报告
# =============================================================================

def render_stock_report(snap: dict, result: dict) -> tuple[str, dict]:
    """渲染个股分析报告。返回 (markdown文本, meta摘要dict)。"""
    tpl = read_text(DIRS["templates"] / "stock_analysis_report.md")
    basic = snap.get("basic") or {}
    quote = snap.get("quote") or {}
    val = snap.get("valuation") or {}
    fin = snap.get("financials") or {}
    div = snap.get("dividends") or {}

    basic_rows = [
        ["名称/代码", f"{basic.get('名称') or '—'}（{result['code']}）"],
        ["行业", f"{basic.get('行业') or '—'}（{basic.get('行业口径', '')}）"],
        ["总市值", fmt_yi(basic.get("总市值"))],
        ["最新收盘价", quote.get("最新收盘价")],
        ["较1年内高点回撤", fmt_pct(quote.get("较1年内高点回撤"))],
        ["PE(TTM) / 历史分位", f"{val.get('PE_TTM') or '—'} / {fmt_pct(val.get('PE历史分位'))}（{val.get('分位窗口', '')}）"],
        ["PB / 历史分位", f"{val.get('PB') or '—'} / {fmt_pct(val.get('PB历史分位'))}"],
        ["股息率TTM", fmt_pct(val.get("股息率TTM"))],
        ["ROE近3年均值", f"{fin.get('ROE近3年均值_pct'):.2f}%" if fin.get("ROE近3年均值_pct") is not None else "—"],
        ["连续分红年数", div.get("连续分红年数")],
        ["派现比例近3年均值", fmt_pct(safe_get(snap, "payout", "近3年均值"))],
        ["收现比 / 净现比（最新年度）",
         f"{fmt_pct(safe_get(snap, 'cashflow_ratios', '收现比_最新'))} / {fmt_pct(safe_get(snap, 'cashflow_ratios', '净现比_最新'))}"],
        ["大股东质押率", fmt_pct(snap.get("pledge_ratio"))],
        ["10年国债收益率", fmt_pct(snap.get("cn10y_yield"))],
    ]

    d = result["dividend"]
    t = result["timing"]
    dividend_md = ("**通用门槛（§2.2，全部满足才进候选池）**\n\n" + _checks_table(d["universal"]) +
                   "\n\n**分类财务门槛**（" + (d["classify"].get("note") or "") + "）\n\n" +
                   _checks_table(d["category"]) +
                   "\n\n**估值买入标准（三选一）**\n\n" + _checks_table(d["valuation_buy"]["items"]) +
                   f"\n\n> {d['valuation_buy']['conclusion']}\n\n> 加分条件：{d['bonus_note']}" +
                   f"\n\n**股息组合结论**：{d['verdict']}")

    scoring_rows = [[s["项"], s["得分"], s["口径"]] for s in t["scoring"]]
    timing_md = ("**备选池入池标准（§2.3）**\n\n" + _checks_table(t["pool"]) +
                 "\n\n**买入门槛条件（§2.4，全部满足）**\n\n" + _checks_table(t["gates"]) +
                 "\n\n**评分表（v4.2.0全自动口径，每项1分）**\n\n" + _table(["评分项", "得分", "口径说明"], scoring_rows) +
                 f"\n\n自动评分：**{t['auto_score_floor']} 分**（自动口径满分4分；政策催化/预期差不参与自动评分，映射阈值不变=偏保守）" +
                 f"\n\n> {t['cooling_note']}\n\n**择时组合结论**：{t['verdict']}")

    risk = result["risk"]
    risk_lines = [f"- 能力圈：{risk['能力圈判定']}"]
    if risk.get("股息组合单票上限") is not None:
        risk_lines.append(f"- 单票仓位上限：股息组合 {fmt_pct(risk['股息组合单票上限'])} ｜ 择时组合 {fmt_pct(risk['择时组合单票上限'])}")
    risk_lines.append(f"- 建仓节奏：{risk['建仓批次']}")
    for key in ("止损参考价", "止盈参考价"):
        if key in risk:
            entries = "；".join(f"{k} → {v}" for k, v in risk[key].items() if k != "说明")
            risk_lines.append(f"- {key}：{entries}（{risk[key]['说明']}）")
    if "时间止损" in risk:
        risk_lines.append(f"- 时间止损：{risk['时间止损']}")

    ann = snap.get("announcements") or []
    logic_lines = ["**数据要点（供决策卡片'买入逻辑'与'反向论证'参考，非结论）**",
                   f"- 盈利能力：ROE近3年均值 {fin.get('ROE近3年均值_pct'):.2f}%，净利润最新同比 {fmt_pct(fin.get('净利润最新同比'))}"
                   if fin.get("ROE近3年均值_pct") is not None else
                   f"- 盈利能力：ROE数据缺失，净利润最新同比 {fmt_pct(fin.get('净利润最新同比'))}",
                   f"- 估值位置：PE分位 {fmt_pct(val.get('PE历史分位'))}，价格区间判定 → {result['zone']['zone']}",
                   f"- 分红记录：连续 {div.get('连续分红年数')} 年",
                   "- 最新公告（近30天）：" + ("；".join(a.get("公告标题", "")[:30] for a in ann[:5]) if ann else "无/缺失"),
                   "",
                   "> 依据§4.4外部观点加工协议：knowledge/reference 中的第三方研报观点仅可作背景论据，任何外部观点必须经加工后才能进入决策流程，不允许'刚看完就买'。"]

    stall = result["zone"].get("volume_stall") or {}
    manual_md = (_checks_table(result["assumptions"]) if result["assumptions"] else "_无_") + \
        "\n\n> 全部判定为程序自动执行（v4.2.0）；上表列出其中依赖'推定口径'或存在数据缺失的项，供复核数据源。"

    filled = tpl.format_map(_SafeDict(
        stock_name=result.get("name") or result["code"],
        stock_code=result["code"],
        report_date=datetime.now().strftime("%Y-%m-%d"),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        config_version=_config_version(),
        basic_section=_table(["项目", "数值"], basic_rows),
        veto_section=_checks_table(result["veto"]),
        dividend_track_section=dividend_md,
        timing_track_section=timing_md,
        valuation_section=(f"**价格区间分级**：{result['zone']['zone']}\n\n> {result['zone']['note']}\n\n" +
                           f"放量滞涨卖出信号（裁决#12）：{'⚠️ 触发' if stall.get('signal') else '未触发'}"
                           f"（{stall.get('口径', '样本不足')}）\n\n" +
                           f"行业均值PE：{safe_get(snap, 'peers', '行业均值PE') or '—'}"
                           f"（{safe_get(snap, 'peers', '行业均值PE口径') or '行业对比数据缺失'}）"),
        risk_params_section="\n".join(risk_lines),
        logic_section="\n".join(logic_lines),
        conclusion_section=f"**综合结论**：{result['overall']}\n\n市场状态背景：{STATE_NAMES.get(result.get('market_state'), '未判定（运行 python run.py judge-state）')}",
        manual_check_section=manual_md,
    ))

    meta = {
        "type": "stock_report", "code": result["code"], "name": result.get("name"),
        "date": datetime.now().strftime("%Y%m%d"),
        "generated_at": datetime.now().isoformat(),
        "close": quote.get("最新收盘价"),
        "pe_ttm": val.get("PE_TTM"), "pe_pct": val.get("PE历史分位"),
        "pb": val.get("PB"), "dividend_yield": val.get("股息率TTM"),
        "zone": result["zone"]["zone"],
        "overall": result["overall"],
        "timing_auto_score": t["auto_score_floor"],
        "assumption_count": len(result["assumptions"]),
        "data_errors": list((snap.get("errors") or {}).keys()),
    }
    return filled, meta
