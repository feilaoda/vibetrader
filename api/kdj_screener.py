"""
KDJ stock screener.

Default behavior is cache-only to avoid a full-market scan triggering thousands of
remote kline requests. Pass refresh_missing=true / --refresh-missing only when
you explicitly want to backfill missing daily data.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Query
from pydantic import BaseModel
from technical_indicators import safe_float as _safe_float

API_DIR = Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.append(str(API_DIR))

router = APIRouter()


@dataclass
class ScreenerConfig:
    j_threshold: float = 80.0
    j_operator: str = "gt"  # gt | lt
    daily_j_min: Optional[float] = None
    daily_j_max: Optional[float] = None
    weekly_j_min: Optional[float] = None
    weekly_j_max: Optional[float] = None
    ma_window: Optional[int] = 60
    kdj_period: int = 9
    kdj_scope: str = "both"  # daily | weekly | both | any
    universe: str = "watchlist"  # watchlist | all | symbols
    market: str = "ashare"  # ashare | us | crypto | all
    symbols: Optional[List[str]] = None
    limit: Optional[int] = None
    bars: int = 260
    refresh_missing: bool = False
    include_etf: bool = True
    include_all: bool = False


class KdjScreenRequest(BaseModel):
    j_threshold: float = 80.0
    j_operator: str = "gt"
    daily_j_min: Optional[float] = None
    daily_j_max: Optional[float] = None
    weekly_j_min: Optional[float] = None
    weekly_j_max: Optional[float] = None
    ma_window: Optional[int] = 60
    kdj_period: int = 9
    kdj_scope: str = "both"
    universe: str = "watchlist"
    market: str = "ashare"
    symbols: Optional[List[str]] = None
    limit: Optional[int] = None
    bars: int = 260
    refresh_missing: bool = False
    include_etf: bool = True
    include_all: bool = False


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _detect_market(symbol: str) -> str:
    sym = _normalize_symbol(symbol)
    if sym.endswith(".US"):
        return "us"
    if sym.endswith((".SH", ".SZ", ".BJ")):
        return "ashare"
    if sym.endswith(("USDT", "USDC", "BUSD", "FDUSD")):
        return "crypto"
    return "ashare"


def _is_cn_index(symbol: str) -> bool:
    try:
        from cache import is_cn_index_symbol

        return bool(is_cn_index_symbol(symbol))
    except Exception:
        return False


def _looks_like_etf(symbol: str) -> bool:
    sym = _normalize_symbol(symbol)
    code = sym.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    return code.startswith(("1", "5"))


def _load_symbol_universe(config: ScreenerConfig) -> List[Dict[str, str]]:
    universe = (config.universe or "watchlist").strip().lower()
    market = (config.market or "all").strip().lower()
    items: List[Dict[str, str]] = []

    if config.symbols:
        universe = "symbols"

    if universe == "symbols":
        for sym in config.symbols or []:
            symbol = _normalize_symbol(sym)
            if symbol:
                items.append({"symbol": symbol, "market": _detect_market(symbol), "name": ""})
    elif universe == "all":
        items.extend(_load_all_symbols_from_daily_klines())
        if not items:
            items.extend(_load_all_symbols_from_db())
    else:
        from watchlist import load_watchlist

        for row in load_watchlist():
            symbol = _normalize_symbol(row.get("symbol") or "")
            if not symbol:
                continue
            items.append({
                "symbol": symbol,
                "market": (row.get("market") or _detect_market(symbol)).lower(),
                "name": row.get("name") or "",
            })

    if market != "all":
        items = [it for it in items if (it.get("market") or _detect_market(it.get("symbol", ""))).lower() == market]

    filtered: List[Dict[str, str]] = []
    seen = set()
    for item in items:
        symbol = _normalize_symbol(item.get("symbol") or "")
        if not symbol or symbol in seen:
            continue
        if _is_cn_index(symbol):
            continue
        if not config.include_etf and _looks_like_etf(symbol):
            continue
        seen.add(symbol)
        filtered.append({**item, "symbol": symbol})
        if config.limit and config.limit > 0 and len(filtered) >= int(config.limit):
            break
    return filtered


def _load_all_symbols_from_db() -> List[Dict[str, str]]:
    """Read symbols table directly; do not trigger remote symbol sync."""
    from db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute("SELECT symbol, code, name, market, type FROM symbols ORDER BY symbol ASC").fetchall()
    except Exception as exc:
        print(f"[KDJ] load symbols failed: {exc}")
        return []
    finally:
        conn.close()

    items: List[Dict[str, str]] = []
    for row in rows or []:
        symbol = _normalize_symbol(row[0] if len(row) > 0 else "")
        if not symbol:
            continue
        items.append({
            "symbol": symbol,
            "code": str(row[1] or "") if len(row) > 1 else "",
            "name": str(row[2] or "") if len(row) > 2 else "",
            "market": _detect_market(symbol),
            "type": str(row[4] or "") if len(row) > 4 else "",
        })
    return items


def _load_all_symbols_from_daily_klines() -> List[Dict[str, str]]:
    """Use the local daily-kline universe; symbols table may only contain a small cached subset."""
    from db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT k.symbol, COALESCE(MAX(s.name), '') AS name "
            "FROM daily_klines k LEFT JOIN symbols s ON k.symbol = s.symbol "
            "WHERE k.period = ? GROUP BY k.symbol ORDER BY k.symbol ASC",
            ("daily",)
        ).fetchall()
    except Exception as exc:
        print(f"[KDJ] load daily-kline symbols failed: {exc}")
        return []
    finally:
        conn.close()

    items: List[Dict[str, str]] = []
    for row in rows or []:
        symbol = _normalize_symbol(row[0] if len(row) > 0 else "")
        if not symbol:
            continue
        items.append({
            "symbol": symbol,
            "name": str(row[1] or "") if len(row) > 1 else "",
            "market": _detect_market(symbol),
        })
    return items


def _j_compare(value: float, threshold: float, operator: str) -> bool:
    if (operator or "gt").strip().lower() in ("lt", "lte", "<", "<="):
        return value < threshold
    return value > threshold


def _j_compare_text(value: float, threshold: float, operator: str) -> str:
    passed = _j_compare(value, threshold, operator)
    if (operator or "gt").strip().lower() in ("lt", "lte", "<", "<="):
        return "<" if passed else ">="
    return ">" if passed else "<="


def _has_custom_j_filters(config: ScreenerConfig) -> bool:
    return any(
        value is not None
        for value in (
            config.daily_j_min,
            config.daily_j_max,
            config.weekly_j_min,
            config.weekly_j_max,
        )
    )


def _range_pass(value: float, minimum: Optional[float], maximum: Optional[float]) -> bool:
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


def _range_reason(label: str, value: float, minimum: Optional[float], maximum: Optional[float]) -> List[str]:
    parts: List[str] = []
    if minimum is not None:
        op = ">=" if value >= minimum else "<"
        parts.append(f"{label}_J={value:.2f}{op}{minimum:g}")
    if maximum is not None:
        op = "<=" if value <= maximum else ">"
        parts.append(f"{label}_J={value:.2f}{op}{maximum:g}")
    return parts


def _scope_pass(daily_j: float, weekly_j: float, threshold: float, scope: str, operator: str) -> bool:
    scope = (scope or "both").strip().lower()
    daily_ok = _j_compare(daily_j, threshold, operator)
    weekly_ok = _j_compare(weekly_j, threshold, operator)
    if scope == "daily":
        return daily_ok
    if scope == "weekly":
        return weekly_ok
    if scope == "any":
        return daily_ok or weekly_ok
    return daily_ok and weekly_ok


def _scope_pass_custom(daily_j: float, weekly_j: float, config: ScreenerConfig) -> bool:
    scope = (config.kdj_scope or "both").strip().lower()
    daily_ok = _range_pass(daily_j, config.daily_j_min, config.daily_j_max)
    weekly_ok = _range_pass(weekly_j, config.weekly_j_min, config.weekly_j_max)
    daily_has_rule = config.daily_j_min is not None or config.daily_j_max is not None
    weekly_has_rule = config.weekly_j_min is not None or config.weekly_j_max is not None

    if scope == "daily":
        return daily_ok
    if scope == "weekly":
        return weekly_ok
    if scope == "any":
        checks: List[bool] = []
        if daily_has_rule:
            checks.append(daily_ok)
        if weekly_has_rule:
            checks.append(weekly_ok)
        if checks:
            return any(checks)
        return daily_ok or weekly_ok

    checks = []
    if daily_has_rule:
        checks.append(daily_ok)
    if weekly_has_rule:
        checks.append(weekly_ok)
    if checks:
        return all(checks)
    return daily_ok and weekly_ok


def _indicator_has_required_fields(indicator: Optional[Dict[str, Any]], config: ScreenerConfig) -> bool:
    if not indicator:
        return False
    if indicator.get("daily_j") is None or indicator.get("weekly_j") is None:
        return False
    if config.ma_window and indicator.get(f"ma{config.ma_window}") is None:
        return False
    return True


def _load_or_build_indicator(symbol: str, config: ScreenerConfig) -> Tuple[Optional[Dict[str, Any]], str]:
    from db import get_latest_technical_indicator, refresh_technical_indicators

    indicator = get_latest_technical_indicator(symbol)
    if _indicator_has_required_fields(indicator, config):
        return indicator, indicator.get("source") or "technical_indicators"

    # First compute from existing daily cache only. This avoids turning a full
    # market screen into thousands of remote kline requests.
    try:
        from cache import load_cache

        cache = load_cache(symbol, "daily") or {}
        klines = cache.get("klines") if isinstance(cache, dict) else []
        if klines:
            refresh_technical_indicators(symbol, klines)
            indicator = get_latest_technical_indicator(symbol)
            if indicator:
                source = indicator.get("source") or "computed_from_cache"
                return indicator, source
    except Exception as exc:
        print(f"[KDJ] compute from cache failed for {symbol}: {exc}")

    if not config.refresh_missing:
        return None, "technical_indicators_miss"

    from cache import get_klines_with_cache

    klines, source = get_klines_with_cache(symbol, "daily", limit=config.bars, include_intraday=False)
    if not klines:
        return None, source or "no_daily_klines"
    refresh_technical_indicators(symbol, klines)
    indicator = get_latest_technical_indicator(symbol)
    if indicator:
        return indicator, f"{indicator.get('source') or 'computed'}:{source}"
    return None, source or "compute_failed"


def screen_symbol(item: Dict[str, str], config: ScreenerConfig) -> Optional[Dict[str, Any]]:
    symbol = _normalize_symbol(item.get("symbol") or "")
    if not symbol:
        return None
    indicator, source = _load_or_build_indicator(symbol, config)
    if not indicator:
        return {
            "symbol": symbol,
            "name": item.get("name") or "",
            "matched": False,
            "reason": "technical indicators not available",
            "source": source,
        }

    daily_kdj = {"k": indicator.get("daily_k"), "d": indicator.get("daily_d"), "j": indicator.get("daily_j")}
    weekly_kdj = {"k": indicator.get("weekly_k"), "d": indicator.get("weekly_d"), "j": indicator.get("weekly_j")}
    if daily_kdj["j"] is None or weekly_kdj["j"] is None:
        return {
            "symbol": symbol,
            "name": item.get("name") or "",
            "matched": False,
            "reason": "KDJ not available",
            "source": source,
        }
    close = _safe_float(indicator.get("close"))
    ma = indicator.get(f"ma{config.ma_window}") if config.ma_window else None
    ma_pass = True if not config.ma_window else (ma is not None and close > ma)
    if _has_custom_j_filters(config):
        j_pass = _scope_pass_custom(float(daily_kdj["j"]), float(weekly_kdj["j"]), config)
    else:
        j_pass = _scope_pass(float(daily_kdj["j"]), float(weekly_kdj["j"]), config.j_threshold, config.kdj_scope, config.j_operator)
    matched = bool(j_pass and ma_pass)

    reasons = []
    scope = (config.kdj_scope or "both").lower()
    daily_j = float(daily_kdj["j"])
    weekly_j = float(weekly_kdj["j"])
    if _has_custom_j_filters(config):
        if scope in ("daily", "both", "any") and (config.daily_j_min is not None or config.daily_j_max is not None):
            reasons.extend(_range_reason("daily", daily_j, config.daily_j_min, config.daily_j_max))
        if scope in ("weekly", "both", "any") and (config.weekly_j_min is not None or config.weekly_j_max is not None):
            reasons.extend(_range_reason("weekly", weekly_j, config.weekly_j_min, config.weekly_j_max))
    else:
        if scope in ("daily", "both", "any"):
            op = _j_compare_text(daily_j, config.j_threshold, config.j_operator)
            reasons.append(f"daily_J={daily_j:.2f}{op}{config.j_threshold:g}")
        if scope in ("weekly", "both", "any"):
            op = _j_compare_text(weekly_j, config.j_threshold, config.j_operator)
            reasons.append(f"weekly_J={weekly_j:.2f}{op}{config.j_threshold:g}")
    if config.ma_window and ma is None:
        reasons.append(f"MA{config.ma_window}=no_data")
    elif ma is not None:
        op = ">" if close > ma else "<="
        reasons.append(f"close={close:.3f}{op}MA{config.ma_window}={ma:.3f}")
    if not matched:
        failed = []
        if not j_pass:
            failed.append("J threshold")
        if not ma_pass:
            failed.append(f"MA{config.ma_window}")
        reasons.append("failed: " + ", ".join(failed))

    return {
        "symbol": symbol,
        "name": item.get("name") or "",
        "matched": matched,
        "date": indicator.get("date"),
        "close": close,
        "ma_window": config.ma_window,
        "ma": ma,
        "daily_kdj": {k: round(float(v), 4) for k, v in daily_kdj.items() if v is not None},
        "weekly_kdj": {k: round(float(v), 4) for k, v in weekly_kdj.items() if v is not None},
        "j_threshold": config.j_threshold,
        "j_operator": config.j_operator,
        "daily_j_min": config.daily_j_min,
        "daily_j_max": config.daily_j_max,
        "weekly_j_min": config.weekly_j_min,
        "weekly_j_max": config.weekly_j_max,
        "kdj_period": config.kdj_period,
        "kdj_scope": config.kdj_scope,
        "source": source,
        "reason": "; ".join(reasons),
    }


def _sort_metric(value: Optional[float], minimum: Optional[float], maximum: Optional[float]) -> float:
    val = _safe_float(value)
    if minimum is not None and maximum is None:
        return -val
    if maximum is not None and minimum is None:
        return val
    if minimum is not None and maximum is not None:
        midpoint = (minimum + maximum) / 2.0
        return abs(val - midpoint)
    return val


def run_kdj_screen(config: ScreenerConfig) -> Dict[str, Any]:
    symbols = _load_symbol_universe(config)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    matched_count = 0
    for item in symbols:
        symbol = item.get("symbol") or ""
        try:
            row = screen_symbol(item, config)
            if row and row.get("matched"):
                matched_count += 1
            if row and (row.get("matched") or config.include_all):
                results.append(row)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    if _has_custom_j_filters(config):
        results.sort(key=lambda x: (
            _sort_metric((x.get("daily_kdj") or {}).get("j"), config.daily_j_min, config.daily_j_max),
            _sort_metric((x.get("weekly_kdj") or {}).get("j"), config.weekly_j_min, config.weekly_j_max),
            str(x.get("symbol") or ""),
        ))
    else:
        reverse_sort = (config.j_operator or "gt").strip().lower() not in ("lt", "lte", "<", "<=")
        results.sort(key=lambda x: (
            _safe_float((x.get("weekly_kdj") or {}).get("j")),
            _safe_float((x.get("daily_kdj") or {}).get("j")),
        ), reverse=reverse_sort)

    return {
        "params": {
            "j_threshold": config.j_threshold,
            "j_operator": config.j_operator,
            "daily_j_min": config.daily_j_min,
            "daily_j_max": config.daily_j_max,
            "weekly_j_min": config.weekly_j_min,
            "weekly_j_max": config.weekly_j_max,
            "ma_window": config.ma_window,
            "kdj_period": config.kdj_period,
            "kdj_scope": config.kdj_scope,
            "universe": config.universe,
            "market": config.market,
            "limit": config.limit,
            "bars": config.bars,
            "refresh_missing": config.refresh_missing,
            "include_etf": config.include_etf,
            "include_all": config.include_all,
        },
        "total": len(symbols),
        "matched": matched_count,
        "data": results,
        "errors": errors[:50],
        "error_count": len(errors),
    }


def _parse_symbols_text(text: Optional[str]) -> Optional[List[str]]:
    if not text:
        return None
    return [part.strip().upper() for part in text.replace("\n", ",").split(",") if part.strip()]


@router.get("/tools/kdj-screen")
def kdj_screen_api(
    j_threshold: float = Query(80.0, description="KDJ J 阈值，默认 80"),
    j_operator: str = Query("gt", description="J值比较方向：gt 大于 / lt 小于"),
    daily_j_min: Optional[float] = Query(None, description="日J最小值（含）"),
    daily_j_max: Optional[float] = Query(None, description="日J最大值（含）"),
    weekly_j_min: Optional[float] = Query(None, description="周J最小值（含）"),
    weekly_j_max: Optional[float] = Query(None, description="周J最大值（含）"),
    ma_window: Optional[int] = Query(60, description="价格需站上的日均线窗口；30/60，0 表示不启用"),
    kdj_period: int = Query(9, description="KDJ RSV 计算周期"),
    kdj_scope: str = Query("both", description="daily / weekly / both / any"),
    universe: str = Query("watchlist", description="watchlist / all / symbols"),
    market: str = Query("ashare", description="ashare / us / crypto / all"),
    symbols: Optional[str] = Query(None, description="逗号分隔的 symbol 列表"),
    limit: Optional[int] = Query(None, description="最多扫描多少个标的"),
    bars: int = Query(260, description="每个标的读取多少根日线"),
    refresh_missing: bool = Query(False, description="缓存缺失时是否拉取远程日线"),
    include_etf: bool = Query(True, description="是否包含 ETF"),
    include_all: bool = Query(False, description="是否返回未命中的标的及原因"),
):
    config = ScreenerConfig(
        j_threshold=j_threshold,
        j_operator=j_operator,
        daily_j_min=daily_j_min,
        daily_j_max=daily_j_max,
        weekly_j_min=weekly_j_min,
        weekly_j_max=weekly_j_max,
        ma_window=None if not ma_window or ma_window <= 0 else int(ma_window),
        kdj_period=int(kdj_period),
        kdj_scope=kdj_scope,
        universe=universe,
        market=market,
        symbols=_parse_symbols_text(symbols),
        limit=limit,
        bars=int(bars),
        refresh_missing=bool(refresh_missing),
        include_etf=bool(include_etf),
        include_all=bool(include_all),
    )
    return run_kdj_screen(config)


@router.post("/tools/kdj-screen")
def kdj_screen_post_api(request: KdjScreenRequest):
    config = ScreenerConfig(
        j_threshold=request.j_threshold,
        j_operator=request.j_operator,
        daily_j_min=request.daily_j_min,
        daily_j_max=request.daily_j_max,
        weekly_j_min=request.weekly_j_min,
        weekly_j_max=request.weekly_j_max,
        ma_window=None if not request.ma_window or request.ma_window <= 0 else int(request.ma_window),
        kdj_period=int(request.kdj_period),
        kdj_scope=request.kdj_scope,
        universe=request.universe,
        market=request.market,
        symbols=request.symbols,
        limit=request.limit,
        bars=int(request.bars),
        refresh_missing=bool(request.refresh_missing),
        include_etf=bool(request.include_etf),
        include_all=bool(request.include_all),
    )
    return run_kdj_screen(config)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="KDJ stock screener")
    parser.add_argument("--j-threshold", type=float, default=80.0, help="J threshold, default 80")
    parser.add_argument("--j-operator", choices=["gt", "lt"], default="gt", help="J comparison: gt or lt")
    parser.add_argument("--daily-j-min", type=float, default=None, help="Minimum daily J value (inclusive)")
    parser.add_argument("--daily-j-max", type=float, default=None, help="Maximum daily J value (inclusive)")
    parser.add_argument("--weekly-j-min", type=float, default=None, help="Minimum weekly J value (inclusive)")
    parser.add_argument("--weekly-j-max", type=float, default=None, help="Maximum weekly J value (inclusive)")
    parser.add_argument("--ma-window", type=int, default=60, help="MA window: 30/60, use 0 to disable")
    parser.add_argument("--kdj-period", type=int, default=9, help="KDJ period, default 9")
    parser.add_argument("--kdj-scope", choices=["daily", "weekly", "both", "any"], default="both")
    parser.add_argument("--universe", choices=["watchlist", "all", "symbols"], default="watchlist")
    parser.add_argument("--market", choices=["ashare", "us", "crypto", "all"], default="ashare")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols, e.g. 600519.SH,588000.SH")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--bars", type=int, default=260)
    parser.add_argument("--refresh-missing", action="store_true", help="Fetch remote daily data when cache is missing")
    parser.add_argument("--exclude-etf", action="store_true", help="Exclude ETF-like symbols")
    parser.add_argument("--include-all", action="store_true", help="Return unmatched symbols with reasons")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    config = ScreenerConfig(
        j_threshold=args.j_threshold,
        j_operator=args.j_operator,
        daily_j_min=args.daily_j_min,
        daily_j_max=args.daily_j_max,
        weekly_j_min=args.weekly_j_min,
        weekly_j_max=args.weekly_j_max,
        ma_window=None if args.ma_window <= 0 else args.ma_window,
        kdj_period=args.kdj_period,
        kdj_scope=args.kdj_scope,
        universe="symbols" if args.symbols else args.universe,
        market=args.market,
        symbols=_parse_symbols_text(args.symbols),
        limit=args.limit,
        bars=args.bars,
        refresh_missing=args.refresh_missing,
        include_etf=not args.exclude_etf,
        include_all=args.include_all,
    )
    result = run_kdj_screen(config)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
