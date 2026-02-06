import json
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import LLM_MODEL
from db import get_connection
from llm import LLMService
from cache import get_klines_with_cache
from paper import OrderCreate, place_order, format_symbol, get_last_close, get_spot_maps, safe_float, _get_cash_balance, _get_initial_capital
from strategy_universe import fetch_symbols_for_strategy
from strategy_defaults import normalize_params, normalize_constraints
from strategy_rules import compute_baseline_signal, serialize_baseline
from trading_time import should_auto_run, now_cn
import re
import urllib.request

_scheduler_started = False

INTERVAL_TO_PERIOD = {
    1: "1",
    3: "1",
    5: "5",
    15: "15",
    30: "30",
    60: "60",
    1440: "daily"
}


def _estimate_fee(side: str, price: float, qty: int) -> float:
    amount = price * qty
    if side == "BUY":
        return round(max(5, amount * 0.0005), 2)
    return round(amount * 0.014, 2)


def _get_market_prefix(symbol: str) -> str:
    code = format_symbol(symbol)
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("0", "2", "3", "1")):
        return "sz"
    if code.startswith(("8", "4")):
        return "bj"
    return "sh"


def _safe_float_local(value) -> float:
    try:
        if value is None:
            return 0
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in ("", "--", "None", "nan"):
                return 0
            return float(text)
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return 0
        return num
    except Exception:
        return 0


def _fetch_tencent_quote(symbol: str) -> Optional[Dict[str, Any]]:
    code = format_symbol(symbol)
    market_prefix = _get_market_prefix(symbol)
    url = f"https://qt.gtimg.cn/q={market_prefix}{code}"

    def _do():
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read()

    raw = _do()
    if not raw:
        return None
    try:
        text = raw.decode("gbk", errors="ignore")
    except Exception:
        text = raw.decode("utf-8", errors="ignore")
    match = re.search(r'="([^"]+)"', text)
    if not match:
        return None
    parts = match.group(1).split("~")
    if len(parts) < 35:
        return None
    name = parts[1] if len(parts) > 1 else None
    price = _safe_float_local(parts[3] if len(parts) > 3 else 0)
    prev_close = _safe_float_local(parts[4] if len(parts) > 4 else 0)
    open_p = _safe_float_local(parts[5] if len(parts) > 5 else 0)
    volume_lot = _safe_float_local(parts[6] if len(parts) > 6 else 0)
    change = _safe_float_local(parts[31] if len(parts) > 31 else (price - prev_close))
    change_pct = _safe_float_local(parts[32] if len(parts) > 32 else ((change / prev_close * 100) if prev_close else 0))
    high = _safe_float_local(parts[33] if len(parts) > 33 else 0)
    low = _safe_float_local(parts[34] if len(parts) > 34 else 0)
    amount = _safe_float_local(parts[37] if len(parts) > 37 else 0) * 10000
    time_str = parts[30] if len(parts) > 30 else ""
    ts = int(now_cn().timestamp() * 1000)
    if time_str and ":" in time_str:
        try:
            dt = datetime.strptime(f"{now_cn().strftime('%Y-%m-%d')} {time_str}", "%Y-%m-%d %H:%M:%S")
            ts = int(dt.timestamp() * 1000)
        except Exception:
            pass
    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "change": change,
        "changePercent": change_pct,
        "open": open_p,
        "high": high,
        "low": low,
        "volume": volume_lot * 100,
        "amount": amount,
        "timestamp": ts,
        "source": "tencent"
    }




def _extract_json(text: str) -> Optional[Dict[str, Any]]:
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


def _fetch_symbols_for_strategy(conn, strategy: Dict[str, Any]) -> List[str]:
    return fetch_symbols_for_strategy(conn, strategy)


def _get_positions_map(conn, strategy_id: int) -> Dict[str, Dict[str, float]]:
    rows = conn.execute(
        "SELECT symbol, quantity, avg_cost FROM paper_positions WHERE strategy_id = ?",
        (strategy_id,)
    ).fetchall()
    positions = {}
    for r in rows:
        symbol = str(r[0]).upper()
        positions[symbol] = {
            "quantity": int(r[1]),
            "avg_cost": float(r[2] or 0)
        }
    return positions


def _estimate_positions_value(positions_map: Dict[str, Dict[str, float]]) -> float:
    if not positions_map:
        return 0.0
    codes = {format_symbol(sym) for sym in positions_map.keys()}
    price_map, _ = get_spot_maps(codes)
    total = 0.0
    for symbol, pos in positions_map.items():
        qty = int(pos.get("quantity") or 0)
        if qty <= 0:
            continue
        code = format_symbol(symbol)
        price = safe_float(price_map.get(code))
        if price <= 0:
            price = get_last_close(symbol)
        if price <= 0:
            price = safe_float(pos.get("avg_cost"))
        if price <= 0:
            continue
        total += price * qty
    return total


def _get_latest_price(symbol: str) -> float:
    qt = _fetch_tencent_quote(symbol)
    if qt:
        price = _safe_float_local(qt.get("price"))
        if price > 0:
            return price
    code = format_symbol(symbol)
    price_map, _ = get_spot_maps({code})
    price = safe_float(price_map.get(code))
    if price > 0:
        return price
    last_close = get_last_close(symbol)
    if last_close > 0:
        return last_close
    return 0


def _build_strategy_prompt(
    strategy_prompt: str,
    symbol: str,
    klines: List[Dict[str, Any]],
    account_context: Dict[str, Any],
    data_note: str = "",
    baseline: Optional[Dict[str, Any]] = None
) -> str:
    llm = LLMService()
    data_context = llm._format_kline_data(klines)
    account_json = json.dumps(account_context, ensure_ascii=False)
    note_text = f"\nData Note: {data_note}\n" if data_note else "\n"
    baseline_text = ""
    if baseline:
        baseline_text = f"\nBaseline signal (rule engine): {serialize_baseline(baseline)}\n"
    return (
        f"{strategy_prompt}\n\n"
        "Sizing rules (must follow):\n"
        "- Use account_context to size orders. Do NOT assume a fixed quantity.\n"
        "- For BUY: total cost (price * quantity + fee) must be <= cash_balance.\n"
        "- SELL only if the symbol exists in account_context.positions with quantity > 0. If not held, output HOLD.\n"
        "- For A-share: quantity must be an integer multiple of 100.\n"
        "- Respect position sizing rules in the strategy prompt. If not specified, keep single position <= 20% of total_equity.\n"
        "- If constraints are not met, output HOLD or reduce quantity.\n\n"
        "Baseline guidance:\n"
        "- A rule-engine baseline signal is provided. Use it as the default unless strong evidence suggests otherwise.\n"
        "- If you override the baseline, explain why in the reason field.\n\n"
        f"Account context (JSON): {account_json}\n\n"
        f"Symbol: {symbol}\n"
        f"{baseline_text}"
        f"{note_text}"
        f"Recent Kline Data:\n{data_context}\n"
        "Return only valid JSON per the specified schema."
    )


def _truncate(text: str, max_len: int = 4000) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...(truncated)"


def _run_strategy_for_symbol(
    strategy: Dict[str, Any],
    symbol: str,
    positions_map: Dict[str, Dict[str, float]],
    cash_balance: float,
    initial_capital: float,
    total_equity: float
) -> Tuple[int, str, str, float]:
    data_note = ""
    klines, _ = get_klines_with_cache(symbol, period="daily", limit=200)
    quote = _fetch_tencent_quote(symbol)
    if quote:
        ts = quote.get("timestamp")
        ts_text = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"
        data_note = (
            f"Realtime quote (Tencent {ts_text}): "
            f"price {quote.get('price')}, open {quote.get('open')}, high {quote.get('high')}, "
            f"low {quote.get('low')}, volume {quote.get('volume')}."
        )
    else:
        data_note = "Realtime quote unavailable; using daily data only."

    if not klines:
        return 0, "no_klines", "", cash_balance

    model_id = strategy.get("model_id") or LLM_MODEL
    prompt = strategy.get("prompt") or ""
    params = normalize_params(strategy.get("params") or {})
    constraints = normalize_constraints(strategy.get("constraints") or {})
    llm = LLMService()
    client = llm._get_client(model_id)
    if not client:
        print(f"[StrategyRunner] LLM client not configured for model {model_id}")
        return 0, "llm_not_configured", "", cash_balance

    positions_list = []
    for sym, pos in positions_map.items():
        positions_list.append({
            "symbol": sym,
            "quantity": int(pos.get("quantity") or 0),
            "avg_cost": float(pos.get("avg_cost") or 0)
        })
    account_context = {
        "initial_capital": round(float(initial_capital), 2),
        "cash_balance": round(float(cash_balance), 2),
        "total_equity": round(float(total_equity), 2),
        "positions": positions_list
    }
    baseline = compute_baseline_signal(
        klines=klines,
        position=positions_map.get(symbol.upper()),
        params=params,
        constraints=constraints,
        account_context=account_context
    )
    user_prompt = _build_strategy_prompt(
        prompt,
        symbol,
        klines,
        account_context,
        data_note=data_note,
        baseline=baseline
    )
    system_prompt = (
        "You are a trading strategy executor. "
        "Return only JSON that follows the provided schema. No extra text."
    )

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
        print(f"[StrategyRunner] LLM call failed for {symbol}: {e}")
        return 0, f"llm_error: {e}", "", cash_balance

    data = _extract_json(content or "")
    if not data:
        print(f"[StrategyRunner] Invalid JSON for {symbol}")
        return 0, "invalid_json", _truncate(content or ""), cash_balance

    actions = None
    if isinstance(data, dict):
        actions = data.get("actions")
        if not isinstance(actions, list):
            # Some models return a single action object like {"action": "BUY", ...}
            if "action" in data or "side" in data:
                actions = [data]
            else:
                actions = []
    else:
        actions = []

    action_count = 0
    error = ""
    no_position_flag = False
    for action in actions:
        if not isinstance(action, dict):
            continue
        act_symbol = action.get("symbol") or symbol
        if format_symbol(act_symbol) != format_symbol(symbol):
            continue
        side = str(action.get("side") or action.get("action") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        qty = action.get("quantity") if action.get("quantity") is not None else action.get("qty")
        qty = int(qty or 0)
        if qty <= 0:
            continue
        if side == "SELL":
            available = positions_map.get(symbol.upper(), {}).get("quantity", 0)
            if available <= 0:
                no_position_flag = True
                continue
            if qty > available:
                qty = available
        order_type = str(action.get("order_type") or action.get("orderType") or "MKT").upper()
        raw_price = action.get("price")
        if isinstance(raw_price, str) and raw_price.strip().upper() in ("MKT", "MARKET"):
            raw_price = 0
            order_type = "MKT"
        price = safe_float(raw_price)
        if order_type == "MKT" or price <= 0:
            price = _get_latest_price(symbol)
        if price <= 0:
            continue
        fee = _estimate_fee(side, price, qty)
        if side == "BUY":
            required = price * qty + fee
            if required > cash_balance:
                error = "insufficient_cash"
                continue
        try:
            place_order(OrderCreate(
                symbol=symbol,
                side=side,
                price=price,
                quantity=qty,
                fee=fee,
                strategy_id=strategy["id"]
            ))
            action_count += 1
            if side == "BUY":
                cash_balance -= price * qty + fee
                pos = positions_map.get(symbol.upper(), {"quantity": 0, "avg_cost": 0})
                pos["quantity"] = int(pos.get("quantity") or 0) + qty
                positions_map[symbol.upper()] = pos
            elif side == "SELL":
                cash_balance += price * qty - fee
                pos = positions_map.get(symbol.upper(), {"quantity": 0, "avg_cost": 0})
                pos["quantity"] = int(pos.get("quantity") or 0) - qty
                if pos["quantity"] <= 0:
                    positions_map.pop(symbol.upper(), None)
                else:
                    positions_map[symbol.upper()] = pos
        except Exception as e:
            print(f"[StrategyRunner] Order failed for {symbol}: {e}")
            return action_count, f"order_error: {e}", _truncate(content or ""), cash_balance

    if action_count == 0 and not error and no_position_flag:
        error = "no_position"
    return action_count, error, _truncate(content or ""), cash_balance


def _create_run_record(conn, strategy: Dict[str, Any], symbols: List[str]) -> Optional[int]:
    try:
        res = conn.execute(
            "INSERT INTO paper_strategy_runs (strategy_id, status, symbols, symbol_count, model_id, run_interval_minutes, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                strategy["id"],
                "running",
                ",".join(symbols),
                len(symbols),
                strategy.get("model_id"),
                strategy.get("run_interval_minutes"),
                datetime.now()
            )
        )
        return int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else None
    except Exception as e:
        print(f"[StrategyRunner] Failed to create run record: {e}")
        return None


def _has_running_run(conn, strategy_id: int) -> bool:
    try:
        row = conn.execute(
            "SELECT id FROM paper_strategy_runs WHERE strategy_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
            (strategy_id,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _update_run_progress(conn, run_id: Optional[int], details: str, action_count: int) -> None:
    if not run_id:
        return
    try:
        conn.execute(
            "UPDATE paper_strategy_runs SET details = ?, action_count = ? WHERE id = ?",
            (details, action_count, run_id)
        )
    except Exception as e:
        print(f"[StrategyRunner] Failed to update progress: {e}")


def _finish_run_record(conn, run_id: Optional[int], status: str, action_count: int, details: str, error: str = "") -> None:
    if not run_id:
        return
    try:
        conn.execute(
            "UPDATE paper_strategy_runs SET status = ?, action_count = ?, details = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, action_count, details, error, datetime.now(), run_id)
        )
    except Exception as e:
        print(f"[StrategyRunner] Failed to update run record: {e}")
        # Try once more with a fresh connection
        try:
            conn2 = get_connection()
            conn2.execute(
                "UPDATE paper_strategy_runs SET status = ?, action_count = ?, details = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, action_count, details, error, datetime.now(), run_id)
            )
            conn2.close()
        except Exception as e2:
            print(f"[StrategyRunner] Failed to update run record with new connection: {e2}")


def _execute_strategy_run(strategy: Dict[str, Any], symbols: List[str], run_id: Optional[int], update_last_run: bool = True) -> Dict[str, Any]:
    conn = get_connection()
    results: List[Dict[str, Any]] = []
    total_actions = 0
    try:
        if not symbols:
            _finish_run_record(conn, run_id, "skipped", 0, "[]", "no_symbols")
            if update_last_run:
                conn.execute("UPDATE paper_strategies SET last_run_at = ? WHERE id = ?", (datetime.now(), strategy["id"]))
            return {"run_id": run_id, "status": "skipped", "action_count": 0}

        positions_map = _get_positions_map(conn, strategy["id"])
        cash_balance = _get_cash_balance(conn, strategy["id"])
        initial_capital = _get_initial_capital(conn, strategy["id"])
        positions_value = _estimate_positions_value(positions_map)
        total_equity = cash_balance + positions_value
        if positions_map:
            held_symbols = [s for s in symbols if positions_map.get(s.upper(), {}).get("quantity", 0) > 0]
            other_symbols = [s for s in symbols if s not in held_symbols]
            symbols = held_symbols + other_symbols
        for symbol in symbols:
            action_count, error, response_text, cash_balance = _run_strategy_for_symbol(
                strategy,
                symbol,
                positions_map,
                cash_balance,
                initial_capital,
                total_equity
            )
            total_actions += action_count
            results.append({
                "symbol": symbol,
                "action_count": action_count,
                "error": error,
                "response": response_text
            })
            _update_run_progress(conn, run_id, json.dumps(results, ensure_ascii=False), total_actions)

        status = "success"
        error_msg = ""
        if any(r.get("error") for r in results):
            status = "partial"
            error_msg = "; ".join([r["error"] for r in results if r.get("error")])
        details = json.dumps(results, ensure_ascii=False)
        _finish_run_record(conn, run_id, status, total_actions, details, error_msg)
        if update_last_run:
            conn.execute("UPDATE paper_strategies SET last_run_at = ? WHERE id = ?", (datetime.now(), strategy["id"]))
        return {"run_id": run_id, "status": status, "action_count": total_actions}
    except Exception as e:
        error_msg = f"exception: {e}"
        try:
            details = json.dumps(results, ensure_ascii=False)
        except Exception:
            details = "[]"
        _finish_run_record(conn, run_id, "failed", total_actions, details, error_msg)
        if update_last_run:
            try:
                conn.execute("UPDATE paper_strategies SET last_run_at = ? WHERE id = ?", (datetime.now(), strategy["id"]))
            except Exception:
                pass
        return {"run_id": run_id, "status": "failed", "action_count": total_actions}
    finally:
        conn.close()


def _execute_strategy(conn, strategy: Dict[str, Any], update_last_run: bool = True) -> Dict[str, Any]:
    if _has_running_run(conn, strategy["id"]):
        return {"status": "running", "action_count": 0}
    now = datetime.now()
    symbols = _fetch_symbols_for_strategy(conn, strategy)
    run_id = _create_run_record(conn, strategy, symbols)
    return _execute_strategy_run(strategy, symbols, run_id, update_last_run=update_last_run)


def run_pending_strategies() -> None:
    conn = get_connection()
    try:
        # Clean up stale running runs to avoid stuck "running" state
        try:
            cutoff = datetime.now() - timedelta(minutes=30)
            conn.execute(
                "UPDATE paper_strategy_runs SET status = 'failed', error = 'stale_running', finished_at = ? "
                "WHERE status = 'running' AND started_at < ?",
                (datetime.now(), cutoff)
            )
        except Exception as e:
            print(f"[StrategyRunner] Failed to cleanup stale runs: {e}")
        if not should_auto_run():
            return
        rows = conn.execute(
            "SELECT id, prompt, model_id, run_interval_minutes, universe_type, universe_symbols, last_run_at, params_json, constraints_json "
            "FROM paper_strategies WHERE is_ai = TRUE AND auto_run_enabled = TRUE"
        ).fetchall()
        now = datetime.now()
        for r in rows:
            strategy = {
                "id": r[0],
                "prompt": r[1] or "",
                "model_id": r[2],
                "run_interval_minutes": r[3] or 1440,
                "universe_type": r[4],
                "universe_symbols": r[5] or "",
                "last_run_at": r[6],
                "params": json.loads(r[7]) if r[7] else None,
                "constraints": json.loads(r[8]) if r[8] else None
            }
            last_run = strategy["last_run_at"]
            interval = int(strategy["run_interval_minutes"] or 1440)
            if last_run and isinstance(last_run, datetime):
                elapsed = now - last_run
                if elapsed < timedelta(minutes=interval):
                    continue

            if _has_running_run(conn, strategy["id"]):
                continue
            _execute_strategy(conn, strategy, update_last_run=True)
    except Exception as e:
        print(f"[StrategyRunner] Error: {e}")
    finally:
        conn.close()


def run_strategy_now(strategy_id: int) -> Dict[str, Any]:
    conn = get_connection()
    try:
        if _has_running_run(conn, strategy_id):
            return {"error": "strategy_running"}
        row = conn.execute(
            "SELECT id, prompt, model_id, run_interval_minutes, universe_type, universe_symbols, last_run_at, params_json, constraints_json "
            "FROM paper_strategies WHERE id = ?",
            (strategy_id,)
        ).fetchone()
        if not row:
            return {"error": "strategy_not_found"}
        strategy = {
            "id": row[0],
            "prompt": row[1] or "",
            "model_id": row[2],
            "run_interval_minutes": row[3] or 1440,
            "universe_type": row[4],
            "universe_symbols": row[5] or "",
            "last_run_at": row[6],
            "params": json.loads(row[7]) if row[7] else None,
            "constraints": json.loads(row[8]) if row[8] else None
        }
        symbols = _fetch_symbols_for_strategy(conn, strategy)
        run_id = _create_run_record(conn, strategy, symbols)
        if not symbols:
            _finish_run_record(conn, run_id, "skipped", 0, "[]", "no_symbols")
            conn.execute("UPDATE paper_strategies SET last_run_at = ? WHERE id = ?", (datetime.now(), strategy["id"]))
            return {"run_id": run_id, "status": "skipped", "action_count": 0}

        thread = threading.Thread(
            target=_execute_strategy_run,
            args=(strategy, symbols, run_id, True),
            daemon=True
        )
        thread.start()
        return {"run_id": run_id, "status": "running", "action_count": 0}
    finally:
        conn.close()


def _scheduler_loop() -> None:
    while True:
        try:
            run_pending_strategies()
        except Exception as e:
            print(f"[StrategyRunner] Loop error: {e}")
        time.sleep(60)


def start_strategy_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
