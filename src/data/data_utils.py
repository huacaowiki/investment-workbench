# -*- coding: utf-8 -*-
"""
data_utils.py — 通用数据清洗、指标计算、格式化、缓存工具
纯函数为主，不依赖网络，是数据层与分析层共用的基础库。
"""
from __future__ import annotations

import math
import time
from datetime import datetime, date
from pathlib import Path

from src.utils.file_utils import DIRS, read_json, write_json, ensure_dir

# =============================================================================
# 数据清洗
# =============================================================================

def to_float(value, default=None):
    """把 '1,234.5'、'12.3%'、'--'、None、numpy标量 统一转成 float；失败返回 default。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return default if isinstance(value, float) and math.isnan(value) else float(value)
    s = str(value).strip().replace(",", "").replace("，", "")
    if s in ("", "--", "-", "None", "nan", "NaN", "null"):
        return default
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        v = float(s)
        return v / 100 if pct else v
    except ValueError:
        return default


def safe_get(d: dict, *keys, default=None):
    """嵌套字典安全取值：safe_get(x, 'a', 'b') == x['a']['b']，路径缺失返回 default。"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def normalize_stock_code(code: str) -> str:
    """规整A股代码：去掉市场前后缀，补齐6位。'sh600519'/'600519.SH'/'600519' → '600519'"""
    s = str(code).strip().lower()
    for token in ("sh", "sz", "bj", ".sh", ".sz", ".bj"):
        s = s.replace(token, "")
    s = "".join(ch for ch in s if ch.isdigit())
    return s.zfill(6)


# =============================================================================
# 指标计算（供选股/估值/风控规则引用，口径与 config 注释一致）
# =============================================================================

def percentile_rank(series: list, value: float) -> float | None:
    """
    计算 value 在 series 中的历史分位（0~1）。
    用于 PE/PB 历史分位判断（stock_selection.yaml pending_confirmation[1]：默认近10年数据）。
    """
    clean = [to_float(x) for x in series]
    clean = [x for x in clean if x is not None and x > 0]
    if not clean or value is None:
        return None
    below = sum(1 for x in clean if x <= value)
    return below / len(clean)


def drawdown_from_high(prices: list) -> float | None:
    """当前价较区间最高点的回撤幅度（正数，0.25=回撤25%）。用于择时门槛'较1年内高点回撤≥25%'。"""
    clean = [to_float(x) for x in prices]
    clean = [x for x in clean if x is not None and x > 0]
    if len(clean) < 2:
        return None
    high = max(clean)
    return (high - clean[-1]) / high


def annualized_volatility(closes: list, window: int = 20) -> float | None:
    """
    近 window 日年化历史波动率（用于 risk_control.yaml 严格风控触发条件③的近似口径，
    见其 pending_confirmation[2]）。
    """
    clean = [to_float(x) for x in closes]
    clean = [x for x in clean if x is not None and x > 0]
    if len(clean) < window + 1:
        return None
    tail = clean[-(window + 1):]
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def moving_average(values: list, window: int) -> float | None:
    """简单移动均线（右侧确认条款'行业指数站上60日线'等使用）。"""
    clean = [to_float(x) for x in values if to_float(x) is not None]
    if len(clean) < window:
        return None
    return sum(clean[-window:]) / window


def yoy_growth(current, previous) -> float | None:
    """同比增速；分母为0或缺失返回None。"""
    c, p = to_float(current), to_float(previous)
    if c is None or p is None or p == 0:
        return None
    return (c - p) / abs(p)


def avg(values: list) -> float | None:
    """均值（自动剔除缺失）。"""
    clean = [to_float(x) for x in values]
    clean = [x for x in clean if x is not None]
    return sum(clean) / len(clean) if clean else None


# =============================================================================
# 技术指标（v4.2.0 自动化口径：技术底部/右侧确认/放量滞涨的量化实现）
# =============================================================================

def rsi_series(closes: list, period: int = 14) -> list:
    """Wilder RSI 序列（长度与输入对齐，前 period 位为 None）。"""
    c = [to_float(x) for x in closes]
    if len([x for x in c if x is not None]) < period + 1:
        return [None] * len(c)
    out: list = [None] * len(c)
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        chg = c[i] - c[i - 1]
        gains += max(chg, 0)
        losses += max(-chg, 0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(period + 1, len(c)):
        chg = c[i] - c[i - 1]
        avg_g = (avg_g * (period - 1) + max(chg, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-chg, 0)) / period
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def macd_lines(closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD：返回 (DIF, DEA) 两个序列。"""
    c = [to_float(x) for x in closes]

    def ema(vals, n):
        out, k = [], 2 / (n + 1)
        prev = None
        for v in vals:
            prev = v if prev is None else v * k + prev * (1 - k)
            out.append(prev)
        return out

    ema_f, ema_s = ema(c, fast), ema(c, slow)
    dif = [f - s for f, s in zip(ema_f, ema_s)]
    dea = ema(dif, signal)
    return dif, dea


def tech_bottom_signal(closes: list) -> dict:
    """
    技术底部共振（v4.2.0裁决#5口径）：
    ① 日线RSI(14)<30 出现于近20个交易日；② MACD零轴下方金叉出现于近15个交易日。
    两者同时满足 → signal=True。
    """
    rsi = rsi_series(closes)
    recent_rsi = [r for r in rsi[-20:] if r is not None]
    cond_rsi = bool(recent_rsi) and min(recent_rsi) < 30
    dif, dea = macd_lines(closes)
    cond_macd = False
    start = max(1, len(closes) - 15)
    for i in range(start, len(closes)):
        if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i] and dif[i] < 0:
            cond_macd = True
            break
    return {"signal": cond_rsi and cond_macd, "RSI超卖近20日": cond_rsi,
            "MACD零下金叉近15日": cond_macd,
            "口径": "替代量化口径（v4.2.0裁决#5）：RSI14<30近20日 且 MACD零轴下金叉近15日"}


def kdj_series(highs: list, lows: list, closes: list, n: int = 9):
    """KDJ(9,3,3)：返回最新 (K, D, J)；样本不足返回 (None, None, None)。"""
    h = [to_float(x) for x in highs]
    l = [to_float(x) for x in lows]
    c = [to_float(x) for x in closes]
    if len(c) < n + 1 or any(v is None for v in (h[-1], l[-1], c[-1])):
        return None, None, None
    k, d = 50.0, 50.0
    for i in range(n - 1, len(c)):
        hh = max(x for x in h[i - n + 1:i + 1] if x is not None)
        ll = min(x for x in l[i - n + 1:i + 1] if x is not None)
        rsv = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
    return round(k, 1), round(d, 1), round(3 * k - 2 * d, 1)


def boll_bands(closes: list, window: int = 20, num_std: float = 2.0):
    """BOLL(20,2)：返回 (下轨, 中轨, 上轨)；样本不足返回 (None,)*3。"""
    c = [to_float(x) for x in closes if to_float(x) is not None]
    if len(c) < window:
        return None, None, None
    tail = c[-window:]
    mid = sum(tail) / window
    var = sum((x - mid) ** 2 for x in tail) / window
    std = math.sqrt(var)
    return round(mid - num_std * std, 2), round(mid, 2), round(mid + num_std * std, 2)


def days_above_ma(closes: list, window: int = 60) -> int:
    """收盘连续站上 window 日均线的天数（右侧确认口径：≥3日记1分）。"""
    c = [to_float(x) for x in closes if to_float(x) is not None]
    if len(c) < window + 1:
        return 0
    days = 0
    for i in range(len(c) - 1, window - 1, -1):
        ma = sum(c[i - window + 1:i + 1]) / window
        if c[i] > ma:
            days += 1
        else:
            break
    return days


def volume_stall_signal(closes: list, amounts: list) -> dict:
    """
    放量滞涨卖出信号（v4.2.0裁决#12口径）：
    近5个交易日内出现 [当日成交额≥1.5×近20日均额 且 当日涨跌幅≤0.5% 且 收盘距近60日最高<5%]。
    """
    c = [to_float(x) for x in closes]
    a = [to_float(x) for x in amounts]
    if len(c) < 61 or len(a) < 25:
        return {"signal": False, "口径": "样本不足"}
    hit_days = []
    for i in range(len(c) - 5, len(c)):
        if i < 60 or a[i] is None or c[i] is None or c[i - 1] in (None, 0):
            continue
        avg20 = avg(a[i - 20:i])
        if not avg20:
            continue
        pct = (c[i] - c[i - 1]) / c[i - 1]
        high60 = max(x for x in c[i - 59:i + 1] if x is not None)
        if a[i] >= 1.5 * avg20 and pct <= 0.005 and c[i] >= 0.95 * high60:
            hit_days.append(i)
    return {"signal": bool(hit_days),
            "口径": "近5日内[成交额≥1.5×20日均额 且 涨跌幅≤0.5% 且 距60日高点<5%]（v4.2.0裁决#12）"}


# =============================================================================
# 格式化（报告展示用）
# =============================================================================

def fmt_yi(value, digits: int = 2) -> str:
    """金额格式化为亿元：123456789 → '1.23亿'。"""
    v = to_float(value)
    if v is None:
        return "—"
    return f"{v / 1e8:.{digits}f}亿"


def fmt_pct(value, digits: int = 2, signed: bool = False) -> str:
    """比率格式化：0.1234 → '12.34%'。signed=True 时带正负号。"""
    v = to_float(value)
    if v is None:
        return "—"
    sign = "+" if (signed and v > 0) else ""
    return f"{sign}{v * 100:.{digits}f}%"


def fmt_num(value, digits: int = 2) -> str:
    """普通数值格式化，缺失显示 '—'。"""
    v = to_float(value)
    return "—" if v is None else f"{v:.{digits}f}"


# =============================================================================
# 本地缓存机制（避免对数据源重复请求）
# 缓存键 = 名称 + 日期；当日数据缓存当日有效，历史数据永久有效。
# =============================================================================

def cache_path(name: str, day: str | None = None) -> Path:
    """缓存文件路径：data/raw/{day}/{name}.json"""
    day = day or datetime.now().strftime("%Y%m%d")
    return DIRS["data_raw"] / day / f"{name}.json"


def load_cache(name: str, day: str | None = None, max_age_seconds: int | None = None):
    """
    读缓存。max_age_seconds 用于盘中数据的时效控制（如行情快照1小时过期）；
    None 表示当日内一直有效。
    """
    p = cache_path(name, day)
    data = read_json(p)
    if data is None:
        return None
    if max_age_seconds is not None:
        saved_at = data.get("_cached_at", 0)
        if time.time() - saved_at > max_age_seconds:
            return None
    return data.get("payload")


def save_cache(name: str, payload, day: str | None = None):
    """写缓存（附时间戳）。payload 需可JSON序列化。"""
    p = cache_path(name, day)
    write_json(p, {"_cached_at": time.time(),
                   "_cached_at_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "payload": payload})
    return p


def df_to_records(df, limit: int | None = None) -> list[dict]:
    """DataFrame → list[dict]（JSON可序列化：日期转字符串、NaN转None）。"""
    if df is None or len(df) == 0:
        return []
    if limit:
        df = df.head(limit)
    records = []
    for _, row in df.iterrows():
        rec = {}
        for k, v in row.items():
            if isinstance(v, (datetime, date)):
                rec[str(k)] = v.isoformat()
            elif isinstance(v, float) and math.isnan(v):
                rec[str(k)] = None
            elif hasattr(v, "item"):   # numpy 标量
                rec[str(k)] = v.item()
            else:
                rec[str(k)] = v
        records.append(rec)
    return records
