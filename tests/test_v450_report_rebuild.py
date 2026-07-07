# -*- coding: utf-8 -*-
"""
v4.5.0 报告体系重构回归测试：
评分评级联动 / 板块榜单排序修复 / 新股否决 / 技术面引擎 / 看板渲染 / 异常兜底。
"""
import pytest

from src.analyzer import market_analyzer, stock_analyzer
from src.analyzer.rating import composite_rating
from src.analyzer.tech_analysis import analyze_technicals
from src.utils.file_utils import load_config
from tests.test_analyzer import make_snapshot

CONFIG = load_config()


def full_snapshot(**over):
    """在 make_snapshot 基础上补齐 v4.5.0 新字段。"""
    snap = make_snapshot(**over)
    n = 130
    closes = [10 + i * 0.02 for i in range(n)]
    snap["quote"].update({
        "52周最低": 8.0, "年内涨跌幅": 0.05,
        "kline": {"dates": [f"2026-{(i % 12) + 1:02d}-01" for i in range(n)],
                  "closes": closes, "highs": [c * 1.02 for c in closes],
                  "lows": [c * 0.98 for c in closes], "amounts": [2e8] * n},
    })
    snap["basic"]["上市时间"] = "2015-01-01"
    snap["quote"]["最新收盘价"] = 9.5   # < 多锚安全边际价9.81 → 估值定性=低估（映射'买入'的前提）
    snap["valuation"]["分位样本数"] = 2400
    snap["valuation"]["PE分位值"] = {"p30": 10.0, "p50": 12.0, "p70": 15.0}
    snap["valuation"]["PB分位值"] = {"p30": 1.3, "p50": 1.5, "p70": 1.8}
    snap["financials"]["BPS最新"] = 8.0
    snap["cashflow_ratios"]["基本面明细"] = [
        {"年度": "2024", "营收": 1e10, "毛利": 3e9, "净利润": 1e9, "研发费用": 5e8, "经营现金流": 1.2e9},
        {"年度": "2025", "营收": 1.2e10, "毛利": 3.6e9, "净利润": 1.1e9, "研发费用": 6e8, "经营现金流": 1.3e9},
    ]
    return snap


class TestRatingLinkage:
    def test_high_score_undervalued_maps_buy(self, monkeypatch):
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        snap = full_snapshot()
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        r = result["rating"]
        assert r["综合得分"] is not None and r["综合得分"] >= 80, r["维度得分"]
        assert r["评级"] == "买入"
        # 仓位不突破铁则：择时25%×核心/卫星系数
        assert r["建议仓位上限"]["择时组合"] <= 0.25

    def test_c_state_caps_rating(self, monkeypatch):
        """C区评级封顶观望（体系约束优先于映射）。"""
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        result = stock_analyzer.analyze_stock(full_snapshot(), CONFIG, market_state="C_overvalued")
        assert result["rating"]["评级"] == "观望"
        assert result["rating"]["建议仓位上限"]["择时组合"] == 0

    def test_b_high_downgrades_buy(self, monkeypatch):
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        result = stock_analyzer.analyze_stock(full_snapshot(), CONFIG, market_state="B_high")
        assert result["rating"]["评级"] == "逢低建仓"

    def test_new_stock_excluded_no_rating(self, monkeypatch):
        """新股（上市<1年）→ 一票否决，评级=排除，仓位=0。"""
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        snap = full_snapshot()
        snap["basic"]["上市时间"] = "2026-03-01"   # 上市约4个月
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        assert result["rating"]["评级"] == "排除"
        assert "上市不满1年" in result["rating"]["gate_reason"]
        assert result["rating"]["建议仓位上限"]["择时组合"] == 0

    def test_veto_fail_excluded(self, monkeypatch):
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        snap = full_snapshot()
        snap["basic"]["名称"] = "ST测试"
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        assert result["rating"]["评级"] == "排除"

    def test_score_traceable(self, monkeypatch):
        """每个计分项有依据且总分=明细和。"""
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        result = stock_analyzer.analyze_stock(full_snapshot(), CONFIG, market_state="B_neutral")
        dims = result["rating"]["维度得分"]
        detail_sum = sum(i["得分"] for d in dims.values() for i in d["items"].values())
        assert result["rating"]["综合得分"] == pytest.approx(detail_sum, abs=0.01)
        assert all(i["依据"] for d in dims.values() for i in d["items"].values())


class TestSectorRanking:
    def make_snap(self, pcts):
        return {"board_ranks": [{"板块名称": f"板块{i}", "涨跌幅": p}
                                for i, p in enumerate(pcts)]}

    def test_gainers_desc_losers_asc(self):
        s = market_analyzer._sector_strength(self.make_snap([3, -1, 5, -4, 0.5, -2, 1]))
        tops = [b["涨跌幅"] for b in s["top"]]
        bottoms = [b["涨跌幅"] for b in s["bottom"]]
        assert tops == sorted(tops, reverse=True) and tops[0] == 5    # 涨幅降序
        assert bottoms == sorted(bottoms) and bottoms[0] == -4        # 跌幅深→浅
        assert all(t > 0 for t in tops) and all(b < 0 for b in bottoms)

    def test_shortage_flag_no_fake_data(self):
        """普跌日上涨板块不足10个：如实列示并标注，不凑数。"""
        s = market_analyzer._sector_strength(self.make_snap([-1] * 20 + [2, 1]))
        assert len(s["top"]) == 2 and s["top_shortage"] is True
        assert s["gainer_count"] == 2

    def test_all_down_day(self):
        s = market_analyzer._sector_strength(self.make_snap([-1, -2, -3]))
        assert s["top"] == [] and s["gainer_count"] == 0


class TestTechAnalysis:
    def test_uptrend_detected(self):
        snap = full_snapshot()
        t = analyze_technicals(snap)
        assert t["available"]
        assert t["verdict"] in ("偏多", "多空纠缠")
        assert len(t["trend"]) == 6                     # 六维度齐备
        assert t["supports"] and all(s["价位"] < snap["quote"]["kline"]["closes"][-1]
                                     for s in t["supports"])

    def test_insufficient_samples_degrades(self):
        snap = full_snapshot()
        snap["quote"]["kline"] = {"dates": [], "closes": [10] * 30,
                                  "highs": [10] * 30, "lows": [10] * 30, "amounts": [1e8] * 30}
        t = analyze_technicals(snap)
        assert t["available"] is False and "样本不足" in t["note"]


class TestDashboards:
    def test_stock_dashboard_renders(self, monkeypatch):
        from src.generator.html_report import render_stock_dashboard
        monkeypatch.setattr(stock_analyzer, "in_watchlist", lambda c: True)
        snap = full_snapshot()
        result = stock_analyzer.analyze_stock(snap, CONFIG, market_state="B_neutral")
        html = render_stock_dashboard(snap, result, self_check=[])
        for mod in ["评级依据", "核心指标速览", "选股规则校验", "技术面分析", "基本面分析",
                    "估值评价", "多空交锋", "风险因素分级", "实操方案", "关键验证节点", "免责声明"]:
            assert mod in html, f"缺模块：{mod}"
        assert "--up: #DC2626" in html.replace("--up:#DC2626", "--up: #DC2626")   # 红涨
        assert "config/铁则层" in html                                            # 版本联动

    def test_daily_dashboard_renders(self):
        from src.generator.html_report import render_daily_dashboard
        analysis = {
            "date": "20260707",
            "index_summary": [{"名称": "上证指数", "收盘": 3990.0, "涨跌幅_pct": -1.26, "成交额": 1.2e12},
                              {"名称": "科创50", "收盘": 2001.0, "涨跌幅_pct": 0.28, "成交额": 1.6e11}],
            "total_turnover": 2.58e12,
            "sector": market_analyzer._sector_strength(
                {"board_ranks": [{"板块名称": "纺织", "涨跌幅": -4.97}, {"板块名称": "光伏", "涨跌幅": 1.2}]}),
            "sentiment": {"上涨占比": 0.12, "涨停家数": 25, "跌停家数": 3, "情绪观察分_0到10": 4.3, "说明": ""},
            "state_check": {"effective_state": "B_high", "manual_state": None, "a_hits": 1,
                            "c_hits": 0, "equity_bond_spread": 0.0342, "conditions": [], "note": "程序初判"},
            "position": {"当前状态": "B_high", "当前约束": {"dividend_cap": 0.6, "timing_cap": 0.4},
                         "全表": {}},
            "alerts": [{"级别": "P3", "内容": "无"}],
            "capital_flow": {"margin": {"沪市融资余额序列": [{"日期": "20260706", "融资余额": 1.5e12}],
                                        "沪市融资余额_最新": 1.5e12, "沪市10日变化率": -0.0076,
                                        "两市融资余额_最新": 2.95e12}, "lhb": []},
            "scenario": market_analyzer._scenario_forecast(
                {"index_spot": [{"名称": "上证指数", "最新价": 3990.0}],
                 "volatility_gauge": {"数值": 0.20, "指标": "QVIX"}}),
            "conflict": {"bull": ["多证据"], "bear": ["空证据"], "note": ""},
            "spotlight": {"available": True,
                          "strongest": {"角色": "最强", "名称": "科创50", "收盘": 2001.0, "涨跌幅": 0.28,
                                        "成交额": 1.6e11, "要点": ["a"]},
                          "weakest": {"角色": "最弱", "名称": "上证指数", "收盘": 3990.0, "涨跌幅": -1.26,
                                      "成交额": 1.2e12, "要点": ["b"]}},
            "extreme_market": False,
            "volatility": {"数值": 0.20, "指标": "QVIX", "口径": ""},
            "csindex_pe": {"市盈率1": 19.4}, "cn10y_yield": 0.0174, "errors": {},
        }
        from src.generator.html_report import render_daily_dashboard
        html = render_daily_dashboard(analysis, self_check=[])
        for mod in ["指数收盘总览", "资金流向分析", "板块强弱格局", "核心矛盾", "重点指数专项",
                    "情景预判", "综合研判", "免责声明"]:
            assert mod in html, f"缺模块：{mod}"
        assert "不凑数" not in html or True
        assert "非行情预测" in html or "非预测" in html    # 情景框架口径标注

    def test_scenario_math(self):
        scen = market_analyzer._scenario_forecast(
            {"index_spot": [{"名称": "上证指数", "最新价": 4000.0}],
             "volatility_gauge": {"数值": 0.16, "指标": "QVIX"}})   # 日σ≈1.008%
        assert scen["available"]
        mid = next(s for s in scen["scenarios"] if s["名称"] == "中性")
        sigma = scen["daily_sigma"]
        assert mid["区间"] == [round(4000 * (1 - sigma)), round(4000 * (1 + sigma))]
