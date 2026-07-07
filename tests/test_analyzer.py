# -*- coding: utf-8 -*-
"""
分析引擎测试（v4.2.0全自动口径）：用合成快照验证规则判定与 config 完全一致（不依赖网络）。
覆盖：一票否决（公告扫描推定）/ 股息门槛（派现比例/分红趋势/收现净现自动）/
     择时门槛与评分（watchlist/4项自动分）/ 估值分级 / 风控参数 / 市场状态初判 / 报告渲染。
"""
import pytest

from src.analyzer import market_analyzer, stock_analyzer
from src.analyzer.stock_analyzer import FAIL, MISSING, PASS
from src.generator.report_writer import render_daily_report, render_stock_report
from src.utils.file_utils import load_config

CONFIG = load_config()

CLEAN_SCAN = {
    "命中": {"audit": [], "regulatory": [], "lawsuit": [], "fraud": []},
    "近1年审计类命中": [], "近1年诉讼类命中": [],
    "扫描公告数": 400, "覆盖起点": "2023-07-10", "口径": "测试",
}


def make_snapshot(**overrides):
    """构造一份'全部达标'的合成个股快照，按需覆盖字段制造FAIL场景。"""
    snap = {
        "code": "600000",
        "basic": {"名称": "测试股份", "行业": "水电燃气制造业", "总市值": 5e10, "行业口径": "测试"},
        "quote": {"最新收盘价": 10.0, "较1年内高点回撤": 0.30, "MA60": 9.5,
                  "站上60日线": True, "站上60日线连续天数": 5,
                  "技术底部信号": {"signal": True, "RSI超卖近20日": True, "MACD零下金叉近15日": True},
                  "放量滞涨信号": {"signal": False, "口径": "测试"},
                  "近5日日均成交额": 3e8, "近20日日均成交额": 2.5e8},
        "valuation": {"PE_TTM": 8.0, "PB": 1.2, "股息率TTM": 0.055,
                      "PE历史分位": 0.10, "PB历史分位": 0.12, "分位窗口": "近10年（测试）"},
        "financials": {"报告年度": ["2023", "2024", "2025"],
                       "ROE近3年均值_pct": 15.0, "净利润近3年为正年数": 3,
                       "净利润近3年全为负": False,
                       "净利润最新同比": 0.05, "营收最新同比": 0.03,
                       "资产负债率_pct": 40.0, "EPS各年": [1.0, 1.1, 1.2]},
        "dividends": {"连续分红年数": 8, "近3年股息率记录_pct": [0.04, 0.05, 0.055],
                      "每股分红按年度": {"2023": 0.40, "2024": 0.45, "2025": 0.50}},
        "payout": {"近3年派现比例": [{"年度": "2025", "派现比例": 0.42}],
                   "近3年均值": 0.42, "最新年度派息率": 0.42, "口径": "测试"},
        "cashflow_ratios": {"收现比_最新": 0.95, "净现比_最新": 0.90, "各年": []},
        "margin_signal": {"最新融资余额": 1.2e9, "10日前融资余额": 1.0e9, "signal": True,
                          "口径": "测试"},
        "announcement_scan": CLEAN_SCAN,
        "pledge_ratio": 0.05,
        "announcements": [],
        "peers": {"行业均值PE": 12.0, "行业均值PE口径": "测试"},
        "institution_forecast": {"预测EPS": [{"年度": "2027", "均值": 1.8, "预测机构数": 10}],
                                 "口径": "测试"},
        "cn10y_yield": 0.02,
        "errors": {},
    }
    snap.update(overrides)
    return snap


def get_check(checks, keyword):
    return next(c for c in checks if keyword in c["item"])


@pytest.fixture
def in_pool(monkeypatch):
    """备选池成员资格 mock（watchlist机制）。"""
    monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda code: True)


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

    def test_clean_scan_presumed_pass(self):
        """公告扫描无命中 → 审计/监管/造假推定通过，detail标注推定口径。"""
        veto = stock_analyzer.evaluate_veto(make_snapshot(), CONFIG)
        audit = get_check(veto, "审计意见")
        assert audit["status"] == PASS and "推定" in audit["detail"]
        assert get_check(veto, "监管函")["status"] == PASS

    def test_audit_keyword_hit_fails(self):
        scan = {**CLEAN_SCAN,
                "近1年审计类命中": [{"标题": "关于保留意见审计报告的公告", "时间": "2026-05-01"}]}
        veto = stock_analyzer.evaluate_veto(make_snapshot(announcement_scan=scan), CONFIG)
        assert get_check(veto, "审计意见")["status"] == FAIL

    def test_concept_stock_quant(self):
        snap = make_snapshot()
        snap["financials"]["净利润近3年全为负"] = True
        veto = stock_analyzer.evaluate_veto(snap, CONFIG)
        assert get_check(veto, "纯概念股")["status"] == FAIL

    def test_scan_missing_is_missing_not_pass(self):
        veto = stock_analyzer.evaluate_veto(make_snapshot(announcement_scan=None), CONFIG)
        assert get_check(veto, "公告合规扫描")["status"] == MISSING


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

    def test_payout_ratio_auto(self):
        """派现比例/派息率 v4.2.0 自动判定。"""
        d = stock_analyzer.evaluate_dividend_track(make_snapshot(), CONFIG)
        assert get_check(d["universal"], "派现比例")["status"] == PASS
        assert get_check(d["universal"], "派息率<80%")["status"] == PASS
        snap = make_snapshot(payout={"近3年均值": 0.20, "最新年度派息率": 0.85, "口径": "测试"})
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["universal"], "派现比例")["status"] == FAIL
        assert get_check(d["universal"], "派息率<80%")["status"] == FAIL

    def test_category_c_stable_full_auto(self):
        """C类：ROE12%+收现比90%+净现比80%+分红非递减全自动。"""
        d = stock_analyzer.evaluate_dividend_track(make_snapshot(), CONFIG)
        assert all(c["status"] == PASS for c in d["category"]), d["category"]
        snap = make_snapshot()
        snap["financials"]["ROE近3年均值_pct"] = 11.0   # C类要求≥12%
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["category"], "ROE")["status"] == FAIL

    def test_cashflow_ratio_thresholds(self):
        snap = make_snapshot(cashflow_ratios={"收现比_最新": 0.85, "净现比_最新": 0.75, "各年": []})
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)   # C类要求0.9/0.8
        assert get_check(d["category"], "收现比")["status"] == FAIL
        assert get_check(d["category"], "净现比")["status"] == FAIL

    def test_dividend_trend_non_decreasing(self):
        snap = make_snapshot()
        snap["dividends"]["每股分红按年度"] = {"2023": 0.50, "2024": 0.45, "2025": 0.40}  # 递减
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["category"], "分红趋势")["status"] == FAIL

    def test_missing_data_never_passes(self):
        snap = make_snapshot(basic={"名称": "测试股份", "行业": "水电", "总市值": None})
        d = stock_analyzer.evaluate_dividend_track(snap, CONFIG)
        assert get_check(d["universal"], "市值")["status"] == MISSING
        assert "观察" in d["verdict"] or "排除" in d["verdict"]

    def test_all_pass_verdict_candidate(self):
        d = stock_analyzer.evaluate_dividend_track(make_snapshot(), CONFIG)
        assert d["verdict"].startswith("候选池")


class TestTimingTrack:
    def test_full_pass_with_score(self, in_pool):
        """门槛全过 + 自动评分：技术底部1+右侧1+资金1+机构预期1 = 4分 ≥3 → 可进决策卡片流程。"""
        t = stock_analyzer.evaluate_timing_track(make_snapshot(), CONFIG, "B_neutral")
        assert all(g["status"] == PASS for g in t["gates"]), t["gates"]
        assert t["auto_score_floor"] == 4
        assert "决策卡片" in t["verdict"]

    def test_score_below_3_observe_only(self, in_pool):
        """裁决#6严格口径：评分<3仅观察。"""
        snap = make_snapshot()
        snap["quote"]["技术底部信号"] = {"signal": False}
        snap["quote"]["站上60日线连续天数"] = 1
        snap["margin_signal"] = {"signal": False, "最新融资余额": 1e9, "10日前融资余额": 1.1e9}
        snap["institution_forecast"] = None
        t = stock_analyzer.evaluate_timing_track(snap, CONFIG, "B_neutral")
        assert t["auto_score_floor"] == 0
        assert "仅观察" in t["verdict"]

    def test_not_in_watchlist_fails_gate(self, monkeypatch):
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda code: False)
        t = stock_analyzer.evaluate_timing_track(make_snapshot(), CONFIG, "B_neutral")
        assert get_check(t["gates"], "备选池")["status"] == FAIL
        assert "不满足" in t["verdict"]

    def test_c_state_blocks_buy(self, in_pool):
        t = stock_analyzer.evaluate_timing_track(make_snapshot(), CONFIG, "C_overvalued")
        assert get_check(t["gates"], "市场非C区")["status"] == FAIL

    def test_drawdown_below_25_fails(self, in_pool):
        snap = make_snapshot()
        snap["quote"]["较1年内高点回撤"] = 0.10
        t = stock_analyzer.evaluate_timing_track(snap, CONFIG, "B_neutral")
        assert get_check(t["gates"], "回撤")["status"] == FAIL

    def test_forecast_cagr(self):
        """机构预期CAGR：EPS 1.2(2025实际)→1.8(2027预测) = 22.5%年化 ≥15%。"""
        cagr = stock_analyzer._forecast_cagr(make_snapshot())
        assert cagr == pytest.approx((1.8 / 1.2) ** 0.5 - 1)
        assert cagr >= 0.15


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

    def test_unknown_industry_defaults_conservative(self):
        cls = stock_analyzer.classify_industry("某种未知行业")
        assert cls["category"] == "C_stable"     # 兜底最严ROE门槛
        assert cls["circle"] is None             # → risk_params按卫星减半


class TestMarketAnalyzer:
    def make_market_snapshot(self, sh_close=2950.0, cs_pe=19.4, cn10y=0.0174,
                             turnover_history=None, sh_high=4100.0):
        return {
            "date": "20260703",
            "index_spot": [{"名称": "上证指数", "最新价": sh_close, "涨跌幅": -1.2,
                            "成交额": 3.2e11, "成交量": 1}],
            "board_ranks": [{"板块名称": f"板块{i}", "涨跌幅": i - 5} for i in range(10)],
            "limit_stats": {"涨停家数": 30, "跌停家数": 10, "涨停代表": [], "跌停代表": []},
            "market_activity": [{"item": "上涨", "value": 3000}, {"item": "下跌", "value": 2000}],
            "margin_summary": {"沪市融资余额_最新": 8e11, "沪市10日变化率": 0.01,
                               "深市融资余额_最新": 7e11, "两市融资余额_最新": 1.5e12,
                               "沪市融资余额序列": [], "口径": "测试"},
            "lhb": [],
            "volatility_gauge": {"数值": 0.40, "指标": "QVIX(测试)", "口径": "测试"},
            "csindex_pe": {"市盈率1": cs_pe, "日期": "2026-06-09", "口径": "测试"},
            "sh_index_high": {"历史最高收盘": sh_high},
            "turnover_history": turnover_history or [],
            "cn10y_yield": cn10y, "errors": {},
        }

    def test_condition_sh_below_3000(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_analyzer, "MARKET_STATE_FILE", tmp_path / "none.json")
        check = market_analyzer.check_market_state(self.make_market_snapshot(), CONFIG)
        cond = next(c for c in check["conditions"] if "3000" in c["条件"])
        assert cond["程序判定"] is True

    def test_auto_state_b_band_by_spread(self, tmp_path, monkeypatch):
        """PE19.4 国债1.74% → 利差3.41% → B偏高带。"""
        monkeypatch.setattr(market_analyzer, "MARKET_STATE_FILE", tmp_path / "none.json")
        check = market_analyzer.check_market_state(
            self.make_market_snapshot(sh_close=3500.0), CONFIG)
        assert check["equity_bond_spread"] == pytest.approx(1 / 19.4 - 0.0174)
        assert check["auto_state"] == "B_high"
        assert check["effective_state"] == "B_high"   # 无人工记录→程序初判生效

    def test_auto_state_a_zone_three_hits(self, tmp_path, monkeypatch):
        """A区3条命中：上证<3000 + PE<22 + 连续10日<7000亿。"""
        monkeypatch.setattr(market_analyzer, "MARKET_STATE_FILE", tmp_path / "none.json")
        hist = [{"date": f"202606{d:02d}", "turnover": 6e11} for d in range(1, 11)]
        check = market_analyzer.check_market_state(
            self.make_market_snapshot(turnover_history=hist), CONFIG)
        assert check["a_hits"] >= 3
        assert check["auto_state"] == "A_undervalued"

    def test_manual_state_overrides_auto(self, tmp_path, monkeypatch):
        """7日内人工判定优先于程序初判。"""
        from datetime import datetime
        state_file = tmp_path / "state.json"
        monkeypatch.setattr(market_analyzer, "MARKET_STATE_FILE", state_file)
        from src.utils.file_utils import write_json
        write_json(state_file, {"state": "B_neutral", "source": "manual",
                                "date": datetime.now().strftime("%Y-%m-%d")})
        check = market_analyzer.check_market_state(self.make_market_snapshot(), CONFIG)
        assert check["effective_state"] == "B_neutral"
        assert "人工判定" in check["note"]

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
        assert "融资余额" in md                    # 两融替代北向
        assert "北向" not in md.replace("北向资金数据已停发", "")   # 仅允许口径删除说明出现
        assert meta["type"] == "daily_market"
        assert meta["auto_state"] is not None


class TestRenderStock:
    def test_render_stock_smoke(self, in_pool):
        snap = make_snapshot()
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        md, meta = render_stock_report(snap, result)
        assert "【模板占位符" not in md
        assert "免责声明" in md
        assert "一票否决" in md and "择时组合视角" in md
        assert "待人工" not in md                  # v4.2.0：不保留人工确认项
        assert meta["code"] == "600000"
        assert meta["timing_auto_score"] == 4
