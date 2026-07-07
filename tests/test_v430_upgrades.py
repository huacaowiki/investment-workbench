# -*- coding: utf-8 -*-
"""
v4.3.0 三任务升级回归测试：
任务一：亏损股PE口径 / 新股分位降级 / C类同比子条件 / CAGR配对 / 风险矩阵 / 自检机制
任务二：多锚估值数学可验算 / 偏差>30%不加权 / 方法不可用降级
任务三：HTML生成（Claude样式内嵌）/ 多格式归档
"""
import pytest

from src.analyzer import multi_valuation, self_check, stock_analyzer
from src.analyzer.stock_analyzer import FAIL, MISSING, PASS
from src.utils.file_utils import load_config
from tests.test_analyzer import CLEAN_SCAN, get_check, make_snapshot

CONFIG = load_config()


class TestTask1EdgeCases:
    def test_negative_pe_never_passes_valuation(self):
        """漏洞修复#1：亏损股 PE=-5 不得因'负数≤30'误判满足估值条款。"""
        snap = make_snapshot()
        snap["valuation"]["PE_TTM"] = -5.0
        snap["valuation"]["PE历史分位"] = 0.0
        d = stock_analyzer.evaluate_dividend_valuation(snap, CONFIG)
        pe_item = get_check(d["items"], "PE≤行业均值")
        assert pe_item["status"] == FAIL and "亏损" in pe_item["detail"]
        t = stock_analyzer.evaluate_timing_track(snap, CONFIG, "B_neutral")
        gate = get_check(t["gates"], "估值三选一")
        assert "①不适用" in gate["detail"]

    def test_new_stock_percentile_downgrade(self):
        """漏洞修复#2：分位样本<250（新股）→ 分位条款降级为数据缺失并标注。"""
        snap = make_snapshot()
        snap["valuation"]["分位样本数"] = 120
        d = stock_analyzer.evaluate_dividend_valuation(snap, CONFIG)
        pe_item = get_check(d["items"], "PE≤行业均值")
        assert pe_item["status"] == MISSING
        assert "样本" in pe_item["detail"]

    def test_c_class_profit_decline_subcondition(self):
        """漏洞修复#3：C类'同比降幅<20%'子条件生效。"""
        snap = make_snapshot()
        snap["financials"]["净利润最新同比"] = -0.25    # 降幅25%
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        item = get_check(d["category"], "同比降幅")
        assert item["status"] == FAIL
        assert d["verdict"].startswith("排除")

    def test_cagr_pairing_with_missing_latest_eps(self):
        """漏洞修复#5：最新年EPS缺失时基期年份与EPS成对回退，CAGR年数正确。"""
        snap = make_snapshot()
        snap["financials"]["报告年度"] = ["2023", "2024", "2025"]
        snap["financials"]["EPS各年"] = [1.0, 1.2, None]   # 2025缺失→基期应回退到2024
        snap["institution_forecast"] = {"预测EPS": [{"年度": "2027", "均值": 1.8}]}
        cagr = stock_analyzer._forecast_cagr(snap)
        assert cagr == pytest.approx((1.8 / 1.2) ** (1 / 3) - 1)   # 2024→2027共3年

    def test_risk_matrix_four_categories(self):
        """漏洞修复#7：四类风险全覆盖，诉讼命中贯通到公司风险区。"""
        snap = make_snapshot()
        snap["announcement_scan"] = {**CLEAN_SCAN,
                                     "近1年诉讼类命中": [{"标题": "重大诉讼公告", "时间": "2026-06-01"}]}
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_high")
        rm = result["risk_matrix"]
        assert set(rm) == {"宏观", "行业", "公司", "估值"}
        assert any("诉讼" in r["内容"] and r["级别"] == "P1" for r in rm["公司"])
        assert any("B偏高" in r["内容"] for r in rm["宏观"])

    def test_zero_market_cap_is_missing_not_fail(self):
        """漏洞修复#6：市值0（数据异常）按MISSING处理。"""
        snap = make_snapshot()
        snap["basic"]["总市值"] = 0
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["universal"], "市值")["status"] == MISSING


class TestTask1SelfCheck:
    def test_clean_result_passes(self, monkeypatch):
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        snap = make_snapshot()
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        warnings = self_check.check_stock_result(result, snap, CONFIG)
        assert warnings == []

    def test_tampered_score_detected(self, monkeypatch):
        """总分与明细不一致 → P0预警。"""
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        snap = make_snapshot()
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        result["timing"]["auto_score_floor"] = 99
        warnings = self_check.check_stock_result(result, snap, CONFIG)
        assert any(w["级别"] == "P0" and "评分" in w["内容"] for w in warnings)

    def test_tampered_verdict_detected(self, monkeypatch):
        """有FAIL但结论未排除 → P0预警（结论自洽性）。"""
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        snap = make_snapshot()
        snap["valuation"]["股息率TTM"] = 0.01    # 制造股息率FAIL
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        result["dividend"]["verdict"] = "候选池：伪造的通过结论"
        warnings = self_check.check_stock_result(result, snap, CONFIG)
        assert any(w["级别"] == "P0" and "矛盾" in w["内容"] for w in warnings)

    def test_daily_hits_consistency(self):
        """日报自检：命中数与条件表判True数一致性。"""
        analysis = {
            "position": {"全表": {k: {} for k in
                                ["A_undervalued", "B_low", "B_neutral", "B_high", "C_overvalued"]}},
            "state_check": {"effective_state": "B_high", "a_hits": 5,   # 故意错
                            "c_hits": 0,
                            "conditions": [{"区": "A区", "条件": "x", "程序判定": True, "当前值": ""}]},
            "sentiment": {"情绪观察分_0到10": 5.0},
            "alerts": [{"级别": "P3", "内容": "x"}],
        }
        warnings = self_check.check_daily_result(analysis, CONFIG)
        assert any("A区命中数" in w["内容"] for w in warnings)


class TestTask2MultiValuation:
    def rich_snapshot(self):
        """构造估值输入齐备的快照（可验算）。"""
        snap = make_snapshot()
        snap["valuation"]["PE分位值"] = {"p30": 10.0, "p50": 12.0, "p70": 15.0}
        snap["valuation"]["PB分位值"] = {"p30": 1.0, "p50": 1.3, "p70": 1.8}
        snap["financials"]["BPS最新"] = 8.0
        return snap

    def test_val_class_mapping(self):
        assert multi_valuation.resolve_val_class("A_financial", "货币金融服务") == "A_financial"
        assert multi_valuation.resolve_val_class("B_cyclical", "煤炭开采") == "B_cyclical"
        assert multi_valuation.resolve_val_class("C_stable", "汽车制造业") == "G_growth"
        assert multi_valuation.resolve_val_class("C_stable", "酒、饮料制造") == "C_stable"

    def test_methods_math_verifiable(self):
        """估值计算可验算：PE分位法区间 = EPS×[p30, p50]，减持=EPS×p70。"""
        snap = self.rich_snapshot()
        mv = multi_valuation.multi_anchor_valuation(snap, CONFIG, "C_stable")
        assert mv["ok"] and mv["val_class"] == "C_stable"
        pe_m = next(m for m in mv["methods"] if m["方法"] == "PE历史分位法")
        assert pe_m["区间"] == pytest.approx([12.0, 14.4])   # EPS=1.2 × [p30,p50]
        assert pe_m["减持阈"] == pytest.approx(18.0)          # EPS × p70
        dv_m = next(m for m in mv["methods"] if m["方法"] == "股息率锚法")
        # DPS=0.50，cn10y=0.02：区间=[0.5/0.05, 0.5/0.04]=[10,12.5]，减持=0.5/0.02=25
        assert dv_m["区间"] == [10.0, 12.5]
        assert dv_m["减持阈"] == 25.0

    def test_combined_weighted_range(self):
        """C类三方法齐备且偏差≤30%时输出加权综合区间与安全边际价。"""
        snap = self.rich_snapshot()
        snap["valuation"]["PB分位值"] = {"p30": 1.3, "p50": 1.5, "p70": 1.8}   # 收敛三方法中值
        mv = multi_valuation.multi_anchor_valuation(snap, CONFIG, "C_stable")
        assert mv["usable_count"] == 3
        assert not mv["diverged"], f"偏差 {mv['divergence_pct']}"
        c = mv["combined"]
        # 加权低点 = .4×(1.2×10) + .35×10 + .25×(8×1.3) = 4.8+3.5+2.6 = 10.9
        assert c["合理区间"][0] == pytest.approx(10.9, abs=0.01)
        assert c["安全边际价"] == pytest.approx(10.9 * 0.9, abs=0.01)

    def test_divergence_over_30pct_no_weighting(self):
        """方法偏差>30% → 不加权，标注偏差原因。"""
        snap = self.rich_snapshot()
        snap["valuation"]["PE分位值"] = {"p30": 40.0, "p50": 50.0, "p70": 60.0}   # 人为拉大偏差
        mv = multi_valuation.multi_anchor_valuation(snap, CONFIG, "C_stable")
        assert mv["diverged"] is True
        assert mv["combined"] is None
        assert any("不强行加权" in n for n in mv["notes"])

    def test_loss_stock_pe_method_unusable(self):
        """亏损股：PE/PEG法标注不可用，不参与加权。"""
        snap = self.rich_snapshot()
        snap["financials"]["EPS各年"] = [-0.5, -0.3, -0.2]
        mv = multi_valuation.multi_anchor_valuation(snap, CONFIG, "C_stable")
        pe_m = next(m for m in mv["methods"] if m["方法"] == "PE历史分位法")
        assert pe_m["可用"] is False and "亏损" in pe_m["不可用原因"]

    def test_insufficient_methods_no_combined(self):
        """可用方法<2 → 不输出综合区间（防单一方法误导）。"""
        snap = make_snapshot()   # 无分位值/BPS → 仅股息锚可用
        mv = multi_valuation.multi_anchor_valuation(snap, CONFIG, "C_stable")
        assert mv["usable_count"] == 1
        assert mv["combined"] is None
        assert any("防止单一方法误导" in n for n in mv["notes"])


class TestTask3Formats:
    def test_html_generation_claude_style(self):
        from src.generator.html_writer import markdown_to_html
        md = "# 标题\n\n| A | B |\n|---|---|\n| +1.50% | -2.30% |\n\n> 提示"
        html = markdown_to_html(md, "测试报告")
        assert "<!DOCTYPE html>" in html
        assert "#4F46E5" in html and "#F7F7F8" in html       # Claude主色/背景
        assert 'class="up">+1.50%' in html                   # 涨跌着色
        assert 'class="down">-2.30%' in html
        assert "<table>" in html
        assert "仅研究分析" in html                            # 页脚免责
        assert "http" not in html.split("</style>")[0]       # CSS无外部依赖

    def test_archiver_multi_format(self, tmp_path, monkeypatch):
        from src.utils import file_utils
        from src.generator import archiver
        monkeypatch.setitem(file_utils.DIRS, "output_daily", tmp_path)
        meta = {"date": "20260707"}
        primary = archiver.archive_daily_report("# 测试日报\n\n内容", meta,
                                                output_format="html")
        assert primary.suffix == ".html" and primary.exists()
        assert (tmp_path / "市场日报_20260707.md").exists()     # MD永远落盘
        index = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
        assert "市场日报_20260707.html" in index                # 索引指向HTML

    def test_archiver_md_only(self, tmp_path, monkeypatch):
        from src.utils import file_utils
        from src.generator import archiver
        monkeypatch.setitem(file_utils.DIRS, "output_daily", tmp_path)
        primary = archiver.archive_daily_report("# 测试", {"date": "20260707"},
                                                output_format="md")
        assert primary.suffix == ".md"
        assert not (tmp_path / "市场日报_20260707.html").exists()
