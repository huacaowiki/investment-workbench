# -*- coding: utf-8 -*-
"""
archiver.py — 报告归档器（任务三升级：MD/HTML/PDF多格式，默认HTML）
命名规则：市场日报 → 市场日报_YYYYMMDD.{md,html,pdf}；个股 → 个股_代码_名称_YYYYMMDD.*。
归档时同步：① 写入 meta JSON（output/*/meta/，供迭代模块统计）② 更新目录索引 INDEX.md。
格式规则（v4.3.0）：
  - MD 永远落盘（机器可读源文件，历史兼容与迭代统计依赖）；
  - 默认格式 html：额外生成 Claude 风格HTML，索引主链接指向HTML；
  - output_format = "md" 只出MD / "pdf"、"all" 追加PDF（Edge无头打印，缺Edge时降级提示）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.utils.file_utils import DIRS, ensure_dir, write_json, write_text

DEFAULT_FORMAT = "html"   # 任务三：用户无特殊指定时默认HTML


def _update_index(folder: Path, entry_line: str, title: str):
    """在目录 INDEX.md 头部插入最新条目（保持新→旧排序，去重）。"""
    index = folder / "INDEX.md"
    lines = []
    if index.exists():
        lines = [l for l in index.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("# ") and l != entry_line]
    content = f"# {title}\n\n" + "\n".join([entry_line] + lines) + "\n"
    write_text(index, content)


def _write_formats(folder: Path, stem: str, markdown: str, title: str,
                   output_format: str) -> dict:
    """
    按格式规则落盘。返回 {"md": Path, "html": Path|None, "pdf": Path|None, "notes": [...]}。
    任何格式转换失败都不影响已生成的其他格式（降级提示）。
    """
    fmt = (output_format or DEFAULT_FORMAT).lower()
    out: dict = {"md": None, "html": None, "pdf": None, "notes": []}
    out["md"] = write_text(folder / f"{stem}.md", markdown)

    if fmt in ("html", "pdf", "all"):
        try:
            from src.generator.html_writer import markdown_to_html
            html = markdown_to_html(markdown, title)
            out["html"] = write_text(folder / f"{stem}.html", html)
        except Exception as exc:  # noqa: BLE001 —— 格式转换失败降级为MD
            out["notes"].append(f"HTML生成失败（已保留MD）：{type(exc).__name__}: {exc}")
    if fmt in ("pdf", "all") and out["html"]:
        from src.generator.html_writer import html_to_pdf
        ok, note = html_to_pdf(out["html"], folder / f"{stem}.pdf")
        if ok:
            out["pdf"] = folder / f"{stem}.pdf"
        out["notes"].append(note)
    return out


def _primary(paths: dict) -> Path:
    """索引与返回值的主文件：优先HTML（默认格式），无则MD。"""
    return paths.get("html") or paths["md"]


def archive_daily_report(markdown: str, meta: dict,
                         output_format: str = DEFAULT_FORMAT) -> Path:
    """归档市场日报（MD必落盘 + 按格式追加HTML/PDF），返回主文件路径。"""
    day = meta.get("date") or datetime.now().strftime("%Y%m%d")
    folder = ensure_dir(DIRS["output_daily"])
    title = f"A股市场日报 {day}"
    paths = _write_formats(folder, f"市场日报_{day}", markdown, title, output_format)
    meta = {**meta, "formats": {k: str(v) for k, v in paths.items() if k != "notes" and v},
            "format_notes": paths["notes"]}
    write_json(folder / "meta" / f"市场日报_{day}.json", meta)
    primary = _primary(paths)
    _update_index(folder, f"- [{day} 市场日报]({primary.name})", "市场日报索引")
    return primary


def archive_stock_report(markdown: str, meta: dict,
                         output_format: str = DEFAULT_FORMAT) -> Path:
    """归档个股分析报告（多格式），返回主文件路径。"""
    day = meta.get("date") or datetime.now().strftime("%Y%m%d")
    code = meta.get("code", "unknown")
    name = (meta.get("name") or "").replace("/", "-") or "未知"
    folder = ensure_dir(DIRS["output_stock"])
    title = f"个股分析报告 {name}（{code}）{day}"
    paths = _write_formats(folder, f"个股_{code}_{name}_{day}", markdown, title, output_format)
    meta = {**meta, "formats": {k: str(v) for k, v in paths.items() if k != "notes" and v},
            "format_notes": paths["notes"]}
    write_json(folder / "meta" / f"个股_{code}_{day}.json", meta)
    primary = _primary(paths)
    _update_index(folder, f"- [{day} {name}（{code}）]({primary.name})", "个股报告索引")
    return primary


def archive_iteration_report(markdown: str, meta: dict, kind: str = "monthly") -> Path:
    """归档迭代报告（monthly/quarterly），返回路径。"""
    period = meta.get("period") or datetime.now().strftime("%Y-%m")
    title = "月度体系迭代报告" if kind == "monthly" else "季度体系深度复盘报告"
    folder = ensure_dir(DIRS["output_iteration"])
    path = folder / f"{title}_{period}.md"
    write_text(path, markdown)
    write_json(folder / "meta" / f"{title}_{period}.json", meta)
    _update_index(folder, f"- [{period} {title}]({title}_{period}.md)", "体系迭代报告索引")
    return path
