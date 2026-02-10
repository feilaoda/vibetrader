#!/usr/bin/env python3
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.cache import get_klines_with_cache  # noqa: E402
from api.symbols import get_all_symbols  # noqa: E402
from api.watchlist import load_watchlist  # noqa: E402
from api.db import (  # noqa: E402
    create_screening_run,
    update_screening_run,
    add_screening_result,
    list_screening_result_symbols,
    get_latest_screening_run,
)


def _load_symbols(universe: str) -> List[str]:
    if universe == "watchlist":
        items = load_watchlist()
        return [item.get("symbol") for item in items if item.get("symbol")]
    df = get_all_symbols()
    if df is None or df.empty:
        return []
    return df["symbol"].tolist()


def _get_klines(symbol: str, limit: int, force_refresh: bool, cache_only: bool) -> Tuple[List[Dict], str]:
    if cache_only:
        klines, source = get_klines_with_cache(
            symbol,
            period="daily",
            limit=limit,
            force_refresh=False,
            include_intraday=False
        )
        return klines, source
    return get_klines_with_cache(
        symbol,
        period="daily",
        limit=limit,
        force_refresh=force_refresh,
        include_intraday=False
    )


def _calc_ma(values: List[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def _parse_ma_list(text: str) -> List[int]:
    items = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = int(part)
        except Exception:
            continue
        if val > 0:
            items.append(val)
    return sorted(set(items))


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen symbols by MA30/MA60 (price below MA).")
    parser.add_argument("--universe", choices=["all", "watchlist"], default="all", help="Symbol universe")
    parser.add_argument("--limit", type=int, default=0, help="Max symbols to screen (0 = all)")
    parser.add_argument("--ma", default="30,60", help="MA windows, comma separated (default: 30,60)")
    parser.add_argument("--kline-limit", type=int, default=120, help="Daily klines to load")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh klines")
    parser.add_argument("--cache-only", action="store_true", help="Use cached klines only (skip remote)")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between symbols")
    parser.add_argument("--run-id", type=int, default=0, help="Resume existing run id")
    parser.add_argument("--resume", action="store_true", help="Resume latest unfinished run")
    parser.add_argument("--output", default="", help="Output JSON file (default: train/ma_screen_*.json)")
    args = parser.parse_args()

    ma_windows = _parse_ma_list(args.ma)
    if not ma_windows:
        print("[ma] invalid --ma", file=sys.stderr)
        return 1

    symbols = _load_symbols(args.universe)
    if not symbols:
        print("[ma] no symbols found", file=sys.stderr)
        return 1

    if args.limit and args.limit > 0:
        symbols = symbols[: args.limit]

    max_window = max(ma_windows)
    kline_limit = max(args.kline_limit, max_window + 5)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or str(ROOT / "train" / f"ma_screen_{timestamp}.json")

    run_id = args.run_id or 0
    if not run_id and args.resume:
        latest = get_latest_screening_run()
        if latest:
            run_id = latest
            print(f"[ma] resume run_id={run_id}")

    model_id = "ma_filter"
    if not run_id:
        run_params = {
            "type": "ma_filter",
            "ma_windows": ma_windows,
            "kline_limit": kline_limit,
            "force_refresh": bool(args.force_refresh),
            "cache_only": bool(args.cache_only),
        }
        run_id = create_screening_run(model_id, args.universe, run_params, len(symbols))
        print(f"[ma] created run_id={run_id}")
    else:
        update_screening_run(run_id, status="running", total=len(symbols))

    processed = set(list_screening_result_symbols(run_id))
    results: List[Dict] = []

    total = len(symbols)
    for idx, symbol in enumerate(symbols, 1):
        if symbol in processed:
            continue
        klines, source = _get_klines(symbol, kline_limit, args.force_refresh, args.cache_only)
        if not klines or len(klines) < max_window:
            result = {
                "symbol": symbol,
                "action": "NO_DATA",
                "score": 0,
                "reason": f"no_klines_or_insufficient(len={len(klines) if klines else 0})",
                "source": source,
            }
            add_screening_result(run_id, symbol, result["action"], result["score"], result["reason"], result, model_id=model_id)
            results.append(result)
            continue

        closes = []
        for k in klines:
            try:
                closes.append(float(k.get("close") or 0))
            except Exception:
                closes.append(0.0)
        last_close = closes[-1]
        ma_values = {w: _calc_ma(closes, w) for w in ma_windows}
        below = {w: (last_close < ma_values[w]) if ma_values[w] is not None else False for w in ma_windows}
        below_any = any(below.values())

        diffs = []
        for w in ma_windows:
            ma = ma_values[w]
            if ma and ma > 0:
                diff_pct = (ma - last_close) / ma * 100
                if diff_pct > 0:
                    diffs.append(diff_pct)
        score = max(diffs) if diffs else 0
        if score < 0:
            score = 0
        if score > 100:
            score = 100
        score = round(score, 2)

        if below_any:
            reasons = []
            for w in ma_windows:
                ma = ma_values[w]
                if ma is None:
                    continue
                flag = "below" if below[w] else "above"
                reasons.append(f"MA{w}={ma:.2f}({flag})")
            reason = f"close={last_close:.2f}; " + ", ".join(reasons)
            action = "WATCH"
        else:
            # 默认只保留低于均线的，其他直接跳过
            continue

        raw = {
            "symbol": symbol,
            "close": last_close,
            "ma": ma_values,
            "below": below,
            "source": source,
        }
        add_screening_result(run_id, symbol, action, score, reason, raw, model_id=model_id)
        results.append({
            "symbol": symbol,
            "action": action,
            "score": score,
            "reason": reason,
            "raw": raw,
        })

        if idx % 50 == 0 or idx == total:
            update_screening_run(run_id, processed=idx)
            payload = {
                "generated_at": datetime.now().isoformat(),
                "universe": args.universe,
                "run_id": run_id,
                "count": len(results),
                "results": results,
            }
            Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"[ma] {idx}/{total} saved -> {output_path}")

        if args.sleep > 0:
            time.sleep(args.sleep)

    update_screening_run(run_id, processed=total, finished=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "universe": args.universe,
        "run_id": run_id,
        "count": len(results),
        "results": results,
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[ma] done -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
