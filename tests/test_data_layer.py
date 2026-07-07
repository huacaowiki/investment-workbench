# -*- coding: utf-8 -*-
"""
数据层测试（mock akshare，不依赖网络——异常兜底与缓存机制是重点）。
真实拉数验证见 tests/test_live_integration.py（标记 live，默认跳过）。
"""
import time

import pandas as pd
import pytest

from src.data import market_data, stock_data
from src.data.data_utils import load_cache, save_cache
from src.utils import file_utils


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """缓存与processed目录指向临时目录，避免污染真实数据；重试退避归零加速测试。"""
    monkeypatch.setitem(file_utils.DIRS, "data_raw", tmp_path / "raw")
    monkeypatch.setitem(file_utils.DIRS, "data_processed", tmp_path / "processed")
    monkeypatch.setattr(market_data, "RETRY_BACKOFF", 0)
    monkeypatch.setattr(market_data, "TURNOVER_HISTORY_FILE",
                        tmp_path / "processed" / "turnover_history.json")


@pytest.fixture
def frozen_closed_day(monkeypatch):
    """把 market_data 的时钟固定在 2026-07-03 16:00（收盘后），使日期相关分支可预测。"""
    from datetime import datetime as real_dt

    class FakeDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 7, 3, 16, 0, 0)

    monkeypatch.setattr(market_data, "datetime", FakeDT)
    return "20260703"


class TestFetchWrapper:
    def test_success_and_cache(self):
        calls = {"n": 0}

        def fake():
            calls["n"] += 1
            return {"v": 1}

        data1, err1 = market_data._fetch("unit_test_item", fake)
        data2, err2 = market_data._fetch("unit_test_item", fake)   # 应命中缓存
        assert data1 == data2 == {"v": 1}
        assert err1 is None and err2 is None
        assert calls["n"] == 1, "第二次调用必须走缓存，不得重复请求"

    def test_failure_returns_error_not_raise(self):
        def boom():
            raise ConnectionError("接口超时")

        data, err = market_data._fetch("unit_test_fail", boom)
        assert data is None
        assert "ConnectionError" in err

    def test_ttl_expiry(self):
        save_cache("unit_ttl", {"v": 1})
        assert load_cache("unit_ttl", max_age_seconds=3600) == {"v": 1}
        assert load_cache("unit_ttl", max_age_seconds=0) is None or True
        # 精确验证：把时间戳做旧
        import json
        from src.data.data_utils import cache_path
        p = cache_path("unit_ttl")
        obj = json.loads(p.read_text(encoding="utf-8"))
        obj["_cached_at"] = time.time() - 7200
        p.write_text(json.dumps(obj), encoding="utf-8")
        assert load_cache("unit_ttl", max_age_seconds=3600) is None


class TestMarketSnapshot:
    def test_snapshot_all_sources_down_degrades_gracefully(self, monkeypatch, frozen_closed_day):
        """全部数据源失败时：不闪退，errors 完整登记，各项为 None。"""
        import akshare as ak
        for fn in ["stock_zh_index_spot_em", "stock_board_industry_name_em",
                   "stock_zt_pool_em", "stock_zt_pool_dtgc_em",
                   "stock_market_activity_legu", "stock_margin_sse",
                   "stock_margin_detail_szse", "index_option_300etf_qvix",
                   "stock_zh_index_value_csindex",
                   "stock_lhb_detail_em", "index_zh_a_hist", "bond_zh_us_rate",
                   # 备源也必须封死，才能验证"彻底断网"场景
                   "stock_zh_index_spot_sina", "stock_sector_spot", "stock_zh_index_daily"]:
            monkeypatch.setattr(ak, fn,
                                lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
                                raising=False)
        snap = market_data.get_market_snapshot(day="20260703", refresh=True)
        assert snap["date"] == "20260703"
        assert snap["index_spot"] is None
        assert len(snap["errors"]) == 10   # 10个数据项全部登记失败

    def test_snapshot_partial_success(self, monkeypatch, frozen_closed_day):
        """指数接口正常、其余失败：正常项有数据，失败项不影响；收盘后成交额入历史序列。"""
        import akshare as ak
        idx_df = pd.DataFrame({
            "名称": ["上证指数", "创业板指"],
            "最新价": [3948.55, 3247.52],
            "涨跌幅": [1.46, 1.96],
            "成交额": [8.972e11, 5.105e11],
            "成交量": [1, 1],
        })
        monkeypatch.setattr(ak, "stock_zh_index_spot_em", lambda **k: idx_df, raising=False)
        for fn in ["stock_board_industry_name_em", "stock_zt_pool_em",
                   "stock_zt_pool_dtgc_em", "stock_market_activity_legu",
                   "stock_margin_sse", "stock_margin_detail_szse", "stock_lhb_detail_em",
                   "index_option_300etf_qvix", "stock_zh_index_value_csindex",
                   "index_zh_a_hist", "bond_zh_us_rate",
                   "stock_sector_spot", "stock_zh_index_daily"]:
            monkeypatch.setattr(ak, fn,
                                lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
                                raising=False)
        snap = market_data.get_market_snapshot(day="20260703", refresh=True)
        assert snap["index_spot"][0]["名称"] == "上证指数"
        assert "index_spot" not in snap["errors"]
        assert "board_ranks" in snap["errors"]
        # 成交额历史序列随快照累积（上证+深成，本例仅上证一条也应入账）
        assert snap["turnover_history"][-1]["date"] == "20260703"


class TestHistoricalIntegrity:
    """历史数据定格保护（v4.2.0修复：盘中TTL不得污染历史缓存与成交额序列）。"""

    def test_past_day_snapshot_never_refetches_intraday_items(self, monkeypatch):
        """历史日期无收盘缓存时，指数快照按缺失处理，绝不调实时接口冒充。"""
        import akshare as ak
        calls = {"n": 0}

        def spy(**k):
            calls["n"] += 1
            return None

        monkeypatch.setattr(ak, "stock_zh_index_spot_em", spy, raising=False)
        monkeypatch.setattr(ak, "stock_zh_index_spot_sina", spy, raising=False)
        data, err = market_data._fetch("market_index_spot", market_data._index_spot,
                                       day="20200101", ttl=market_data.INTRADAY_TTL)
        assert data is None
        assert "历史日期" in err
        assert calls["n"] == 0, "历史日期缺缓存时绝不能调用实时接口"

    def test_past_day_turnover_never_overwritten(self, frozen_closed_day):
        """历史日期已有定格成交额时，任何后续写入不得覆盖。"""
        from src.utils.file_utils import write_json
        write_json(market_data.TURNOVER_HISTORY_FILE,
                   {"history": [{"date": "20260701", "turnover": 3.0e12}]})
        result = market_data.update_turnover_history("20260701", 5.0e11)   # 尝试用错误值覆写
        assert result[-1]["turnover"] == 3.0e12   # 原值保持定格

    def test_intraday_partial_value_not_recorded(self, monkeypatch):
        """盘中（<15:05）当日成交额不入库。"""
        from datetime import datetime as real_dt

        class FakeDT(real_dt):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 7, 3, 10, 30, 0)   # 盘中

        monkeypatch.setattr(market_data, "datetime", FakeDT)
        result = market_data.update_turnover_history("20260703", 4.4e11)
        assert all(h["date"] != "20260703" for h in result)


class TestStockSnapshot:
    def test_stock_snapshot_offline_degrades(self, monkeypatch):
        """全部接口失败：快照仍返回、不闪退；扫描/两融类返回空口径而非异常。"""
        import akshare as ak
        for fn in ["stock_individual_info_em", "stock_zh_a_hist", "stock_a_indicator_lg",
                   "stock_financial_analysis_indicator", "stock_fhps_detail_em",
                   "stock_gpzy_pledge_ratio_em", "stock_zh_a_disclosure_report_cninfo",
                   "stock_board_industry_cons_em", "stock_profit_forecast_ths",
                   "stock_margin_detail_sse", "stock_margin_detail_szse",
                   "stock_financial_report_sina", "bond_zh_us_rate",
                   # 备源同样封死
                   "stock_zh_a_daily", "stock_profile_cninfo", "stock_zh_valuation_baidu"]:
            monkeypatch.setattr(ak, fn,
                                lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
                                raising=False)
        snap = stock_data.get_stock_snapshot("sh600519", refresh=True)
        assert snap["code"] == "600519"                    # 代码规整
        assert snap["basic"] is None
        assert "payout" in snap                            # 派现比例段恒存在（缺数时口径说明）
        assert snap["payout"]["近3年均值"] is None
        assert len(snap["errors"]) >= 8

    def test_quote_summary_math(self, monkeypatch):
        """行情摘要的回撤/均线/成交额计算正确性。"""
        import akshare as ak
        n = 300
        closes = [100.0] * (n - 1) + [75.0]     # 高点100，现价75 → 回撤25%
        hist = pd.DataFrame({"收盘": closes, "成交额": [2e8] * n,
                             "最高": closes, "最低": closes,
                             "日期": [f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]})
        monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **k: hist, raising=False)
        q = stock_data._quote_summary("600519")
        assert abs(q["较1年内高点回撤"] - 0.25) < 1e-9      # 择时门槛临界值
        assert q["近5日日均成交额"] == 2e8
        assert q["MA60"] is not None and q["站上60日线"] is False
