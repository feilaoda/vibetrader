import threading
import time
from datetime import datetime, timedelta
import os
from typing import Dict, Optional

from db import get_connection
from cache import get_klines_with_cache, load_cache, _latest_trading_date
from industry import (
    _normalize_symbol,
    _load_profiles,
    _get_industry_from_db,
    _get_profile_override,
    _get_profile_by_id,
    _get_industry_settings,
    _recommend_profile,
    _parse_indicator_specs,
    _get_indicator_cache,
    _set_indicator_cache,
    _is_cache_fresh,
)

_scheduler_started = False


def _last_n_trading_dates(n: int) -> list:
    dates = []
    current = _latest_trading_date()
    while len(dates) < n:
        if current.weekday() < 5:
            dates.append(current)
        current = current - timedelta(days=1)
    return dates


def _is_leader_cache_incomplete(symbol: str) -> bool:
    cache = load_cache(symbol, "daily")
    klines = (cache or {}).get("klines", []) if cache else []
    if not klines:
        return True
    last_dates = {k.get("date") for k in klines if k.get("date")}
    if not last_dates:
        return True
    try:
        window = int(os.getenv("INDUSTRY_LEADER_WINDOW_DAYS", "30") or 30)
        if window <= 0:
            return False
        required = _last_n_trading_dates(window)
        for dt in required:
            if dt.strftime("%Y-%m-%d") not in last_dates:
                return True
    except Exception:
        return False
    return False


def _should_seed(conn, symbol: str, refresh_minutes: int) -> bool:
    key = f"sector_leader_seed:{symbol}"
    cached = _get_indicator_cache(conn, key)
    if cached:
        _, _, _, updated_at = cached
        if _is_cache_fresh(updated_at, refresh_minutes):
            return False
    return True


def _collect_leader_specs(conn) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT symbol FROM symbol_industry_settings WHERE enabled = TRUE"
    ).fetchall()
    if not rows:
        return {}
    profiles = _load_profiles(conn)
    leader_refresh: Dict[str, int] = {}
    for row in rows:
        sym = _normalize_symbol(row[0])
        settings = _get_industry_settings(conn, sym)
        if not settings or not settings[0]:
            continue
        industry_row = _get_industry_from_db(conn, sym)
        industry = industry_row[1] if industry_row else None
        override = _get_profile_override(conn, sym)
        profile = None
        if override and override[0]:
            profile = _get_profile_by_id(profiles, override[0])
        if not profile or not profile.get("enabled", True):
            rec = _recommend_profile(sym, industry, profiles)
            profile = _get_profile_by_id(profiles, rec.get("profile")) if profiles else None
        config = (profile or {}).get("config", {}) or {}
        refresh = int(config.get("refresh_minutes") or 30)
        specs = _parse_indicator_specs(config)
        for spec in specs:
            if (spec.get("type") or "").strip() != "sector_leaders":
                continue
            symbols = spec.get("symbols") or spec.get("items") or []
            if isinstance(symbols, str):
                symbols = [symbols]
            item_refresh = int(spec.get("refresh_minutes") or refresh)
            for item in symbols:
                leader = _normalize_symbol(str(item))
                current = leader_refresh.get(leader)
                if current is None or item_refresh < current:
                    leader_refresh[leader] = item_refresh
    return leader_refresh


def _should_refresh(conn, symbol: str, refresh_minutes: int) -> bool:
    key = f"sector_leader_sync:{symbol}"
    cached = _get_indicator_cache(conn, key)
    if cached:
        _, _, _, updated_at = cached
        if _is_cache_fresh(updated_at, refresh_minutes):
            return False
    return True


def _refresh_leader(conn, symbol: str, force_refresh: bool = False) -> Optional[str]:
    try:
        klines, source = get_klines_with_cache(symbol, period="daily", limit=2, force_refresh=force_refresh, include_intraday=True)
        last_date = None
        if klines:
            last = klines[-1]
            last_date = last.get("date") or last.get("time")
        payload = {
            "symbol": symbol,
            "last_date": last_date,
            "source": source
        }
        _set_indicator_cache(conn, f"sector_leader_sync:{symbol}", payload, source or "daily_cache")
        return None
    except Exception as exc:
        _set_indicator_cache(conn, f"sector_leader_sync:{symbol}", {}, "daily_cache", error=str(exc))
        return str(exc)


def _scheduler_loop() -> None:
    while True:
        try:
            conn = get_connection()
            try:
                leader_specs = _collect_leader_specs(conn)
                if not leader_specs:
                    time.sleep(60)
                    continue
                for leader, refresh in leader_specs.items():
                    # Seed missing/incomplete data with a force refresh
                    if _is_leader_cache_incomplete(leader):
                        if _should_seed(conn, leader, refresh):
                            err = _refresh_leader(conn, leader, force_refresh=True)
                            payload = {
                                "symbol": leader,
                                "seeded": True,
                                "error": err or ""
                            }
                            _set_indicator_cache(conn, f"sector_leader_seed:{leader}", payload, "daily_cache", error=err)
                            time.sleep(0.2)
                            continue
                    if not _should_refresh(conn, leader, refresh):
                        continue
                    _refresh_leader(conn, leader, force_refresh=False)
                    time.sleep(0.2)
            finally:
                conn.close()
        except Exception as exc:
            print(f"[IndustryScheduler] error: {exc}")
        time.sleep(60)


def start_industry_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
