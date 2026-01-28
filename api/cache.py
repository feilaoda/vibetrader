"""
股票数据缓存模块
- 历史数据缓存到本地 JSON 文件
- 交易时间内每分钟更新
- 收盘5分钟后停止自动更新
- 支持多数据源切换 (eastmoney / sina)
"""

import json
import os
from datetime import datetime, date, timedelta, time as dtime
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Literal
import akshare as ak

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
    now = datetime.now().time()
    for start, end in TRADING_HOURS:
        if start <= now <= end:
            return True
    return False


def should_auto_refresh() -> bool:
    """判断是否应该自动刷新"""
    now = datetime.now()
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
    if not cache:
        return True, "no_cache"
    
    last_update = cache.get("last_update_time")
    today_str = date.today().strftime("%Y%m%d")
    cache_date = cache.get("last_update_date", "")
    
    if cache_date != today_str:
        return True, "not_today"
    
    if period in DAILY_PERIODS:
        if not should_auto_refresh():
            return False, "after_market"
        if last_update:
            elapsed = datetime.now().timestamp() - last_update
            if elapsed < 60:
                return False, "too_recent"
        return True, "trading_time"
    else:
        if not should_auto_refresh():
            return False, "after_market"
        if last_update:
            elapsed = datetime.now().timestamp() - last_update
            if elapsed < 60:
                return False, "too_recent"
        return True, "minute_refresh"


def format_sina_symbol(symbol: str) -> str:
    """格式化为新浪格式: sh600519 或 sz000001"""
    code = symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")
    if code.startswith(("6", "9")):
        return f"sh{code}"
    elif code.startswith(("0", "2", "3")):
        return f"sz{code}"
    else:
        return f"sh{code}"


def fetch_from_eastmoney(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    """从东方财富获取数据"""
    print(f"[DataSource] Using eastmoney for {code}")
    try:
        if period in DAILY_PERIODS:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period=period,
                start_date=fetch_start,
                end_date=fetch_end,
                adjust="qfq",
            )
        else:
            df = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period=period,
                start_date=f"{fetch_start} 09:30:00",
                end_date=f"{fetch_end} 15:00:00",
                adjust="qfq",
            )
        
        if df is None or df.empty:
            return None
        
        klines = []
        for _, row in df.iterrows():
            try:
                if period in DAILY_PERIODS:
                    date_str = str(row["日期"])
                    timestamp = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
                else:
                    date_str = str(row["时间"])
                    timestamp = int(datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
                
                klines.append({
                    "date": date_str.split(" ")[0] if " " in date_str else date_str,
                    "time": date_str if " " in date_str else None,
                    "openTime": timestamp,
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": float(row["收盘"]),
                    "volume": float(row["成交量"]),
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
        
        klines = []
        for _, row in df.iterrows():
            try:
                # 新浪数据使用英文字段名: date, open, high, low, close, volume
                date_str = str(row["date"])
                timestamp = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
                
                klines.append({
                    "date": date_str,
                    "time": None,
                    "openTime": timestamp,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "closeTime": timestamp + 86400000 - 1,
                })
            except Exception as e:
                print(f"[sina] Error parsing row: {e}")
                continue
        
        return klines
    except Exception as e:
        print(f"[sina] Error: {e}")
        return None


def fetch_klines_from_source(code: str, period: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    """从配置的数据源获取数据，失败则回退到另一个"""
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
    
    return result


def get_klines_with_cache(
    symbol: str,
    period: str = "daily",
    start_date: str = None,
    end_date: str = None,
    limit: int = 365,
    force_refresh: bool = False
) -> List[Dict]:
    """获取K线数据（带缓存）"""
    today = date.today()
    today_str = today.strftime("%Y%m%d")
    now = datetime.now()
    
    code = symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")
    
    cache = load_cache(symbol, period)
    cached_klines = cache.get("klines", []) if cache else []
    
    if not force_refresh:
        need_refresh, reason = should_refresh_cache(cache, period)
        if not need_refresh:
            print(f"[Cache] Using cache for {symbol} ({reason})")
            return cached_klines[-limit:]
    else:
        reason = "force_refresh"
    
    print(f"[Cache] Refreshing {symbol} (reason: {reason})")
    
    if cached_klines and not force_refresh:
        last_date = cached_klines[-1].get("date", "")
        if last_date:
            fetch_start = last_date.replace("-", "")
        else:
            fetch_start = start_date or (today - timedelta(days=365)).strftime("%Y%m%d")
    else:
        fetch_start = start_date or (today - timedelta(days=365)).strftime("%Y%m%d")
    
    fetch_end = end_date or today_str
    
    print(f"[Cache] Fetching {code} from {fetch_start} to {fetch_end}")
    
    new_klines = fetch_klines_from_source(code, period, fetch_start, fetch_end)
    
    if new_klines is None:
        print(f"[Cache] No new data for {symbol}")
        return cached_klines[-limit:]
    
    print(f"[Cache] Got {len(new_klines)} rows from API")
    
    if cached_klines and new_klines and not force_refresh:
        existing_times = {k.get("openTime") for k in cached_klines}
        for kline in new_klines:
            if kline.get("openTime") not in existing_times:
                cached_klines.append(kline)
        cached_klines.sort(key=lambda x: x.get("openTime", 0))
    elif new_klines:
        cached_klines = new_klines
    
    save_cache(symbol, period, {
        "symbol": symbol,
        "period": period,
        "last_update_date": today_str,
        "last_update_time": now.timestamp(),
        "klines": cached_klines,
    })
    
    return cached_klines[-limit:]


def force_sync(symbol: str, period: str = "daily") -> List[Dict]:
    """强制同步数据"""
    print(f"[Cache] Force sync triggered for {symbol}")
    return get_klines_with_cache(symbol, period, force_refresh=True)
