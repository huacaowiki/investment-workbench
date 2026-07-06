# -*- coding: utf-8 -*-
"""
archiver.py — 报告归档器
命名规则：市场日报 → 市场日报_YYYYMMDD.md；个股 → 个股_代码_名称_YYYYMMDD.md。
归档时同步：① 写入 meta JSON（output/*/meta/，供迭代模块统计）② 更新目录索引 INDEX.md。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.utils.file_utils import DIRS, ensure_dir, write_json, write_text


def _update_index(folder: Path, entry_line: str, title: str):
    """在目录 INDEX.md 头部插入最新条目（保持新→旧排序，去重）。"""
    index = folder / "INDEX.md"
    lines = []
    if index.exists():
        lines = [l for l in index.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("# ") and l != entry_line]
    content = f"# {title}\n\n" + "\n".join([entry_line] + lines) + "\n"
    write_text(index, content)


def archive_daily_report(markdown: str, meta: dict) -> Path:
    """归档市场日报，返回报告路径。"""
    day = meta.get("date") or datetime.now().strftime("%Y%m%d")
    folder = ensure_dir(DIRS["output_daily"])
    path = folder / f"市场日报_{day}.md"
    write_text(path, markdown)
    write_json(folder / "meta" / f"市场日报_{day}.json", meta)
    _update_index(folder, f"- [{day} 市场日报](市场日报_{day}.md)", "市场日报索引")
    return path


def archive_stock_report(markdown: str, meta: dict) -> Path:
    """归档个股分析报告，返回报告路径。"""
    day = meta.get("date") or datetime.now().strftime("%Y%m%d")
    code = meta.get("code", "unknown")
    name = (meta.get("name") or "").replace("/", "-") or "未知"
    folder = ensure_dir(DIRS["output_stock"])
    path = folder / f"个股_{code}_{name}_{day}.md"
    write_text(path, markdown)
    write_json(folder / "meta" / f"个股_{code}_{day}.json", meta)
    _update_index(folder, f"- [{day} {name}（{code}）](个股_{code}_{name}_{day}.md)", "个股报告索引")
    return path


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
