#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.cache import get_klines_with_cache, _latest_trading_date  # noqa: E402
from api.llm import LLMService  # noqa: E402


def _parse_date(date_str: str) -> datetime:
    text = (date_str or "").strip()
    if not text:
        raise ValueError("end_date is required (YYYYMMDD or YYYY-MM-DD)")
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d")
    return datetime.strptime(text, "%Y-%m-%d")


def _format_ymd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM backtest: analyze last N days of daily klines.")
    parser.add_argument("--symbol", required=True, help="Symbol like 002050.SZ or 510300.SH")
    parser.add_argument("--end-date", required=False, help="End date (YYYYMMDD or YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=365, help="Number of calendar days to look back (default: 365)")
    parser.add_argument("--model", default=None, help="LLM model ID (optional)")
    parser.add_argument("--mode", default="assistant", choices=["assistant", "chat"], help="LLM mode")
    parser.add_argument("--user-input", default=None, help="Custom user prompt (optional)")
    parser.add_argument("--context-config", default=None, help="JSON string to override context_config")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh kline cache")
    parser.add_argument("--save-history", action="store_true", help="Save messages to chat history (default: off)")

    args = parser.parse_args()

    if args.days <= 0:
        print("days must be >= 1", file=sys.stderr)
        return 2

    if args.end_date:
        end_dt = _parse_date(args.end_date)
    else:
        end_dt = datetime.combine(_latest_trading_date(), datetime.min.time())

    start_dt = end_dt - timedelta(days=args.days - 1)
    start_str = _format_ymd(start_dt)
    end_str = _format_ymd(end_dt)

    klines, source = get_klines_with_cache(
        args.symbol,
        period="daily",
        start_date=start_str,
        end_date=end_str,
        limit=args.days + 30,
        force_refresh=args.force_refresh,
        include_intraday=False
    )

    if not klines:
        print(f"[backtest] no klines found for {args.symbol} ({start_str} -> {end_str})", file=sys.stderr)
        return 1

    default_prompt = (
        f"Analyze trend and key levels for {args.symbol} using daily data "
        f"from {start_str} to {end_str}. Summarize the trend and next-step bias."
    )
    user_input = args.user_input or default_prompt

    context_config = {
        "enable_memory": False,
        "enable_retrieval": False,
        "history_limit": 0,
        "recent_limit": 0,
        "save_history": bool(args.save_history),
        "kline_rows_assistant": min(365, len(klines)),
        "kline_rows_chat": min(365, len(klines)),
    }

    if args.context_config:
        try:
            overrides = json.loads(args.context_config)
            if isinstance(overrides, dict):
                context_config.update(overrides)
        except Exception as exc:
            print(f"[backtest] invalid context-config JSON: {exc}", file=sys.stderr)
            return 2

    print(
        f"[backtest] symbol={args.symbol} range={start_str}->{end_str} "
        f"klines={len(klines)} source={source} model={args.model or 'default'} mode={args.mode}"
    )

    llm = LLMService()
    response = llm.analyze_stock(
        args.symbol,
        klines,
        model=args.model,
        user_input=user_input,
        mode=args.mode,
        context_config=context_config
    )

    if isinstance(response, str):
        print(response)
        return 1

    for chunk in response:
        sys.stdout.write(chunk)
        sys.stdout.flush()

    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
