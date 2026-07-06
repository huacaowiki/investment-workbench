# -*- coding: utf-8 -*-
"""
system_iter 迭代引擎测试。
核心回归：① 统计计算正确 ② 草案结构完整 ③ 绝不修改 config/ ④ 受保护原则不出修改建议。
"""
import hashlib
from pathlib import Path

import pytest

from src.analyzer import system_iter
from src.utils import file_utils
from src.utils.file_utils import DIRS, PROJECT_ROOT, load_config, write_json


def _config_digest() -> str:
    """config/ 四份YAML内容指纹，用于验证迭代前后一字未改。"""
    h = hashlib.sha256()
    for name in file_utils.CONFIG_FILES:
        h.update((DIRS["config"] / name).read_bytes())
    return h.hexdigest()


@pytest.fixture
def sandbox_output(tmp_path, monkeypatch):
    """输出目录指向临时目录（迭代报告写入不污染真实output）。"""
    for key in ["output_daily", "output_stock", "output_iteration"]:
        monkeypatch.setitem(file_utils.DIRS, key, tmp_path / key)
    return tmp_path


def seed_metas(tmp_path):
    """铺设两天日报 + 一份个股报告的meta，验证统计逻辑。"""
    write_json(tmp_path / "output_daily" / "meta" / "市场日报_20260601.json", {
        "type": "daily_market", "date": "20260601", "sentiment_score": 6.0,
        "index": {"上证指数": {"close": 3900, "pct": 0.5}},
        "top_sectors": ["光伏", "储能", "白酒"], "bottom_sectors": ["军工"],
        "effective_state": None, "data_errors": []})
    write_json(tmp_path / "output_daily" / "meta" / "市场日报_20260602.json", {
        "type": "daily_market", "date": "20260602", "sentiment_score": 4.0,
        "index": {"上证指数": {"close": 3920, "pct": 0.51}},   # 前日观察分6≥5且次日+0.51% → 方向一致
        "top_sectors": ["光伏", "银行", "煤炭"], "bottom_sectors": ["军工"],
        "effective_state": None, "data_errors": ["lhb"]})
    write_json(tmp_path / "output_stock" / "meta" / "个股_600519_20260615.json", {
        "type": "stock_report", "code": "600519", "name": "贵州茅台",
        "date": "20260615", "close": 1400.0, "pe_ttm": 20, "pe_pct": 0.05,
        "zone": "持有区", "overall": "股息组合：排除（存在门槛FAIL项）",
        "timing_auto_score_floor": 0, "manual_item_count": 11, "data_errors": []})


class TestStats:
    def test_daily_stats(self, sandbox_output):
        seed_metas(sandbox_output)
        metas = system_iter._load_metas(file_utils.DIRS["output_daily"], ["202606"])
        stats = system_iter.stat_daily_reports(metas)
        assert stats["覆盖天数"] == 2
        assert stats["数据缺失天数"] == 1
        assert stats["市场状态有人工判定的天数"] == 0
        assert stats["情绪观察分次日方向一致率"] == 1.0     # 唯一样本方向一致
        assert stats["领涨板块次日持续率"] == pytest.approx(1 / 3)   # 光伏留榜

    def test_stock_stats_offline(self, sandbox_output, monkeypatch):
        seed_metas(sandbox_output)
        monkeypatch.setattr(system_iter, "_latest_price", lambda code: 1190.0)
        metas = system_iter._load_metas(file_utils.DIRS["output_stock"], ["202606"])
        stats = system_iter.stat_stock_reports(metas)
        assert stats["报告数"] == 1
        dev = stats["估值偏差样本"][0]
        assert dev["区间涨跌"] == pytest.approx((1190 - 1400) / 1400)
        assert stats["止损止盈触发"][0]["触发"] == ["触及-15%清仓线"]   # -15%

    def test_period_months(self):
        assert system_iter._period_months("2026-06") == ["202606"]
        assert system_iter._period_months("2026-Q2", quarterly=True) == ["202604", "202605", "202606"]


class TestSafety:
    def test_iteration_never_touches_config(self, sandbox_output, monkeypatch):
        """最关键回归：跑一次月度迭代，config/ 四份YAML指纹必须一字不变。"""
        seed_metas(sandbox_output)
        monkeypatch.setattr(system_iter, "_latest_price", lambda code: None)
        before = _config_digest()
        path = system_iter.run_monthly_iteration("2026-06")
        assert _config_digest() == before
        assert Path(path).exists()
        assert str(file_utils.DIRS["output_iteration"]) in str(path)   # 只写output

    def test_drafts_have_required_fields(self, sandbox_output, monkeypatch):
        seed_metas(sandbox_output)
        monkeypatch.setattr(system_iter, "_latest_price", lambda code: None)
        config = load_config()
        metas_d = system_iter._load_metas(file_utils.DIRS["output_daily"], ["202606"])
        metas_s = system_iter._load_metas(file_utils.DIRS["output_stock"], ["202606"])
        drafts = system_iter.build_draft_revisions(
            config, system_iter.stat_daily_reports(metas_d),
            system_iter.stat_stock_reports(metas_s), ["202606"])
        for d in drafts:
            for field in ["类型", "位置", "修改前", "修改后", "理由", "数据支撑", "风险等级"]:
                assert field in d, f"草案缺字段 {field}"

    def test_protected_principles_never_modified(self, sandbox_output, monkeypatch):
        """受保护原则只允许出现在'不生成修改建议'的声明条目中。"""
        seed_metas(sandbox_output)
        monkeypatch.setattr(system_iter, "_latest_price", lambda code: None)
        config = load_config()
        drafts = system_iter.build_draft_revisions(config, {"覆盖天数": 0}, {"报告数": 0}, ["202606"])
        for d in drafts:
            if "防火墙" in str(d.get("修改前", "")) or "借钱" in str(d.get("修改前", "")):
                assert d["类型"] == "受保护原则（不生成修改建议）"

    def test_short_period_freezes_parameters(self, sandbox_output, monkeypatch):
        """§4.3：覆盖<6个月时必须包含参数冻结草案。"""
        monkeypatch.setattr(system_iter, "_latest_price", lambda code: None)
        config = load_config()
        drafts = system_iter.build_draft_revisions(config, {"覆盖天数": 0}, {"报告数": 0}, ["202606"])
        assert any(d["类型"] == "参数调整冻结提示" for d in drafts)

    def test_quarterly_runs(self, sandbox_output, monkeypatch):
        seed_metas(sandbox_output)
        monkeypatch.setattr(system_iter, "_latest_price", lambda code: None)
        before = _config_digest()
        path = system_iter.run_quarterly_iteration("2026-Q2")
        assert _config_digest() == before
        content = Path(path).read_text(encoding="utf-8")
        assert "全周期收益匹配度分析" in content
        assert "不会也不能自动修改 config/" in content
