# -*- coding: utf-8 -*-
"""
version_manager.py — config/ 配置版本管理
功能：
  1. backup_config(note)   把当前4份YAML快照到 config/history/<版本号>/ 并登记清单
  2. list_versions()       列出全部历史版本
  3. rollback(version_id)  回滚到指定版本（回滚前自动把当前配置先备份一次）
设计约束（铁则）：
  - 本模块是唯一被允许写 config/*.yaml 的代码路径，且只在用户显式执行 rollback 时发生；
  - 分析/迭代模块一律不得 import 本模块的 rollback。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from src.utils.file_utils import DIRS, CONFIG_FILES, ensure_dir, read_json, write_json

MANIFEST = DIRS["config_history"] / "manifest.json"


def _next_version_id() -> str:
    """版本号格式：v{序号:03d}_{YYYYMMDD_HHMMSS}，序号递增保证可排序。"""
    manifest = read_json(MANIFEST, default={"versions": []})
    seq = len(manifest["versions"]) + 1
    return f"v{seq:03d}_{datetime.now():%Y%m%d_%H%M%S}"


def backup_config(note: str = "") -> str:
    """
    备份当前 config/*.yaml 到 config/history/<版本号>/。
    返回版本号。note 用于记录本次备份原因（如"确认待确认项#4"）。
    """
    version_id = _next_version_id()
    dest = ensure_dir(DIRS["config_history"] / version_id)
    backed = []
    for fname in CONFIG_FILES:
        src = DIRS["config"] / fname
        if src.exists():
            shutil.copy2(src, dest / fname)
            backed.append(fname)

    manifest = read_json(MANIFEST, default={"versions": []})
    manifest["versions"].append({
        "version_id": version_id,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note or "手动备份",
        "files": backed,
    })
    write_json(MANIFEST, manifest)
    return version_id


def list_versions() -> list[dict]:
    """返回历史版本清单（时间正序）。"""
    return read_json(MANIFEST, default={"versions": []})["versions"]


def rollback(version_id: str) -> dict:
    """
    回滚 config/ 到指定历史版本。
    安全机制：回滚前先把"当前配置"自动备份一份（note标注），保证任何回滚可再回滚。
    返回 {"restored": [...], "safety_backup": 版本号}
    """
    src_dir = DIRS["config_history"] / version_id
    if not src_dir.exists():
        available = [v["version_id"] for v in list_versions()]
        raise ValueError(f"版本 {version_id} 不存在。可用版本：{available}")

    safety = backup_config(note=f"回滚到 {version_id} 前的自动安全备份")

    restored = []
    for fname in CONFIG_FILES:
        src = src_dir / fname
        if src.exists():
            shutil.copy2(src, DIRS["config"] / fname)
            restored.append(fname)

    manifest = read_json(MANIFEST, default={"versions": []})
    manifest["versions"].append({
        "version_id": _next_version_id(),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": f"执行回滚：恢复到 {version_id}",
        "files": restored,
        "rollback_of": version_id,
    })
    write_json(MANIFEST, manifest)
    return {"restored": restored, "safety_backup": safety}
