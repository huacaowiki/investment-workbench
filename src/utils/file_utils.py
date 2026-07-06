# -*- coding: utf-8 -*-
"""
file_utils.py — 文件读写、路径管理、自动归档工具
所有模块统一通过本文件获取项目路径，避免硬编码；配置读取只读不写（铁则）。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import yaml

# 项目根目录 = 本文件向上三级（src/utils/file_utils.py → 根）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 常用目录集中定义，其他模块一律 from src.utils.file_utils import DIRS
DIRS = {
    "config": PROJECT_ROOT / "config",
    "config_history": PROJECT_ROOT / "config" / "history",
    "templates": PROJECT_ROOT / "templates",
    "knowledge_reference": PROJECT_ROOT / "knowledge" / "reference",
    "knowledge_drafts": PROJECT_ROOT / "knowledge" / "drafts",
    "output_daily": PROJECT_ROOT / "output" / "daily_market",
    "output_stock": PROJECT_ROOT / "output" / "stock_reports",
    "output_iteration": PROJECT_ROOT / "output" / "iteration",
    "data_raw": PROJECT_ROOT / "data" / "raw",
    "data_processed": PROJECT_ROOT / "data" / "processed",
    "archive": PROJECT_ROOT / "archive",
}

CONFIG_FILES = ["investment_system.yaml", "stock_selection.yaml",
                "valuation_model.yaml", "risk_control.yaml"]


def ensure_dir(path: Path) -> Path:
    """确保目录存在并返回该目录。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path | str, default: str | None = None) -> str | None:
    """读取文本文件；不存在时返回 default（不抛异常，避免链路闪退）。"""
    p = Path(path)
    if not p.exists():
        return default
    return p.read_text(encoding="utf-8")


def write_text(path: Path | str, content: str) -> Path:
    """写入文本文件（UTF-8，自动建父目录）。"""
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(content, encoding="utf-8")
    return p


def read_json(path: Path | str, default=None):
    """读取JSON；文件缺失或损坏时返回 default。"""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path | str, data, indent: int = 2) -> Path:
    """写入JSON（UTF-8、保留中文）。"""
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=indent, default=str),
                 encoding="utf-8")
    return p


def load_yaml(path: Path | str) -> dict:
    """读取YAML文件为dict；文件不存在时抛 FileNotFoundError（配置缺失属致命错误）。"""
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(name: str | None = None) -> dict:
    """
    读取铁则层配置。
    - load_config("risk_control") → 单份配置dict
    - load_config() → {"investment_system": {...}, "stock_selection": {...}, ...}
    注意：本函数只读。任何代码禁止写 config/ 目录（体系铁则）。
    """
    if name:
        fname = name if name.endswith(".yaml") else f"{name}.yaml"
        return load_yaml(DIRS["config"] / fname)
    return {f.replace(".yaml", ""): load_yaml(DIRS["config"] / f) for f in CONFIG_FILES}


def archive_file(src: Path | str, dest_dir: Path | str, timestamp: bool = True) -> Path:
    """把文件归档到指定目录（复制而非移动，原件保留）；可选加时间戳防覆盖。"""
    src, dest_dir = Path(src), Path(dest_dir)
    ensure_dir(dest_dir)
    name = src.name
    if timestamp:
        stem, suffix = src.stem, src.suffix
        name = f"{stem}_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
    dest = dest_dir / name
    shutil.copy2(src, dest)
    return dest


def today_str(fmt: str = "%Y%m%d") -> str:
    """当前日期字符串，报告命名统一入口。"""
    return datetime.now().strftime(fmt)
