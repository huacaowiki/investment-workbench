# -*- coding: utf-8 -*-
"""
分析引擎测试：用合成快照验证规则判定与 config 完全一致（不依赖网络）。
覆盖：一票否决 / 股息门槛 / 择时门槛与评分保守口径 / 估值分级 / 风控参数 / 报告渲染。
"""
import pytest

from src.analyzer import market_analyzer, stock_analyzer
from src.analyzer.stock_analyzer import FAIL, MANUAL, MISSING, PASS
from src.generator.report_writer import render_daily_report, render_stock_report
from src.utils.file_utils import load_config

CONFIG = load_config()


def make_snapshot(**overrides):
    """构造一份'全部达标'的合成个股快照，按需覆盖字段制造FAIL场景。"""
    snap = {
        "code": "600000",
        "basic": {"名称": "测试股份", "行业": "水电燃气", "总市值": 5e10, "行业口径": "测试"},
        "quote": {"最新收盘价": 10.0, "较1年内高点回撤": 0.30, "MA60": 9.5,
                  "站上60日线": True, "近5日日均成交额": 3e8, "近20日日均成交额": 2.5e8},
        "valuation": {"PE_TTM": 8.0, "PB": 1.2, "股息率TTM": 0.055,
                      "PE历史分位": 0.10, "PB历史分位": 0.12, "分位窗口": "近10年（测试）"},
        "financials": {"ROE近3年均值_pct": 15.0, "净利润近3年为正年数": 3,
                       "净利润最新同比": 0.05, "营收最新同比": 0.03,
                       "资产负债率_pct": 40.0, "收现比_pct": None, "净现比_pct": None},
        "dividends": {"连续分红年数": 8, "近3年股息率记录_pct": [0.04, 0.05, 0.055]},
        "pledge_ratio": 0.05,
        "announcements": [],
        "peers": {"行业均值PE": 12.0, "行业均值PE口径": "测试"},
        "institution_forecast": None,
        "cn10y_yield": 0.02,
        "errors": {},
    }
    snap.update(overrides)
    return snap


def get_check(checks, keyword):
    return next(c for c in checks if keyword in c["item"])


class TestVeto:
    def test_st_stock_fails(self):
        snap = make_snapshot(basic={"名称": "ST测试", "行业": "银行", "总市值": 5e10})
        veto = stock_analyzer.evaluate_veto(snap, CONFIG)
        assert get_check(veto, "ST")["status"] == FAIL
        result = stock_analyzer.analyze_stock(snap, CONFIG)
        assert result["overall"].startswith("排除：命中一票否决")

    def test_pledge_over_30_fails(self):
        veto = stock_analyzer.evaluate_veto(make_snapshot(pledge_ratio=0.35), CONFIG)
        assert get_check(veto, "质押率")["status"] == FAIL

    def test_audit_always_manual(self):
        veto = stock_analyzer.evaluate_veto(make_snapshot(), CONFIG)
        assert get_check(veto, "审计意见")["status"] == MANUAL


class TestDividendTrack:
    def test_yield_floor_absolute_3pct(self):
        """§2.2：股息率须≥max(1.5×国债, 3%)——国债1.74%时门槛应为3%绝对底线。"""
        snap = make_snapshot(cn10y_yield=0.0174)
        snap["valuation"]["股息率TTM"] = 0.025    # 2.5% < 3%
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["universal"], "股息率≥max")["status"] == FAIL
        snap["valuation"]["股息率TTM"] = 0.031
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["universal"], "股息率≥max")["status"] == PASS

    def test_market_cap_threshold(self):
        snap = make_snapshot()
        snap["basic"]["总市值"] = 2.9e10          # 290亿 < 300亿
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["universal"], "市值")["status"] == FAIL
        assert d["verdict"].startswith("排除")

    def test_category_c_stable_roe12(self):
        snap = make_snapshot()
        snap["financials"]["ROE近3年均值_pct"] = 11.0   # C类要求≥12%
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["category"], "ROE")["status"] == FAIL

    def test_missing_data_never_passes(self):
        snap = make_snapshot(basic={"名称": "测试股份", "行业": "水电", "总市值": None})
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["universal"], "市值")["status"] == MISSING
        assert "观察" in d["verdict"] or "排除" in d["verdict"]

    def test_valuation_buy_three_choose_one(self):
        d = stock_analyzer.evaluate_dividend_valuation(make_snapshot(), CONFIG)
        assert d["met"] is True    # PE 8<行业12 且分位10%；PB 1.2≤2.5分位12%；股息率5.5%≥2×2%


class TestTimingTrack:
    def test_gates_all_computable_pass(self):
        t = stock_analyzer.evaluate_timing_track(make_snapshot(), CONFIG, "B_neutral")
        gates = {g["item"]: g["status"] for g in t["gates"]}
        assert gates["较1年内高点回撤≥25%"] == PASS
        assert gates["市场非C区"] == PASS

    def test_c_state_blocks_buy(self):
        t = stock_analyzer.evaluate_timing_track(make_snapshot(), CONFIG, "C_overvalued")
        assert get_check(t["gates"], "市场非C区")["status"] == FAIL
        assert "不满足" in t["verdict"]

    def test_drawdown_below_25_fails(self):
        snap = make_snapshot()
        snap["quote"]["较1年内高点回撤"] = 0.10
        t = stock_analyzer.evaluate_timing_track(snap, CONFIG, "B_neutral")
        assert get_check(t["gates"], "回撤")["status"] == FAIL

    def test_manual_scores_default_zero_conservative(self):
        """待确认#5口径：人工评分项缺省0分；<3分不给建仓建议。"""
        t = stock_analyzer.evaluate_timing_track(make_snapshot(), CONFIG, "B_neutral")
        assert t["auto_score_floor"] <= 2
        assert "观察" in t["verdict"] or "补核" in t["verdict"]


class TestZoneAndRisk:
    def test_sell_zone_on_pe70(self):
        snap = make_snapshot()
        snap["valuation"]["PE历史分位"] = 0.75
        dv = stock_analyzer.evaluate_dividend_valuation(snap, CONFIG)
        z = stock_analyzer.price_zone(snap, dv, CONFIG)
        assert z["zone"].startswith("减持区")

    def test_excluded_circle_no_position(self):
        r = stock_analyzer.risk_params(make_snapshot(), CONFIG, "excluded")
        assert r["股息组合单票上限"] is None
        assert "禁止建仓" in r["能力圈判定"]

    def test_satellite_halves_caps(self):
        r = stock_analyzer.risk_params(make_snapshot(), CONFIG, "satellite")
        assert r["股息组合单票上限"] == pytest.approx(0.075)   # 15% × 0.5
        assert r["择时组合单票上限"] == pytest.approx(0.125)   # 25% × 0.5

    def test_stop_loss_prices(self):
        r = stock_analyzer.risk_params(make_snapshot(), CONFIG, "core")
        assert r["止损参考价"]["-10%减仓50%"] == 9.0
        assert r["止损参考价"]["-15%无条件清仓"] == 8.5


class TestMarketAnalyzer:
    def make_market_snapshot(self):
        return {
            "date": "20260703",
            "index_spot": [{"名称": "上证指数", "最新价": 2950.0, "涨跌幅": -1.2,
                            "成交额": 3.2e11, "成交量": 1}],
            "board_ranks": [{"板块名称": f"板块{i}", "涨跌幅": i - 5} for i in range(10)],
            "limit_stats": {"涨停家数": 30, "跌停家数": 10, "涨停代表": [], "跌停代表": []},
            "market_activity": [{"item": "上涨", "value": 3000}, {"item": "下跌", "value": 2000}],
            "hsgt_flow": [], "lhb": [],
            "hs300_volatility": {"近20日年化波动率": 0.40},
            "cn10y_yield": 0.02, "errors": {},
        }

    def test_state_conditions_sh_below_3000(self):
        check = market_analyzer.check_market_state(self.make_market_snapshot(), CONFIG)
        cond = next(c for c in check["conditions"] if "3000" in c["条件"])
        assert cond["程序判定"] is True

    def test_never_auto_decides_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_analyzer, "MARKET_STATE_FILE", tmp_path / "none.json")
        check = market_analyzer.check_market_state(self.make_market_snapshot(), CONFIG)
        assert check["effective_state"] is None   # 程序绝不拍板

    def test_vol_over_35_triggers_p1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_analyzer, "MARKET_STATE_FILE", tmp_path / "none.json")
        analysis = market_analyzer.analyze_market(self.make_market_snapshot(), CONFIG)
        assert any(a["级别"] == "P1" and "严格风控" in a["内容"] for a in analysis["alerts"])

    def test_render_daily_smoke(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_analyzer, "MARKET_STATE_FILE", tmp_path / "none.json")
        analysis = market_analyzer.analyze_market(self.make_market_snapshot(), CONFIG)
        md, meta = render_daily_report(analysis)
        assert "【模板占位符" not in md            # 模板占位符全部有内容
        assert "免责声明" in md
        assert meta["type"] == "daily_market"


class TestRenderStock:
    def test_render_stock_smoke(self):
        snap = make_snapshot()
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        md, meta = render_stock_report(snap, result)
        assert "【模板占位符" not in md
        assert "免责声明" in md
        assert "一票否决" in md and "择时组合视角" in md
        assert meta["code"] == "600000"
