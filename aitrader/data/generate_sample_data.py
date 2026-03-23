#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def generate_sample_prices(path: Path, days: int = 300, seed: int = 42) -> Path:
    rng = random.Random(seed)
    symbols = [
        ("SPY", 430.0, 0.0004),
        ("QQQ", 360.0, 0.0006),
        ("IWM", 190.0, 0.0003),
        ("XLE", 82.0, 0.0002),
        ("XLK", 180.0, 0.0007),
        ("SMH", 210.0, 0.0008),
        ("TLT", 98.0, 0.0001),
        ("GLD", 185.0, 0.0002),
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    days_list = _business_days(date(2024, 1, 2), days)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "close", "volume"])

        for symbol, base_price, drift in symbols:
            price = base_price
            base_volume = rng.randint(3_000_000, 20_000_000)
            for d in days_list:
                shock = rng.gauss(0.0, 0.012)
                price = max(1.0, price * (1.0 + drift + shock))
                vol_noise = 1.0 + rng.uniform(-0.35, 0.35)
                volume = int(base_volume * vol_noise)
                writer.writerow([d.isoformat(), symbol, f"{price:.4f}", volume])

    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic sample prices for aitrader backtest.")
    parser.add_argument("--output", default="aitrader/data/sample_prices.csv", help="Output CSV path")
    parser.add_argument("--days", type=int, default=300, help="Business days to generate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    if args.days < 80:
        raise SystemExit("days must be >= 80 for default lookbacks")

    output = generate_sample_prices(Path(args.output), days=args.days, seed=args.seed)
    print(f"[aitrader] sample data written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
