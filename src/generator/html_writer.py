# -*- coding: utf-8 -*-
"""
html_writer.py — Markdown报告 → Claude官方风格HTML / PDF（任务三，2026-07-07）
- HTML：python-markdown 渲染 + 全量内嵌CSS（Claude桌面端设计语言），离线打开样式完整；
- PDF：调用本机 Edge 无头模式打印HTML（Chromium渲染，与HTML视觉零偏差），
  分页规则：卡片/表格不跨页断裂；每页底部固定页脚（报告名+生成日期）。
设计基准（用户任务三指定）：
  背景 #F7F7F8 / 卡片 #FFFFFF / 主色 #4F46E5 / 正文 #1F2937 / 次文 #6B7280
  涨 #059669 跌 #DC2626 / 警示 #FFF7ED 底 #C2410C 字 / 12px圆角 / 1px浅边框 / 极柔投影
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

# Claude 桌面端设计风格（全部内嵌，无外部依赖）
CLAUDE_CSS = """
:root {
  --bg: #F7F7F8; --card: #FFFFFF; --primary: #4F46E5;
  --text: #1F2937; --muted: #6B7280; --up: #059669; --down: #DC2626;
  --warn-bg: #FFF7ED; --warn-fg: #C2410C; --border: #E5E7EB;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
               "PingFang SC", "Noto Sans SC", sans-serif;
  font-size: 15px; line-height: 1.6;
}
.report {
  max-width: 860px; margin: 0 auto; background: var(--card);
  border: 1px solid var(--border); border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.03);
  padding: 40px 48px;
}
h1 { font-size: 26px; line-height: 1.35; margin: 0 0 12px; letter-spacing: -0.01em; }
h2 {
  font-size: 19px; margin: 36px 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--border); letter-spacing: -0.01em;
}
h3 { font-size: 16px; margin: 24px 0 10px; color: var(--text); }
p { margin: 10px 0; }
strong { font-weight: 600; }
a { color: var(--primary); text-decoration: none; }
hr { border: none; border-top: 1px solid var(--border); margin: 28px 0; }
blockquote {
  margin: 14px 0; padding: 10px 16px; background: var(--warn-bg);
  border-left: 3px solid var(--warn-fg); border-radius: 0 12px 12px 0;
  color: var(--warn-fg); font-size: 13.5px;
}
blockquote p { margin: 4px 0; }
table {
  width: 100%; border-collapse: collapse; margin: 14px 0; font-size: 13.5px;
  border: none; page-break-inside: avoid;
}
th {
  text-align: left; font-weight: 600; color: var(--muted); font-size: 12.5px;
  padding: 8px 10px; border-bottom: 1px solid var(--border); background: transparent;
}
td { padding: 8px 10px; border-bottom: 1px solid #F0F0F2; vertical-align: top; }
tr:hover td { background: #FAFAFB; }
tr:last-child td { border-bottom: none; }
ul, ol { margin: 10px 0; padding-left: 22px; }
li { margin: 4px 0; }
code {
  background: #F3F4F6; border-radius: 6px; padding: 1px 6px;
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 13px;
}
.up { color: var(--up); } .down { color: var(--down); }
.meta-note { color: var(--muted); font-size: 12.5px; }
.footer {
  max-width: 860px; margin: 20px auto 0; text-align: center;
  color: var(--muted); font-size: 12px;
}
/* 打印/PDF：卡片表格不跨页断裂，每页重复页脚 */
@media print {
  body { background: #FFFFFF; padding: 0 0 40px 0; }
  .report { border: none; box-shadow: none; padding: 8px 6px; max-width: 100%; }
  h2 { page-break-after: avoid; }
  table, blockquote { page-break-inside: avoid; }
  tr:hover td { background: transparent; }
  .footer { display: none; }
  .pdf-footer {
    display: block; position: fixed; bottom: 0; left: 0; right: 0;
    text-align: center; font-size: 10.5px; color: #6B7280;
    border-top: 1px solid #E5E7EB; padding: 6px 0 2px; background: #FFFFFF;
  }
}
.pdf-footer { display: none; }
@page { margin: 16mm 12mm 20mm 12mm; }
"""

# 涨跌幅上色：+x.xx% → 绿涨色；-x.xx% → 红跌色（A股习惯以体系配色为准：up=#059669）
_PCT_RE = re.compile(r'(?<![\w.])([+-]\d+(?:\.\d+)?%)')


def _colorize(html: str) -> str:
    """给带符号百分比着色（表格/正文通用），不动无符号百分比（阈值类）。"""
    def repl(m):
        v = m.group(1)
        cls = "up" if v.startswith("+") else "down"
        return f'<span class="{cls}">{v}</span>'
    return _PCT_RE.sub(repl, html)


def markdown_to_html(md_text: str, title: str) -> str:
    """Markdown → 完整HTML文档（Claude风格，CSS内嵌）。"""
    import markdown as mdlib
    body = mdlib.markdown(md_text, extensions=["tables", "sane_lists"])
    body = _colorize(body)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{CLAUDE_CSS}</style>
</head>
<body>
<div class="report">
{body}
</div>
<div class="footer">投资研究工作台 · {title} · 生成于 {generated} · 仅研究分析，不构成投资建议</div>
<div class="pdf-footer">{title} ｜ 生成日期 {generated} ｜ 投资研究工作台 · 仅研究分析不构成投资建议</div>
</body>
</html>"""


def _find_edge() -> str | None:
    """定位本机 Edge（Chromium）可执行文件。"""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def html_to_pdf(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    """
    HTML → PDF：Edge无头打印（Chromium渲染引擎，与浏览器显示零偏差）。
    返回 (成功?, 说明)。Edge缺失或打印失败时不抛异常，由调用方降级提示。
    """
    edge = _find_edge()
    if not edge:
        return False, "未找到本机Edge浏览器，PDF跳过（HTML/MD已生成）"
    try:
        with tempfile.TemporaryDirectory() as tmp_profile:   # 独立profile避免与用户会话冲突
            r = subprocess.run(
                [edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                 f"--user-data-dir={tmp_profile}",
                 f"--print-to-pdf={pdf_path}", str(html_path.resolve().as_uri())],
                capture_output=True, timeout=90)
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            return True, "PDF已生成（Edge无头打印，与HTML同引擎渲染）"
        return False, f"Edge打印未产出有效PDF：{r.stderr.decode(errors='replace')[:150]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"PDF生成异常：{type(exc).__name__}: {exc}"
