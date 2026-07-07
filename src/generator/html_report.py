# -*- coding: utf-8 -*-
"""
html_report.py — 看板式HTML报告渲染器（v4.5.0 报告体系重构）
组件化直渲染：分析结果dict → Claude桌面端风格卡片看板（不经过Markdown转换通道）。
视觉规范：背景#F7F7F8/卡片#FFFFFF/主色#4F46E5/正文#1F2937/次文#6B7280/12px圆角；
涨跌配色按A股习惯：上涨/多头=红#DC2626，下跌/空头=绿#059669（用户2026-07-07指定）。
CSS全内嵌零外部依赖；中文字体栈 Microsoft YaHei→SimHei fallback；打印分页防断裂。
"""
from __future__ import annotations

import html as _html
from datetime import datetime

from src.data.data_utils import fmt_num, fmt_pct, fmt_yi, safe_get, to_float
from src.utils.file_utils import load_config

UP, DOWN = "#DC2626", "#059669"   # A股：红涨绿跌

CSS = """
:root { --bg:#F7F7F8; --card:#FFFFFF; --primary:#4F46E5; --text:#1F2937;
  --muted:#6B7280; --up:#DC2626; --down:#059669; --warn-bg:#FFF7ED;
  --warn-fg:#C2410C; --border:#E5E7EB; --soft:#F3F4F6; }
*{box-sizing:border-box} body{margin:0;padding:28px 14px;background:var(--bg);
  color:var(--text);font-family:-apple-system,"Segoe UI","Microsoft YaHei","SimHei",
  "PingFang SC",sans-serif;font-size:14.5px;line-height:1.6}
.wrap{max-width:960px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;
  box-shadow:0 1px 3px rgba(0,0,0,.04),0 4px 14px rgba(0,0,0,.03);
  padding:22px 26px;margin-bottom:16px;page-break-inside:avoid}
.card h2{font-size:17px;margin:0 0 14px;padding-bottom:10px;
  border-bottom:1px solid var(--border);letter-spacing:-.01em}
.card h3{font-size:14.5px;margin:16px 0 8px}
h1{font-size:24px;margin:0 0 6px;letter-spacing:-.01em}
.rule-bar{border-left:3px solid var(--primary);background:var(--soft);
  border-radius:0 10px 10px 0;padding:8px 14px;font-size:12.5px;color:var(--muted);margin:10px 0}
.pill{display:inline-block;border-radius:999px;padding:2px 12px;font-size:12px;
  font-weight:600;margin-right:6px;white-space:nowrap}
.pill-up{background:#FEF2F2;color:var(--up)} .pill-down{background:#ECFDF5;color:var(--down)}
.pill-amber{background:#FFFBEB;color:#B45309} .pill-gray{background:var(--soft);color:var(--muted)}
.pill-primary{background:#EEF2FF;color:var(--primary)}
.up{color:var(--up);font-weight:600} .down{color:var(--down);font-weight:600}
.muted{color:var(--muted)} .small{font-size:12px}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px;page-break-inside:avoid}
th{text-align:left;font-weight:600;color:var(--muted);font-size:12px;
  padding:7px 9px;border-bottom:1px solid var(--border)}
td{padding:7px 9px;border-bottom:1px solid #F0F0F2;vertical-align:top}
tr:hover td{background:#FAFAFB} tr:last-child td{border-bottom:none}
.grid{display:grid;gap:10px} .g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)} .g2{grid-template-columns:1fr 1fr}
.kv{background:var(--soft);border-radius:10px;padding:10px 12px}
.kv .k{font-size:11.5px;color:var(--muted)} .kv .v{font-size:16px;font-weight:650;margin-top:2px}
.hero{display:grid;grid-template-columns:180px 1fr;gap:16px;align-items:stretch}
.hero-rating{border-radius:12px;display:flex;flex-direction:column;align-items:center;
  justify-content:center;padding:18px;color:#fff}
.hero-rating .r{font-size:26px;font-weight:700} .hero-rating .s{font-size:12px;opacity:.9;margin-top:4px}
.bar-row{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12.5px}
.bar-label{width:110px;text-align:right;color:var(--muted);flex-shrink:0}
.bar-track{flex:1;background:var(--soft);border-radius:6px;height:16px;position:relative;overflow:hidden}
.bar-fill{height:100%;border-radius:6px}
.bar-val{width:90px;flex-shrink:0}
.div-row{display:grid;grid-template-columns:1fr 130px 1fr;align-items:center;gap:8px;
  margin:4px 0;font-size:12.5px}
.div-left,.div-right{height:14px;position:relative}
.div-left .bar-fill{position:absolute;right:0;background:var(--down);border-radius:6px 0 0 6px}
.div-right .bar-fill{position:absolute;left:0;background:var(--up);border-radius:0 6px 6px 0}
.gauge{position:relative;background:linear-gradient(90deg,#FCA5A5 0%,#FEF3C7 45%,#D1FAE5 100%);
  height:14px;border-radius:7px;margin:26px 6px 30px}
.gauge .tick{position:absolute;top:-22px;font-size:11px;color:var(--muted);transform:translateX(-50%)}
.gauge .tickb{position:absolute;top:18px;font-size:11px;color:var(--muted);transform:translateX(-50%)}
.gauge .cur{position:absolute;top:-6px;width:4px;height:26px;background:var(--text);
  border-radius:2px;transform:translateX(-50%)}
.risk-hi{background:#FEF2F2;border-left:3px solid var(--up)}
.risk-mid{background:#FFFBEB;border-left:3px solid #B45309}
.risk-low{background:var(--soft);border-left:3px solid var(--muted)}
.risk-item{border-radius:0 10px 10px 0;padding:9px 13px;margin:7px 0;font-size:13px}
.scen{border-radius:12px;padding:14px;border:1px solid var(--border)}
.scen h4{margin:0 0 6px;font-size:14px} .scen .rng{font-size:18px;font-weight:700;margin:4px 0}
.scen-opt{background:#FEF2F2} .scen-mid{background:var(--soft)} .scen-pes{background:#ECFDF5}
.timeline{list-style:none;padding-left:0;margin:8px 0}
.timeline li{position:relative;padding:4px 0 10px 22px;border-left:2px solid var(--border);margin-left:8px}
.timeline li::before{content:"";position:absolute;left:-6px;top:9px;width:10px;height:10px;
  border-radius:50%;background:var(--primary)}
.callout{background:var(--warn-bg);color:var(--warn-fg);border-radius:10px;
  padding:9px 14px;font-size:12.5px;margin:10px 0}
.footer{color:var(--muted);font-size:11.5px;line-height:1.7;padding:14px 6px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.side{background:var(--soft);border-radius:10px;padding:12px 14px;font-size:13px}
.side h4{margin:0 0 8px;font-size:13.5px}
.side ul{margin:0;padding-left:18px} .side li{margin:4px 0}
@media print { body{background:#fff;padding:0 0 34px} .card{box-shadow:none}
  tr:hover td{background:transparent}
  .pdf-footer{display:block;position:fixed;bottom:0;left:0;right:0;text-align:center;
    font-size:10px;color:var(--muted);border-top:1px solid var(--border);
    padding:5px 0 2px;background:#fff} }
.pdf-footer{display:none}
@media (max-width:640px){.g4,.g3{grid-template-columns:1fr 1fr}.two-col,.hero{grid-template-columns:1fr}}
@page{margin:14mm 11mm 18mm 11mm}
"""

FOOTNOTE = """
<div class="card footer">
<b>指标口径</b>：PE/PB历史分位=近10年三级数据源链（乐咕→自算→百度，窗口随值标注）；派现比例=每股现金分红(税前)/当年EPS；
收现比/净现比=新浪年报现金流口径；技术指标（MA/MACD/KDJ/BOLL）为标准参数量化计算；波动率=300ETF期权QVIX（备源HV20）；
股债利差=1/中证全指PE−10年国债；两融=交易所官方数据（北向数据已停发）。审计/监管/诉讼为公告标题关键词扫描推定口径。<br>
<b>数据来源</b>：akshare聚合的公开接口（东方财富/新浪财经/巨潮资讯/中证指数/同花顺/交易所官网），可能存在延迟或误差，关键决策前请复核原始公告。<br>
<b>免责声明</b>：本报告由个人投资研究工作台自动生成，全部评级、仓位、估值结论均为 config/ 铁则层规则的程序化执行结果，
仅为个人研究记录与策略框架支撑，不构成任何证券投资建议或买卖推荐。市场有风险，决策需独立判断并自担后果。
</div>"""


def esc(v) -> str:
    return _html.escape(str(v if v is not None else "暂无数据"))


def _versions() -> str:
    try:
        cfg = load_config()
        main_v = safe_get(cfg, "investment_system", "meta", "version") or "?"
        parts = [f"{k.split('_')[0]} v{safe_get(cfg, k, 'meta', 'version')}"
                 for k in ("stock_selection", "valuation_model", "risk_control")]
        return f"v{main_v}（{'；'.join(parts)}）"
    except Exception:
        return "读取失败"


def pct_cell(v, digits=2) -> str:
    """涨跌幅单元格：红涨绿跌。输入为小数（0.05=5%）。"""
    f = to_float(v)
    if f is None:
        return '<span class="muted">暂无数据</span>'
    cls = "up" if f > 0 else ("down" if f < 0 else "muted")
    return f'<span class="{cls}">{f * 100:+.{digits}f}%</span>'


def num_cell(v, digits=2) -> str:
    f = to_float(v)
    return f"{f:.{digits}f}" if f is not None else '<span class="muted">暂无数据</span>'


def table(headers: list, rows: list) -> str:
    if not rows:
        return '<p class="muted small">暂无数据</p>'
    h = "".join(f"<th>{c}</th>" for c in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


def card(title: str, body: str, extra: str = "") -> str:
    return f'<div class="card">{f"<h2>{title}</h2>" if title else ""}{extra}{body}</div>'


def kv(k: str, v: str, sub: str = "") -> str:
    return (f'<div class="kv"><div class="k">{k}</div><div class="v">{v}</div>'
            f'{f"<div class=\'k\'>{sub}</div>" if sub else ""}</div>')


def status_pill(status: str) -> str:
    m = {"PASS": ("通过", "pill-down"), "FAIL": ("不通过", "pill-up"),
         "MISSING": ("数据缺失", "pill-amber")}
    t, c = m.get(status, (status, "pill-gray"))
    # 注：通过=绿系胶囊（合规语义），不通过=红警示——与涨跌色语义区分开
    return f'<span class="pill {c}">{t}</span>'


def _doc(title: str, body: str) -> str:
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>{CSS}</style></head><body><div class="wrap">{body}{FOOTNOTE}</div>
<div class="pdf-footer">{title} ｜ 生成日期 {gen} ｜ 投资研究工作台 · 仅研究分析不构成投资建议</div>
</body></html>"""


# =============================================================================
# 个股深度分析看板（11模块）
# =============================================================================

def render_stock_dashboard(snap: dict, result: dict,
                           self_check: list[dict] | None = None) -> str:
    name = result.get("name") or result.get("code")
    code = result.get("code")
    rating = result.get("rating") or {}
    quote = snap.get("quote") or {}
    val = snap.get("valuation") or {}
    fin = snap.get("financials") or {}
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = []

    # ---- 模块1 头部综合裁决区 ----
    r = rating.get("评级", "观望")
    r_color = {"买入": UP, "逢低建仓": "#B45309", "观望": "#6B7280", "排除": "#374151"}.get(r, "#6B7280")
    score = rating.get("综合得分")
    entry = rating.get("入场价区间")
    target = rating.get("目标价区间")
    pos = rating.get("建议仓位上限") or {}
    hero_kvs = "".join([
        kv("入场价区间", f"{entry[0]}~{entry[1]}" if entry else "暂无数据",
           "多锚安全边际价~合理区间下沿" if entry else "多锚未加权/不可用"),
        kv("目标价区间", f"{target[0]}~{target[1]}" if target else "暂无数据",
           "合理区间上沿~高估阈值" if target else ""),
        kv("潜在上行空间", fmt_pct(rating.get("潜在上行空间")) if rating.get("潜在上行空间") is not None else "暂无数据"),
        kv("止损参考", num_cell(rating.get("止损参考")), "体系-15%硬止损位（§3.2）"),
        kv("建议仓位上限", f"择时 {fmt_pct(safe_get(pos, '择时组合'))} / 股息 {fmt_pct(safe_get(pos, '股息组合'))}",
           pos.get("口径", "")),
        kv("盈亏比", num_cell(rating.get("盈亏比")), "（目标下沿−现价）/（现价−止损）"),
        kv("持有周期", rating.get("持有周期", "—").split("（")[0]),
        kv("信心水平", rating.get("信心水平", "—"), rating.get("信心口径", "")[:40]),
        kv("标的池等级", rating.get("pool_level", "—"), "综合得分映射（v4.4.0）"),
    ])
    parts.append(card("", f"""
<h1>个股深度分析报告 · {esc(name)}（{esc(code)}）</h1>
<div class="muted small">数据截至 {gen} ｜ 报告属性：体系规则程序化执行结果</div>
<div class="rule-bar">执行标准：config/铁则层 {_versions()} ｜ 本报告仅为研究分析与策略框架支撑，不构成投资建议</div>
<div class="hero">
  <div class="hero-rating" style="background:{r_color}">
    <div class="r">{esc(r)}</div>
    <div class="s">综合得分 {score if score is not None else '—'}/100</div>
    <div class="s">{esc(rating.get('估值定性', '—'))}估值</div>
  </div>
  <div class="grid g3">{hero_kvs}</div>
</div>
<div class="muted small" style="margin-top:8px">评级依据：{esc(rating.get('评级依据', '—'))}｜估值定性依据：{esc(rating.get('估值定性依据', '—'))}</div>
""" + (f'<div class="callout">⛔ {esc(rating.get("gate_reason"))}</div>' if rating.get("gate_reason") else "")
        + (f'<div class="callout">{esc(rating.get("门槛提示"))}</div>'
           if rating.get("门槛提示") and "⚠️" in str(rating.get("门槛提示")) else "")))

    # ---- 模块2 核心指标速览 ----
    y_high, y_low = quote.get("52周最高"), quote.get("52周最低")
    band = (f"{num_cell(y_low)} ~ {num_cell(y_high)}"
            if (y_high and y_low) else "暂无数据")
    parts.append(card("二、核心指标速览", f"""<div class="grid g4">
{kv('现价', num_cell(quote.get('最新收盘价')))}
{kv('总市值', fmt_yi(safe_get(snap, 'basic', '总市值')))}
{kv('PE(TTM)', num_cell(val.get('PE_TTM')), esc((val.get('分位窗口') or '')[:22]))}
{kv('PB', num_cell(val.get('PB')))}
{kv('年内涨跌幅', pct_cell(quote.get('年内涨跌幅')))}
{kv('52周波幅', band)}
{kv('股息率(TTM)', fmt_pct(val.get('股息率TTM')) if val.get('股息率TTM') is not None else '暂无数据')}
{kv('ROE(近3年均值)', f"{fin.get('ROE近3年均值_pct'):.2f}%" if fin.get('ROE近3年均值_pct') is not None else '暂无数据')}
</div>"""))

    # ---- 模块3 选股规则校验 ----
    veto_rows = [[esc(c["item"]), status_pill(c["status"]), esc(c["detail"][:70]), esc(c["source"])]
                 for c in (result.get("veto") or [])]
    dims = rating.get("维度得分") or {}
    score_rows = []
    for dim, dd in dims.items():
        for iname, it in (dd.get("items") or {}).items():
            score_rows.append([esc(dim), esc(iname), f"{it['得分']:g} / {it['满分']:g}", esc(it["依据"])])
    parts.append(card("三、选股规则校验（规则联动 · stock_selection v4.4.0）",
        "<h3>一票否决项</h3>" + table(["检查项", "结果", "依据", "来源"], veto_rows) +
        "<h3>综合评分明细（PASS=满分 / FAIL·缺失=0，映射口径见config composite_scoring）</h3>" +
        table(["维度", "计分项", "得分", "计算依据"], score_rows) +
        f"<div class='grid g3' style='margin-top:8px'>{kv('最终综合得分', f'{score}/100' if score is not None else '暂无数据')}"
        f"{kv('标的池等级', esc(rating.get('pool_level', '—')))}"
        f"{kv('门槛制判定', '门槛FAIL直接排除（评分仅为展示映射）')}</div>"))

    # ---- 模块4 技术面分析 ----
    tech = result.get("technicals") or {}
    if tech.get("available"):
        trend_rows = [[esc(t["维度"]),
                       f'<span class="{"up" if t["方向"] in ("多", "偏多", "放量") else "down" if t["方向"] in ("空", "偏空", "缩量") else "muted"}">{esc(t["方向"])}</span>',
                       esc(t["描述"])] for t in tech["trend"]]
        lv_rows = ([[esc(s["档位"]), f'<span class="down">{s["价位"]}</span>', esc(s["依据"])]
                    for s in tech["supports"]] +
                   [[esc(rr["档位"]), f'<span class="up">{rr["价位"]}</span>', esc(rr["依据"])]
                    for rr in tech["resistances"]])
        tech_body = (table(["维度", "方向", "量化描述"], trend_rows) +
                     "<h3>关键价位表（支撑绿·阻力红，按距现价分档）</h3>" +
                     table(["档位", "价位", "依据"], list(lv_rows)))
    else:
        tech_body = f'<div class="callout">{esc(tech.get("note", "技术面不可用"))}</div>'
    parts.append(card("四、技术面分析（六维量化）", tech_body,
                      extra=f'<div class="muted small">{esc(tech.get("note", ""))}</div>'))

    # ---- 模块5 基本面分析 ----
    detail = safe_get(snap, "cashflow_ratios", "基本面明细") or []
    fund_body = ""
    if detail:
        latest = detail[-1]
        prev = detail[-2] if len(detail) > 1 else {}
        max_v = max((abs(to_float(latest.get(k)) or 0) for k in
                     ("营收", "毛利", "净利润", "研发费用", "经营现金流")), default=1) or 1
        bars = ""
        for k in ("营收", "毛利", "净利润", "研发费用", "经营现金流"):
            v = to_float(latest.get(k))
            p = to_float(prev.get(k))
            yoy = (v - p) / abs(p) if (v is not None and p) else None
            w = abs(v or 0) / max_v * 100
            color = UP if (v or 0) >= 0 else DOWN
            bars += (f'<div class="bar-row"><div class="bar-label">{k}（{esc(latest.get("年度"))}）</div>'
                     f'<div class="bar-track"><div class="bar-fill" style="width:{w:.0f}%;background:{color}"></div></div>'
                     f'<div class="bar-val">{fmt_yi(v)} {pct_cell(yoy, 1) if yoy is not None else ""}</div></div>')
        np_yoy = fin.get("净利润最新同比")
        rev_yoy = fin.get("营收最新同比")
        points = [
            f"净利润最新同比 {fmt_pct(np_yoy, signed=True) if np_yoy is not None else '暂无数据'}"
            + ("（降幅超20%，C类分红门槛不达标信号）" if (np_yoy is not None and np_yoy < -0.2) else ""),
            f"营收最新同比 {fmt_pct(rev_yoy, signed=True) if rev_yoy is not None else '暂无数据'}",
            f"经营现金流质量：收现比 {fmt_pct(safe_get(snap, 'cashflow_ratios', '收现比_最新'))} / "
            f"净现比 {fmt_pct(safe_get(snap, 'cashflow_ratios', '净现比_最新'))}",
            f"盈利能力：ROE近3年均值 {fin.get('ROE近3年均值_pct'):.2f}%" if fin.get("ROE近3年均值_pct") is not None else "ROE数据缺失",
            f"派现比例近3年均值 {fmt_pct(safe_get(snap, 'payout', '近3年均值'))}（分红/EPS自动口径）",
        ]
        fund_body = (bars + "<h3>最新财报核心要点（数据直述）</h3><ul>" +
                     "".join(f"<li>{p}</li>" for p in points) + "</ul>")
    else:
        fund_body = '<p class="muted">基本面明细数据缺失（新浪年报接口不可用），仅保留速览区核心指标</p>'
    parts.append(card("五、基本面分析", fund_body))

    # ---- 模块6 估值评价（多锚点） ----
    mv = result.get("multi_valuation") or {}
    val_parts = []
    pe_now = val.get("PE_TTM")
    if pe_now is not None and pe_now <= 0:
        val_parts.append('<div class="callout">⚠️ 净利润为负，PE估值失效，已切换估值体系（PE/PEG法自动停用）</div>')
    vi_state = rating.get("估值定性", "—")
    val_parts.append(f"<p>当前估值定性：<b>{esc(vi_state)}</b>（{esc(rating.get('估值定性依据', ''))}）；"
                     f"PE历史分位 {fmt_pct(val.get('PE历史分位'))} / PB历史分位 {fmt_pct(val.get('PB历史分位'))}"
                     f"（窗口：{esc(val.get('分位窗口', '暂无数据'))}）</p>")
    if mv.get("ok"):
        m_rows = [[f"{esc(m['方法'])}<br><span class='muted small'>{esc(m['层级'])} · 权重{m['权重']:.0%}</span>",
                   ("✅ 可用" if m["可用"] else f"<span class='muted'>不可用：{esc(m['不可用原因'])}</span>"),
                   (f"[{m['区间'][0]}, {m['区间'][1]}]" if m["区间"] else "—"),
                   (m["减持阈"] or "—"),
                   esc(m["参数"][:60]) if m["可用"] else "—"]
                  for m in mv.get("methods", [])]
        val_parts.append("<h3>多锚点估值测算（valuation_model v4.3.0行业方法集：" +
                         esc(mv.get("val_class_name", "")) + "）</h3>" +
                         table(["方法", "适用性", "合理区间(元)", "减持阈(元)", "核心参数"], m_rows))
        val_parts.append("<div class='muted small'>测算过程：" +
                         "；".join(f"{m['方法']}：{esc(m['过程'])}" for m in mv.get("methods", []) if m["可用"]) +
                         "</div>")
        combined = mv.get("combined")
        price = to_float(quote.get("最新收盘价"))
        if mv.get("diverged"):
            val_parts.append(f'<div class="callout">⚠️ 方法间偏差 {fmt_pct(mv.get("divergence_pct"))} 超过30%阈值，'
                             f'不强行加权：{esc("；".join(mv.get("notes", []))[:180])}</div>')
        elif combined:
            low, high = combined["合理区间"]
            stop = rating.get("止损参考")
            sell = combined["高估阈值"]
            lo_axis = min(x for x in (stop, combined["安全边际价"], low, price) if x) * 0.97
            hi_axis = max(x for x in (sell, high, price) if x) * 1.03
            span = hi_axis - lo_axis or 1

            def pos_pct(x):
                return max(1, min(99, (x - lo_axis) / span * 100))
            ticks = "".join(
                f'<div class="tick" style="left:{pos_pct(v):.1f}%">{lab}<br>{v}</div>'
                for v, lab in [(combined["安全边际价"], "安全边际"), (high, "区间上沿"), (sell, "高估阈")] if v)
            tickb = "".join(
                f'<div class="tickb" style="left:{pos_pct(v):.1f}%">{lab} {v}</div>'
                for v, lab in [(stop, "止损"), (low, "区间下沿")] if v)
            cur = f'<div class="cur" style="left:{pos_pct(price):.1f}%"></div>' if price else ""
            val_parts.append(
                f"<h3>综合估值结论（偏差 {fmt_pct(mv.get('divergence_pct'))} ≤30%，加权：{esc(combined['权重口径'])}）</h3>"
                f"<div class='grid g3'>{kv('综合合理区间', f'[{low}, {high}] 元')}"
                f"{kv('安全边际买入价', f'{combined['安全边际价']} 元', '区间下沿×0.90')}"
                f"{kv('高估警示价', f'{sell} 元', '加权减持阈')}</div>"
                f'<div class="gauge">{ticks}{tickb}{cur}</div>'
                f"<div class='muted small'>▍黑色游标=现价 {price}；{esc(combined.get('现价位置', ''))}</div>")
        else:
            val_parts.append(f'<div class="callout">{esc("；".join(mv.get("notes", [])) or "综合区间不可用")}</div>')
    else:
        val_parts.append(f'<p class="muted">多锚估值不可用：{esc(mv.get("error", "未执行"))}</p>')
    # 估值逻辑拆解 + 机构预期
    cagr_rows = safe_get(snap, "institution_forecast", "预测EPS") or []
    logic_bits = []
    pe_pct_v = val.get("PE历史分位")
    if pe_pct_v is not None:
        logic_bits.append(f"历史分位视角：当前PE处近10年 {fmt_pct(pe_pct_v)} 分位，"
                          f"{'处于低估惯性区' if pe_pct_v < 0.3 else '处于中枢区间' if pe_pct_v < 0.7 else '已进入高分位警示区'}")
    if cagr_rows:
        logic_bits.append("机构一致预期（同花顺预测EPS）：" +
                          "；".join(f"{r.get('年度')}年 {r.get('均值')} 元（{r.get('预测机构数')}家）" for r in cagr_rows))
    ind_pe = safe_get(snap, "peers", "行业均值PE")
    logic_bits.append(f"同业对标：行业均值PE {num_cell(ind_pe)}" if ind_pe else "同业对标：行业均值PE暂无数据")
    val_parts.append("<h3>估值逻辑拆解</h3><ul>" + "".join(f"<li>{b}</li>" for b in logic_bits) + "</ul>")
    parts.append(card("六、估值评价（多锚点体系）", "".join(val_parts)))

    # ---- 模块7 核心叙事与多空交锋 ----
    rm_ = result.get("risk_matrix") or {}
    t = result.get("timing") or {}
    bull_pts, bear_pts = _stock_bull_bear(snap, result)
    max_w = max([w for _, w in bull_pts + bear_pts] or [1])
    div_rows = ""
    for i in range(max(len(bull_pts), len(bear_pts))):
        lp = bear_pts[i] if i < len(bear_pts) else None
        rp = bull_pts[i] if i < len(bull_pts) else None
        lbar = (f'<div class="bar-fill" style="width:{lp[1] / max_w * 100:.0f}%"></div>' if lp else "")
        rbar = (f'<div class="bar-fill" style="width:{rp[1] / max_w * 100:.0f}%"></div>' if rp else "")
        div_rows += (f'<div class="div-row"><div class="div-left">{lbar}</div>'
                     f'<div class="muted small" style="text-align:center">'
                     f'{esc(lp[0]) if lp else ""}｜{esc(rp[0]) if rp else ""}</div>'
                     f'<div class="div-right">{rbar}</div></div>')
    parts.append(card("七、核心叙事与多空交锋",
        '<div class="muted small">权重=证据强度工程分（数据驱动），红=多头证据、绿=空头证据（A股配色）</div>' +
        div_rows +
        f"""<div class="two-col" style="margin-top:10px">
<div class="side"><h4 class="up">多头核心逻辑</h4><ul>{''.join(f'<li>{esc(p)}（权重{w}）</li>' for p, w in bull_pts)}</ul></div>
<div class="side"><h4 class="down">空头核心逻辑</h4><ul>{''.join(f'<li>{esc(p)}（权重{w}）</li>' for p, w in bear_pts)}</ul></div>
</div><div class="muted small">可信度口径：证据均来自当期数据与体系判定结果，权重为可验算的工程分（满分100），非主观观点</div>"""))

    # ---- 模块8 风险因素分级评估 ----
    risk_html = ""
    level_map = {"P0": ("高风险", "risk-hi"), "P1": ("高风险", "risk-hi"),
                 "P2": ("中风险", "risk-mid"), "P3": ("低风险", "risk-low")}
    for cat in ("宏观", "行业", "公司", "估值"):
        items = rm_.get(cat) or []
        risk_html += f"<h3>{cat}风险</h3>"
        for it in items:
            lv, cls = level_map.get(it["级别"], ("低风险", "risk-low"))
            risk_html += (f'<div class="risk-item {cls}"><b>[{lv}]</b> {esc(it["内容"])}</div>')
    parts.append(card("八、风险因素分级评估（宏观/行业/公司/估值 · P0-P1=高 P2=中 P3=低）", risk_html))

    # ---- 模块9 实操方案 ----
    price = to_float(quote.get("最新收盘价"))
    risk_p = result.get("risk") or {}
    entry_rows = [
        ["首仓 30%", "满足门槛+评分≥3 + 决策卡片定稿 + 24小时冷静期（§3.2）",
         f"{entry[0]}~{entry[1]}" if entry else "多锚区间不可用"],
        ["二仓 30%", "较首仓下跌≥5%，或横盘≥2周后放量突破（§3.2）", "较首仓成本-5%附近"],
        ["三仓 40%", "较首仓下跌≥8% 且基本面逻辑未变（§3.2）", "较首仓成本-8%附近"],
    ]
    sl = risk_p.get("止损参考价") or {}
    tp = risk_p.get("止盈参考价") or {}
    exit_rows = [
        ["止损一档", "亏损达-10%", f"减仓50%（参考价 {sl.get('-10%减仓50%', '—')}）"],
        ["止损二档", "亏损达-15%", f"无条件清仓（参考价 {sl.get('-15%无条件清仓', '—')}）"],
        ["逻辑止损", "买入逻辑被证伪", "立即清仓，不看价格（§3.2）"],
        ["止盈一档", "盈利+20%或达目标价", f"减持1/3（参考价 {tp.get('+20%减持1/3', '—')}），余仓移动止盈"],
        ["止盈二档", "盈利+40%或PE达历史70%分位", f"再减1/3（参考价 {tp.get('+40%再减1/3', '—')}）"],
        ["移动止盈", "自高点回落≥8% / 放量滞涨信号触发", "无条件清仓剩余（§3.2/裁决#12）"],
        ["时间止损", "3个月未盈利 / 6个月无起色 / 满12个月", "减50% / 清仓 / 强制重评（§3.2）"],
    ]
    parts.append(card("九、实操方案（risk_control铁则，仓位不突破单票上限）",
                      "<h3>分批建仓（30/30/40）</h3>" + table(["批次", "触发条件", "成本区间"], entry_rows) +
                      "<h3>止盈止损</h3>" + table(["场景", "触发条件", "操作"], exit_rows) +
                      f"<div class='muted small'>建仓方案仅在评级∈{{买入, 逢低建仓}}且完成决策卡片后适用；"
                      f"当前评级：<b>{esc(r)}</b>；能力圈：{esc(risk_p.get('能力圈判定', '—'))}</div>"))

    # ---- 模块10 关键验证节点 ----
    nodes = _verification_nodes(snap)
    parts.append(card("十、关键验证节点（跟踪日历）",
                      '<ul class="timeline">' +
                      "".join(f"<li><b>{esc(n['时间'])}</b> ｜ {esc(n['事件'])}<br>"
                              f"<span class='muted small'>{esc(n['验证逻辑'])}</span></li>" for n in nodes) +
                      "</ul>"))

    # ---- 自检与口径 ----
    sc_html = ("✅ 逻辑自检通过：规则一致性/结论自洽性/数值合法性/边界口径校验全部通过"
               if not self_check else
               "".join(f'<div class="risk-item risk-mid">[{w["级别"]}] {esc(w["内容"])}</div>' for w in self_check))
    assum = result.get("assumptions") or []
    parts.append(card("附：推定口径与逻辑自检",
                      "<h3>推定判定与数据口径</h3>" +
                      table(["判定项", "状态", "口径"], [[esc(a["item"]), status_pill(a["status"]),
                                                    esc(a["detail"][:80])] for a in assum[:12]]) +
                      f"<h3>逻辑自检结果</h3>{sc_html}"))

    return _doc(f"个股分析报告 {name}（{code}）", "".join(parts))


def _stock_bull_bear(snap: dict, result: dict) -> tuple[list, list]:
    """多空证据与权重（工程分：来源=体系判定结果，可验算）。"""
    bull, bear = [], []
    rating = result.get("rating") or {}
    fin = snap.get("financials") or {}
    val = snap.get("valuation") or {}
    t = result.get("timing") or {}
    score = rating.get("综合得分")
    if score is not None:
        (bull if score >= 60 else bear).append((f"综合得分 {score}/100", min(95, int(abs(score)))))
    roe = fin.get("ROE近3年均值_pct")
    if roe is not None:
        (bull if roe >= 12 else bear).append((f"ROE近3年均值 {roe:.1f}%", min(90, int(roe * 4))))
    cagr_note = next((s for s in (t.get("scoring") or []) if s["项"] == "机构预期"), None)
    if cagr_note and cagr_note.get("得分"):
        bull.append(("机构一致预期CAGR≥15%", 75))
    pe_pct = val.get("PE历史分位")
    if pe_pct is not None:
        if pe_pct < 0.3:
            bull.append((f"估值处历史低分位 {pe_pct:.0%}", 70))
        elif pe_pct >= 0.7:
            bear.append((f"估值处历史高分位 {pe_pct:.0%}", 70))
    np_yoy = fin.get("净利润最新同比")
    if np_yoy is not None and np_yoy < 0:
        bear.append((f"净利润同比 {np_yoy:.1%}", min(90, int(abs(np_yoy) * 200))))
    dd = safe_get(snap, "quote", "较1年内高点回撤")
    if dd is not None:
        if dd >= 0.25:
            bull.append((f"回撤 {dd:.0%} 达择时门槛（安全垫）", 55))
        else:
            bear.append((f"回撤 {dd:.0%} 未达25%门槛（调整未充分）", 55))
    tech = result.get("technicals") or {}
    if tech.get("verdict") == "偏多":
        bull.append(("技术面六维偏多", 50))
    elif tech.get("verdict") == "偏空":
        bear.append(("技术面六维偏空", 50))
    mv = result.get("multi_valuation") or {}
    if mv.get("diverged"):
        bear.append((f"估值方法分歧 {fmt_pct(mv.get('divergence_pct'))}（定价逻辑存分歧）", 45))
    return bull[:5], bear[:5]


def _verification_nodes(snap: dict) -> list[dict]:
    """关键验证节点：财报日历（固定披露窗口）+ 体系检查节点 + 近期公告跟进。"""
    now = datetime.now()
    year = now.year
    fixed = [
        {"月份": (4, 30), "时间": f"{year}-04-30前", "事件": "年报+一季报披露截止",
         "验证逻辑": "核验净利同比是否修复至>-20%（C类门槛）、派现比例与分红连续性"},
        {"月份": (8, 31), "时间": f"{year}-08-31前", "事件": "中报披露截止",
         "验证逻辑": "核验营收/净利同比转正与否（择时门槛'增速不为负'）、收现比/净现比质量"},
        {"月份": (10, 31), "时间": f"{year}-10-31前", "事件": "三季报披露截止",
         "验证逻辑": "核验机构一致预期CAGR的兑现进度（评分'机构预期'项复算）"},
    ]
    nodes = []
    for f in fixed:
        m, d = f["月份"]
        if (m, d) < (now.month, now.day):
            f["时间"] = f["时间"].replace(str(year), str(year + 1))
        nodes.append({k: v for k, v in f.items() if k != "月份"})
    nodes.append({"时间": "每周日", "事件": "市场状态周判（§1.3）",
                  "验证逻辑": "A/B/C区切换直接改变本标的'非C区'门槛与仓位映射"})
    nodes.append({"时间": "每季度", "事件": "备选池审核（§2.3）",
                  "验证逻辑": "门槛不达标移出watchlist → 择时'在池'门槛失效"})
    ann = (snap.get("announcements") or [])[:2]
    for a in ann:
        nodes.append({"时间": str(a.get("公告时间", ""))[:10], "事件": f"公告跟进：{str(a.get('公告标题', ''))[:28]}",
                      "验证逻辑": "§3.5预案：公告日不操作，24小时消化后对照决策卡片判断"})
    return sorted(nodes, key=lambda n: n["时间"])


# =============================================================================
# 每日市场分析看板（9模块）
# =============================================================================

def render_daily_dashboard(analysis: dict,
                           self_check: list[dict] | None = None) -> str:
    day = analysis.get("date", "")
    day_fmt = f"{day[:4]}-{day[4:6]}-{day[6:]}" if len(day) == 8 else day
    idx = analysis.get("index_summary") or []
    sent = analysis.get("sentiment") or {}
    sector = analysis.get("sector") or {}
    state = analysis.get("state_check") or {}
    parts = []

    # ---- 模块1 头部总览 ----
    sh = next((r for r in idx if r["名称"] == "上证指数"), None)
    up_n, down_n = sector.get("gainer_count", 0), sector.get("loser_count", 0)
    headline = _daily_headline(idx, sent, sector)
    total = analysis.get("total_turnover")
    keypoints = [
        f"两市成交 {fmt_yi(total)}" if total else "成交额暂无数据",
        (f"板块 {up_n}涨/{down_n}跌" +
         (f"，{sector['top'][0].get('板块名称')}领涨" if sector.get("top") else "")),
        f"市场状态 {STATE_CN.get(state.get('effective_state'), '未判定')}"
        f"（{'人工判定' if state.get('manual_state') else '程序初判'}）",
        f"两融余额较10日前 {fmt_pct(safe_get(analysis, 'capital_flow', 'margin', '沪市10日变化率'), signed=True)}",
    ]
    extreme = ('<div class="callout">⚠️ 今日行情极端（涨跌停结构异常），数据参考性下降</div>'
               if analysis.get("extreme_market") else "")
    parts.append(card("", f"""
<h1>A股市场日报 · {day_fmt}</h1>
<div class="muted small">报告属性：体系规则程序化执行的每日市场结构复盘</div>
<div class="rule-bar">执行标准：config/铁则层 {_versions()} ｜ 仅研究分析，不构成投资建议</div>
<h2 style="border:none;padding:6px 0 0;font-size:20px">{esc(headline)}</h2>
<div>{''.join(f'<span class="pill pill-primary">{esc(p)}</span>' for p in keypoints)}</div>{extreme}"""))

    # ---- 模块2 六大指数总览 ----
    idx_rows = []
    for r in idx:
        pct = to_float(r.get("涨跌幅_pct"))
        judge = ("最强" if pct == max(to_float(x.get("涨跌幅_pct")) or -99 for x in idx) else
                 "最弱" if pct == min(to_float(x.get("涨跌幅_pct")) or 99 for x in idx) else
                 "走强" if (pct or 0) > 0 else "承压")
        jcls = {"最强": "pill-up", "走强": "pill-up", "最弱": "pill-down", "承压": "pill-down"}[judge]
        idx_rows.append([esc(r.get("名称")), num_cell(r.get("收盘")),
                         pct_cell(pct / 100 if pct is not None else None),
                         fmt_yi(r.get("成交额")),
                         '<span class="muted small">暂无数据*</span>',
                         f'<span class="pill {jcls}">{judge}</span>'])
    parts.append(card("二、指数收盘总览",
                      table(["指数", "收盘", "涨跌幅", "成交额", "主力净流入", "强弱判断"], idx_rows) +
                      '<div class="muted small">* 主力净流入（四类资金拆分）数据源已停用（东财接口封锁），以下方两融维度替代观察；'
                      '万得全A/北证50无公开可编程数据源，以沪深300/中证500替代呈现</div>'))

    # ---- 模块3 资金流向 ----
    margin = safe_get(analysis, "capital_flow", "margin") or {}
    series = margin.get("沪市融资余额序列") or []
    mbars = ""
    if series:
        vals = [s["融资余额"] for s in series if s.get("融资余额")]
        vmin, vmax = min(vals), max(vals)
        span = (vmax - vmin) or 1
        for s in series[-8:]:
            v = s.get("融资余额")
            w = 30 + (v - vmin) / span * 65 if v else 0
            d_raw = str(s.get("日期")).replace("-", "")
            d_lab = f"{d_raw[-4:-2]}-{d_raw[-2:]}" if len(d_raw) >= 4 else d_raw
            mbars += (f'<div class="bar-row"><div class="bar-label">{esc(d_lab)}</div>'
                      f'<div class="bar-track"><div class="bar-fill" style="width:{w:.0f}%;background:{UP}"></div></div>'
                      f'<div class="bar-val">{fmt_yi(v)}</div></div>')
    chg = margin.get("沪市10日变化率")
    interp = [
        f"沪市融资余额 {fmt_yi(margin.get('沪市融资余额_最新'))}，较10个交易日前 {fmt_pct(chg, signed=True) if chg is not None else '暂无数据'}"
        + (f"——杠杆资金{'延续净流入，风险偏好未退潮' if (chg or 0) > 0 else '边际回落，风险偏好收敛'}" if chg is not None else ""),
        f"两市融资余额合计 {fmt_yi(margin.get('两市融资余额_最新'))}",
        f"上涨占比 {fmt_pct(sent.get('上涨占比'))}，涨停 {sent.get('涨停家数') if sent.get('涨停家数') is not None else '—'} 家 / "
        f"跌停 {sent.get('跌停家数') if sent.get('跌停家数') is not None else '—'} 家",
        "超大单/大单/中单/小单四类资金拆分：暂无数据（原东财接口停用，不编造）",
    ]
    parts.append(card("三、资金流向分析", f"""<div class="two-col">
<div><h3>沪市融资余额（近8个交易日）</h3>{mbars or '<p class="muted">暂无数据</p>'}</div>
<div class="side"><h4>核心资金动向解读</h4><ul>{''.join(f'<li>{p}</li>' for p in interp)}</ul></div>
</div>"""))

    # ---- 模块4 板块强弱格局 ----
    def sec_rows(items, up=True):
        return [[esc(b.get("板块名称")),
                 pct_cell((to_float(b.get("涨跌幅")) or 0) / 100),
                 f'<span class="pill {"pill-up" if up else "pill-down"}">{esc(b.get("标签", ""))}</span>']
                for b in items]
    shortage_up = ('<div class="callout">今日上涨板块数量不足10个，已如实列示全部上涨板块，不凑数</div>'
                   if sector.get("top_shortage") else "")
    shortage_dn = ('<div class="callout">今日下跌板块数量不足10个，已如实列示</div>'
                   if sector.get("bottom_shortage") else "")
    parts.append(card("四、板块强弱格局（涨幅榜降序 / 跌幅榜按跌幅深→浅）", f"""<div class="two-col">
<div><h3 class="up">涨幅榜 Top{len(sector.get('top') or [])}（共{up_n}个板块收涨）</h3>
{table(['板块', '涨跌幅', '标签'], sec_rows(sector.get('top') or [], True))}{shortage_up}</div>
<div><h3 class="down">跌幅榜 Top{len(sector.get('bottom') or [])}（共{down_n}个板块收跌）</h3>
{table(['板块', '涨跌幅', '标签'], sec_rows(sector.get('bottom') or [], False))}{shortage_dn}</div>
</div><div class="muted small">数据源：{esc(sector.get('source_note', ''))}；标签为涨跌幅分档的数据标注，非叙事判断</div>"""))

    # ---- 模块5 核心矛盾 ----
    conflict = analysis.get("conflict") or {}
    parts.append(card("五、当日核心矛盾深度解读", f"""<div class="two-col">
<div class="side"><h4 class="up">多头证据（数据直述）</h4><ul>{''.join(f'<li>{esc(p)}</li>' for p in conflict.get('bull', []))}</ul></div>
<div class="side"><h4 class="down">空头证据（数据直述）</h4><ul>{''.join(f'<li>{esc(p)}</li>' for p in conflict.get('bear', []))}</ul></div>
</div><div class="muted small">{esc(conflict.get('note', ''))}</div>"""))

    # ---- 模块6 重点指数专项 ----
    spot = analysis.get("spotlight") or {}
    if spot.get("available"):
        def spot_block(s):
            cls = "up" if (s["涨跌幅"] or 0) > 0 else "down"
            return (f"<div class='side'><h4>当日{s['角色']}：{esc(s['名称'])} "
                    f"<span class='{cls}'>{(s['涨跌幅'] or 0):+.2f}%</span></h4><ul>" +
                    "".join(f"<li>{esc(p)}</li>" for p in s["要点"]) + "</ul></div>")
        spot_html = (f"<div class='two-col'>{spot_block(spot['strongest'])}{spot_block(spot['weakest'])}</div>"
                     f"<div class='muted small'>{esc(spot.get('note', ''))}</div>")
    else:
        spot_html = '<p class="muted">指数数据缺失，专项分析不可用</p>'
    parts.append(card("六、重点指数专项分析", spot_html))

    # ---- 模块7 情景预判 ----
    scen = analysis.get("scenario") or {}
    if scen.get("available"):
        cls_map = {"乐观": "scen-opt", "中性": "scen-mid", "悲观": "scen-pes"}
        cards_ = "".join(
            f"""<div class="scen {cls_map.get(s['名称'], 'scen-mid')}"><h4>{esc(s['名称'])}情景 ｜ 概率 {esc(s['概率'])}</h4>
<div class="rng">上证 {s['区间'][0]} ~ {s['区间'][1]}</div>
<div class="small muted">触发条件：{esc(s['触发条件'])}</div></div>"""
            for s in scen.get("scenarios", []))
        scen_html = f'<div class="grid g3">{cards_}</div><div class="callout">{esc(scen.get("note", ""))}</div>'
    else:
        scen_html = f'<p class="muted">{esc(scen.get("note", "情景框架不可用"))}</p>'
    parts.append(card("七、下一交易日情景预判（波动率观察框架）", scen_html))

    # ---- 模块8 综合研判 ----
    alerts = analysis.get("alerts") or []
    alert_html = "".join(
        f'<div class="risk-item {"risk-hi" if a["级别"] in ("P0", "P1") else "risk-mid" if a["级别"] == "P2" else "risk-low"}">'
        f'[{a["级别"]}] {esc(a["内容"])}</div>' for a in alerts)
    pos = analysis.get("position") or {}
    cur = pos.get("当前约束") or {}
    state_cn = STATE_CN.get(state.get("effective_state"), "未判定")
    signals = [
        f"市场状态 {state_cn}：股息组合仓位上限 "
        f"{fmt_pct(cur.get('dividend_cap')) if cur.get('dividend_cap') is not None else esc(cur.get('dividend_note', '—'))}，"
        f"择时组合 {fmt_pct(cur.get('timing_cap')) if cur.get('timing_cap') is not None else '—'}"
        + (f"（{esc(cur.get('timing_note'))}）" if cur.get("timing_note") else ""),
        f"股债利差 {fmt_pct(state.get('equity_bond_spread'))}（A区条件>5%/C区条件<2%），"
        f"A区命中 {state.get('a_hits')} 条 / C区命中 {state.get('c_hits')} 条",
        f"波动率 {fmt_pct(safe_get(analysis, 'volatility', '数值'))}（严格风控触发线35%，§1.5）",
        "体系操作边界：所有买卖以决策卡片+预设止盈止损触发，本报告不提供具体买卖点",
    ]
    parts.append(card("八、综合研判与操作提示（体系信号匹配）",
                      f"<p><b>当日市场性质</b>：{esc(headline)}</p>"
                      f"<h3>体系信号核对</h3><ul>{''.join(f'<li>{s}</li>' for s in signals)}</ul>"
                      f"<h3>核心风险提示</h3>{alert_html}"))

    # ---- 自检 ----
    sc_html = ("✅ 逻辑自检通过（命中数/仓位映射/情绪分/状态枚举校验全过）" if not self_check else
               "".join(f'<div class="risk-item risk-mid">[{w["级别"]}] {esc(w["内容"])}</div>' for w in self_check))
    parts.append(card("附：数据口径与逻辑自检",
                      f"<ul><li>10年国债 {fmt_pct(analysis.get('cn10y_yield'))}；中证全指PE1 "
                      f"{safe_get(analysis, 'csindex_pe', '市盈率1') or '暂无数据'}</li>"
                      + "".join(f"<li>⚠️ {k} 拉取失败：{esc(str(v)[:60])}（按缺失处理）</li>"
                                for k, v in (analysis.get("errors") or {}).items())
                      + f"</ul><h3>逻辑自检</h3>{sc_html}"))

    return _doc(f"A股市场日报 {day_fmt}", "".join(parts))


STATE_CN = {"A_undervalued": "A区·低估", "B_low": "B偏低", "B_neutral": "B中性",
            "B_high": "B偏高", "C_overvalued": "C区·高估", None: "未判定"}


def _daily_headline(idx: list, sent: dict, sector: dict) -> str:
    """一句话行情定性（数据模板生成）。"""
    pcts = [to_float(r.get("涨跌幅_pct")) for r in idx if to_float(r.get("涨跌幅_pct")) is not None]
    if not pcts:
        return "行情数据缺失"
    up_cnt = sum(1 for p in pcts if p > 0)
    strongest = max(idx, key=lambda r: to_float(r.get("涨跌幅_pct")) or -99)
    weakest = min(idx, key=lambda r: to_float(r.get("涨跌幅_pct")) or 99)
    ur = sent.get("上涨占比")
    if up_cnt == len(pcts):
        tone = "指数全线收红"
    elif up_cnt == 0:
        tone = "指数全线收绿"
    else:
        tone = f"指数分化（{up_cnt}/{len(pcts)}收红）"
    breadth = (f"，个股上涨占比 {ur:.0%}" if ur is not None else "")
    return (f"{tone}：{strongest.get('名称')} {to_float(strongest.get('涨跌幅_pct')) or 0:+.2f}% 最强，"
            f"{weakest.get('名称')} {to_float(weakest.get('涨跌幅_pct')) or 0:+.2f}% 最弱{breadth}")
