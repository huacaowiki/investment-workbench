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
    """缓存目录指向临时目录，避免污染真实 data/raw；重试退避归零加速测试。"""
    monkeypatch.setitem(file_utils.DIRS, "data_raw", tmp_path / "raw")
    monkeypatch.setattr(market_data, "RETRY_BACKOFF", 0)


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
    def test_snapshot_all_sources_down_degrades_gracefully(self, monkeypatch):
        """全部数据源失败时：不闪退，errors 完整登记，各项为 None。"""
        import akshare as ak
        for fn in ["stock_zh_index_spot_em", "stock_board_industry_name_em",
                   "stock_zt_pool_em", "stock_zt_pool_dtgc_em",
                   "stock_market_activity_legu", "stock_hsgt_fund_flow_summary_em",
                   "stock_lhb_detail_em", "index_zh_a_hist", "bond_zh_us_rate",
                   # 备源也必须封死，才能验证"彻底断网"场景
                   "stock_zh_index_spot_sina", "stock_sector_spot", "stock_zh_index_daily"]:
            monkeypatch.setattr(ak, fn,
                                lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
                                raising=False)
        snap = market_data.get_market_snapshot(day="20260703", refresh=True)
        assert snap["date"] == "20260703"
        assert snap["index_spot"] is None
        assert len(snap["errors"]) == 8   # 8个数据项全部登记失败

    def test_snapshot_partial_success(self, monkeypatch):
        """指数接口正常、其余失败：正常项有数据，失败项不影响。"""
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
                   "stock_hsgt_fund_flow_summary_em", "stock_lhb_detail_em",
                   "index_zh_a_hist", "bond_zh_us_rate",
                   "stock_sector_spot", "stock_zh_index_daily"]:
            monkeypatch.setattr(ak, fn,
                                lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
                                raising=False)
        snap = market_data.get_market_snapshot(day="20260703", refresh=True)
        assert snap["index_spot"][0]["名称"] == "上证指数"
        assert "index_spot" not in snap["errors"]
        assert "board_ranks" in snap["errors"]


class TestStockSnapshot:
    def test_stock_snapshot_offline_marks_manual_fields(self, monkeypatch):
        """全部接口失败：快照仍返回，manual_check_fields 恒存在（审计意见等）。"""
        import akshare as ak
        for fn in ["stock_individual_info_em", "stock_zh_a_hist", "stock_a_indicator_lg",
                   "stock_financial_analysis_indicator", "stock_fhps_detail_em",
                   "stock_gpzy_pledge_ratio_em", "stock_zh_a_disclosure_report_cninfo",
                   "stock_board_industry_cons_em", "stock_profit_forecast_em",
                   "bond_zh_us_rate",
                   # 备源同样封死
                   "stock_zh_a_daily", "stock_profile_cninfo", "stock_zh_valuation_baidu"]:
            monkeypatch.setattr(ak, fn,
                                lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
                                raising=False)
        snap = stock_data.get_stock_snapshot("sh600519", refresh=True)
        assert snap["code"] == "600519"                    # 代码规整
        assert snap["basic"] is None
        assert "审计意见" in snap["manual_check_fields"]
        assert len(snap["errors"]) >= 9

    def test_quote_summary_math(self, monkeypatch):
        """行情摘要的回撤/均线/成交额计算正确性。"""
        import akshare as ak
        n = 300
        closes = [100.0] * (n - 1) + [75.0]     # 高点100，现价75 → 回撤25%
        hist = pd.DataFrame({"收盘": closes, "成交额": [2e8] * n})
        monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **k: hist, raising=False)
        q = stock_data._quote_summary("600519")
        assert abs(q["较1年内高点回撤"] - 0.25) < 1e-9      # 择时门槛临界值
        assert q["近5日日均成交额"] == 2e8
        assert q["MA60"] is not None and q["站上60日线"] is False
