# -*- coding: utf-8 -*-
"""file_utils 与 version_manager 单元测试（用 tmp_path + monkeypatch 隔离，不碰真实 config）。"""
from pathlib import Path

import pytest

from src.utils import file_utils, version_manager
from src.utils.file_utils import (load_config, read_json, read_text, write_json,
                                  write_text)


class TestFileIO:
    def test_text_roundtrip(self, tmp_path):
        p = tmp_path / "sub" / "a.md"
        write_text(p, "你好")                      # 自动建父目录
        assert read_text(p) == "你好"

    def test_read_missing_returns_default(self, tmp_path):
        assert read_text(tmp_path / "nope.md", default="") == ""
        assert read_json(tmp_path / "nope.json", default={}) == {}

    def test_json_roundtrip(self, tmp_path):
        p = tmp_path / "x.json"
        write_json(p, {"中文": 1.5})
        assert read_json(p) == {"中文": 1.5}

    def test_broken_json_returns_default(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert read_json(p, default="fallback") == "fallback"


class TestLoadRealConfig:
    """铁则层配置必须永远可被程序读取（阶段一自检标准回归）。"""

    def test_all_four_configs_loadable(self):
        cfg = load_config()
        assert set(cfg) == {"investment_system", "stock_selection",
                            "valuation_model", "risk_control"}

    def test_key_thresholds_quantified(self):
        cfg = load_config()
        # 抽查核心量化阈值与 v4.1 原文一致
        assert cfg["risk_control"]["position_caps"]["timing"]["single_stock"] == 0.25
        assert cfg["risk_control"]["position_caps"]["dividend"]["single_industry"] == 0.30
        assert cfg["stock_selection"]["timing_portfolio"]["gate_conditions"]["rules"][2]["threshold"] == 0.25
        assert cfg["investment_system"]["portfolios"]["dividend"]["max_drawdown_tolerance"] == 0.15


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """把 DIRS 的 config 指到临时目录，version_manager 全部操作被隔离。"""
    cfg_dir = tmp_path / "config"
    hist_dir = cfg_dir / "history"
    cfg_dir.mkdir()
    for name in file_utils.CONFIG_FILES:
        (cfg_dir / name).write_text(f"version: original-{name}\n", encoding="utf-8")
    monkeypatch.setitem(file_utils.DIRS, "config", cfg_dir)
    monkeypatch.setitem(file_utils.DIRS, "config_history", hist_dir)
    monkeypatch.setattr(version_manager, "MANIFEST", hist_dir / "manifest.json")
    return cfg_dir


class TestVersionManager:
    def test_backup_creates_snapshot_and_manifest(self, isolated_config):
        vid = version_manager.backup_config(note="单测备份")
        versions = version_manager.list_versions()
        assert len(versions) == 1
        assert versions[0]["version_id"] == vid
        assert versions[0]["note"] == "单测备份"
        assert set(versions[0]["files"]) == set(file_utils.CONFIG_FILES)
        snap = file_utils.DIRS["config_history"] / vid / "risk_control.yaml"
        assert "original" in snap.read_text(encoding="utf-8")

    def test_rollback_restores_and_keeps_safety_backup(self, isolated_config):
        vid = version_manager.backup_config(note="v1")
        # 模拟用户手动修改配置
        target = isolated_config / "risk_control.yaml"
        target.write_text("version: modified\n", encoding="utf-8")
        result = version_manager.rollback(vid)
        assert "risk_control.yaml" in result["restored"]
        assert "original" in target.read_text(encoding="utf-8")   # 已恢复
        # 回滚前的修改被安全备份，可再回滚
        safety_dir = file_utils.DIRS["config_history"] / result["safety_backup"]
        assert "modified" in (safety_dir / "risk_control.yaml").read_text(encoding="utf-8")

    def test_rollback_unknown_version_raises(self, isolated_config):
        with pytest.raises(ValueError, match="不存在"):
            version_manager.rollback("v999_none")
