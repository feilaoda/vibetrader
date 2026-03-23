#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from yahoo import fetch_chart

from aitrader.data.generate_sample_data import generate_sample_prices


def _parse_symbols(symbols: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for raw in symbols:
        text = (raw or "").strip().upper()
        if not text:
            continue
        for part in text.split(","):
            symbol = part.strip().upper()
            if not symbol or symbol in seen:
                continue
            out.append(symbol)
            seen.add(symbol)
    return out


def _default_dates(start_date: Optional[str], end_date: Optional[str]) -> Tuple[str, str]:
    end = (end_date or "").strip()
    start = (start_date or "").strip()
    if not end:
        end = datetime.utcnow().strftime("%Y%m%d")
    if not start:
        start = (datetime.utcnow() - timedelta(days=550)).strftime("%Y%m%d")
    return start, end


def fetch_yahoo_prices_to_csv(
    symbols: Sequence[str],
    output_csv: Path,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    interval: str = "1d",
) -> Tuple[Path, Dict[str, int], int]:
    tickers = _parse_symbols(symbols)
    if not tickers:
        raise ValueError("symbols is empty")

    start, end = _default_dates(start_date, end_date)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    rows: List[Tuple[str, str, float, float]] = []
    counts: Dict[str, int] = {}
    for ticker in tickers:
        klines = fetch_chart(ticker, start, end, interval=interval) or []
        counts[ticker] = len(klines)
        for k in klines:
            date_str = (k.get("date") or "").strip()
            close = k.get("close")
            if not date_str or close is None:
                continue
            rows.append((date_str, ticker, float(close), float(k.get("volume") or 0.0)))

    rows.sort(key=lambda x: (x[0], x[1]))
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "close", "volume"])
        for date_str, symbol, close, volume in rows:
            writer.writerow([date_str, symbol, f"{close:.6f}", int(volume)])

    return output_csv, counts, len(rows)


def ensure_price_csv(data_source: dict, root: Path = ROOT) -> Tuple[Path, Dict[str, object]]:
    provider = str(data_source.get("provider", "local_csv")).strip().lower()
    csv_path_raw = str(data_source.get("csv_path", "aitrader/data/sample_prices.csv"))
    csv_path = Path(csv_path_raw)
    if not csv_path.is_absolute():
        csv_path = root / csv_path

    if provider in {"local_csv", "csv"}:
        if not csv_path.exists() and bool(data_source.get("auto_generate_sample", True)):
            days = int(data_source.get("sample_days", 320))
            seed = int(data_source.get("sample_seed", 42))
            generate_sample_prices(csv_path, days=days, seed=seed)
            return csv_path, {"provider": "sample", "generated": True, "rows": days * 8}
        return csv_path, {"provider": "csv", "generated": False}

    if provider == "yahoo":
        force_refresh = bool(data_source.get("force_refresh", False))
        symbols = _parse_symbols(data_source.get("symbols", []))
        if not symbols:
            raise ValueError("data_source.symbols is required when provider=yahoo")

        start_date = data_source.get("start_date")
        end_date = data_source.get("end_date")
        interval = str(data_source.get("interval", "1d"))

        if force_refresh or not csv_path.exists():
            output_csv, counts, rows = fetch_yahoo_prices_to_csv(
                symbols=symbols,
                output_csv=csv_path,
                start_date=str(start_date) if start_date else None,
                end_date=str(end_date) if end_date else None,
                interval=interval,
            )
            active_symbols = sum(1 for v in counts.values() if v > 0)
            if rows == 0 and bool(data_source.get("fallback_to_sample", True)):
                days = int(data_source.get("sample_days", 320))
                seed = int(data_source.get("sample_seed", 42))
                generate_sample_prices(csv_path, days=days, seed=seed)
                return csv_path, {
                    "provider": "sample_fallback",
                    "requested_provider": "yahoo",
                    "generated": True,
                    "rows": days * 8,
                }

            return output_csv, {
                "provider": "yahoo",
                "requested_symbols": len(symbols),
                "active_symbols": active_symbols,
                "rows": rows,
                "counts": counts,
            }

        return csv_path, {"provider": "yahoo_cache", "generated": False}

    raise ValueError(f"Unsupported data source provider: {provider}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load market prices to local CSV for aitrader.")
    parser.add_argument("--provider", default="yahoo", choices=["yahoo", "local_csv"], help="Data provider")
    parser.add_argument("--symbols", default="SPY,QQQ,IWM,TLT,GLD", help="Comma-separated symbols for yahoo")
    parser.add_argument("--start-date", default=None, help="Start date, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="End date, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--interval", default="1d", help="Yahoo interval, default 1d")
    parser.add_argument("--csv-path", default="aitrader/data/market_prices.csv", help="Output CSV path")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh remote data")
    args = parser.parse_args()

    config = {
        "provider": args.provider,
        "csv_path": args.csv_path,
        "symbols": _parse_symbols([args.symbols]),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "interval": args.interval,
        "force_refresh": bool(args.force_refresh),
    }
    csv_path, meta = ensure_price_csv(config)
    print(f"[aitrader] csv={csv_path}")
    print(f"[aitrader] meta={meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
