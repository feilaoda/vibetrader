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
import urllib.request
from datetime import datetime, date, timedelta, time as dtime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Literal
import akshare as ak
from akshare_guard import should_skip_remote, record_failure, record_success, throttle

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

CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else None


def _now_cn() -> datetime:
    return datetime.now(CN_TZ) if CN_TZ else datetime.now()


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


def load_cache(symbol: str, period: str) -> Optional[Dict]:
    """加载缓存数据"""
    cache_path = get_cache_path(symbol, period)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Cache] Error loading cache: {e}")
    return None


def save_cache(symbol: str, period: str, data: Dict):
    """保存缓存数据"""
    cache_path = get_cache_path(symbol, period)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"[Cache] Saved {len(data.get('klines', []))} klines to {cache_path.name}")
    except Exception as e:
        print(f"[Cache] Error saving cache: {e}")


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


def _get_market_prefix_from_code(code: str) -> str:
    if code.startswith(("6", "9", "5")):
        return "sh"
    if code.startswith(("0", "2", "3", "1")):
        return "sz"
    if code.startswith(("8", "4")):
        return "bj"
    return "sh"


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


def _ymd_compact_to_iso(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    if "-" in date_str:
        return date_str
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


def fetch_from_tencent(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    """从腾讯获取日线数据（ETF 兜底）"""
    if period not in DAILY_PERIODS:
        return None
    start_iso = _ymd_compact_to_iso(fetch_start)
    end_iso = _ymd_compact_to_iso(fetch_end)
    if not start_iso or not end_iso:
        return None
    market_prefix = _get_market_prefix_from_code(code)
    key = f"{market_prefix}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={key},day,{start_iso},{end_iso},640,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        node = data.get("data", {}).get(key) or {}
        series = node.get("qfqday") or node.get("day") or []
        if not series:
            return None
        klines = []
        for row in series:
            if not row or len(row) < 6:
                continue
            date_str = row[0]
            timestamp = _to_timestamp_cn(date_str, "%Y-%m-%d")
            volume_raw = _safe_float(row[5])
            volume = volume_raw * 100 if volume_raw else 0
            klines.append({
                "date": date_str,
                "time": None,
                "openTime": timestamp,
                "open": _safe_float(row[1]),
                "high": _safe_float(row[3]),
                "low": _safe_float(row[4]),
                "close": _safe_float(row[2]),
                "volume": float(volume),
                "closeTime": timestamp + 86400000 - 1,
            })
        return klines
    except Exception as e:
        print(f"[tencent] Error: {e}")
        return None


def _fetch_tencent_quote(code: str) -> Optional[Dict]:
    market_prefix = _get_market_prefix_from_code(code)
    url = f"https://qt.gtimg.cn/q={market_prefix}{code}"

    def _do():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read()

    raw = _do()
    if not raw:
        return None
    try:
        text = raw.decode("gbk", errors="ignore")
    except Exception:
        text = raw.decode("utf-8", errors="ignore")
    match = re.search(r'="([^"]+)"', text)
    if not match:
        return None
    parts = match.group(1).split("~")
    if len(parts) < 35:
        return None
    price = _safe_float(parts[3] if len(parts) > 3 else 0)
    prev_close = _safe_float(parts[4] if len(parts) > 4 else 0)
    open_p = _safe_float(parts[5] if len(parts) > 5 else 0)
    volume_lot = _safe_float(parts[6] if len(parts) > 6 else 0)
    high = _safe_float(parts[33] if len(parts) > 33 else 0)
    low = _safe_float(parts[34] if len(parts) > 34 else 0)
    time_str = parts[30] if len(parts) > 30 else ""
    ts = int(_now_cn().timestamp() * 1000)
    if time_str and ":" in time_str:
        try:
            dt = datetime.strptime(f"{_now_cn().strftime('%Y-%m-%d')} {time_str}", "%Y-%m-%d %H:%M:%S")
            if CN_TZ:
                dt = dt.replace(tzinfo=CN_TZ)
            ts = int(dt.timestamp() * 1000)
        except Exception:
            pass
    return {
        "price": price,
        "open": open_p,
        "high": high,
        "low": low,
        "volume": volume_lot * 100,
        "timestamp": ts,
        "prev_close": prev_close
    }


def build_daily_kline_from_tencent(code: str, date_ymd: str) -> Optional[Dict]:
    if not date_ymd:
        return None
    quote = _fetch_tencent_quote(code)
    if not quote:
        return None
    close_price = _safe_float(quote.get("price"))
    if close_price <= 0:
        return None
    open_price = _safe_float(quote.get("open")) or close_price
    high_price = _safe_float(quote.get("high")) or max(open_price, close_price)
    low_price = _safe_float(quote.get("low")) or min(open_price, close_price)
    volume = _safe_float(quote.get("volume"))
    date_iso = datetime.strptime(date_ymd, "%Y%m%d").strftime("%Y-%m-%d")
    timestamp = _to_timestamp_cn(date_iso, "%Y-%m-%d")
    return {
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


def build_daily_kline_from_minutes(code: str, date_ymd: str, force_remote: bool = False) -> Optional[Dict]:
    """Build a daily kline for date_ymd (YYYYMMDD) from 1m data."""
    if should_skip_remote(force_remote):
        print("[Backoff] Skip minute data fetch")
        return None

    try:
        if is_etf(code):
            if hasattr(ak, "fund_etf_hist_min_em"):
                throttle(scope="akshare_minute")
                df = ak.fund_etf_hist_min_em(
                    symbol=code,
                    period="1",
                    start_date=f"{date_ymd} 09:30:00",
                    end_date=f"{date_ymd} 15:00:00",
                    adjust="qfq",
                )
            else:
                return None
        else:
            throttle(scope="akshare_minute")
            df = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period="1",
                start_date=f"{date_ymd} 09:30:00",
                end_date=f"{date_ymd} 15:00:00",
                adjust="qfq",
            )
    except Exception as e:
        print(f"[intraday] Error fetching minute data: {e}")
        record_failure(f"minute_kline_failed: {e}")
        return None

    if df is None or df.empty:
        return None

    open_col = _pick_column(df, ["开盘", "open"])
    close_col = _pick_column(df, ["收盘", "close"])
    high_col = _pick_column(df, ["最高", "high"])
    low_col = _pick_column(df, ["最低", "low"])
    vol_col = _pick_column(df, ["成交量", "volume"])
    amt_col = _pick_column(df, ["成交额", "amount"])

    if not all([open_col, close_col, high_col, low_col]):
        return None

    open_price = _safe_float(df.iloc[0][open_col])
    close_price = _safe_float(df.iloc[-1][close_col])
    high_price = _safe_float(df[high_col].max())
    low_price = _safe_float(df[low_col].min())
    volume_raw = _safe_float(df[vol_col].sum()) if vol_col else 0
    multiplier = _infer_volume_multiplier(df, vol_col, amt_col, close_col)
    volume = volume_raw * multiplier if volume_raw else 0

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
    return kline


def fetch_from_eastmoney(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    """从东方财富获取数据"""
    print(f"[DataSource] Using eastmoney for {code}")
    try:
        if is_etf(code):
             # ETF 数据
            if period in DAILY_PERIODS:
                throttle(scope="akshare_kline")
                df = ak.fund_etf_hist_em(
                    symbol=code,
                    period=period,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    adjust="qfq",
                )
            else:
                # 分钟数据 (ETF 优先使用专用接口，失败再回退到股票接口)
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
            # 股票数据
            if period in DAILY_PERIODS:
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
        
        if df is None or df.empty:
            return None

        vol_col = _pick_column(df, ["成交量", "volume"])
        amt_col = _pick_column(df, ["成交额", "amount"])
        close_col = _pick_column(df, ["收盘", "close"])
        multiplier = _infer_volume_multiplier(df, vol_col, amt_col, close_col) if vol_col else 100
        
        klines = []
        for _, row in df.iterrows():
            try:
                if period in DAILY_PERIODS:
                    date_str = str(row["日期"])
                    timestamp = _to_timestamp_cn(date_str, "%Y-%m-%d")
                else:
                    date_str = str(row["时间"])
                    timestamp = _to_timestamp_cn(date_str, "%Y-%m-%d %H:%M:%S")
                
                volume_raw = _safe_float(row["成交量"])
                # Convert to shares using inferred multiplier (hands -> shares).
                volume = volume_raw * multiplier if volume_raw else 0
                klines.append({
                    "date": date_str.split(" ")[0] if " " in date_str else date_str,
                    "time": date_str if " " in date_str else None,
                    "openTime": timestamp,
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": float(row["收盘"]),
                    "volume": float(volume),
                    "closeTime": timestamp + (86400000 if period in DAILY_PERIODS else 60000) - 1,
                })
            except Exception as e:
                print(f"[eastmoney] Error parsing row: {e}")
                continue
        
        return klines
    except Exception as e:
        print(f"[eastmoney] Error: {e}")
        return None


def fetch_from_sina(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    """从新浪财经获取数据"""
    sina_symbol = format_sina_symbol(code)
    print(f"[DataSource] Using sina for {sina_symbol}")
    
    try:
        if period in DAILY_PERIODS:
            if is_etf(code):
                 # ETF (Sina)
                 throttle(scope="akshare_kline")
                 df = ak.fund_etf_hist_sina(
                    symbol=sina_symbol,
                )
                 # SINA ETF 接口可能不支持时间范围筛选，需要手动过滤
                 # 且列名可能不同
                 if df is not None and not df.empty:
                    df['date'] = df['date'].astype(str)
                    mask = (df['date'] >= datetime.strptime(fetch_start, "%Y%m%d").strftime("%Y-%m-%d")) & \
                           (df['date'] <= datetime.strptime(fetch_end, "%Y%m%d").strftime("%Y-%m-%d"))
                    df = df.loc[mask]
            else:
                throttle(scope="akshare_kline")
                df = ak.stock_zh_a_daily(
                    symbol=sina_symbol,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    adjust="qfq",
                )
        else:
            # 新浪不支持分钟级别，回退到东方财富
            print(f"[sina] Minute data not supported, falling back to eastmoney")
            return fetch_from_eastmoney(code, period, fetch_start, fetch_end)
        
        if df is None or df.empty:
            return None
        
        vol_col = _pick_column(df, ["volume", "成交量"])
        amt_col = _pick_column(df, ["amount", "成交额"])
        close_col = _pick_column(df, ["close", "收盘"])
        multiplier = 100
        if vol_col:
            if amt_col:
                multiplier = _infer_volume_multiplier(df, vol_col, amt_col, close_col)
            else:
                try:
                    median_vol = _safe_float(df[vol_col].median())
                except Exception:
                    median_vol = 0
                # Heuristic: Sina daily volume may already be in shares.
                # If median is very large, assume shares to avoid 100x inflation.
                if median_vol >= 5e7:
                    multiplier = 1

        klines = []
        for _, row in df.iterrows():
            try:
                # 新浪数据使用英文字段名: date, open, high, low, close, volume
                date_str = str(row["date"])
                timestamp = _to_timestamp_cn(date_str, "%Y-%m-%d")
                
                volume_raw = _safe_float(row[vol_col]) if vol_col else 0
                # Convert to shares using inferred/heuristic multiplier.
                volume = volume_raw * multiplier if volume_raw else 0
                klines.append({
                    "date": date_str,
                    "time": None,
                    "openTime": timestamp,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(volume),
                    "closeTime": timestamp + 86400000 - 1,
                })
            except Exception as e:
                print(f"[sina] Error parsing row: {e}")
                continue

        # Check if missing today
        if klines and period in DAILY_PERIODS:
            try:
                last_date = klines[-1]['date']
                now = _now_cn()
                # Check if it's a weekday and time is > 09:30? No need, logic is if date < today
                today_str = now.strftime("%Y-%m-%d")
                
                if last_date < today_str:
                    print(f"[sina] Last date {last_date} < {today_str}, trying to fetch today from eastmoney...")
                    t_str = now.strftime("%Y%m%d")
                    
                    # Retry logic for today's data
                    for retry in range(3):
                        try:
                            # Try fetch just today
                            todays_data = fetch_from_eastmoney(code, period, t_str, t_str)
                            if todays_data:
                                # Append unique
                                if todays_data[0]['date'] > last_date:
                                    klines.extend(todays_data)
                                    print(f"[sina] Appended today's candle (Retry {retry+1}): {todays_data[0]['date']} {todays_data[0]['close']}")
                                    break
                        except Exception as e:
                            print(f"[sina] Retry {retry+1} failed to fetch today: {e}")
                            import time
                            time.sleep(1)
            except Exception as e:
                print(f"[sina] Error checking today: {e}")
        
        return klines
    except Exception as e:
        print(f"[sina] Error: {e}")
        return None


def fetch_klines_from_source(code: str, period: str, fetch_start: str, fetch_end: str, force_remote: bool = False) -> Optional[List[Dict]]:
    """从配置的数据源获取数据，失败则回退到另一个"""
    if should_skip_remote(force_remote):
        print("[Backoff] Skip remote kline fetch")
        return None

    if period in DAILY_PERIODS and is_etf(code):
        prefer_codes = _get_tencent_prefer_etf_codes()
        if code in prefer_codes:
            print(f"[DataSource] Prefer tencent for ETF {code}")
            tencent = fetch_from_tencent(code, period, fetch_start, fetch_end)
            if tencent is not None:
                record_success()
                return tencent

    source = get_data_source()
    
    if source == "sina":
        result = fetch_from_sina(code, period, fetch_start, fetch_end)
        if result is None:
            print(f"[DataSource] Sina failed, trying eastmoney...")
            result = fetch_from_eastmoney(code, period, fetch_start, fetch_end)
    else:
        result = fetch_from_eastmoney(code, period, fetch_start, fetch_end)
        if result is None:
            print(f"[DataSource] Eastmoney failed, trying sina...")
            result = fetch_from_sina(code, period, fetch_start, fetch_end)
    if result is None:
        if period in DAILY_PERIODS and is_etf(code):
            print(f"[DataSource] Eastmoney/Sina failed, trying tencent for {code}")
            tencent = fetch_from_tencent(code, period, fetch_start, fetch_end)
            if tencent is not None:
                record_success()
                return tencent
        record_failure("klines_fetch_failed")
        return None

    record_success()
    return result


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
    
    if not force_refresh:
        need_refresh, reason = should_refresh_cache(cache, period)
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
        last_date = cached_klines[-1].get("date", "")
        if last_date:
            fetch_start = _to_ymd_compact(last_date)
        else:
            fetch_start = _to_ymd_compact(start_date) or (effective_date - timedelta(days=365)).strftime("%Y%m%d")
    else:
        fetch_start = _to_ymd_compact(start_date) or (effective_date - timedelta(days=365)).strftime("%Y%m%d")
    
    fetch_end = _to_ymd_compact(end_date) or effective_str
    
    print(f"[Cache] Fetching {code} from {fetch_start} to {fetch_end}")
    
    new_klines = fetch_klines_from_source(code, period, fetch_start, fetch_end, force_remote=force_refresh)
    
    if new_klines is None:
        print(f"[Cache] No new data for {symbol}")
        temp_klines = cached_klines
        intraday_enabled = period in DAILY_PERIODS and include_intraday and _now_cn().weekday() < 5 and trade_date == today
        if intraday_enabled:
            today_kline = build_daily_kline_from_minutes(code, effective_str, force_remote=force_refresh)
            if today_kline:
                today_date = today_kline.get("date")
                today_kline = _normalize_intraday_volume(today_kline, temp_klines)
                temp_klines = [k for k in temp_klines if k.get("date") != today_date]
                temp_klines.append(today_kline)
                temp_klines.sort(key=lambda x: x.get("openTime", 0))
            else:
                tencent_kline = build_daily_kline_from_tencent(code, effective_str)
                if tencent_kline:
                    today_date = tencent_kline.get("date")
                    tencent_kline = _normalize_intraday_volume(tencent_kline, temp_klines)
                    temp_klines = [k for k in temp_klines if k.get("date") != today_date]
                    temp_klines.append(tencent_kline)
                    temp_klines.sort(key=lambda x: x.get("openTime", 0))
        source = "backoff" if should_skip_remote(force_refresh) else "cache"
        if period in DAILY_PERIODS:
            temp_klines = _strip_future_klines(temp_klines, trade_iso)
        filtered = _filter_klines_by_date(temp_klines, start_date, end_date)
        return filtered[-limit:], source
    
    source = get_data_source() # capture source for return
    
    if cached_klines and new_klines and not force_refresh:
        existing_times = {k.get("openTime") for k in cached_klines}
        for kline in new_klines:
            if kline.get("openTime") not in existing_times:
                cached_klines.append(kline)
        cached_klines.sort(key=lambda x: x.get("openTime", 0))
    elif new_klines:
        cached_klines = new_klines

    # Add or refresh today's daily kline using minute data (intraday view).
    intraday_enabled = period in DAILY_PERIODS and include_intraday and _now_cn().weekday() < 5 and trade_date == today
    if intraday_enabled:
        today_kline = build_daily_kline_from_minutes(code, effective_str, force_remote=force_refresh)
        if today_kline:
            today_date = today_kline.get("date")
            today_kline = _normalize_intraday_volume(today_kline, cached_klines)
            cached_klines = [k for k in cached_klines if k.get("date") != today_date]
            cached_klines.append(today_kline)
            cached_klines.sort(key=lambda x: x.get("openTime", 0))
        else:
            has_today = any(k.get("date") == effective_iso for k in cached_klines)
            if not has_today:
                tencent_kline = build_daily_kline_from_tencent(code, effective_str)
                if tencent_kline:
                    today_date = tencent_kline.get("date")
                    tencent_kline = _normalize_intraday_volume(tencent_kline, cached_klines)
                    cached_klines = [k for k in cached_klines if k.get("date") != today_date]
                    cached_klines.append(tencent_kline)
                    cached_klines.sort(key=lambda x: x.get("openTime", 0))
                elif cached_today:
                    cached_klines.append(cached_today)
                    cached_klines.sort(key=lambda x: x.get("openTime", 0))

    if period in DAILY_PERIODS:
        cached_klines = _strip_future_klines(cached_klines, trade_iso)

    save_cache(symbol, period, {
        "symbol": symbol,
        "period": period,
        "last_update_date": effective_str,
        "last_update_time": now.timestamp(),
        "klines": cached_klines,
    })
    
    filtered = _filter_klines_by_date(cached_klines, start_date, end_date)
    return filtered[-limit:], source


def force_sync(symbol: str, period: str = "daily") -> Tuple[List[Dict], str]:
    """强制同步数据"""
    print(f"[Cache] Force sync triggered for {symbol}")
    return get_klines_with_cache(symbol, period, force_refresh=True)
