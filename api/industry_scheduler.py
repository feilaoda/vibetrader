import threading
import time
from datetime import timedelta, time as dtime
import os
from typing import Dict, Optional, Any

from db import get_connection
from cache import get_klines_with_cache, load_cache, _latest_trading_date
from industry import (
    _normalize_symbol,
    _normalize_market_index_symbol,
    _resolve_market_index_candidates,
    _safe_float,
    _resolve_profile_config,
    _parse_indicator_specs,
    _get_indicator_cache,
    _set_indicator_cache,
    _is_cache_fresh,
    _fetch_indicator_with_cache,
    _build_market_thermometer_payload,
)
from trading_time import now_cn

_scheduler_started = False
_CN_TRADING_SESSIONS = [
    (dtime(9, 30), dtime(11, 30)),
    (dtime(13, 0), dtime(15, 0)),
]


def _to_minutes(t: dtime) -> int:
    return t.hour * 60 + t.minute


def _in_market_refresh_window(current) -> bool:
    if current.weekday() >= 5:
        return False
    now_m = _to_minutes(current.time())
    for start, end in _CN_TRADING_SESSIONS:
        start_m = _to_minutes(start)
        end_m = _to_minutes(end)
        in_session = start_m <= now_m <= end_m
        in_pre_open = max(0, start_m - 10) <= now_m < start_m
        in_post_close = end_m < now_m <= end_m + 10
        if in_session or in_pre_open or in_post_close:
            return True
    return False


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
    leader_refresh: Dict[str, int] = {}
    for row in rows:
        sym = _normalize_symbol(row[0])
        ctx = _resolve_profile_config(conn, sym)
        if not ctx.get("enabled"):
            continue
        config = ctx.get("config", {}) or {}
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
        klines, source = get_klines_with_cache(symbol, period="daily", limit=2, force_refresh=force_refresh, include_intraday=False)
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


def _collect_market_thermometer_specs(conn) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT symbol FROM symbol_industry_settings WHERE enabled = TRUE"
    ).fetchall()
    if not rows:
        return {}
    target_refresh = max(1, int(os.getenv("INDUSTRY_MARKET_REFRESH_MINUTES", "5") or 5))
    specs_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sym = _normalize_symbol(row[0])
        ctx = _resolve_profile_config(conn, sym)
        if not ctx.get("enabled"):
            continue
        config = ctx.get("config", {}) or {}
        specs = _parse_indicator_specs(config)
        mt_specs = [s for s in specs if (s.get("type") or "").strip() == "market_thermometer"]
        if not mt_specs:
            mt_specs = [{"type": "market_thermometer"}]
        for spec in mt_specs:
            broad = (
                ctx.get("market_broad_index")
                or spec.get("broad_index")
                or spec.get("all_market_index")
                or spec.get("index")
                or "000985.SH"
            )
            style = (
                ctx.get("market_style_index")
                or spec.get("style_index")
                or spec.get("style")
                or ("000688.SH" if sym.startswith("588") else "000300.SH")
            )
            broad_norm = _normalize_market_index_symbol(broad) or "000985.SH"
            style_norm = _normalize_market_index_symbol(style) or ("000688.SH" if sym.startswith("588") else "000300.SH")
            include_breadth = bool(spec.get("include_breadth", True))
            turnover_baseline = _safe_float(spec.get("turnover_baseline_trillion"), default=1.0)
            refresh = int(spec.get("refresh_minutes") or config.get("refresh_minutes") or target_refresh)
            refresh = min(max(1, refresh), target_refresh)
            key = f"market_thermometer:{broad_norm}:{style_norm}:{int(include_breadth)}:{turnover_baseline:.2f}"
            existing = specs_map.get(key)
            if existing and existing.get("refresh_minutes", refresh) <= refresh:
                continue
            specs_map[key] = {
                "key": key,
                "broad_index": broad_norm,
                "style_index": style_norm,
                "include_breadth": include_breadth,
                "turnover_baseline_trillion": turnover_baseline,
                "refresh_minutes": refresh,
            }
    return specs_map


def _refresh_market_thermometer(conn, spec: Dict[str, Any]) -> None:
    key = str(spec.get("key") or "")
    if not key:
        return
    refresh = max(1, int(spec.get("refresh_minutes") or 5))
    broad = str(spec.get("broad_index") or "000985.SH")
    style = str(spec.get("style_index") or "000300.SH")
    include_breadth = bool(spec.get("include_breadth", True))
    turnover_baseline = _safe_float(spec.get("turnover_baseline_trillion"), default=1.0)
    # Keep broad/style index daily cache warm so market thermometer date stays current.
    refreshed_indices = set()
    for raw_idx in (broad, style):
        for idx in _resolve_market_index_candidates(raw_idx):
            if idx in refreshed_indices:
                continue
            refreshed_indices.add(idx)
            try:
                get_klines_with_cache(
                    idx,
                    period="daily",
                    limit=260,
                    force_refresh=False,
                    include_intraday=True,
                )
            except Exception as exc:
                print(f"[IndustryScheduler] market index refresh failed for {idx}: {exc}")
    _fetch_indicator_with_cache(
        conn,
        key,
        refresh,
        lambda b=broad, s=style, ib=include_breadth, tb=turnover_baseline: _build_market_thermometer_payload("", b, s, ib, tb),
        source="tencent"
    )


def _scheduler_loop() -> None:
    while True:
        try:
            conn = get_connection()
            try:
                leader_specs = _collect_leader_specs(conn)
                if leader_specs:
                    print(f"[IndustryScheduler] syncing {len(leader_specs)} leader symbols...")
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

                if _in_market_refresh_window(now_cn()):
                    mt_specs = _collect_market_thermometer_specs(conn)
                    if mt_specs:
                        print(f"[IndustryScheduler] syncing {len(mt_specs)} market thermometer specs...")
                        for spec in mt_specs.values():
                            _refresh_market_thermometer(conn, spec)
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
