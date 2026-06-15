from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

API_DIR = Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from cache import _latest_trading_date, get_klines_with_cache  # noqa: SLF001
from db import get_connection


def _load_daily_symbols() -> List[Tuple[str, str]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, MAX(date) AS last_date "
            "FROM daily_klines WHERE period = ? GROUP BY symbol ORDER BY symbol ASC",
            ("daily",),
        ).fetchall()
    finally:
        conn.close()
    return [((row[0] or "").upper(), str(row[1] or "")) for row in rows if row and row[0]]


def _sync_one(symbol: str, bars: int, force: bool, include_intraday: bool) -> Dict[str, str | int]:
    started = time.time()
    try:
        klines, source = get_klines_with_cache(
            symbol,
            "daily",
            limit=bars,
            force_refresh=force,
            include_intraday=include_intraday,
        )
        latest = str((klines[-1] or {}).get("date") or "") if klines else ""
        return {
            "symbol": symbol,
            "ok": 1 if klines else 0,
            "count": len(klines or []),
            "latest": latest,
            "source": source or "",
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "ok": 0,
            "count": 0,
            "latest": "",
            "source": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": int((time.time() - started) * 1000),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync local daily kline cache for symbols already present in DB.")
    parser.add_argument("--limit", type=int, default=0, help="Max symbols to sync; 0 means all.")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent workers.")
    parser.add_argument("--bars", type=int, default=365, help="Rows returned per symbol.")
    parser.add_argument("--all", action="store_true", help="Sync all symbols instead of stale-only.")
    parser.add_argument("--force", action="store_true", help="Force remote refresh.")
    parser.add_argument("--include-intraday", action="store_true", help="Build today's daily bar from intraday data.")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    target = _latest_trading_date().strftime("%Y-%m-%d")
    symbols = _load_daily_symbols()
    if not args.all:
        symbols = [(sym, last) for sym, last in symbols if not last or last < target]
    if args.limit and args.limit > 0:
        symbols = symbols[: args.limit]

    total = len(symbols)
    print(f"[DailySync] target={target} total={total} workers={args.workers} force={args.force} intraday={args.include_intraday}")
    if total == 0:
        return 0

    ok = 0
    failed = 0
    updated = 0
    latest_counts: Dict[str, int] = {}
    started = time.time()
    workers = max(1, int(args.workers or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_sync_one, sym, args.bars, bool(args.force), bool(args.include_intraday)): (sym, last)
            for sym, last in symbols
        }
        for idx, fut in enumerate(as_completed(futures), start=1):
            sym, before = futures[fut]
            result = fut.result()
            latest = str(result.get("latest") or "")
            latest_counts[latest or ""] = latest_counts.get(latest or "", 0) + 1
            if result.get("ok"):
                ok += 1
                if latest and latest > before:
                    updated += 1
            else:
                failed += 1
            if idx % max(1, args.progress_every) == 0 or idx == total:
                elapsed = time.time() - started
                print(
                    f"[DailySync] {idx}/{total} ok={ok} updated={updated} failed={failed} "
                    f"last={sym}:{before}->{latest or '--'} elapsed={elapsed:.1f}s"
                )
                if result.get("error"):
                    print(f"[DailySync][Error] {sym} {result.get('error')}")

    top_dates = sorted(latest_counts.items(), key=lambda x: x[0], reverse=True)[:8]
    print(f"[DailySync] done ok={ok} updated={updated} failed={failed} elapsed={time.time() - started:.1f}s")
    print(f"[DailySync] latest_dates={top_dates}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
