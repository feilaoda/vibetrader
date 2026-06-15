import os
import threading
import time
from datetime import datetime, time as dtime
from typing import List

from watchlist import load_watchlist
from cache import (
    get_klines_with_cache,
    load_cache,
    _latest_trading_date,
    _now_cn,
    should_auto_refresh,
    US_INDEX_CACHE_TTL_SECONDS,
)
from us_indices import is_us_index_symbol

_scheduler_started = False


def _parse_hhmm(value: str, default: dtime) -> dtime:
    try:
        text = value.strip()
        if not text:
            return default
        parts = text.split(":")
        if len(parts) != 2:
            return default
        return dtime(int(parts[0]), int(parts[1]))
    except Exception:
        return default


def _in_premarket(now: datetime) -> bool:
    start = _parse_hhmm(os.getenv("WATCHLIST_PREMARKET_START", "08:30"), dtime(8, 30))
    end = _parse_hhmm(os.getenv("WATCHLIST_PREMARKET_END", "09:30"), dtime(9, 30))
    return start <= now.time() <= end


def _needs_us_refresh(symbol: str) -> bool:
    cache = load_cache(symbol, "daily")
    if not cache:
        return True
    last_update = cache.get("last_update_time")
    if not last_update:
        return True
    try:
        elapsed = datetime.utcnow().timestamp() - float(last_update)
    except Exception:
        return True
    return elapsed > US_INDEX_CACHE_TTL_SECONDS


def _needs_cn_refresh(symbol: str, trade_iso: str) -> bool:
    cache = load_cache(symbol, "daily")
    if not cache:
        return True
    klines = cache.get("klines", []) if cache else []
    if not klines:
        return True
    for k in reversed(klines[-5:]):
        if k.get("date") == trade_iso:
            return False
    return True


def _collect_symbols() -> List[str]:
    items = load_watchlist()
    symbols = []
    for item in items:
        if (item.get("market") or "").lower() not in ("ashare", "us"):
            continue
        sym = (item.get("symbol") or "").upper()
        if sym:
            symbols.append(sym)
    return symbols


def _sync_symbols(symbols: List[str]) -> None:
    if not symbols:
        return
    print(f"[WatchlistScheduler] syncing {len(symbols)} symbols...")
    now = _now_cn()
    trade_iso = _latest_trading_date(now).strftime("%Y-%m-%d")
    for sym in symbols:
        try:
            if is_us_index_symbol(sym):
                if not _needs_us_refresh(sym):
                    continue
            else:
                if not _needs_cn_refresh(sym, trade_iso):
                    continue
            get_klines_with_cache(sym, period="daily", limit=2, force_refresh=False, include_intraday=False)
            time.sleep(0.2)
        except Exception as exc:
            print(f"[WatchlistScheduler] sync failed {sym}: {exc}")


def _scheduler_loop() -> None:
    interval = int(os.getenv("WATCHLIST_SYNC_INTERVAL_SECONDS", "60") or 60)
    while True:
        try:
            now = _now_cn()
            if now.weekday() >= 5:
                time.sleep(interval)
                continue
            if not (should_auto_refresh() or _in_premarket(now)):
                time.sleep(interval)
                continue
            symbols = _collect_symbols()
            _sync_symbols(symbols)
        except Exception as exc:
            print(f"[WatchlistScheduler] error: {exc}")
        time.sleep(interval)


def start_watchlist_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
