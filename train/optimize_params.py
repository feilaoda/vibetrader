#!/usr/bin/env python3
import argparse
import json
import sys
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.db import get_connection  # noqa: E402
from api.cache import load_cache, get_klines_with_cache  # noqa: E402
from api.strategy_defaults import (  # noqa: E402
    normalize_objectives,
    normalize_constraints,
    normalize_params,
    normalize_optimization
)
from api.strategy_optimizer import _score_metrics, _apply_prompt_override, _generate_candidates  # noqa: E402
from api.strategy_universe import fetch_symbols_for_strategy  # noqa: E402
from api.llm import LLMService  # noqa: E402
from api.strategy_rules import compute_baseline_signal  # noqa: E402
from api.config import LLM_MODEL  # noqa: E402


def _config_hash(config: Dict[str, Any]) -> str:
    raw = json.dumps(config, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _load_checkpoint(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_checkpoint(path: Path, base_state: Dict[str, Any] | None, rule_update: Dict[str, Any] | None = None, llm_update: Dict[str, Any] | None = None) -> None:
    state = base_state.copy() if isinstance(base_state, dict) else {}
    if rule_update is not None:
        state["rule"] = rule_update
    if llm_update is not None:
        state["llm"] = llm_update
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _parse_date(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None


def _kline_date_iso(kline: Dict[str, Any]) -> str:
    date_str = kline.get("date") or ""
    if not date_str:
        time_str = kline.get("time")
        if isinstance(time_str, str) and time_str:
            date_str = time_str.split(" ")[0]
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


def _load_cached_daily(symbol: str, start_date: str | None, end_date: str | None) -> List[Dict[str, Any]]:
    cache = load_cache(symbol, "daily")
    if not cache:
        return []
    klines = cache.get("klines") or []
    filtered = []
    for k in klines:
        date_iso = _kline_date_iso(k)
        if not date_iso:
            continue
        if start_date and date_iso < start_date:
            continue
        if end_date and date_iso > end_date:
            continue
        filtered.append(k)
    filtered.sort(key=lambda x: x.get("openTime", 0))
    return filtered


def _prepare_symbol_data(symbols: List[str], start_date: str, end_date: str) -> Dict[str, Dict[str, Any]]:
    data = {}
    for symbol in symbols:
        klines = _load_cached_daily(symbol, start_date, end_date)
        if len(klines) < 30:
            continue
        dates = []
        open_prices = []
        close = []
        high = []
        low = []
        for k in klines:
            date_iso = _kline_date_iso(k)
            dt = _parse_date(date_iso)
            if not dt:
                continue
            dates.append(dt.date())
            open_prices.append(float(k.get("open") or 0))
            close.append(float(k.get("close") or 0))
            high.append(float(k.get("high") or 0))
            low.append(float(k.get("low") or 0))
        if len(dates) < 30:
            continue
        index_map = {d: i for i, d in enumerate(dates)}
        data[symbol] = {
            "dates": dates,
            "open": open_prices,
            "close": close,
            "high": high,
            "low": low,
            "index_map": index_map
        }
    return data


def _compute_metrics(equity_curve: List[float], trade_pnls: List[float], total_traded_value: float) -> Dict[str, float]:
    if len(equity_curve) < 2:
        return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "turnover": 0.0, "win_rate": 0.0}
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        curr = equity_curve[i]
        returns.append(curr / prev - 1 if prev > 0 else 0.0)
    days = len(returns)
    total_return = equity_curve[-1] / equity_curve[0] - 1
    annual_return = (1 + total_return) ** (252 / max(days, 1)) - 1 if days > 0 else 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak if peak > 0 else 0
        max_dd = max(max_dd, drawdown)
    avg_ret = sum(returns) / len(returns) if returns else 0.0
    var = sum((r - avg_ret) ** 2 for r in returns) / len(returns) if returns else 0.0
    std = var ** 0.5
    sharpe = (avg_ret / std) * (252 ** 0.5) if std > 0 else 0.0
    avg_equity = sum(equity_curve) / len(equity_curve) if equity_curve else 0.0
    turnover = total_traded_value / (avg_equity * max(days, 1)) if avg_equity > 0 else 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    win_rate = wins / len(trade_pnls) if trade_pnls else 0.0
    return {
        "annual_return": annual_return,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "turnover": turnover,
        "win_rate": win_rate
    }


def _extract_json(text: str) -> Dict[str, Any] | None:
    if not text:
        return None
    content = text.strip()
    if content.startswith("```"):
        content = content.replace("```json", "").replace("```", "").strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(content[start:end + 1])
    except Exception:
        return None


def _build_llm_prompt(
    strategy_prompt: str,
    symbol: str,
    klines: List[Dict[str, Any]],
    account_context: Dict[str, Any],
    baseline: Dict[str, Any]
) -> str:
    llm = LLMService()
    data_context = llm._format_kline_data(klines)
    account_json = json.dumps(account_context, ensure_ascii=False)
    baseline_text = json.dumps(baseline, ensure_ascii=False)
    return (
        f"{strategy_prompt}\n\n"
        "Sizing rules (must follow):\n"
        "- Use account_context to size orders. Do NOT assume a fixed quantity.\n"
        "- For BUY: total cost (price * quantity + fee) must be <= cash_balance.\n"
        "- For A-share: quantity must be an integer multiple of 100.\n"
        "- Respect position sizing rules in the strategy prompt. If not specified, keep single position <= 20% of total_equity.\n"
        "- If constraints are not met, output HOLD or reduce quantity.\n\n"
        "Baseline guidance:\n"
        "- A rule-engine baseline signal is provided. Use it as the default unless strong evidence suggests otherwise.\n"
        "- If you override the baseline, explain why in the reason field.\n\n"
        f"Account context (JSON): {account_json}\n"
        f"Symbol: {symbol}\n"
        f"Baseline signal (rule engine): {baseline_text}\n\n"
        f"Recent Kline Data:\n{data_context}\n"
        "Return only valid JSON per the specified schema."
    )


def _llm_generate_prompt_variants(base_prompt: str, objectives: Dict[str, Any], constraints: Dict[str, Any], params: Dict[str, Any], count: int, model_id: str) -> List[str]:
    if count <= 0:
        return [base_prompt]
    llm = LLMService()
    client = llm._get_client(model_id)
    if not client:
        print("[train] LLM client not configured, fallback to base prompt.")
        return [base_prompt]
    system_prompt = (
        "You are a quant prompt optimizer. "
        "Generate concise strategy prompts that follow the required JSON schema. "
        "Return only JSON."
    )
    user_prompt = (
        "Given the base prompt and objectives, generate variants that aim to improve performance. "
        "Keep constraints and sizing rules aligned.\n"
        f"Base prompt:\n{base_prompt}\n\n"
        f"Objectives: {json.dumps(objectives, ensure_ascii=False)}\n"
        f"Constraints: {json.dumps(constraints, ensure_ascii=False)}\n"
        f"Params: {json.dumps(params, ensure_ascii=False)}\n"
        f"Return JSON: {{\"prompts\": [\"...\", \"...\"]}} with {count} variants."
    )
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.4
        )
        content = response.choices[0].message.content if response.choices else ""
    except Exception as e:
        print(f"[train] Prompt generation failed: {e}")
        return [base_prompt]
    data = _extract_json(content or "")
    prompts = []
    if isinstance(data, dict):
        items = data.get("prompts")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str) and item.strip():
                    prompts.append(item.strip())
    if not prompts:
        prompts = [base_prompt]
    if base_prompt not in prompts:
        prompts.insert(0, base_prompt)
    return prompts[: max(count, 1)]


def _apply_action(
    symbol: str,
    side: str,
    qty: int,
    price: float,
    positions: Dict[str, Dict[str, Any]],
    cash: float
) -> Tuple[float, float, float]:
    if qty <= 0 or price <= 0:
        return cash, 0.0, 0.0
    side = side.upper()
    if side == "BUY":
        fee = max(5, price * qty * 0.0005)
        total = price * qty + fee
        if total > cash:
            return cash, 0.0, 0.0
        pos = positions.get(symbol, {"quantity": 0, "avg_cost": 0.0})
        old_qty = int(pos.get("quantity") or 0)
        old_cost = float(pos.get("avg_cost") or 0.0)
        new_qty = old_qty + qty
        total_cost = old_qty * old_cost + qty * price + fee
        pos["quantity"] = new_qty
        pos["avg_cost"] = total_cost / new_qty if new_qty > 0 else 0.0
        positions[symbol] = pos
        cash -= total
        return cash, fee, 0.0
    if side == "SELL":
        pos = positions.get(symbol)
        if not pos:
            return cash, 0.0, 0.0
        available = int(pos.get("quantity") or 0)
        if available <= 0:
            return cash, 0.0, 0.0
        if qty > available:
            qty = available
        fee = price * qty * 0.014
        cash += price * qty - fee
        avg_cost = float(pos.get("avg_cost") or 0.0)
        pnl = (price - avg_cost) * qty - fee
        remaining = available - qty
        if remaining <= 0:
            positions.pop(symbol, None)
        else:
            pos["quantity"] = remaining
            positions[symbol] = pos
        return cash, fee, pnl
    return cash, 0.0, 0.0


def _llm_backtest(
    data: Dict[str, Dict[str, Any]],
    symbols: List[str],
    prompt: str,
    params: Dict[str, Any],
    constraints: Dict[str, Any],
    objectives: Dict[str, Any],
    initial_capital: float,
    model_id: str,
    detail_f,
    prompt_index: int,
    max_days: int | None = None,
    log_prompts: bool = True,
    progress_every: int = 50
) -> Dict[str, Any]:
    llm = LLMService()
    client = llm._get_client(model_id)
    if not client:
        return {"error": "llm_not_configured"}

    positions: Dict[str, Dict[str, Any]] = {}
    cash = float(initial_capital)
    equity_curve: List[float] = []
    trade_pnls: List[float] = []
    total_traded_value = 0.0
    max_positions = int(constraints.get("max_positions") or 8)
    max_daily_trades = int(constraints.get("max_daily_trades") or 5)

    timeline = sorted({d for series in data.values() for d in series["dates"]})
    if max_days and len(timeline) > max_days:
        timeline = timeline[-max_days:]
    total_possible = len(timeline) * max(len(symbols), 1)
    call_count = 0
    print(f"[llm] prompt#{prompt_index} start: days={len(timeline)} symbols={len(symbols)} total_calls~{total_possible}")
    for current_date in timeline:
        daily_trades = 0
        # update prices
        last_price = {}
        for symbol in symbols:
            series = data.get(symbol)
            if not series:
                continue
            idx = series["index_map"].get(current_date)
            if idx is None:
                continue
            last_price[symbol] = series["close"][idx]

        equity = cash + sum(last_price.get(sym, 0) * int(pos.get("quantity") or 0) for sym, pos in positions.items())
        equity_curve.append(equity)

        actions = []
        for symbol in symbols:
            series = data.get(symbol)
            if not series:
                continue
            idx = series["index_map"].get(current_date)
            if idx is None:
                continue
            klines = []
            start_idx = max(0, idx - 120)
            for i in range(start_idx, idx + 1):
                date_iso = series["dates"][i].strftime("%Y-%m-%d")
                klines.append({
                    "date": date_iso,
                    "open": series["open"][i] if "open" in series else series["close"][i],
                    "high": series["high"][i],
                    "low": series["low"][i],
                    "close": series["close"][i],
                    "volume": 0
                })

            account_context = {
                "initial_capital": round(float(initial_capital), 2),
                "cash_balance": round(float(cash), 2),
                "total_equity": round(float(equity), 2),
                "positions": [
                    {"symbol": s, "quantity": int(p.get("quantity") or 0), "avg_cost": float(p.get("avg_cost") or 0)}
                    for s, p in positions.items()
                ]
            }
            baseline = compute_baseline_signal(
                klines=klines,
                position=positions.get(symbol),
                params=params,
                constraints=constraints,
                account_context=account_context
            )
            user_prompt = _build_llm_prompt(prompt, symbol, klines, account_context, baseline)
            system_prompt = "You are a trading strategy executor. Return only JSON that follows the provided schema. No extra text."
            call_count += 1
            if progress_every and (call_count == 1 or call_count % progress_every == 0):
                print(f"[llm] prompt#{prompt_index} call {call_count}/{total_possible} {current_date} {symbol}")
            try:
                response = client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1
                )
                content = response.choices[0].message.content if response.choices else ""
            except Exception as e:
                detail_f.write(json.dumps({
                    "prompt_index": prompt_index,
                    "date": str(current_date),
                    "symbol": symbol,
                    "error": f"llm_error:{e}"
                }, ensure_ascii=False) + "\n")
                continue

            data_json = _extract_json(content or "")
            payload = {
                "prompt_index": prompt_index,
                "date": str(current_date),
                "symbol": symbol,
                "baseline": baseline,
                "llm_output": content,
                "llm_parsed": data_json
            }
            if log_prompts:
                payload["system_prompt"] = system_prompt
                payload["user_prompt"] = user_prompt
            detail_f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            if not data_json:
                continue
            action_obj = None
            if isinstance(data_json, dict):
                acts = data_json.get("actions")
                if isinstance(acts, list) and acts:
                    action_obj = acts[0]
                elif "action" in data_json or "side" in data_json:
                    action_obj = data_json
            if not isinstance(action_obj, dict):
                continue
            side = str(action_obj.get("side") or action_obj.get("action") or "").upper()
            if side not in ("BUY", "SELL"):
                continue
            qty = action_obj.get("quantity") if action_obj.get("quantity") is not None else action_obj.get("qty")
            qty = int(qty or 0)
            if qty <= 0:
                continue
            price = last_price.get(symbol, 0)
            if price <= 0:
                continue
            actions.append({"symbol": symbol, "side": side, "qty": qty, "price": price})

        # execute sells then buys
        for action in actions:
            if daily_trades >= max_daily_trades:
                break
            if action["side"] != "SELL":
                continue
            cash, fee, pnl = _apply_action(action["symbol"], "SELL", action["qty"], action["price"], positions, cash)
            if fee > 0:
                total_traded_value += action["price"] * action["qty"]
                trade_pnls.append(pnl)
                daily_trades += 1
        for action in actions:
            if daily_trades >= max_daily_trades:
                break
            if action["side"] != "BUY":
                continue
            if len(positions) >= max_positions and action["symbol"] not in positions:
                continue
            lot_qty = int(action["qty"] / 100) * 100
            cash_before = cash
            cash, fee, _ = _apply_action(action["symbol"], "BUY", lot_qty, action["price"], positions, cash)
            if cash != cash_before:
                total_traded_value += action["price"] * lot_qty
                daily_trades += 1

        detail_f.write(json.dumps({
            "prompt_index": prompt_index,
            "date": str(current_date),
            "cash": round(cash, 2),
            "equity": round(cash + sum(last_price.get(sym, 0) * int(pos.get("quantity") or 0) for sym, pos in positions.items()), 2),
            "positions": {s: int(p.get("quantity") or 0) for s, p in positions.items()},
            "daily_trades": daily_trades
        }, ensure_ascii=False) + "\n")

    metrics = _compute_metrics(equity_curve, trade_pnls, total_traded_value)
    return {"metrics": metrics}


def _backtest_window(data: Dict[str, Dict[str, Any]], window_dates: List[datetime.date], params: Dict[str, Any], constraints: Dict[str, Any], initial_capital: float) -> Dict[str, Any]:
    BUY_FEE_RATE = 0.0005
    SELL_FEE_RATE = 0.014
    LOT_SIZE = 100

    max_positions = int(constraints.get("max_positions") or 8)
    max_daily_trades = int(constraints.get("max_daily_trades") or 5)
    max_position_pct = float(constraints.get("max_position_pct") or 20.0) / 100.0
    stop_loss_pct = float(constraints.get("stop_loss_pct") or 5.0) / 100.0
    take_profit_pct = float(constraints.get("take_profit_pct") or 15.0) / 100.0

    ma_short = int(params.get("ma_short") or 5)
    ma_long = int(params.get("ma_long") or 20)
    momentum_days = int(params.get("momentum_days") or 20)
    breakout_window = int(params.get("breakout_window") or 20)

    positions: Dict[str, Dict[str, Any]] = {}
    cash = float(initial_capital)
    equity_curve: List[float] = []
    trade_pnls: List[float] = []
    total_traded_value = 0.0
    last_price: Dict[str, float] = {}

    window_start = window_dates[0]
    start_idx_map: Dict[str, int] = {}
    for symbol, series in data.items():
        idx = series["index_map"].get(window_start)
        if idx is None:
            # find first date in window for this symbol
            for d in window_dates:
                idx = series["index_map"].get(d)
                if idx is not None:
                    break
        if idx is not None:
            start_idx_map[symbol] = idx

    for current_date in window_dates:
        daily_trades = 0
        for symbol, series in data.items():
            idx = series["index_map"].get(current_date)
            if idx is None:
                continue
            last_price[symbol] = series["close"][idx]

        equity = cash
        for symbol, pos in positions.items():
            price = last_price.get(symbol, pos.get("entry_price", 0))
            equity += price * pos.get("qty", 0)
        equity_curve.append(equity)

        sell_list: List[Tuple[str, float]] = []
        for symbol, pos in list(positions.items()):
            series = data.get(symbol)
            if not series:
                continue
            idx = series["index_map"].get(current_date)
            if idx is None:
                continue
            start_idx = start_idx_map.get(symbol, idx)
            if idx - start_idx < ma_long:
                continue
            close_price = series["close"][idx]
            entry_price = pos.get("entry_price", close_price)
            if entry_price > 0:
                if close_price <= entry_price * (1 - stop_loss_pct) or close_price >= entry_price * (1 + take_profit_pct):
                    sell_list.append((symbol, close_price))
                    continue
            ma_l = sum(series["close"][idx - ma_long:idx]) / ma_long
            if close_price < ma_l:
                sell_list.append((symbol, close_price))

        for symbol, price in sell_list:
            if daily_trades >= max_daily_trades:
                break
            pos = positions.pop(symbol, None)
            if not pos:
                continue
            qty = pos.get("qty", 0)
            if qty <= 0:
                continue
            value = qty * price
            fee = value * SELL_FEE_RATE
            cash += value - fee
            total_traded_value += value
            pnl = (price - pos.get("entry_price", price)) * qty - fee
            trade_pnls.append(pnl)
            daily_trades += 1

        if daily_trades < max_daily_trades and len(positions) < max_positions:
            candidates = []
            for symbol, series in data.items():
                if symbol in positions:
                    continue
                idx = series["index_map"].get(current_date)
                if idx is None:
                    continue
                start_idx = start_idx_map.get(symbol, idx)
                if idx - start_idx < max(ma_long, momentum_days, breakout_window):
                    continue
                close_price = series["close"][idx]
                ma_s = sum(series["close"][idx - ma_short:idx]) / ma_short
                ma_l = sum(series["close"][idx - ma_long:idx]) / ma_long
                momentum = close_price / series["close"][idx - momentum_days] - 1 if series["close"][idx - momentum_days] > 0 else 0
                recent_high = max(series["high"][idx - breakout_window:idx])
                if close_price > ma_l and ma_s > ma_l and close_price >= recent_high and momentum > 0:
                    candidates.append((symbol, momentum, close_price))

            candidates.sort(key=lambda x: x[1], reverse=True)
            slots = max_positions - len(positions)
            if slots > 0 and equity > 0:
                allocation_value = min(max_position_pct, 1.0 / max_positions) * equity
                for symbol, _momentum, price in candidates:
                    if daily_trades >= max_daily_trades or slots <= 0:
                        break
                    qty = int(allocation_value / price / LOT_SIZE) * LOT_SIZE
                    if qty <= 0:
                        continue
                    value = qty * price
                    fee = max(5, value * BUY_FEE_RATE)
                    if value + fee > cash:
                        max_qty = int((cash / (price * (1 + BUY_FEE_RATE))) / LOT_SIZE) * LOT_SIZE
                        if max_qty <= 0:
                            continue
                        qty = max_qty
                        value = qty * price
                        fee = max(5, value * BUY_FEE_RATE)
                    cash -= (value + fee)
                    positions[symbol] = {"qty": qty, "entry_price": price}
                    total_traded_value += value
                    slots -= 1
                    daily_trades += 1

    metrics = _compute_metrics(equity_curve, trade_pnls, total_traded_value)
    return {"metrics": metrics}


def _to_ymd(date_iso: str) -> str:
    return date_iso.replace("-", "")


def check_and_sync_cache(symbols: List[str], start_date: str, end_date: str, min_days: int, auto_sync: bool) -> Tuple[List[str], str]:
    ok_symbols = []
    effective_start = start_date
    for symbol in symbols:
        klines = _load_cached_daily(symbol, start_date, end_date)
        first_date = _kline_date_iso(klines[0]) if klines else ""
        last_date = _kline_date_iso(klines[-1]) if klines else ""
        needs_sync = False
        reason = ""
        if not klines:
            needs_sync = True
            reason = "no_cache"
        elif len(klines) < min_days:
            needs_sync = True
            reason = f"insufficient_records({len(klines)})"
        elif first_date and first_date > start_date:
            needs_sync = True
            reason = f"missing_start({first_date})"
        elif last_date and last_date < end_date:
            needs_sync = True
            reason = f"missing_end({last_date})"

        if needs_sync and auto_sync:
            print(f"[cache] {symbol} {reason} -> syncing...")
            get_klines_with_cache(
                symbol,
                period="daily",
                start_date=_to_ymd(start_date),
                end_date=_to_ymd(end_date),
                limit=5000,
                force_refresh=True,
                include_intraday=False
            )
            klines = _load_cached_daily(symbol, start_date, end_date)
            first_date = _kline_date_iso(klines[0]) if klines else ""
            last_date = _kline_date_iso(klines[-1]) if klines else ""
            if not klines:
                print(f"[cache] {symbol} still empty after sync")
                continue
            if len(klines) < min_days:
                print(f"[cache] {symbol} still insufficient ({len(klines)}) after sync")
                continue
            if first_date and first_date > start_date:
                print(f"[cache] {symbol} start missing after sync ({first_date}), advance start")
                if first_date > effective_start:
                    effective_start = first_date
            if last_date and last_date < end_date:
                print(f"[cache] {symbol} end missing after sync ({last_date})")
                continue
        elif needs_sync and not auto_sync:
            print(f"[cache] {symbol} {reason} (sync skipped)")
            if reason.startswith("missing_start"):
                needs_sync = False
            else:
                continue

        if first_date and first_date > start_date:
            print(f"[cache] {symbol} start missing ({first_date}), advance start")
            if first_date > effective_start:
                effective_start = first_date
        ok_symbols.append(symbol)
    return ok_symbols, effective_start


def train(
    symbols: List[str],
    params: Dict[str, Any],
    constraints: Dict[str, Any],
    objectives: Dict[str, Any],
    optimization: Dict[str, Any],
    initial_capital: float,
    train_days: int,
    mode: str,
    max_days: int | None,
    start_date: str,
    end_date: str,
    log_dir: Path,
    run_id: str,
    progress_every: int = 20,
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 10,
    resume_state: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    data = _prepare_symbol_data(symbols, start_date, end_date)
    if not data:
        return {"error": "no_cached_data"}

    timeline = sorted({d for series in data.values() for d in series["dates"]})
    if len(timeline) <= train_days:
        return {
            "error": "insufficient_data",
            "available_days": len(timeline),
            "first_date": str(timeline[0]) if timeline else "",
            "last_date": str(timeline[-1]) if timeline else ""
        }

    candidates = _generate_candidates(params, constraints, optimization)
    stats = []
    for cand in candidates:
        stats.append({
            "params": cand["params"],
            "constraints": cand["constraints"],
            "score_sum": 0.0,
            "score_count": 0,
            "metrics_sum": {},
            "metrics_count": 0
        })

    eval_count = 0
    start_idx = train_days
    log_mode = "w"
    if resume_state:
        try:
            rule_state = resume_state.get("rule") or {}
            has_progress = bool(rule_state.get("eval_count") or rule_state.get("next_idx"))
            if has_progress:
                start_idx = int(rule_state.get("next_idx") or train_days)
                eval_count = int(rule_state.get("eval_count") or 0)
                saved_stats = rule_state.get("candidates") or []
                if saved_stats and len(saved_stats) == len(stats) and "score_sum" in saved_stats[0]:
                    stats = saved_stats
                log_mode = "a"
                print(f"[train] resume eval_count={eval_count} next_idx={start_idx}")
            else:
                log_mode = "w"
        except Exception:
            start_idx = train_days
            eval_count = 0
            log_mode = "w"
    summary_path = log_dir / f"summary_{run_id}.log"
    detail_path = log_dir / f"detail_{run_id}.jsonl"
    total_eval = len(timeline) - train_days
    print(f"[train] logs: {summary_path} , {detail_path}")
    print(f"[train] total_evals={total_eval} (mode={mode}, train_days={train_days})")
    with open(summary_path, log_mode, encoding="utf-8") as summary_f, open(detail_path, log_mode, encoding="utf-8") as detail_f:
        for idx in range(start_idx, len(timeline)):
            if max_days and eval_count >= max_days:
                break
            if mode == "rolling":
                window_start_idx = idx - train_days
            else:
                window_start_idx = 0
            window_dates = timeline[window_start_idx: idx + 1]
            window_label = f"{window_dates[0]} -> {window_dates[-1]}"
            for j, cand in enumerate(stats):
                result = _backtest_window(data, window_dates, cand["params"], cand["constraints"], initial_capital)
                metrics = result.get("metrics") or {}
                score = _score_metrics(metrics, objectives)
                cand["score_sum"] = float(cand.get("score_sum") or 0.0) + float(score)
                cand["score_count"] = int(cand.get("score_count") or 0) + 1
                metrics_sum = cand.get("metrics_sum") or {}
                for k, v in metrics.items():
                    metrics_sum[k] = float(metrics_sum.get(k, 0.0)) + float(v)
                cand["metrics_sum"] = metrics_sum
                cand["metrics_count"] = int(cand.get("metrics_count") or 0) + 1
                detail_f.write(json.dumps({
                    "eval_index": eval_count + 1,
                    "window_start": str(window_dates[0]),
                    "window_end": str(window_dates[-1]),
                    "candidate_index": j,
                    "params": cand["params"],
                    "constraints": cand["constraints"],
                    "metrics": metrics,
                    "score": score
                }, ensure_ascii=False) + "\n")
            eval_count += 1
            best_score = max((float(c["score_sum"]) / max(int(c["score_count"]), 1) for c in stats), default=0.0)
            summary_f.write(f"[{eval_count}] {window_label} best_avg_score={best_score:.6f}\n")
            summary_f.flush()
            if progress_every and (eval_count == 1 or eval_count % progress_every == 0):
                print(f"[train] {eval_count}/{total_eval} {window_label} best_avg_score={best_score:.6f}")
            if checkpoint_path and checkpoint_every and eval_count % checkpoint_every == 0:
                _save_checkpoint(
                    checkpoint_path,
                    resume_state,
                    {
                        "next_idx": idx + 1,
                        "eval_count": eval_count,
                        "candidates": stats
                    }
                )

    summary = []
    for cand in stats:
        avg_score = float(cand.get("score_sum") or 0.0) / max(int(cand.get("score_count") or 1), 1)
        avg_metrics = {}
        metrics_sum = cand.get("metrics_sum") or {}
        metrics_count = int(cand.get("metrics_count") or 0)
        for k, v in metrics_sum.items():
            avg_metrics[k] = float(v) / max(metrics_count, 1)
        summary.append({
            "params": cand["params"],
            "constraints": cand["constraints"],
            "avg_score": avg_score,
            "avg_metrics": avg_metrics,
            "eval_count": int(cand.get("score_count") or 0)
        })

    summary.sort(key=lambda x: x["avg_score"], reverse=True)
    best = summary[0] if summary else None
    if checkpoint_path:
        _save_checkpoint(
            checkpoint_path,
            resume_state,
            rule_update={
                "next_idx": len(timeline),
                "eval_count": eval_count,
                "candidates": stats
            }
        )
    return {
        "status": "ok",
        "symbols": symbols,
        "train_days": train_days,
        "mode": mode,
        "evaluated_days": eval_count,
        "best": best,
        "candidates": summary
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train strategy parameters with walk-forward backtests.")
    parser.add_argument("--strategy-id", type=int, default=None)
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--train-days", type=int, default=126)
    parser.add_argument("--train-years", type=int, default=1, help="Lookback years used when start-date not provided")
    parser.add_argument("--mode", type=str, choices=["expanding", "rolling"], default="rolling")
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--apply", action="store_true", help="Apply best params to DB strategy (requires --strategy-id)")
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--list", action="store_true", help="List strategies and exit")
    parser.add_argument("--no-sync", action="store_true", help="Disable auto sync if cache is missing")
    parser.add_argument("--progress-every", type=int, default=20, help="Print progress every N evals")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM prompt optimization stage")
    parser.add_argument("--prompt-variants", type=int, default=4, help="Number of LLM prompt variants")
    parser.add_argument("--llm-model", type=str, default="", help="Override LLM model for training")
    parser.add_argument("--no-log-prompts", action="store_true", help="Disable logging system/user prompts in LLM detail log")
    parser.add_argument("--llm-progress-every", type=int, default=50, help="Print LLM progress every N calls")
    parser.add_argument("--reset", action="store_true", help="Reset training checkpoint and start fresh")
    parser.add_argument("--checkpoint-every", type=int, default=10, help="Save checkpoint every N evals")
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.list:
            rows = conn.execute("SELECT id, name, type, is_ai FROM paper_strategies ORDER BY id").fetchall()
            for r in rows:
                print(f"{r[0]}\t{r[1]}\t{r[2]}\tAI={bool(r[3])}")
            return
        strategy = None
        if args.strategy_id:
            row = conn.execute(
                "SELECT id, prompt, objectives_json, constraints_json, params_json, optimization_json, initial_capital, universe_type, universe_symbols "
                "FROM paper_strategies WHERE id = ?",
                (args.strategy_id,)
            ).fetchone()
            if not row:
                print("strategy_not_found")
                return
            strategy = {
                "id": row[0],
                "prompt": row[1] or "",
                "objectives": json.loads(row[2]) if row[2] else None,
                "constraints": json.loads(row[3]) if row[3] else None,
                "params": json.loads(row[4]) if row[4] else None,
                "optimization": json.loads(row[5]) if row[5] else None,
                "initial_capital": row[6] or 100000,
                "universe_type": row[7],
                "universe_symbols": row[8] or ""
            }
        objectives = normalize_objectives(strategy.get("objectives") if strategy else None)
        constraints = normalize_constraints(strategy.get("constraints") if strategy else None)
        params = normalize_params(strategy.get("params") if strategy else None)
        optimization = normalize_optimization(strategy.get("optimization") if strategy else None)

        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        elif strategy:
            symbols = fetch_symbols_for_strategy(conn, strategy)
            max_symbols = int(optimization.get("max_symbols") or 30)
            symbols = symbols[:max_symbols]
        else:
            print("No symbols provided. Use --symbols or --strategy-id.")
            return

        if not symbols:
            print("No symbols to train.")
            return

        end_date = args.end_date or datetime.now().strftime("%Y-%m-%d")
        if args.start_date:
            start_date = args.start_date
        else:
            years = max(int(args.train_years or 1), 1)
            start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365 * years)).strftime("%Y-%m-%d")

        checkpoint_dir = Path(__file__).parent / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / f"strategy_{strategy.get('id') if strategy else 'custom'}.json"
        checkpoint_state = None
        if not args.reset:
            checkpoint_state = _load_checkpoint(checkpoint_path)
        if checkpoint_state and not args.start_date:
            prev_cfg = checkpoint_state.get("config") or {}
            if prev_cfg.get("start_date"):
                start_date = prev_cfg.get("start_date")

        symbols, effective_start = check_and_sync_cache(symbols, start_date, end_date, args.train_days, not args.no_sync)
        if not symbols:
            print("No symbols available after cache check.")
            return
        if effective_start and effective_start > start_date:
            print(f"[train] start_date advanced to {effective_start} due to cache availability")
            start_date = effective_start

        print(f"[train] symbols={len(symbols)} start={start_date} end={end_date}")
        symbols_for_hash = sorted(symbols)
        end_date_mode = "explicit" if args.end_date else "auto"
        config = {
            "strategy_id": strategy.get("id") if strategy else None,
            "symbols": symbols_for_hash,
            "train_days": args.train_days,
            "mode": args.mode,
            "start_date": start_date,
            "end_date_mode": end_date_mode,
            "end_date": end_date if args.end_date else None,
            "objectives": objectives,
            "constraints": constraints,
            "params": params,
            "optimization": optimization,
            "llm_enabled": not args.no_llm,
            "prompt_variants": args.prompt_variants,
            "llm_model": args.llm_model or (strategy.get("model_id") if strategy else None) or LLM_MODEL
        }
        config_hash = _config_hash(config)
        if checkpoint_state and checkpoint_state.get("config_hash") != config_hash:
            print("[train] config changed, start fresh")
            checkpoint_state = None
        if checkpoint_state:
            run_id = checkpoint_state.get("run_id") or datetime.now().strftime("%Y%m%d_%H%M%S")
            print(f"[train] resume from checkpoint {checkpoint_path.name}")
        else:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            checkpoint_state = {
                "config_hash": config_hash,
                "run_id": run_id,
                "config": config,
                "timeline": {
                    "start_date": start_date,
                    "end_date": end_date
                }
            }
            _save_checkpoint(checkpoint_path, checkpoint_state)
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        result = train(
            symbols=symbols,
            params=params,
            constraints=constraints,
            objectives=objectives,
            optimization=optimization,
            initial_capital=float(strategy.get("initial_capital") if strategy else 100000),
            train_days=args.train_days,
            mode=args.mode,
            max_days=args.max_days,
            start_date=start_date,
            end_date=end_date,
            log_dir=log_dir,
            run_id=run_id,
            progress_every=args.progress_every,
            checkpoint_path=checkpoint_path,
            checkpoint_every=args.checkpoint_every,
            resume_state=checkpoint_state
        )
        checkpoint_state = _load_checkpoint(checkpoint_path) or checkpoint_state

        if result.get("status") != "ok":
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        llm_summary = None
        if not args.no_llm and strategy:
            model_id = args.llm_model or strategy.get("model_id") or LLM_MODEL
            best = result.get("best") or {}
            best_params = best.get("params") or params
            best_constraints = best.get("constraints") or constraints
            llm_state = (checkpoint_state.get("llm") if checkpoint_state else None) or {}
            prompt_variants = llm_state.get("prompt_variants")
            if not prompt_variants:
                prompt_variants = _llm_generate_prompt_variants(
                    strategy.get("prompt") or "",
                    objectives,
                    best_constraints,
                    best_params,
                    args.prompt_variants,
                    model_id
                )
                llm_state["prompt_variants"] = prompt_variants
                _save_checkpoint(checkpoint_path, checkpoint_state, llm_update=llm_state)
            llm_results = llm_state.get("results") or {}
            llm_log_mode = "a" if llm_results else "w"
            print(f"[llm] variants={len(prompt_variants)} model={model_id}")
            llm_summary_path = log_dir / f"llm_summary_{run_id}.log"
            llm_detail_path = log_dir / f"llm_detail_{run_id}.jsonl"
            data = _prepare_symbol_data(symbols, start_date, end_date)
            with open(llm_summary_path, llm_log_mode, encoding="utf-8") as summary_f, open(llm_detail_path, llm_log_mode, encoding="utf-8") as detail_f:
                for idx, prompt_variant in enumerate(prompt_variants):
                    if str(idx) in llm_results:
                        print(f"[llm] prompt#{idx} already done, skip")
                        continue
                    print(f"[llm] evaluating prompt variant {idx + 1}/{len(prompt_variants)}")
                    metrics_result = _llm_backtest(
                        data=data,
                        symbols=symbols,
                        prompt=prompt_variant,
                        params=best_params,
                        constraints=best_constraints,
                        objectives=objectives,
                        initial_capital=float(strategy.get("initial_capital") or 100000),
                        model_id=model_id,
                        detail_f=detail_f,
                        prompt_index=idx,
                        max_days=args.max_days,
                        log_prompts=not args.no_log_prompts,
                        progress_every=args.llm_progress_every
                    )
                    metrics = metrics_result.get("metrics") or {}
                    score = _score_metrics(metrics, objectives)
                    print(f"[llm] prompt#{idx} score={score:.6f}")
                    llm_results[str(idx)] = {
                        "prompt_index": idx,
                        "prompt": prompt_variant,
                        "metrics": metrics,
                        "score": score
                    }
                    summary_f.write(f"[{idx}] score={score:.6f} metrics={json.dumps(metrics, ensure_ascii=False)}\n")
                    summary_f.flush()
                    llm_state["results"] = llm_results
                    _save_checkpoint(checkpoint_path, checkpoint_state, llm_update=llm_state)
            llm_list = list(llm_results.values())
            llm_list.sort(key=lambda x: x["score"], reverse=True)
            llm_summary = {
                "best_prompt_index": llm_list[0]["prompt_index"] if llm_list else None,
                "best_score": llm_list[0]["score"] if llm_list else None,
                "best_metrics": llm_list[0]["metrics"] if llm_list else None,
                "variants": len(llm_list),
                "log_summary": str(llm_summary_path),
                "log_detail": str(llm_detail_path)
            }
            result["llm_prompt_optimization"] = {
                "model_id": model_id,
                "variants": llm_list
            }

        output = args.output or str(Path(__file__).parent / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Saved results to {output}")

        best = result.get("best")
        if best and args.apply and strategy:
            updated_prompt_source = strategy.get("prompt") or ""
            if llm_summary and result.get("llm_prompt_optimization"):
                llm_best = result["llm_prompt_optimization"]["variants"][0] if result["llm_prompt_optimization"]["variants"] else None
                if llm_best and llm_best.get("prompt"):
                    updated_prompt_source = llm_best["prompt"]
            updated_prompt = _apply_prompt_override(updated_prompt_source, best["params"], best["constraints"], objectives)
            summary = {
                "avg_score": best.get("avg_score"),
                "avg_metrics": best.get("avg_metrics"),
                "evaluated_days": result.get("evaluated_days"),
                "mode": result.get("mode"),
                "train_days": result.get("train_days"),
                "llm": llm_summary
            }
            conn.execute(
                "UPDATE paper_strategies SET params_json = ?, constraints_json = ?, objectives_json = ?, optimization_json = ?, prompt = ?, last_optimized_at = ?, last_optimization_score = ?, last_optimization_summary = ? WHERE id = ?",
                (
                    json.dumps(best["params"], ensure_ascii=False),
                    json.dumps(best["constraints"], ensure_ascii=False),
                    json.dumps(objectives, ensure_ascii=False),
                    json.dumps(optimization, ensure_ascii=False),
                    updated_prompt,
                    datetime.now(),
                    float(best.get("avg_score") or 0.0),
                    json.dumps(summary, ensure_ascii=False),
                    strategy["id"]
                )
            )
            print(f"Applied best params to strategy {strategy['id']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
