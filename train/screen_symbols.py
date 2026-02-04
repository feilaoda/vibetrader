#!/usr/bin/env python3
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.cache import get_klines_with_cache, _latest_trading_date  # noqa: E402
from api.llm import LLMService  # noqa: E402
from api.symbols import get_all_symbols  # noqa: E402
from api.watchlist import load_watchlist  # noqa: E402
from api.db import (
    create_screening_run,
    update_screening_run,
    add_screening_result,
    list_screening_result_symbols,
    get_latest_screening_run,
)  # noqa: E402


PROMPT_TEMPLATE = (
    "你是股票筛选器，请基于给定日线数据输出简洁JSON，不要Markdown。\n"
    "输出格式：{{\"symbol\":\"{symbol}\",\"action\":\"BUY|WATCH|SKIP\",\"score\":0-100,\"reason\":\"一句话\"}}\n"
    "标准：BUY=建议入场，WATCH=重点观察，SKIP=暂不关注。"
)


def _last_n_trading_dates(n: int) -> List[str]:
    dates: List[str] = []
    current = _latest_trading_date()
    while len(dates) < n:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current = current - timedelta(days=1)
    return dates


def _has_missing_recent_days(klines: List[Dict], window: int = 30) -> bool:
    if not klines:
        return True
    last_dates = {k.get("date") for k in klines if k.get("date")}
    if not last_dates:
        return True
    required = _last_n_trading_dates(window)
    return any(date not in last_dates for date in required)


def _parse_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    # try to extract first {...}
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except Exception:
        return None


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch screen symbols with LLM (no memory/history).")
    parser.add_argument("--universe", choices=["all", "watchlist"], default="all", help="Symbol universe")
    parser.add_argument("--limit", type=int, default=0, help="Max symbols to screen (0 = all)")
    parser.add_argument("--model", default=None, help="LLM model id (optional)")
    parser.add_argument("--kline-limit", type=int, default=365, help="Daily klines to include")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh klines")
    parser.add_argument("--cache-only", action="store_true", help="Use cached klines only (skip remote)")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between LLM calls")
    parser.add_argument("--output", default="", help="Output JSON file (default: train/screen_results_*.json)")
    parser.add_argument("--run-id", type=int, default=0, help="Resume existing run id")
    parser.add_argument("--resume", action="store_true", help="Resume latest unfinished run")
    args = parser.parse_args()

    symbols = _load_symbols(args.universe)
    if not symbols:
        print("[screen] no symbols found", file=sys.stderr)
        return 1

    if args.limit and args.limit > 0:
        symbols = symbols[: args.limit]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or str(ROOT / "train" / f"screen_results_{timestamp}.json")

    run_id = args.run_id or 0
    if not run_id and args.resume:
        latest = get_latest_screening_run()
        if latest:
            run_id = latest
            print(f"[screen] resume run_id={run_id}")

    model_id = args.model or "default"
    if not run_id:
        run_params = {
            "kline_limit": args.kline_limit,
            "force_refresh": bool(args.force_refresh),
            "cache_only": bool(args.cache_only),
        }
        run_id = create_screening_run(model_id, args.universe, run_params, len(symbols))
        print(f"[screen] created run_id={run_id}")
    else:
        update_screening_run(run_id, status="running", total=len(symbols))

    processed = set(list_screening_result_symbols(run_id))
    results: List[Dict] = []

    llm = LLMService()
    context_config = {
        "enable_memory": False,
        "enable_retrieval": False,
        "history_limit": 0,
        "recent_limit": 0,
        "save_history": False,
        "kline_rows_assistant": args.kline_limit,
        "kline_rows_chat": args.kline_limit,
    }

    total = len(symbols)
    for idx, symbol in enumerate(symbols, 1):
        if symbol in processed:
            continue
        klines, source = _get_klines(symbol, args.kline_limit, args.force_refresh, args.cache_only)
        window = min(30, args.kline_limit)
        needs_refresh = False
        reasons = []
        if not klines or len(klines) < args.kline_limit:
            missing_count = 0 if not klines else len(klines)
            needs_refresh = True
            reasons.append(f"len={missing_count}")
        if not needs_refresh and _has_missing_recent_days(klines, window):
            needs_refresh = True
            reasons.append(f"missing_recent_{window}")
        if needs_refresh and not args.cache_only:
            reason_text = ",".join(reasons) if reasons else "missing"
            print(f"[screen] {symbol} missing daily data ({reason_text}), refreshing...")
            klines, source = _get_klines(symbol, args.kline_limit, True, False)
        if not klines:
            result = {
                "symbol": symbol,
                "action": "NO_DATA",
                "score": 0,
                "reason": "no_klines",
                "source": source
            }
            results.append(result)
            add_screening_result(run_id, symbol, result["action"], result["score"], result["reason"], result, model_id=model_id)
            continue

        user_input = PROMPT_TEMPLATE.format(symbol=symbol)
        response = llm.analyze_stock(
            symbol,
            klines,
            model=args.model,
            user_input=user_input,
            mode="assistant",
            context_config=context_config
        )
        if isinstance(response, str):
            result = {
                "symbol": symbol,
                "action": "ERROR",
                "score": 0,
                "reason": response
            }
            results.append(result)
            add_screening_result(run_id, symbol, result["action"], result["score"], result["reason"], result, model_id=model_id)
        else:
            text = "".join(list(response))
            parsed = _parse_json(text) or {}
            action = parsed.get("action") or parsed.get("signal") or "UNKNOWN"
            score = parsed.get("score") if parsed.get("score") is not None else 0
            reason = parsed.get("reason") or parsed.get("summary") or text.strip()[:200]
            result = {
                "symbol": symbol,
                "action": action,
                "score": score,
                "reason": reason,
                "raw": parsed if parsed else None,
            }
            results.append(result)
            add_screening_result(run_id, symbol, action, score, reason, parsed, model_id=model_id)

        if idx % 10 == 0 or idx == total:
            update_screening_run(run_id, processed=idx)
            payload = {
                "generated_at": datetime.now().isoformat(),
                "model": args.model or "default",
                "universe": args.universe,
                "run_id": run_id,
                "count": len(results),
                "results": results,
            }
            Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"[screen] {idx}/{total} saved -> {output_path}")

        if args.sleep > 0:
            time.sleep(args.sleep)

    update_screening_run(run_id, processed=total, finished=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "model": args.model or "default",
        "universe": args.universe,
        "run_id": run_id,
        "count": len(results),
        "results": results,
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[screen] done -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
