import json
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from db import get_connection
from cache import get_klines_with_cache
from strategy_universe import fetch_symbols_for_strategy
from strategy_defaults import (
    normalize_objectives,
    normalize_constraints,
    normalize_params,
    normalize_optimization
)
from trading_time import now_cn

BUY_FEE_RATE = 0.0005
SELL_FEE_RATE = 0.014
LOT_SIZE = 100

_scheduler_started = False


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _build_override_block(params: Dict[str, Any], constraints: Dict[str, Any], objectives: Dict[str, Any]) -> str:
    payload = {
        "params": params,
        "constraints": constraints,
        "objectives": objectives
    }
    return (
        "\n\n# <AUTO_STRATEGY_OVERRIDE>\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "# </AUTO_STRATEGY_OVERRIDE>\n"
    )


def _apply_prompt_override(prompt: str, params: Dict[str, Any], constraints: Dict[str, Any], objectives: Dict[str, Any]) -> str:
    block = _build_override_block(params, constraints, objectives)
    if not prompt:
        return block.strip()
    start = prompt.find("# <AUTO_STRATEGY_OVERRIDE>")
    end = prompt.find("# </AUTO_STRATEGY_OVERRIDE>")
    if start != -1 and end != -1 and end > start:
        end = end + len("# </AUTO_STRATEGY_OVERRIDE>")
        return prompt[:start].rstrip() + block + prompt[end:].lstrip()
    return prompt.rstrip() + block


def _compute_metrics(equity_curve: List[float], trade_pnls: List[float], total_traded_value: float) -> Dict[str, float]:
    if len(equity_curve) < 2:
        return {
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "turnover": 0.0,
            "win_rate": 0.0
        }
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        curr = equity_curve[i]
        if prev <= 0:
            returns.append(0.0)
        else:
            returns.append(curr / prev - 1)
    days = len(returns)
    total_return = equity_curve[-1] / equity_curve[0] - 1
    annual_return = (1 + total_return) ** (252 / max(days, 1)) - 1 if days > 0 else 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak if peak > 0 else 0
        if drawdown > max_dd:
            max_dd = drawdown

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


def _score_metrics(metrics: Dict[str, float], objectives: Dict[str, Any]) -> float:
    w_ret = _safe_float(objectives.get("annual_return_weight"))
    w_dd = _safe_float(objectives.get("max_drawdown_weight"))
    w_sharpe = _safe_float(objectives.get("sharpe_weight"))
    w_turn = _safe_float(objectives.get("turnover_weight"))
    score = (
        w_ret * metrics.get("annual_return", 0.0)
        - w_dd * metrics.get("max_drawdown", 0.0)
        + w_sharpe * metrics.get("sharpe", 0.0)
        - w_turn * metrics.get("turnover", 0.0)
    )
    return float(score)


def _generate_candidates(params: Dict[str, Any], constraints: Dict[str, Any], optimization: Dict[str, Any]) -> List[Dict[str, Any]]:
    base = {
        "params": params,
        "constraints": constraints
    }
    candidates = [base]
    count = int(optimization.get("candidate_count") or 6)
    seed = int(now_cn().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    for _ in range(max(count - 1, 0)):
        p = dict(params)
        c = dict(constraints)
        p["ma_short"] = max(3, int(p.get("ma_short", 5) + rnd.randint(-2, 2)))
        p["ma_long"] = max(p["ma_short"] + 5, int(p.get("ma_long", 20) + rnd.randint(-5, 5)))
        p["momentum_days"] = max(5, int(p.get("momentum_days", 20) + rnd.randint(-5, 5)))
        p["breakout_window"] = max(10, int(p.get("breakout_window", 20) + rnd.randint(-5, 10)))

        c["max_position_pct"] = min(35.0, max(5.0, float(c.get("max_position_pct", 20.0) + rnd.uniform(-5, 5))))
        c["max_positions"] = max(2, int(c.get("max_positions", 8) + rnd.randint(-2, 2)))
        c["max_daily_trades"] = max(1, int(c.get("max_daily_trades", 5) + rnd.randint(-1, 2)))
        c["stop_loss_pct"] = min(15.0, max(2.0, float(c.get("stop_loss_pct", 5.0) + rnd.uniform(-2, 2))))
        c["take_profit_pct"] = min(40.0, max(5.0, float(c.get("take_profit_pct", 15.0) + rnd.uniform(-5, 5))))
        candidates.append({"params": p, "constraints": c})
    return candidates


def _prepare_symbol_data(symbols: List[str], start_date: str, end_date: str) -> Dict[str, Dict[str, Any]]:
    data = {}
    for symbol in symbols:
        klines, _ = get_klines_with_cache(symbol, period="daily", start_date=start_date, end_date=end_date, limit=800)
        if not klines or len(klines) < 30:
            continue
        dates = []
        close = []
        high = []
        low = []
        for k in klines:
            date_str = k.get("date") or ""
            if not date_str:
                continue
            dt = _parse_date(date_str)
            if not dt:
                continue
            dates.append(dt)
            close.append(_safe_float(k.get("close")))
            high.append(_safe_float(k.get("high")))
            low.append(_safe_float(k.get("low")))
        if len(dates) < 30:
            continue
        index_map = {d: i for i, d in enumerate(dates)}
        data[symbol] = {
            "dates": dates,
            "close": close,
            "high": high,
            "low": low,
            "index_map": index_map
        }
    return data


def _backtest(symbols: List[str], params: Dict[str, Any], constraints: Dict[str, Any], initial_capital: float, start_date: str, end_date: str) -> Dict[str, Any]:
    data = _prepare_symbol_data(symbols, start_date, end_date)
    if not data:
        return {"metrics": {}, "equity_curve": [], "trade_pnls": [], "total_traded_value": 0.0}

    all_dates = sorted({d for series in data.values() for d in series["dates"]})
    max_positions = int(constraints.get("max_positions") or 8)
    max_daily_trades = int(constraints.get("max_daily_trades") or 5)
    max_position_pct = float(constraints.get("max_position_pct") or 20.0) / 100.0
    stop_loss_pct = float(constraints.get("stop_loss_pct") or 5.0) / 100.0
    take_profit_pct = float(constraints.get("take_profit_pct") or 15.0) / 100.0

    positions: Dict[str, Dict[str, Any]] = {}
    cash = float(initial_capital)
    equity_curve: List[float] = []
    trade_pnls: List[float] = []
    total_traded_value = 0.0
    last_price: Dict[str, float] = {}

    ma_short = int(params.get("ma_short") or 5)
    ma_long = int(params.get("ma_long") or 20)
    momentum_days = int(params.get("momentum_days") or 20)
    breakout_window = int(params.get("breakout_window") or 20)

    for current_date in all_dates:
        daily_trades = 0
        # Update prices
        for symbol, series in data.items():
            idx = series["index_map"].get(current_date)
            if idx is None:
                continue
            last_price[symbol] = series["close"][idx]

        # Compute equity
        equity = cash
        for symbol, pos in positions.items():
            price = last_price.get(symbol, pos.get("entry_price", 0))
            equity += price * pos.get("qty", 0)
        equity_curve.append(equity)

        # Generate sell signals
        sell_list: List[Tuple[str, float]] = []
        for symbol, pos in list(positions.items()):
            series = data.get(symbol)
            if not series:
                continue
            idx = series["index_map"].get(current_date)
            if idx is None:
                continue
            close_price = series["close"][idx]
            entry_price = pos.get("entry_price", close_price)
            if entry_price > 0:
                if close_price <= entry_price * (1 - stop_loss_pct) or close_price >= entry_price * (1 + take_profit_pct):
                    sell_list.append((symbol, close_price))
                    continue
            if idx >= ma_long:
                ma_l = sum(series["close"][idx - ma_long:idx]) / ma_long
                if close_price < ma_l:
                    sell_list.append((symbol, close_price))
        # Execute sells
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

        # Generate buy signals
        if daily_trades < max_daily_trades and len(positions) < max_positions:
            candidates = []
            for symbol, series in data.items():
                if symbol in positions:
                    continue
                idx = series["index_map"].get(current_date)
                if idx is None:
                    continue
                if idx < max(ma_long, momentum_days, breakout_window):
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
    return {
        "metrics": metrics,
        "equity_curve": equity_curve,
        "trade_pnls": trade_pnls,
        "total_traded_value": total_traded_value
    }


def _maybe_tune_objectives(metrics: Dict[str, float], objectives: Dict[str, Any]) -> Dict[str, Any]:
    tuned = dict(objectives)
    if metrics.get("max_drawdown", 0) > 0.2:
        tuned["max_drawdown_weight"] = min(2.0, _safe_float(tuned.get("max_drawdown_weight")) + 0.1)
    if metrics.get("annual_return", 0) < 0.05:
        tuned["annual_return_weight"] = min(2.0, _safe_float(tuned.get("annual_return_weight")) + 0.1)
    return tuned


def optimize_strategy(conn, strategy: Dict[str, Any]) -> Dict[str, Any]:
    objectives = normalize_objectives(strategy.get("objectives"))
    constraints = normalize_constraints(strategy.get("constraints"))
    params = normalize_params(strategy.get("params"))
    optimization = normalize_optimization(strategy.get("optimization"))

    if not optimization.get("enabled", True):
        return {"status": "disabled"}

    backtest_years = int(optimization.get("backtest_years") or 2)
    end_date = now_cn().date()
    start_date = end_date - timedelta(days=backtest_years * 365)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    symbols = strategy.get("symbols") or []
    max_symbols = int(optimization.get("max_symbols") or 30)
    symbols = symbols[:max_symbols]
    if not symbols:
        return {"status": "no_symbols"}

    candidates = _generate_candidates(params, constraints, optimization)
    best = None
    results = []
    for candidate in candidates:
        bt = _backtest(symbols, candidate["params"], candidate["constraints"], float(strategy.get("initial_capital") or 100000), start_str, end_str)
        metrics = bt.get("metrics") or {}
        score = _score_metrics(metrics, objectives)
        results.append({
            "params": candidate["params"],
            "constraints": candidate["constraints"],
            "metrics": metrics,
            "score": score
        })
        if best is None or score > best["score"]:
            best = results[-1]

    if not best:
        return {"status": "no_result"}

    current_bt = _backtest(symbols, params, constraints, float(strategy.get("initial_capital") or 100000), start_str, end_str)
    current_metrics = current_bt.get("metrics") or {}
    current_score = _score_metrics(current_metrics, objectives)

    min_improvement = float(optimization.get("min_improvement_pct") or 0) / 100.0
    improved = best["score"] >= current_score * (1 + min_improvement) if current_score != 0 else True

    summary = {
        "current_score": current_score,
        "best_score": best["score"],
        "current_metrics": current_metrics,
        "best_metrics": best["metrics"],
        "candidate_count": len(candidates)
    }

    auto_replace = bool(optimization.get("auto_replace"))
    auto_update_prompt = bool(optimization.get("auto_update_prompt", True))
    auto_tune_objectives = bool(optimization.get("auto_tune_objectives", True))

    updated_objectives = objectives
    if auto_tune_objectives:
        updated_objectives = _maybe_tune_objectives(best["metrics"], objectives)

    if improved and auto_replace:
        updated_prompt = strategy.get("prompt") or ""
        if auto_update_prompt:
            updated_prompt = _apply_prompt_override(updated_prompt, best["params"], best["constraints"], updated_objectives)
        conn.execute(
            "UPDATE paper_strategies SET params_json = ?, constraints_json = ?, objectives_json = ?, optimization_json = ?, prompt = ?, last_optimized_at = ?, last_optimization_score = ?, last_optimization_summary = ? WHERE id = ?",
            (
                json.dumps(best["params"], ensure_ascii=False),
                json.dumps(best["constraints"], ensure_ascii=False),
                json.dumps(updated_objectives, ensure_ascii=False),
                json.dumps(optimization, ensure_ascii=False),
                updated_prompt,
                now_cn(),
                float(best["score"]),
                json.dumps(summary, ensure_ascii=False),
                strategy["id"]
            )
        )
    else:
        conn.execute(
            "UPDATE paper_strategies SET objectives_json = ?, optimization_json = ?, last_optimized_at = ?, last_optimization_score = ?, last_optimization_summary = ? WHERE id = ?",
            (
                json.dumps(updated_objectives, ensure_ascii=False),
                json.dumps(optimization, ensure_ascii=False),
                now_cn(),
                float(current_score),
                json.dumps(summary, ensure_ascii=False),
                strategy["id"]
            )
        )
    return {
        "status": "updated" if improved and auto_replace else "evaluated",
        "summary": summary
    }


def run_pending_optimizations() -> None:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, prompt, objectives_json, constraints_json, params_json, optimization_json, initial_capital, universe_type, universe_symbols, last_optimized_at "
            "FROM paper_strategies WHERE is_ai = TRUE"
        ).fetchall()
        now = now_cn()
        for r in rows:
            last_opt = r[9]
            if last_opt and isinstance(last_opt, datetime) and last_opt.date() == now.date():
                continue
            strategy = {
                "id": r[0],
                "prompt": r[1] or "",
                "objectives": json.loads(r[2]) if r[2] else None,
                "constraints": json.loads(r[3]) if r[3] else None,
                "params": json.loads(r[4]) if r[4] else None,
                "optimization": json.loads(r[5]) if r[5] else None,
                "initial_capital": r[6] or 100000,
                "universe_type": r[7],
                "universe_symbols": r[8] or ""
            }
            symbols = fetch_symbols_for_strategy(conn, strategy)
            strategy["symbols"] = symbols
            optimization = normalize_optimization(strategy.get("optimization"))
            if not optimization.get("enabled", True):
                continue
            optimize_strategy(conn, strategy)
    except Exception as e:
        print(f"[Optimizer] Error: {e}")
    finally:
        conn.close()


def _scheduler_loop() -> None:
    while True:
        try:
            run_pending_optimizations()
        except Exception as e:
            print(f"[Optimizer] Loop error: {e}")
        time.sleep(60)


def start_optimizer_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
