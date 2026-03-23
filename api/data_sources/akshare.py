from typing import Optional, Dict, List, Any
import os
from datetime import datetime

from .types import DataType
from .utils import safe_float, pick_column, to_timestamp_cn, ymd_compact_to_iso
from akshare_guard import should_skip_remote, record_failure, record_success, throttle, akshare_disabled


def _is_etf(code: str) -> bool:
    return str(code).startswith(("15", "16", "5"))


def _normalize_code(symbol: str) -> str:
    return str(symbol or "").upper().replace("SH", "").replace("SZ", "").replace(".", "")


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
        return ak
    except Exception:
        return None


def _parse_enabled_types() -> Optional[set]:
    raw = os.getenv("AKSHARE_ENABLED_TYPES", "").strip()
    if not raw:
        return None
    mapping = {
        "kline_minute": DataType.KLINE_MINUTE,
        "minute": DataType.KLINE_MINUTE,
        "kline_daily": DataType.KLINE_DAILY,
        "daily": DataType.KLINE_DAILY,
        "realtime": DataType.REALTIME,
        "symbols": DataType.SYMBOLS,
        "fundamentals": DataType.FUNDAMENTALS,
        "market_breadth": DataType.MARKET_BREADTH,
        "breadth": DataType.MARKET_BREADTH,
        "industry": DataType.INDUSTRY,
        "index_spot": DataType.INDEX_SPOT,
    }
    enabled = set()
    for token in [t.strip().lower() for t in raw.split(",") if t.strip()]:
        dt = mapping.get(token)
        if dt:
            enabled.add(dt)
    return enabled


def enabled(data_type: Optional[DataType] = None) -> bool:
    if akshare_disabled():
        return False
    if data_type is None:
        return True
    allowlist = _parse_enabled_types()
    if allowlist is not None:
        return data_type in allowlist
    overrides = {
        DataType.KLINE_MINUTE: "AKSHARE_ENABLE_MINUTE",
        DataType.MARKET_BREADTH: "AKSHARE_ENABLE_BREADTH",
        DataType.INDUSTRY: "AKSHARE_ENABLE_INDUSTRY",
    }
    if any(os.getenv(k, "").strip() for k in overrides.values()):
        key = overrides.get(data_type)
        if not key:
            return False
        return os.getenv(key, "").strip().lower() in ("1", "true", "yes", "on")
    # Default: only allow minute data unless explicitly enabled.
    return data_type == DataType.KLINE_MINUTE


def _parse_em_df(df, period: str) -> Optional[List[Dict]]:
    if df is None or df.empty:
        return None
    klines = []
    vol_col = pick_column(df, ["成交量", "volume"])
    amt_col = pick_column(df, ["成交额", "amount"])
    close_col = pick_column(df, ["收盘", "close"])

    def _infer_multiplier() -> int:
        if not vol_col:
            return 100
        if not amt_col or not close_col:
            return 100
        ratios = []
        for _, row in df.head(200).iterrows():
            vol = safe_float(row.get(vol_col))
            amt = safe_float(row.get(amt_col))
            price = safe_float(row.get(close_col))
            if vol <= 0 or amt <= 0 or price <= 0:
                continue
            ratios.append((amt / vol) / price)
        if not ratios:
            return 100
        ratios.sort()
        median = ratios[len(ratios) // 2]
        if median >= 10:
            return 100
        if median < 0.1 and (median * 10000) >= 10:
            return 100
        return 1

    multiplier = _infer_multiplier()

    for _, row in df.iterrows():
        try:
            if period in ("daily", "weekly", "monthly"):
                date_str = str(row.get("日期"))
                timestamp = to_timestamp_cn(date_str, "%Y-%m-%d")
            else:
                date_str = str(row.get("时间"))
                timestamp = to_timestamp_cn(date_str, "%Y-%m-%d %H:%M:%S")

            volume_raw = safe_float(row.get(vol_col)) if vol_col else 0
            volume = volume_raw * multiplier if volume_raw else 0
            klines.append({
                "date": date_str.split(" ")[0] if " " in date_str else date_str,
                "time": date_str if " " in date_str else None,
                "openTime": timestamp,
                "open": float(row.get("开盘")),
                "high": float(row.get("最高")),
                "low": float(row.get("最低")),
                "close": float(row.get("收盘")),
                "volume": float(volume),
                "closeTime": timestamp + (86400000 if period in ("daily", "weekly", "monthly") else 60000) - 1,
            })
        except Exception:
            continue
    return klines or None


def fetch_kline_eastmoney(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    data_type = DataType.KLINE_DAILY if period in ("daily", "weekly", "monthly") else DataType.KLINE_MINUTE
    if not enabled(data_type):
        return None
    ak = _load_akshare()
    if not ak:
        return None
    if should_skip_remote(scope="kline_akshare"):
        return None
    try:
        if _is_etf(code):
            if period in ("daily", "weekly", "monthly"):
                throttle(scope="akshare_kline")
                df = ak.fund_etf_hist_em(
                    symbol=code,
                    period=period,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    adjust="qfq",
                )
            else:
                df = None
                if hasattr(ak, "fund_etf_hist_min_em"):
                    try:
                        throttle(scope="akshare_minute")
                        df = ak.fund_etf_hist_min_em(
                            symbol=code,
                            period=period,
                            start_date=f"{fetch_start} 09:30:00",
                            end_date=f"{fetch_end} 15:00:00",
                            adjust="qfq",
                        )
                    except Exception:
                        df = None
                if df is None:
                    throttle(scope="akshare_minute")
                    df = ak.stock_zh_a_hist_min_em(
                        symbol=code,
                        period=period,
                        start_date=f"{fetch_start} 09:30:00",
                        end_date=f"{fetch_end} 15:00:00",
                        adjust="qfq",
                    )
        else:
            if period in ("daily", "weekly", "monthly"):
                throttle(scope="akshare_kline")
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period=period,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    adjust="qfq",
                )
            else:
                throttle(scope="akshare_minute")
                df = ak.stock_zh_a_hist_min_em(
                    symbol=code,
                    period=period,
                    start_date=f"{fetch_start} 09:30:00",
                    end_date=f"{fetch_end} 15:00:00",
                    adjust="qfq",
                )
    except Exception as e:
        record_failure(f"akshare_kline_failed: {e}", scope="kline_akshare")
        return None

    record_success(scope="kline_akshare")
    return _parse_em_df(df, period)


def fetch_kline_sina(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    if not enabled(DataType.KLINE_DAILY):
        return None
    ak = _load_akshare()
    if not ak:
        return None
    if should_skip_remote(scope="kline_sina"):
        return None
    try:
        if period in ("daily", "weekly", "monthly"):
            if _is_etf(code):
                throttle(scope="akshare_kline")
                df = ak.fund_etf_hist_sina(symbol=f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}")
                if df is not None and not df.empty:
                    df["date"] = df["date"].astype(str)
                    start_iso = ymd_compact_to_iso(fetch_start)
                    end_iso = ymd_compact_to_iso(fetch_end)
                    if start_iso and end_iso:
                        df = df[(df["date"] >= start_iso) & (df["date"] <= end_iso)]
            else:
                throttle(scope="akshare_kline")
                df = ak.stock_zh_a_daily(
                    symbol=f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}",
                    start_date=fetch_start,
                    end_date=fetch_end,
                    adjust="qfq",
                )
        else:
            return None
    except Exception as e:
        record_failure(f"sina_kline_failed: {e}", scope="kline_sina")
        return None

    if df is None or df.empty:
        return None

    vol_col = pick_column(df, ["volume", "成交量"])
    amt_col = pick_column(df, ["amount", "成交额"])
    close_col = pick_column(df, ["close", "收盘"])
    multiplier = 100
    if vol_col and amt_col:
        ratios = []
        for _, row in df.head(200).iterrows():
            vol = safe_float(row.get(vol_col))
            amt = safe_float(row.get(amt_col))
            price = safe_float(row.get(close_col)) if close_col else 0
            if vol <= 0 or amt <= 0 or price <= 0:
                continue
            ratios.append((amt / vol) / price)
        if ratios:
            ratios.sort()
            median = ratios[len(ratios) // 2]
            if median < 10:
                multiplier = 1

    klines = []
    for _, row in df.iterrows():
        try:
            date_str = str(row.get("date"))
            timestamp = to_timestamp_cn(date_str, "%Y-%m-%d")
            volume_raw = safe_float(row.get(vol_col)) if vol_col else 0
            volume = volume_raw * multiplier if volume_raw else 0
            klines.append({
                "date": date_str,
                "time": None,
                "openTime": timestamp,
                "open": float(row.get("open")),
                "high": float(row.get("high")),
                "low": float(row.get("low")),
                "close": float(row.get("close")),
                "volume": float(volume),
                "closeTime": timestamp + 86400000 - 1,
            })
        except Exception:
            continue
    record_success(scope="kline_sina")
    return klines or None


def fetch_realtime(symbol: str) -> Optional[Dict[str, Any]]:
    if not enabled(DataType.REALTIME):
        return None
    ak = _load_akshare()
    if not ak:
        return None
    code = _normalize_code(symbol)
    is_etf = _is_etf(code)
    if should_skip_remote(scope="realtime_akshare"):
        return None
    try:
        throttle(scope="akshare_realtime")
        if is_etf:
            df = ak.fund_etf_spot_em()
        else:
            df = ak.stock_zh_a_spot_em()
    except Exception as e:
        record_failure(f"realtime_failed: {e}", scope="realtime_akshare")
        return None
    if df is None or df.empty:
        return None
    row = df[df["代码"] == code]
    if row.empty:
        return None
    r = row.iloc[0]
    price = safe_float(r.get("最新价"))
    vol_raw = safe_float(r.get("成交量"))
    amt_raw = safe_float(r.get("成交额"))
    multiplier = 100
    if vol_raw > 0 and amt_raw > 0 and price > 0:
        ratio = (amt_raw / vol_raw) / price
        if ratio <= 2:
            multiplier = 1
    return {
        "symbol": symbol,
        "name": r.get("名称"),
        "price": price,
        "change": safe_float(r.get("涨跌额")),
        "changePercent": safe_float(r.get("涨跌幅")),
        "open": safe_float(r.get("开盘价") or r.get("今开")),
        "high": safe_float(r.get("最高价") or r.get("最高")),
        "low": safe_float(r.get("最低价") or r.get("最低")),
        "volume": vol_raw * multiplier,
        "amount": amt_raw,
        "timestamp": int(datetime.utcnow().timestamp() * 1000),
        "source": "akshare",
        "stale": False,
    }


def fetch_symbols() -> Optional[Any]:
    if not enabled(DataType.SYMBOLS):
        return None
    ak = _load_akshare()
    if not ak:
        return None
    if should_skip_remote(scope="symbols_akshare"):
        return None
    try:
        throttle(scope="akshare_symbols")
        df_stock = ak.stock_info_a_code_name()
    except Exception:
        record_failure("symbols_fetch_failed", scope="symbols_akshare")
        df_stock = None
    try:
        throttle(scope="akshare_symbols")
        df_etf = ak.fund_etf_spot_em()
    except Exception:
        record_failure("symbols_fetch_failed", scope="symbols_akshare")
        df_etf = None
    return {"stocks": df_stock, "etfs": df_etf}


def fetch_fundamentals(symbols: List[str]) -> Optional[Dict[str, Any]]:
    if not enabled(DataType.FUNDAMENTALS):
        return None
    ak = _load_akshare()
    if not ak:
        return None
    stock_df = None
    etf_df = None
    codes = [_normalize_code(s) for s in symbols]
    stock_codes = [c for c in codes if not _is_etf(c)]
    etf_codes = [c for c in codes if _is_etf(c)]
    try:
        if stock_codes:
            throttle(scope="akshare_fundamentals")
            stock_df = ak.stock_zh_a_spot_em()
        if etf_codes:
            throttle(scope="akshare_fundamentals")
            etf_df = ak.fund_etf_spot_em()
    except Exception as e:
        record_failure(f"fundamentals_fetch_failed: {e}", scope="fundamentals")
        return None
    record_success(scope="fundamentals")
    return {"stocks": stock_df, "etfs": etf_df}


def fetch_market_breadth() -> Optional[Dict[str, Any]]:
    if not enabled(DataType.MARKET_BREADTH):
        return None
    ak = _load_akshare()
    if not ak:
        return None
    if should_skip_remote(scope="breadth_akshare"):
        return None
    try:
        df = ak.stock_zh_a_spot_em()
    except Exception:
        record_failure("breadth_fetch_failed", scope="breadth_akshare")
        return None
    if df is None or df.empty:
        return None
    pct_col = pick_column(df, ["涨跌幅", "pct_chg", "涨跌幅%"])
    amt_col = pick_column(df, ["成交额", "amount"])
    if not pct_col:
        return None
    adv = dec = flat = up_limit = down_limit = 0
    turnover = 0.0
    for _, row in df.iterrows():
        pct = safe_float(row.get(pct_col))
        if pct > 0:
            adv += 1
        elif pct < 0:
            dec += 1
        else:
            flat += 1
        if pct >= 9.7:
            up_limit += 1
        if pct <= -9.7:
            down_limit += 1
        if amt_col:
            turnover += safe_float(row.get(amt_col))
    return {
        "advancers": adv,
        "decliners": dec,
        "flat": flat,
        "up_limit": up_limit,
        "down_limit": down_limit,
        "turnover": turnover,
    }


def fetch_industry_cninfo(standard: str) -> Optional[Any]:
    if not enabled(DataType.INDUSTRY):
        return None
    ak = _load_akshare()
    if not ak:
        return None
    if should_skip_remote(scope="industry_akshare"):
        return None
    try:
        return ak.stock_industry_change_cninfo(symbol=standard)
    except Exception:
        record_failure("industry_fetch_failed", scope="industry_akshare")
        return None


def supports(data_type: DataType) -> bool:
    return data_type in {
        DataType.KLINE_DAILY,
        DataType.KLINE_MINUTE,
        DataType.REALTIME,
        DataType.SYMBOLS,
        DataType.FUNDAMENTALS,
        DataType.MARKET_BREADTH,
        DataType.INDUSTRY,
    }


def fetch(data_type: DataType, **kwargs) -> Optional[Any]:
    if data_type == DataType.KLINE_DAILY:
        code = _normalize_code(kwargs.get("symbol") or kwargs.get("code") or "")
        if not code:
            return None
        period = kwargs.get("period") or "daily"
        fetch_start = kwargs.get("start")
        fetch_end = kwargs.get("end")
        provider = kwargs.get("provider", "eastmoney")
        if provider == "sina":
            return fetch_kline_sina(code, period, fetch_start, fetch_end)
        return fetch_kline_eastmoney(code, period, fetch_start, fetch_end)
    if data_type == DataType.KLINE_MINUTE:
        code = _normalize_code(kwargs.get("symbol") or kwargs.get("code") or "")
        if not code:
            return None
        period = kwargs.get("period") or "1"
        fetch_start = kwargs.get("start")
        fetch_end = kwargs.get("end")
        return fetch_kline_eastmoney(code, period, fetch_start, fetch_end)
    if data_type == DataType.REALTIME:
        return fetch_realtime(kwargs.get("symbol") or "")
    if data_type == DataType.SYMBOLS:
        return fetch_symbols()
    if data_type == DataType.FUNDAMENTALS:
        return fetch_fundamentals(kwargs.get("symbols") or [])
    if data_type == DataType.MARKET_BREADTH:
        return fetch_market_breadth()
    if data_type == DataType.INDUSTRY:
        standard = kwargs.get("standard") or "证监会行业分类标准"
        return fetch_industry_cninfo(standard)
    return None
