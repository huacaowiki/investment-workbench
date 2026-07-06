# -*- coding: utf-8 -*-
"""data_utils 纯函数单元测试（不依赖网络）。"""
import math

from src.data.data_utils import (annualized_volatility, avg, drawdown_from_high,
                                 fmt_pct, fmt_yi, moving_average, normalize_stock_code,
                                 percentile_rank, safe_get, to_float, yoy_growth)


class TestToFloat:
    def test_normal(self):
        assert to_float("1,234.5") == 1234.5
        assert to_float(3) == 3.0

    def test_percent(self):
        assert math.isclose(to_float("12.3%"), 0.123)

    def test_missing(self):
        assert to_float("--") is None
        assert to_float(None) is None
        assert to_float("", default=0) == 0
        assert to_float(float("nan")) is None

    def test_garbage(self):
        assert to_float("abc", default=-1) == -1


class TestIndicators:
    def test_percentile_rank(self):
        series = list(range(1, 101))          # 1..100
        assert math.isclose(percentile_rank(series, 30), 0.30)
        assert percentile_rank([], 5) is None
        assert percentile_rank([1, 2, 3], None) is None

    def test_percentile_excludes_nonpositive(self):
        # 负PE（亏损期）应被剔除，不影响分位
        series = [-10, -5] + list(range(1, 101))
        assert math.isclose(percentile_rank(series, 30), 0.30)

    def test_drawdown(self):
        # 高点100 → 现价70：回撤30%，满足择时门槛≥25%
        assert math.isclose(drawdown_from_high([80, 100, 90, 70]), 0.30)
        assert drawdown_from_high([100]) is None

    def test_moving_average(self):
        assert moving_average([1] * 60 + [2] * 0, 60) == 1.0
        assert moving_average([1, 2], 60) is None

    def test_yoy(self):
        assert math.isclose(yoy_growth(120, 100), 0.20)
        assert math.isclose(yoy_growth(-50, -100), 0.50)   # 亏损收窄为正增长
        assert yoy_growth(1, 0) is None

    def test_volatility(self):
        flat = [100.0] * 30
        assert math.isclose(annualized_volatility(flat, 20), 0.0)
        assert annualized_volatility([1, 2], 20) is None

    def test_avg(self):
        assert avg([1, None, "3"]) == 2.0
        assert avg([]) is None


class TestFormat:
    def test_fmt_yi(self):
        assert fmt_yi(1.23e8) == "1.23亿"
        assert fmt_yi(None) == "—"

    def test_fmt_pct(self):
        assert fmt_pct(0.1234) == "12.34%"
        assert fmt_pct(0.05, signed=True) == "+5.00%"
        assert fmt_pct(None) == "—"


class TestMisc:
    def test_normalize_code(self):
        assert normalize_stock_code("sh600519") == "600519"
        assert normalize_stock_code("600519.SH") == "600519"
        assert normalize_stock_code("2594") == "002594"

    def test_safe_get(self):
        d = {"a": {"b": 1}}
        assert safe_get(d, "a", "b") == 1
        assert safe_get(d, "a", "x", default=9) == 9
        assert safe_get(d, "z") is None
