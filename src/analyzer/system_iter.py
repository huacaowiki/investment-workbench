# -*- coding: utf-8 -*-
"""
system_iter.py — 投资体系自进化迭代引擎（月度 / 季度）
数据来源：output/daily_market/meta/、output/stock_reports/meta/ 的机器可读摘要，
以及周期内新增的 knowledge/（迭代场景是 drafts 目录唯一被允许读取的场景）。

安全铁则（硬编码，不可绕过）：
  1. 本模块只生成修订草案，绝不写 config/ 目录（模块内无任何指向config的写路径；
     另有 tests/test_system_iter.py 回归验证）；
  2. 每条草案必须含：修改前 / 修改后 / 理由 / 数据支撑 / 风险等级；
  3. 受保护底层原则（risk_control.yaml iteration_constraints.protected_principles）
     一律不生成修改建议；
  4. 遵守§4.3：参数调整需≥6个月数据回顾——样本不足时草案只标"继续观察"，不建议改参数。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.data.data_utils import fmt_pct, safe_get, to_float
from src.generator.archiver import archive_iteration_report
from src.utils.file_utils import DIRS, PROJECT_ROOT, load_config, read_json

RISK_LEVELS = {"低": "仅口径澄清/流程补全，不改变规则实质",
               "中": "参数微调，影响单一环节",
               "高": "影响买卖决策的规则变更（默认冷静一个月，§4.3）"}


# =============================================================================
# 历史报告读取
# =============================================================================

def _load_metas(folder: Path, prefix_period: str) -> list[dict]:
    """读取 meta 目录内属于指定周期（YYYYMM前缀列表）的摘要。"""
    meta_dir = folder / "meta"
    if not meta_dir.exists():
        return []
    out = []
    for p in sorted(meta_dir.glob("*.json")):
        m = read_json(p)
        if m and str(m.get("date", ""))[:6] in prefix_period:
            out.append(m)
    return out


def _period_months(period: str, quarterly: bool = False) -> list[str]:
    """'2026-06' → ['202606']；'2026-Q2' → ['202604','202605','202606']"""
    if quarterly:
        year, q = period.split("-Q")
        start_month = (int(q) - 1) * 3 + 1
        return [f"{year}{m:02d}" for m in range(start_month, start_month + 3)]
    return [period.replace("-", "")]


def _new_knowledge_files(months: list[str]) -> dict:
    """周期内新增（按修改时间）的知识库文件清单。drafts仅此场景可读。"""
    result = {"reference": [], "drafts": []}
    for key, base in [("reference", DIRS["knowledge_reference"]),
                      ("drafts", DIRS["knowledge_drafts"])]:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y%m")
                if mtime in months:
                    result[key].append(str(p.relative_to(PROJECT_ROOT)))
    return result


# =============================================================================
# 统计分析
# =============================================================================

def stat_daily_reports(daily_metas: list[dict]) -> dict:
    """市场日报统计：覆盖、数据质量、情绪观察分与次日走势的回验。"""
    n = len(daily_metas)
    if n == 0:
        return {"覆盖天数": 0, "说明": "本周期无市场日报，无法统计"}
    err_days = sum(1 for m in daily_metas if m.get("data_errors"))
    state_days = sum(1 for m in daily_metas if m.get("effective_state"))

    # 情绪观察分回验：观察分≥5 视为偏强观察，与次日上证涨跌对照（工程化回验，非预测考核）
    hits, total_pairs = 0, 0
    ordered = sorted(daily_metas, key=lambda m: m.get("date", ""))
    for prev, cur in zip(ordered, ordered[1:]):
        s = prev.get("sentiment_score")
        nxt = safe_get(cur, "index", "上证指数", "pct")
        if s is None or nxt is None:
            continue
        total_pairs += 1
        if (s >= 5 and nxt >= 0) or (s < 5 and nxt < 0):
            hits += 1
    # 板块观点胜率：当日领涨板块次日仍在领涨榜的持续率
    persist, persist_total = 0, 0
    for prev, cur in zip(ordered, ordered[1:]):
        tp, tc = prev.get("top_sectors") or [], cur.get("top_sectors") or []
        if tp and tc:
            persist_total += len(tp)
            persist += len(set(tp) & set(tc))
    return {
        "覆盖天数": n,
        "数据缺失天数": err_days,
        "市场状态有人工判定的天数": state_days,
        "情绪观察分次日回验样本": total_pairs,
        "情绪观察分次日方向一致率": (hits / total_pairs) if total_pairs else None,
        "领涨板块次日持续率": (persist / persist_total) if persist_total else None,
        "说明": "一致率/持续率为工程化回验指标（体系本身不做行情预测），仅用于观察报告参考价值",
    }


def stat_stock_reports(stock_metas: list[dict]) -> dict:
    """个股报告统计：结论分布、估值偏差率、止损止盈档位触发核对（需现价）。"""
    n = len(stock_metas)
    if n == 0:
        return {"报告数": 0, "说明": "本周期无个股报告"}
    verdict_dist = {}
    for m in stock_metas:
        key = "排除" if "排除" in (m.get("overall") or "") else (
            "观察/待补核" if "观察" in (m.get("overall") or "") or "补核" in (m.get("overall") or "")
            else "其他")
        verdict_dist[key] = verdict_dist.get(key, 0) + 1

    deviations, triggers = [], []
    for m in sorted(stock_metas, key=lambda x: x.get("date", "")):
        code, close0 = m.get("code"), to_float(m.get("close"))
        if not code or not close0:
            continue
        price_now = _latest_price(code)
        if price_now is None:
            continue
        chg = (price_now - close0) / close0
        deviations.append({"code": code, "name": m.get("name"),
                           "报告日收盘": close0, "当前价": price_now,
                           "区间涨跌": chg, "报告日PE分位": m.get("pe_pct"),
                           "报告日分级": m.get("zone")})
        hit = []
        if chg <= -0.15:
            hit.append("触及-15%清仓线")
        elif chg <= -0.10:
            hit.append("触及-10%减仓线")
        if chg >= 0.40:
            hit.append("触及+40%二档止盈")
        elif chg >= 0.20:
            hit.append("触及+20%一档止盈")
        if hit:
            triggers.append({"code": code, "name": m.get("name"), "触发": hit, "区间涨跌": fmt_pct(chg)})
    return {"报告数": n, "结论分布": verdict_dist,
            "估值偏差样本": deviations, "止损止盈触发": triggers,
            "说明": "估值偏差=报告日至今涨跌幅（假设按报告日价格跟踪）；样本不足6个月时仅观察不建议改参数（§4.3）"}


def _latest_price(code: str) -> float | None:
    """取个股最新价（缓存优先，离线安全：失败返回None）。"""
    try:
        from src.data.stock_data import get_stock_snapshot
        snap = get_stock_snapshot(code)   # 命中当日缓存则无网络请求
        return safe_get(snap, "quote", "最新收盘价")
    except Exception:
        return None


# =============================================================================
# 修订草案生成
# =============================================================================

def build_draft_revisions(config: dict, daily_stats: dict, stock_stats: dict,
                          months: list[str]) -> list[dict]:
    """
    基于统计证据生成修订草案。原则：
    - 无数据支撑不出草案；样本<6个月的参数问题只出"继续观察"级草案；
    - 待确认项(pending_confirmation)始终列入草案清单请用户裁决（这是口径确认，非规则修改）；
    - 受保护原则绝不建议修改。
    """
    protected = safe_get(config, "risk_control", "iteration_constraints",
                         "protected_principles") or []
    drafts = []

    # 1) 待确认项裁决请求（若配置仍有未裁决的 pending_confirmation 则列入；
    #    v4.2.0 起12条已全部裁决为 confirmed_decisions，此循环通常为空——保留以兼容未来新增模糊点）
    for fname in ["investment_system", "stock_selection", "valuation_model", "risk_control"]:
        for item in (config.get(fname, {}).get("pending_confirmation") or []):
            drafts.append({
                "类型": "待确认口径裁决",
                "位置": f"config/{fname}.yaml pending_confirmation[{item.get('id')}]",
                "修改前": f"临时口径：{item.get('interim_rule')}",
                "修改后": "（请用户裁决后固化：确认临时口径 / 给出正式口径）",
                "理由": f"原文模糊点：{item.get('item')}",
                "数据支撑": f"原文出处：{item.get('original_text')}",
                "风险等级": "低",
            })

    # 2) 数据驱动观察项
    if daily_stats.get("覆盖天数", 0) > 0 and daily_stats.get("市场状态有人工判定的天数", 0) == 0:
        drafts.append({
            "类型": "流程执行缺口",
            "位置": "§1.3 市场状态周判流程（非规则修改）",
            "修改前": "每周日晚更新市场状态，记录在案",
            "修改后": "建议：把 python run.py set-state 纳入每周日流程清单（规则本身不变）",
            "理由": "周期内所有日报均无人工市场状态记录，§1.4仓位映射与§2.4'非C区'门槛无法生效核对",
            "数据支撑": f"覆盖 {daily_stats['覆盖天数']} 天日报，人工判定天数 0",
            "风险等级": "低",
        })
    err_days = daily_stats.get("数据缺失天数", 0)
    cover = daily_stats.get("覆盖天数", 0)
    if cover and err_days / cover > 0.3:
        drafts.append({
            "类型": "数据口径观察",
            "位置": "数据层（不涉及config规则）",
            "修改前": "当前主源东财、备源新浪/巨潮/百度",
            "修改后": "继续观察；若缺失率持续>30%考虑调整主备源顺序",
            "理由": "数据缺失天数占比过高会影响门槛判定完整性",
            "数据支撑": f"{err_days}/{cover} 天存在数据缺失",
            "风险等级": "低",
        })
    if len(months) < 6:
        drafts.append({
            "类型": "参数调整冻结提示",
            "位置": "全部量化参数",
            "修改前": "（各现行参数不变）",
            "修改后": "本周期不建议任何参数调整",
            "理由": "§4.3：参数调整基于≥6个月数据回顾，不因单次交易/短期波动修改",
            "数据支撑": f"本次迭代覆盖 {len(months)} 个月 < 6个月",
            "风险等级": "低",
        })

    # 3) 受保护原则声明（永不建议修改）
    drafts.append({
        "类型": "受保护原则（不生成修改建议）",
        "位置": "risk_control.yaml iteration_constraints.protected_principles",
        "修改前": "；".join(protected),
        "修改后": "（本引擎设计上不对以上原则生成修改建议）",
        "理由": "核心理念与风险偏好类底层原则默认不主动建议修改（阶段四安全机制）",
        "数据支撑": "—",
        "风险等级": "—",
    })
    return drafts


# =============================================================================
# 报告渲染
# =============================================================================

def _render_report(title: str, period: str, months: list[str], daily_stats: dict,
                   stock_stats: dict, knowledge: dict, drafts: list[dict],
                   extra_sections: str = "") -> str:
    dev_rows = "\n".join(
        f"| {d['code']} {d['name'] or ''} | {d['报告日收盘']} | {d['当前价']} | "
        f"{fmt_pct(d['区间涨跌'], signed=True)} | {fmt_pct(d['报告日PE分位'])} | {d['报告日分级']} |"
        for d in stock_stats.get("估值偏差样本", [])) or "| —（样本不足） | | | | | |"
    trig_rows = "\n".join(f"- {t['code']} {t['name']}：{'；'.join(t['触发'])}（区间 {t['区间涨跌']}）"
                          for t in stock_stats.get("止损止盈触发", [])) or "- 无触发记录"
    draft_md = ""
    for i, d in enumerate(drafts, 1):
        draft_md += (f"\n### 草案 {i}（{d['类型']}｜风险等级：{d['风险等级']}）\n\n"
                     f"- **位置**：{d['位置']}\n- **修改前**：{d['修改前']}\n"
                     f"- **修改后**：{d['修改后']}\n- **理由**：{d['理由']}\n"
                     f"- **数据支撑**：{d['数据支撑']}\n")
    kn_ref = "\n".join(f"- {p}" for p in knowledge["reference"]) or "- 无新增"
    kn_drafts = "\n".join(f"- {p}" for p in knowledge["drafts"]) or "- 无新增"

    return f"""# {title} · {period}

> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 覆盖月份：{', '.join(months)}
> ⚠️ **本报告所有修订均为草案，不会也不能自动修改 config/。**
> 生效流程：用户逐条确认 → `python run.py backup-config` 备份 → 手动修改YAML → 记入《体系演化日志》

---

## 一、市场日报统计

| 指标 | 数值 |
|---|---|
| 覆盖天数 | {daily_stats.get('覆盖天数')} |
| 数据缺失天数 | {daily_stats.get('数据缺失天数', '—')} |
| 市场状态有人工判定的天数 | {daily_stats.get('市场状态有人工判定的天数', '—')} |
| 情绪观察分次日方向一致率 | {fmt_pct(daily_stats.get('情绪观察分次日方向一致率'))}（样本 {daily_stats.get('情绪观察分次日回验样本', 0)}） |
| 领涨板块次日持续率 | {fmt_pct(daily_stats.get('领涨板块次日持续率'))} |

> {daily_stats.get('说明', '')}

## 二、个股报告统计

- 报告数：{stock_stats.get('报告数')}
- 结论分布：{stock_stats.get('结论分布', '—')}

**估值偏差样本（报告日 → 当前）**

| 标的 | 报告日收盘 | 当前价 | 区间涨跌 | 报告日PE分位 | 报告日分级 |
|---|---|---|---|---|---|
{dev_rows}

**止损止盈档位触发核对**（按§3.2档位对照区间涨跌，非实盘记录）

{trig_rows}

> {stock_stats.get('说明', '')}

## 三、周期内新增知识库资料

**参考层（knowledge/reference）**
{kn_ref}

**草稿层（knowledge/drafts，仅迭代场景读取）**
{kn_drafts}

## 四、逻辑偏差与规则盲区识别

- 待人工核验项高频出现（审计意见/派现比例/收现比等接口不可得字段）——属数据可得性盲区，
  已在草案中列为口径裁决项，不构成规则缺陷
- 本周期样本量见上表；样本不足的结论一律标注"继续观察"，不做归因
{extra_sections}

## 五、配置修订草案（共 {len(drafts)} 条，全部需人工确认）

{draft_md}

---

## 迭代安全声明

1. 本引擎无写入 config/ 的代码路径，所有草案仅落盘于 output/iteration/；
2. 受保护底层原则不生成修改建议；
3. 依据§4.3：同一参数一年内最多修改2次；重大迭代冷静一个月后执行；
4. 确认草案后的操作步骤见《使用手册.md》"体系迭代操作流程"。

**免责声明**：本报告仅为体系复盘与研究支撑，不构成任何投资建议。
"""


# =============================================================================
# 对外入口
# =============================================================================

def run_monthly_iteration(period: str | None = None) -> Path:
    """月度迭代：period 形如 '2026-06'，默认上一个自然月。"""
    if not period:
        first = datetime.now().replace(day=1)
        period = (first - timedelta(days=1)).strftime("%Y-%m")
    months = _period_months(period)
    config = load_config()
    daily = _load_metas(DIRS["output_daily"], months)
    stocks = _load_metas(DIRS["output_stock"], months)
    daily_stats = stat_daily_reports(daily)
    stock_stats = stat_stock_reports(stocks)
    knowledge = _new_knowledge_files(months)
    drafts = build_draft_revisions(config, daily_stats, stock_stats, months)
    md = _render_report("月度体系迭代报告", period, months, daily_stats, stock_stats,
                        knowledge, drafts)
    meta = {"type": "iteration_monthly", "period": period,
            "date": months[0] + "01", "draft_count": len(drafts),
            "daily_covered": daily_stats.get("覆盖天数"), "stock_covered": stock_stats.get("报告数")}
    return archive_iteration_report(md, meta, kind="monthly")


def run_quarterly_iteration(period: str | None = None) -> Path:
    """季度深度迭代：period 形如 '2026-Q2'，默认上一个自然季度。"""
    if not period:
        now = datetime.now()
        q = (now.month - 1) // 3   # 当前季度序号-1 即上季度
        year = now.year if q > 0 else now.year - 1
        q = q if q > 0 else 4
        period = f"{year}-Q{q}"
    months = _period_months(period, quarterly=True)
    config = load_config()
    daily = _load_metas(DIRS["output_daily"], months)
    stocks = _load_metas(DIRS["output_stock"], months)
    daily_stats = stat_daily_reports(daily)
    stock_stats = stat_stock_reports(stocks)
    knowledge = _new_knowledge_files(months)
    drafts = build_draft_revisions(config, daily_stats, stock_stats, months)

    extra = """
### 季度深度专项

**全周期收益匹配度分析**：两组合实际收益/回撤数据在券商账户，本系统不接入实盘账户——
请按《使用手册》模板人工填入季度收益率与最大回撤后，对照目标（股息8-12%/回撤≤15%；
择时年化20%/回撤≤20%）评估匹配度。

**选股模型有效性验证**：见第二节结论分布与估值偏差样本；有效性结论需≥6个月样本（§4.3）。

**估值模型偏差复盘**：对比报告日PE分位与区间涨跌（第二节表），观察低分位标的是否体现
安全边际；样本不足时不下结论。
"""
    md = _render_report("季度体系深度复盘报告", period, months, daily_stats, stock_stats,
                        knowledge, drafts, extra_sections=extra)
    meta = {"type": "iteration_quarterly", "period": period,
            "date": months[0] + "01", "draft_count": len(drafts)}
    return archive_iteration_report(md, meta, kind="quarterly")
