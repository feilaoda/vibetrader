"""
股票数据缓存模块
- 历史数据缓存到本地 JSON 文件
- 交易时间内每分钟更新
- 收盘5分钟后停止自动更新
- 支持多数据源切换 (eastmoney / sina)
"""

import json
import os
import re
from datetime import datetime, date, timedelta, time as dtime, timezone
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Literal
from akshare_guard import should_skip_remote, record_failure, record_success
from data_sources import DataType
from data_sources.router import fetch as router_fetch
from data_sources.tencent import build_daily_from_quote
from us_indices import is_us_index_symbol, resolve_us_index_ticker

# 配置代理
# PROXY = "http://127.0.0.1:33210"
# os.environ["HTTP_PROXY"] = PROXY
# os.environ["HTTPS_PROXY"] = PROXY
# os.environ["http_proxy"] = PROXY
# os.environ["https_proxy"] = PROXY

# 缓存目录
CACHE_DIR = Path(__file__).parent / "__datacache__"
CACHE_DIR.mkdir(exist_ok=True)

# 配置文件路径
CONFIG_PATH = Path(__file__).parent / "config.json"

# 数据源类型
DataSourceType = Literal["eastmoney", "sina"]

# A股交易时间
TRADING_HOURS = [
    (dtime(9, 30), dtime(11, 30)),   # 上午
    (dtime(13, 0), dtime(15, 0)),    # 下午
]

# 收盘后停止自动刷新的分钟数
STOP_AUTO_REFRESH_AFTER_MINUTES = 5

# 日线级别周期
DAILY_PERIODS = ["daily", "weekly", "monthly"]

# 分钟级别周期 (东方财富API格式)
MINUTE_PERIODS = ["1", "5", "15", "30", "60"]

US_INDEX_CACHE_TTL_SECONDS = 6 * 3600

CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else None


def _now_cn() -> datetime:
    return datetime.now(CN_TZ) if CN_TZ else datetime.now()


def _source_scope(source: str) -> str:
    return f"kline_{source}"


def _source_disabled(source: str) -> bool:
    key = f"DISABLE_{source.upper()}"
    return os.getenv(key, "").strip().lower() in ("1", "true", "yes", "on")


def _should_skip_source(source: str, force_remote: bool = False) -> bool:
    if _source_disabled(source):
        return True
    return should_skip_remote(force_remote, scope=_source_scope(source))


CN_INDEX_SH_CODES = {
    "000001", "000016", "000300", "000852", "000905", "000985", "000688",
}
CN_INDEX_SZ_CODES = {
    "399001", "399006", "399005",
}


def _normalize_cn_index_symbol(symbol: str) -> Optional[str]:
    if not symbol:
        return None
    sym = symbol.strip().upper()
    if sym.endswith(".IDX"):
        sym = sym[:-4]
    code = sym.replace(".SH", "").replace(".SZ", "").replace(".", "")
    if not code:
        return None
    code = code.zfill(6)
    if code in CN_INDEX_SH_CODES:
        return f"sh{code}"
    if code in CN_INDEX_SZ_CODES:
        return f"sz{code}"
    # If suffix is explicit, respect it for known prefixes.
    if sym.endswith(".SH"):
        return f"sh{code}"
    if sym.endswith(".SZ"):
        return f"sz{code}"
    return None


def is_cn_index_symbol(symbol: str) -> bool:
    sym = (symbol or "").strip().upper()
    if not sym:
        return False
    code = sym.replace(".SH", "").replace(".SZ", "").replace(".IDX", "").replace(".", "")
    if not code:
        return False
    code = code.zfill(6)
    if code in CN_INDEX_SH_CODES:
        return sym.endswith(".SH") or "." not in sym
    if code in CN_INDEX_SZ_CODES:
        return sym.endswith(".SZ") or "." not in sym
    return False


def _record_source_failure(source: str, error: str) -> None:
    record_failure(error, scope=_source_scope(source))


def _record_source_success(source: str) -> None:
    record_success(scope=_source_scope(source))


def _today_cn() -> date:
    return _now_cn().date()


def _latest_trading_date(now: Optional[datetime] = None) -> date:
    current = now or _now_cn()
    trade_date = current.date()
    # If before market open, use previous trading day
    if current.time() < dtime(9, 30):
        trade_date = trade_date - timedelta(days=1)
    # Roll back weekends
    while trade_date.weekday() >= 5:
        trade_date = trade_date - timedelta(days=1)
    return trade_date


def _normalize_ymd(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    text = str(date_str)
    if "-" in text:
        return text
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _to_ymd_compact(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    text = str(date_str).strip()
    if not text:
        return None
    if "-" in text:
        return text.replace("-", "")
    if len(text) == 8 and text.isdigit():
        return text
    return text


def _kline_date_iso(kline: Dict) -> str:
    date_str = kline.get("date") or ""
    if not date_str:
        time_str = kline.get("time")
        if isinstance(time_str, str) and time_str:
            date_str = time_str.split(" ")[0]
    return _normalize_ymd(date_str) or ""


def _filter_klines_by_date(klines: List[Dict], start_date: Optional[str], end_date: Optional[str]) -> List[Dict]:
    start_iso = _normalize_ymd(start_date)
    end_iso = _normalize_ymd(end_date)
    if not start_iso and not end_iso:
        return klines
    filtered = []
    for k in klines:
        date_iso = _kline_date_iso(k)
        if not date_iso:
            continue
        if start_iso and date_iso < start_iso:
            continue
        if end_iso and date_iso > end_iso:
            continue
        filtered.append(k)
    return filtered


def _strip_future_klines(klines: List[Dict], max_date_iso: str) -> List[Dict]:
    if not max_date_iso:
        return klines
    cleaned = []
    for k in klines:
        date_iso = _kline_date_iso(k)
        if not date_iso or date_iso <= max_date_iso:
            cleaned.append(k)
    return cleaned


def is_minute_period(period: str) -> bool:
    """判断是否是分钟级别周期"""
    return period in MINUTE_PERIODS or period not in DAILY_PERIODS


def load_config() -> Dict:
    """加载配置"""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except:
            pass
    return {"data_source": "sina"}  # 默认使用 sina


def save_config(config: Dict):
    """保存配置"""
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f)


def get_data_source() -> DataSourceType:
    """获取当前数据源"""
    return load_config().get("data_source", "sina")


def set_data_source(source: DataSourceType):
    """设置数据源"""
    config = load_config()
    config["data_source"] = source
    save_config(config)
    print(f"[Config] Data source set to: {source}")


def is_trading_time() -> bool:
    """判断当前是否在交易时间内"""
    now_dt = _now_cn()
    if now_dt.weekday() >= 5:
        return False
    now = now_dt.time()
    for start, end in TRADING_HOURS:
        if start <= now <= end:
            return True
    return False


def should_auto_refresh() -> bool:
    """判断是否应该自动刷新"""
    now = _now_cn()
    if now.weekday() >= 5:
        return False
    current_time = now.time()
    
    if is_trading_time():
        return True
    
    close_time = dtime(15, 0)
    close_with_buffer = dtime(15, STOP_AUTO_REFRESH_AFTER_MINUTES)
    
    if close_time <= current_time <= close_with_buffer:
        return True
    
    return False


def get_cache_path(symbol: str, period: str) -> Path:
    """获取缓存文件路径"""
    safe_symbol = symbol.replace(".", "_").replace("/", "_")
    return CACHE_DIR / f"{safe_symbol}_{period}.json"


def _load_cache_file(symbol: str, period: str) -> Optional[Dict]:
    cache_path = get_cache_path(symbol, period)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Cache] Error loading cache: {e}")
    return None


def _save_cache_file(symbol: str, period: str, data: Dict):
    cache_path = get_cache_path(symbol, period)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"[Cache] Saved {len(data.get('klines', []))} klines to {cache_path.name}")
    except Exception as e:
        print(f"[Cache] Error saving cache: {e}")


def _load_daily_cache_db(symbol: str, period: str) -> Optional[Dict]:
    try:
        from db import get_daily_klines_cache
        return get_daily_klines_cache(symbol, period)
    except Exception as e:
        print(f"[Cache][DB] Error loading daily cache for {symbol}/{period}: {e}")
        return None


def _save_daily_cache_db(symbol: str, period: str, data: Dict) -> bool:
    try:
        from db import save_daily_klines_cache
        saved = bool(save_daily_klines_cache(symbol, period, data))
        if saved and period == "daily":
            try:
                from db import refresh_technical_indicators

                refresh_technical_indicators(symbol, data.get("klines") if isinstance(data, dict) else None)
            except Exception as e:
                print(f"[Cache][DB] Error refreshing indicators for {symbol}: {e}")
        return saved
    except Exception as e:
        print(f"[Cache][DB] Error saving daily cache for {symbol}/{period}: {e}")
        return False


def load_cache(symbol: str, period: str) -> Optional[Dict]:
    """加载缓存数据（1d/1w/1M 走 DB，分钟级保留文件缓存）"""
    if period in DAILY_PERIODS:
        db_cache = _load_daily_cache_db(symbol, period)
        if db_cache and isinstance(db_cache.get("klines"), list):
            return db_cache
        file_cache = _load_cache_file(symbol, period)
        if file_cache:
            if _save_daily_cache_db(symbol, period, file_cache):
                migrated = _load_daily_cache_db(symbol, period)
                if migrated and isinstance(migrated.get("klines"), list):
                    print(f"[Cache][DB] Migrated {symbol}/{period} file cache to DB")
                    return migrated
            return file_cache
        return db_cache
    return _load_cache_file(symbol, period)


def save_cache(symbol: str, period: str, data: Dict):
    """保存缓存数据（1d/1w/1M 存 DB，失败回退文件缓存）"""
    if period in DAILY_PERIODS:
        if _save_daily_cache_db(symbol, period, data):
            print(f"[Cache][DB] Saved {len(data.get('klines', []))} klines for {symbol}/{period}")
            return
    _save_cache_file(symbol, period, data)


def should_refresh_cache(cache: Dict, period: str) -> Tuple[bool, str]:
    """判断是否需要刷新缓存"""
    if _now_cn().weekday() >= 5:
        return False, "non_trading_day"
    if not cache:
        return True, "no_cache"
    
    last_update = cache.get("last_update_time")
    if period in DAILY_PERIODS:
        today_str = _latest_trading_date().strftime("%Y%m%d")
    else:
        today_str = _today_cn().strftime("%Y%m%d")
    cache_date = cache.get("last_update_date", "")
    
    if cache_date != today_str:
        return True, "not_today"
    
    if period in DAILY_PERIODS:
        if not should_auto_refresh():
            return False, "after_market"
        if last_update:
            elapsed = _now_cn().timestamp() - last_update
            if elapsed < 60:
                return False, "too_recent"
        return True, "trading_time"
    else:
        if not should_auto_refresh():
            return False, "after_market"
        if last_update:
            elapsed = _now_cn().timestamp() - last_update
            if elapsed < 60:
                return False, "too_recent"
        return True, "minute_refresh"


def _yahoo_interval(period: str) -> str:
    if period in ("weekly", "1w", "1W"):
        return "1wk"
    if period in ("monthly", "1M"):
        return "1mo"
    return "1d"


def _ymd_to_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    if "-" in s:
        s = s.replace("-", "")
    if len(s) != 8 or not s.isdigit():
        return None
    return int(s)


def _should_refresh_us_cache(cache: Dict) -> Tuple[bool, str]:
    if not cache:
        return True, "no_cache"
    last_update = cache.get("last_update_time")
    if not last_update:
        return True, "no_timestamp"
    elapsed = datetime.utcnow().timestamp() - float(last_update)
    if elapsed > US_INDEX_CACHE_TTL_SECONDS:
        return True, "stale"
    return False, "cache"


def _needs_us_range_refresh(cached_klines: List[Dict], start_date: Optional[str], end_date: Optional[str]) -> bool:
    if not cached_klines:
        return True
    start_i = _ymd_to_int(start_date)
    end_i = _ymd_to_int(end_date)
    first = _ymd_to_int(cached_klines[0].get("date"))
    last = _ymd_to_int(cached_klines[-1].get("date"))
    if start_i and first and start_i < first:
        return True
    if end_i and last and end_i > last:
        return True
    return False


def _parse_iso_date(date_str: Optional[str]) -> Optional[date]:
    iso = _normalize_ymd(date_str)
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except Exception:
        return None


def _needs_daily_range_refresh(
    cached_klines: List[Dict],
    start_date: Optional[str],
    end_date: Optional[str],
    latest_date_iso: str,
) -> bool:
    if not cached_klines:
        return True
    first_iso = _kline_date_iso(cached_klines[0])
    last_iso = _kline_date_iso(cached_klines[-1])
    if not first_iso or not last_iso:
        return True

    start_iso = _normalize_ymd(start_date)
    end_iso = _normalize_ymd(end_date) or latest_date_iso

    if start_iso and start_iso < first_iso:
        return True
    if end_iso and end_iso > last_iso:
        return True
    return False


def _has_missing_recent_trading_days(cached_klines: List[Dict], window: int = 30) -> bool:
    if not cached_klines:
        return True
    if window <= 0:
        return False
    available = {k.get("date") for k in cached_klines if k.get("date")}
    if not available:
        return True

    first_cached = _parse_iso_date(cached_klines[0].get("date"))
    if not first_cached:
        return True

    required: List[str] = []
    current = _latest_trading_date()
    while len(required) < window:
        if current.weekday() < 5:
            required.append(current.strftime("%Y-%m-%d"))
        current = current - timedelta(days=1)
    if not required:
        return False

    oldest_required = _parse_iso_date(required[-1])
    if oldest_required and first_cached > oldest_required:
        # New listing or short-history symbol: do not treat missing older days as a gap.
        return False
    return any(day not in available for day in required)


def _merge_klines(existing: List[Dict], incoming: List[Dict], period: str) -> List[Dict]:
    if not existing:
        return list(incoming or [])
    if not incoming:
        return list(existing or [])

    if period in DAILY_PERIODS:
        by_date: Dict[str, Dict] = {}
        for item in existing:
            day = _kline_date_iso(item)
            if day:
                by_date[day] = item
        for item in incoming:
            day = _kline_date_iso(item)
            if day:
                by_date[day] = item
        merged = list(by_date.values())
        merged.sort(key=lambda x: _kline_date_iso(x))
        return merged

    by_open_time: Dict[int, Dict] = {}
    for item in existing:
        ts = int(item.get("openTime", 0) or 0)
        if ts > 0:
            by_open_time[ts] = item
    for item in incoming:
        ts = int(item.get("openTime", 0) or 0)
        if ts > 0:
            by_open_time[ts] = item
    merged = list(by_open_time.values())
    merged.sort(key=lambda x: x.get("openTime", 0))
    return merged


def _to_iso_date_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return ""
    if " " in text:
        text = text.split(" ")[0]
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    try:
        dt = datetime.fromisoformat(text)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _aggregate_daily_klines(klines: List[Dict], period: str) -> List[Dict]:
    if period == "daily":
        return klines
    if period not in ("weekly", "monthly"):
        return klines
    if not klines:
        return []
    groups: Dict[str, List[Dict]] = {}
    for item in klines:
        day = _kline_date_iso(item)
        if not day:
            continue
        try:
            dt = datetime.strptime(day, "%Y-%m-%d")
        except Exception:
            continue
        if period == "weekly":
            iso = dt.isocalendar()
            key = f"{iso.year}-{iso.week:02d}"
        else:
            key = f"{dt.year}-{dt.month:02d}"
        groups.setdefault(key, []).append(item)

    merged: List[Dict] = []
    for key in sorted(groups.keys()):
        rows = sorted(groups[key], key=lambda x: x.get("openTime", 0))
        if not rows:
            continue
        first = rows[0]
        last = rows[-1]
        high = max(_safe_float(r.get("high")) for r in rows)
        low = min(_safe_float(r.get("low")) for r in rows)
        volume = sum(_safe_float(r.get("volume")) for r in rows)
        merged.append({
            "date": first.get("date"),
            "time": None,
            "openTime": first.get("openTime", 0),
            "open": _safe_float(first.get("open")),
            "high": high,
            "low": low,
            "close": _safe_float(last.get("close")),
            "volume": volume,
            "closeTime": last.get("closeTime", first.get("openTime", 0)),
        })
    return merged


def _parse_us_daily_df_to_klines(
    df,
    start_date: Optional[str],
    end_date: Optional[str],
    period: str = "daily"
) -> List[Dict]:
    if df is None or df.empty:
        return []

    date_col = _pick_column(df, ["date", "日期", "index", "Date"])
    open_col = _pick_column(df, ["open", "开盘", "Open"])
    high_col = _pick_column(df, ["high", "最高", "High"])
    low_col = _pick_column(df, ["low", "最低", "Low"])
    close_col = _pick_column(df, ["close", "收盘", "Close"])
    vol_col = _pick_column(df, ["volume", "成交量", "Volume"])

    if not all([date_col, open_col, high_col, low_col, close_col]):
        return []

    start_iso = _normalize_ymd(start_date)
    end_iso = _normalize_ymd(end_date)

    out: List[Dict] = []
    for _, row in df.iterrows():
        date_iso = _to_iso_date_text(row[date_col])
        if not date_iso:
            continue
        if start_iso and date_iso < start_iso:
            continue
        if end_iso and date_iso > end_iso:
            continue
        open_time = int(datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        volume = _safe_float(row[vol_col]) if vol_col else 0.0
        out.append({
            "date": date_iso,
            "time": None,
            "openTime": open_time,
            "open": _safe_float(row[open_col]),
            "high": _safe_float(row[high_col]),
            "low": _safe_float(row[low_col]),
            "close": _safe_float(row[close_col]),
            "volume": volume,
            "closeTime": open_time + 86400000 - 1,
        })

    out.sort(key=lambda x: x.get("openTime", 0))
    return _aggregate_daily_klines(out, period)



def get_us_index_klines_with_cache(
    symbol: str,
    period: str = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 365,
    force_refresh: bool = False
) -> Tuple[List[Dict], str]:
    ticker = resolve_us_index_ticker(symbol) or symbol
    if not ticker:
        return [], "unsupported"

    if period not in DAILY_PERIODS:
        period = "daily"

    cache = load_cache(symbol, period)
    cached_klines = cache.get("klines", []) if cache else []

    need_refresh, reason = _should_refresh_us_cache(cache)
    range_refresh = _needs_us_range_refresh(cached_klines, start_date, end_date)
    if not force_refresh and not need_refresh and not range_refresh:
        filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
        return filtered[-limit:], "cache"

    now = datetime.utcnow()
    fetch_start = _to_ymd_compact(start_date)
    fetch_end = _to_ymd_compact(end_date) or now.strftime("%Y%m%d")
    if not fetch_start:
        if cached_klines and not force_refresh:
            last_date = cached_klines[-1].get("date")
            fetch_start = _to_ymd_compact(last_date) or (now - timedelta(days=365)).strftime("%Y%m%d")
        else:
            fetch_start = (now - timedelta(days=365)).strftime("%Y%m%d")

    interval = _yahoo_interval(period)
    fetch_source = "yahoo"
    data, channel = router_fetch(
        DataType.KLINE_DAILY,
        market="us",
        symbol=ticker,
        start=fetch_start,
        end=fetch_end,
        interval=interval,
    )
    new_klines = data or []
    if channel:
        fetch_source = channel

    if not new_klines:
        filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
        return filtered[-limit:], "cache"

    if cached_klines and not force_refresh:
        cached_klines = _merge_klines(cached_klines, new_klines, period)
    else:
        cached_klines = new_klines

    save_cache(symbol, period, {
        "symbol": symbol,
        "period": period,
        "last_update_date": now.strftime("%Y%m%d"),
        "last_update_time": now.timestamp(),
        "source": fetch_source,
        "klines": cached_klines,
    })

    filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
    return filtered[-limit:], fetch_source


def _fetch_cn_index_daily(symbol: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    idx_symbol = _normalize_cn_index_symbol(symbol)
    if not idx_symbol:
        return None
    code = idx_symbol[2:]
    try:
        data, _ = router_fetch(
            DataType.KLINE_DAILY,
            channels=["tencent"],
            symbol=code,
            period="daily",
            start=fetch_start,
            end=fetch_end,
        )
        return data
    except Exception as e:
        print(f"[index_daily] Tencent fetch failed for {idx_symbol}: {e}")
        return None


def get_cn_index_klines_with_cache(
    symbol: str,
    period: str = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 365,
    force_refresh: bool = False
) -> Tuple[List[Dict], str]:
    if period not in DAILY_PERIODS:
        period = "daily"

    now = _now_cn()
    trade_date = _latest_trading_date(now)
    trade_iso = trade_date.strftime("%Y-%m-%d")
    trade_str = trade_date.strftime("%Y%m%d")

    cache = load_cache(symbol, period)
    cached_klines = cache.get("klines", []) if cache else []

    daily_gap_window = max(1, int(os.getenv("DAILY_GAP_CHECK_WINDOW", "30") or 30))
    daily_backfill_days = max(1, int(os.getenv("DAILY_SYNC_LOOKBACK_DAYS", "40") or 40))
    daily_range_refresh = _needs_daily_range_refresh(cached_klines, start_date, end_date, trade_iso)
    daily_gap_refresh = _has_missing_recent_trading_days(cached_klines, daily_gap_window)

    if not force_refresh:
        need_refresh, reason = should_refresh_cache(cache, period)
        if daily_range_refresh:
            need_refresh = True
            reason = "range_missing"
        elif daily_gap_refresh:
            need_refresh = True
            reason = f"missing_recent_{daily_gap_window}"
        if not need_refresh:
            filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
            filtered = _strip_future_klines(filtered, trade_iso)
            return filtered[-limit:], "cache"
    else:
        reason = "force_refresh"

    print(f"[Cache] Refreshing {symbol} (reason: {reason})")

    if cached_klines and not force_refresh:
        start_iso = _normalize_ymd(start_date)
        first_cached = _kline_date_iso(cached_klines[0]) if cached_klines else ""
        last_cached = _kline_date_iso(cached_klines[-1]) if cached_klines else ""
        fetch_start_iso = None
        if start_iso and first_cached and start_iso < first_cached:
            fetch_start_iso = start_iso
        if not fetch_start_iso:
            anchor_date = _parse_iso_date(last_cached or trade_iso)
            if anchor_date:
                fetch_start_iso = (anchor_date - timedelta(days=daily_backfill_days)).strftime("%Y-%m-%d")
            else:
                fetch_start_iso = start_iso or (trade_date - timedelta(days=365)).strftime("%Y-%m-%d")
            if start_iso and start_iso < fetch_start_iso:
                fetch_start_iso = start_iso
        fetch_start = _to_ymd_compact(fetch_start_iso) or (trade_date - timedelta(days=365)).strftime("%Y%m%d")
    else:
        fetch_start = _to_ymd_compact(start_date) or (trade_date - timedelta(days=365)).strftime("%Y%m%d")

    fetch_end = _to_ymd_compact(end_date) or trade_str
    if fetch_end > trade_str:
        fetch_end = trade_str

    print(f"[Cache] Fetching index {symbol} from {fetch_start} to {fetch_end}")
    new_klines = _fetch_cn_index_daily(symbol, fetch_start, fetch_end)
    if new_klines is None:
        temp_klines = cached_klines
        temp_klines = _strip_future_klines(temp_klines, trade_iso)
        filtered = _filter_klines_by_date(temp_klines, start_date, end_date)
        return filtered[-limit:], "cache"

    if cached_klines and not force_refresh:
        cached_klines = _merge_klines(cached_klines, new_klines, period)
    else:
        cached_klines = new_klines

    cached_klines = _strip_future_klines(cached_klines, trade_iso)
    save_cache(symbol, period, {
        "symbol": symbol,
        "period": period,
        "last_update_date": trade_str,
        "last_update_time": now.timestamp(),
        "source": "cn_index",
        "klines": cached_klines,
    })

    filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
    return filtered[-limit:], "cn_index"


def _is_us_equity_symbol(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    if not sym or is_us_index_symbol(sym):
        return False
    if sym.endswith(".US"):
        return True
    if sym.endswith((".SH", ".SZ", ".BJ", ".HK", ".IDX")):
        return False
    # Common crypto quote suffixes; avoid mistaking them as US tickers.
    if sym.endswith(("USDT", "USDC", "BUSD", "FDUSD", "PERP")):
        return False
    # Allow bare tickers like AAPL / TSLA / BRK.B / BRK-B.
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym):
        return True
    return False


def _resolve_us_equity_ticker(symbol: str) -> Optional[str]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    if sym.endswith(".US"):
        ticker = sym[:-3].strip()
    else:
        ticker = sym
    if not ticker:
        return None
    # Keep dot and dash for tickers like BRK.B / BRK-B.
    return re.sub(r"[^A-Z0-9\.\-]", "", ticker)


def get_us_equity_klines_with_cache(
    symbol: str,
    period: str = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 365,
    force_refresh: bool = False
) -> Tuple[List[Dict], str]:
    ticker = _resolve_us_equity_ticker(symbol)
    if not ticker:
        return [], "unsupported"

    if period not in DAILY_PERIODS:
        period = "daily"

    cache = load_cache(symbol, period)
    cached_klines = cache.get("klines", []) if cache else []

    need_refresh, _ = _should_refresh_us_cache(cache)
    range_refresh = _needs_us_range_refresh(cached_klines, start_date, end_date)
    if not force_refresh and not need_refresh and not range_refresh:
        filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
        return filtered[-limit:], "cache"

    now = datetime.utcnow()
    fetch_start = _to_ymd_compact(start_date)
    fetch_end = _to_ymd_compact(end_date) or now.strftime("%Y%m%d")
    if not fetch_start:
        if cached_klines and not force_refresh:
            last_date = cached_klines[-1].get("date")
            fetch_start = _to_ymd_compact(last_date) or (now - timedelta(days=365)).strftime("%Y%m%d")
        else:
            fetch_start = (now - timedelta(days=365)).strftime("%Y%m%d")

    interval = _yahoo_interval(period)
    fetch_source = "yahoo"
    data, channel = router_fetch(
        DataType.KLINE_DAILY,
        market="us",
        symbol=ticker,
        start=fetch_start,
        end=fetch_end,
        interval=interval,
    )
    new_klines = data or []
    if channel:
        fetch_source = channel
    if not new_klines:
        filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
        return filtered[-limit:], "cache"

    if cached_klines and not force_refresh:
        cached_klines = _merge_klines(cached_klines, new_klines, period)
    else:
        cached_klines = new_klines

    save_cache(symbol, period, {
        "symbol": symbol,
        "period": period,
        "last_update_date": now.strftime("%Y%m%d"),
        "last_update_time": now.timestamp(),
        "source": fetch_source,
        "klines": cached_klines,
    })

    filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
    return filtered[-limit:], fetch_source


def is_etf(code: str) -> bool:
    """判断是否为 ETF 基金 (简单规则: 以 15, 16 或 5 开头)"""
    return code.startswith(("15", "16", "5"))


def format_sina_symbol(symbol: str) -> str:
    """格式化为新浪格式: sh600519 或 sz000001"""
    code = symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")
    if code.startswith(("6", "9", "5")): # 5 for SH ETF
        return f"sh{code}"
    elif code.startswith(("0", "2", "3", "1")): # 1 for SZ ETF
        return f"sz{code}"
    else:
        return f"sh{code}"


def _get_tencent_prefer_etf_codes() -> set:
    raw = os.getenv("TENCENT_DAILY_ETF_CODES", "159941")
    items = re.split(r"[,\s]+", raw.strip()) if raw else []
    codes = set()
    for item in items:
        if not item:
            continue
        code = item.upper().replace("SH", "").replace("SZ", "").replace(".", "")
        if code:
            codes.add(code)
    return codes


def _pick_column(df, candidates: List[str]) -> Optional[str]:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _safe_float(value) -> float:
    try:
        if value is None:
            return 0
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in ("", "--", "None", "nan"):
                return 0
            return float(text)
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return 0
        return num
    except Exception:
        return 0




def _to_timestamp_cn(date_str: str, fmt: str) -> int:
    dt = datetime.strptime(date_str, fmt)
    if CN_TZ:
        dt = dt.replace(tzinfo=CN_TZ)
    return int(dt.timestamp() * 1000)


def _infer_volume_multiplier(df, volume_col: str, amount_col: Optional[str], price_col: Optional[str]) -> int:
    """Infer whether volume is in lots (hands) or shares using amount/price ratio."""
    if not amount_col or amount_col not in df.columns:
        return 100
    ratios = []
    for _, row in df.head(200).iterrows():
        vol = _safe_float(row[volume_col])
        amt = _safe_float(row[amount_col])
        if vol <= 0 or amt <= 0:
            continue
        price = _safe_float(row[price_col]) if price_col else 0
        if price <= 0:
            continue
        ratios.append((amt / vol) / price)
    if not ratios:
        return 100
    ratios.sort()
    median = ratios[len(ratios) // 2]
    # Typical ratio is ~100 when volume is in lots and amount is in RMB.
    if median >= 10:
        return 100
    # Some sources report amount in 10k RMB; detect tiny ratios and re-evaluate.
    if median < 0.1 and (median * 10000) >= 10:
        return 100
    return 1


def _normalize_intraday_volume(kline: Dict, history: List[Dict]) -> Dict:
    """Fix obvious 100x/10000x volume spikes vs recent daily history."""
    try:
        vol = _safe_float(kline.get("volume"))
        if vol <= 0:
            return kline
        date_str = kline.get("date")
        vols = []
        for item in history:
            if date_str and item.get("date") == date_str:
                continue
            v = _safe_float(item.get("volume"))
            if v > 0:
                vols.append(v)
        if len(vols) < 5:
            return kline
        vols.sort()
        median = vols[len(vols) // 2]
        if median <= 0:
            return kline
        ratio = vol / median
        if ratio <= 30:
            return kline
        candidates = [(vol / 100.0), (vol / 10000.0)]
        for candidate in candidates:
            if candidate <= 0:
                continue
            r = candidate / median
            if 0.2 <= r <= 5:
                kline["volume"] = candidate
                return kline
    except Exception:
        return kline
    return kline


def _env_enabled(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _build_intraday_daily_kline(
    code: str,
    date_ymd: str,
    history: List[Dict],
    force_remote: bool = False
) -> Optional[Dict]:
    # Prefer Tencent quote-derived daily bar to avoid unstable minute API timeouts.
    tencent_kline = build_daily_from_quote(code, date_ymd)
    if tencent_kline:
        return _normalize_intraday_volume(tencent_kline, history)

    if not _env_enabled("INTRADAY_MINUTE_FALLBACK", "0"):
        return None

    minute_kline = build_daily_kline_from_minutes(code, date_ymd, force_remote=force_remote)
    if minute_kline:
        return _normalize_intraday_volume(minute_kline, history)
    return None


def build_daily_kline_from_minutes(code: str, date_ymd: str, force_remote: bool = False) -> Optional[Dict]:
    """Build a daily kline for date_ymd (YYYYMMDD) from 1m data."""
    if _should_skip_source("eastmoney", force_remote):
        print("[Backoff] Skip eastmoney minute data fetch")
        return None
    if should_skip_remote(force_remote):
        print("[Backoff] Skip minute data fetch")
        return None

    data, channel = router_fetch(
        DataType.KLINE_MINUTE,
        symbol=code,
        period="1",
        start=date_ymd,
        end=date_ymd,
    )
    if not data:
        return None
    minute_klines = list(data)
    minute_klines.sort(key=lambda x: x.get("openTime") or 0)
    if not minute_klines:
        return None

    try:
        open_price = _safe_float(minute_klines[0].get("open"))
        close_price = _safe_float(minute_klines[-1].get("close"))
        high_price = max(_safe_float(item.get("high")) for item in minute_klines)
        low_price = min(_safe_float(item.get("low")) for item in minute_klines)
        volume = sum(_safe_float(item.get("volume")) for item in minute_klines)
    except Exception:
        return None

    date_iso = datetime.strptime(date_ymd, "%Y%m%d").strftime("%Y-%m-%d")
    timestamp = _to_timestamp_cn(date_iso, "%Y-%m-%d")

    kline = {
        "date": date_iso,
        "time": None,
        "openTime": timestamp,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "closeTime": timestamp + 86400000 - 1,
    }
    record_success()
    if channel:
        _record_source_success(channel)
    return kline


def fetch_from_eastmoney(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    """从东方财富获取数据"""
    if _should_skip_source("eastmoney"):
        print("[Backoff] Skip eastmoney kline fetch")
        return None
    print(f"[DataSource] Using eastmoney for {code}")
    data_type = DataType.KLINE_DAILY if period in DAILY_PERIODS else DataType.KLINE_MINUTE
    data, channel = router_fetch(
        data_type,
        channels=["akshare"],
        symbol=code,
        period=period,
        start=fetch_start,
        end=fetch_end,
        provider="eastmoney",
    )
    if channel:
        _record_source_success(channel)
    return data


def fetch_from_sina(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    """从新浪财经获取数据"""
    if _should_skip_source("sina"):
        print("[Backoff] Skip sina kline fetch")
        return None
    sina_symbol = format_sina_symbol(code)
    print(f"[DataSource] Using sina for {sina_symbol}")
    data_type = DataType.KLINE_DAILY if period in DAILY_PERIODS else DataType.KLINE_MINUTE
    data, channel = router_fetch(
        data_type,
        channels=["sina"],
        symbol=code,
        period=period,
        start=fetch_start,
        end=fetch_end,
        provider="sina",
    )
    if channel:
        _record_source_success(channel)
    return data


def fetch_klines_from_source(code: str, period: str, fetch_start: str, fetch_end: str, force_remote: bool = False) -> Optional[List[Dict]]:
    """从配置的数据源获取数据，失败则回退到另一个"""
    if should_skip_remote(force_remote):
        print("[Backoff] Skip remote kline fetch")
        return None
    if period in DAILY_PERIODS and is_etf(code):
        prefer_codes = _get_tencent_prefer_etf_codes()
        if code in prefer_codes:
            print(f"[DataSource] Prefer tencent for ETF {code}")
            data, channel = router_fetch(
                DataType.KLINE_DAILY,
                channels=["tencent", "sina", "akshare"],
                symbol=code,
                period=period,
                start=fetch_start,
                end=fetch_end,
                provider="eastmoney",
            )
            if data is not None:
                record_success()
                return data

    source = get_data_source()
    channels = ["sina", "akshare", "tencent"] if source == "sina" else ["akshare", "sina", "tencent"]
    data_type = DataType.KLINE_DAILY if period in DAILY_PERIODS else DataType.KLINE_MINUTE
    data, channel = router_fetch(
        data_type,
        channels=channels,
        symbol=code,
        period=period,
        start=fetch_start,
        end=fetch_end,
        provider="eastmoney" if source != "sina" else "sina",
    )
    if data is None:
        record_failure("klines_fetch_failed")
        return None

    record_success()
    return data


def get_klines_with_cache(
    symbol: str,
    period: str = "daily",
    start_date: str = None,
    end_date: str = None,
    limit: int = 365,
    force_refresh: bool = False,
    include_intraday: bool = True
) -> Tuple[List[Dict], str]:
    """获取K线数据（带缓存）"""
    if is_cn_index_symbol(symbol):
        return get_cn_index_klines_with_cache(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            force_refresh=force_refresh,
        )
    if is_us_index_symbol(symbol):
        return get_us_index_klines_with_cache(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            force_refresh=force_refresh
        )
    if _is_us_equity_symbol(symbol):
        return get_us_equity_klines_with_cache(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            force_refresh=force_refresh
        )
    now = _now_cn()
    today = now.date()
    today_str = today.strftime("%Y%m%d")
    today_iso = today.strftime("%Y-%m-%d")
    trade_date = _latest_trading_date(now)
    trade_str = trade_date.strftime("%Y%m%d")
    trade_iso = trade_date.strftime("%Y-%m-%d")
    effective_date = trade_date if period in DAILY_PERIODS else today
    effective_str = trade_str if period in DAILY_PERIODS else today_str
    effective_iso = trade_iso if period in DAILY_PERIODS else today_iso
    
    code = symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")
    
    cache = load_cache(symbol, period)
    cached_klines = cache.get("klines", []) if cache else []
    cached_today = None
    if cached_klines:
        for k in cached_klines:
            if k.get("date") == effective_iso:
                cached_today = k
                break

    daily_gap_window = max(1, int(os.getenv("DAILY_GAP_CHECK_WINDOW", "30") or 30))
    daily_backfill_days = max(1, int(os.getenv("DAILY_SYNC_LOOKBACK_DAYS", "40") or 40))
    daily_range_refresh = False
    daily_gap_refresh = False
    if period in DAILY_PERIODS:
        daily_range_refresh = _needs_daily_range_refresh(cached_klines, start_date, end_date, trade_iso)
        daily_gap_refresh = _has_missing_recent_trading_days(cached_klines, daily_gap_window)
    
    if not force_refresh:
        need_refresh, reason = should_refresh_cache(cache, period)
        if period in DAILY_PERIODS:
            if daily_range_refresh:
                need_refresh = True
                reason = "range_missing"
            elif daily_gap_refresh:
                need_refresh = True
                reason = f"missing_recent_{daily_gap_window}"
        if not need_refresh:
            print(f"[Cache] Using cache for {symbol} ({reason})")
            filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
            if period in DAILY_PERIODS:
                filtered = _strip_future_klines(filtered, trade_iso)
            return filtered[-limit:], "cache"
    else:
        reason = "force_refresh"
    
    print(f"[Cache] Refreshing {symbol} (reason: {reason})")
    
    if cached_klines and not force_refresh:
        if period in DAILY_PERIODS:
            start_iso = _normalize_ymd(start_date)
            first_cached = _kline_date_iso(cached_klines[0]) if cached_klines else ""
            last_cached = _kline_date_iso(cached_klines[-1]) if cached_klines else ""
            fetch_start_iso = None
            if start_iso and first_cached and start_iso < first_cached:
                fetch_start_iso = start_iso
            if not fetch_start_iso:
                anchor_date = _parse_iso_date(last_cached or effective_iso)
                if anchor_date:
                    fetch_start_iso = (anchor_date - timedelta(days=daily_backfill_days)).strftime("%Y-%m-%d")
                else:
                    fetch_start_iso = start_iso or (effective_date - timedelta(days=365)).strftime("%Y-%m-%d")
                if start_iso and start_iso < fetch_start_iso:
                    fetch_start_iso = start_iso
            fetch_start = _to_ymd_compact(fetch_start_iso) or (effective_date - timedelta(days=365)).strftime("%Y%m%d")
        else:
            last_date = cached_klines[-1].get("date", "")
            if last_date:
                fetch_start = _to_ymd_compact(last_date)
            else:
                fetch_start = _to_ymd_compact(start_date) or (effective_date - timedelta(days=365)).strftime("%Y%m%d")
    else:
        fetch_start = _to_ymd_compact(start_date) or (effective_date - timedelta(days=365)).strftime("%Y%m%d")
    
    fetch_end = _to_ymd_compact(end_date) or effective_str
    if period in DAILY_PERIODS and fetch_end > effective_str:
        fetch_end = effective_str
    
    print(f"[Cache] Fetching {code} from {fetch_start} to {fetch_end}")
    
    new_klines = fetch_klines_from_source(code, period, fetch_start, fetch_end, force_remote=force_refresh)
    
    if new_klines is None:
        print(f"[Cache] No new data for {symbol}")
        temp_klines = cached_klines
        intraday_enabled = period in DAILY_PERIODS and include_intraday and _now_cn().weekday() < 5 and trade_date == today
        if intraday_enabled:
            today_kline = _build_intraday_daily_kline(code, effective_str, temp_klines, force_remote=force_refresh)
            if today_kline:
                today_date = today_kline.get("date")
                temp_klines = [k for k in temp_klines if k.get("date") != today_date]
                temp_klines.append(today_kline)
                temp_klines.sort(key=lambda x: x.get("openTime", 0))
        source = "backoff" if should_skip_remote(force_refresh) else "cache"
        if period in DAILY_PERIODS:
            temp_klines = _strip_future_klines(temp_klines, trade_iso)
        # Persist refreshed intraday bar / heartbeat update even when remote fetch returns no rows.
        # This keeps downstream context builders (e.g. market thermometer) reading the latest cache state.
        try:
            save_cache(symbol, period, {
                "symbol": symbol,
                "period": period,
                "last_update_date": effective_str,
                "last_update_time": now.timestamp(),
                "source": source,
                "klines": temp_klines,
            })
        except Exception:
            pass
        filtered = _filter_klines_by_date(temp_klines, start_date, end_date)
        return filtered[-limit:], source
    
    source = get_data_source() # capture source for return
    
    if cached_klines and new_klines and not force_refresh:
        cached_klines = _merge_klines(cached_klines, new_klines, period)
    elif new_klines:
        cached_klines = new_klines

    # Add or refresh today's daily kline using minute data (intraday view).
    intraday_enabled = period in DAILY_PERIODS and include_intraday and _now_cn().weekday() < 5 and trade_date == today
    if intraday_enabled:
        today_kline = _build_intraday_daily_kline(code, effective_str, cached_klines, force_remote=force_refresh)
        if today_kline:
            today_date = today_kline.get("date")
            cached_klines = [k for k in cached_klines if k.get("date") != today_date]
            cached_klines.append(today_kline)
            cached_klines.sort(key=lambda x: x.get("openTime", 0))
        else:
            has_today = any(k.get("date") == effective_iso for k in cached_klines)
            if not has_today:
                if cached_today:
                    cached_klines.append(cached_today)
                    cached_klines.sort(key=lambda x: x.get("openTime", 0))

    if period in DAILY_PERIODS:
        cached_klines = _strip_future_klines(cached_klines, trade_iso)

    save_cache(symbol, period, {
        "symbol": symbol,
        "period": period,
        "last_update_date": effective_str,
        "last_update_time": now.timestamp(),
        "source": source,
        "klines": cached_klines,
    })
    
    filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
    return filtered[-limit:], source


def force_sync(symbol: str, period: str = "daily", include_intraday: Optional[bool] = None) -> Tuple[List[Dict], str]:
    """强制同步数据"""
    if include_intraday is None:
        include_intraday = period not in DAILY_PERIODS
    print(f"[Cache] Force sync triggered for {symbol}")
    return get_klines_with_cache(symbol, period, force_refresh=True, include_intraday=bool(include_intraday))
