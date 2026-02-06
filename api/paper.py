from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Set
from datetime import datetime, date, time as dtime
from pathlib import Path
import json
from db import get_connection
from strategy_defaults import (
    normalize_objectives,
    normalize_constraints,
    normalize_params,
    normalize_optimization,
    DEFAULT_OBJECTIVES,
    DEFAULT_CONSTRAINTS,
    DEFAULT_PARAMS,
    DEFAULT_OPTIMIZATION
)
from akshare_guard import should_skip_remote, record_failure, record_success, throttle
from strategy_universe import fetch_symbols_for_strategy
from strategy_optimizer import optimize_strategy
import akshare as ak


router = APIRouter()

SPOT_CACHE_PATH = Path(__file__).parent / "__datacache__" / "spot_cache.json"
SPOT_CACHE_TTL_SECONDS = 60
TRADING_HOURS = [
    (dtime(9, 30), dtime(11, 30)),
    (dtime(13, 0), dtime(15, 0)),
]

_spot_cache: Optional[Dict] = None

class OrderCreate(BaseModel):
    symbol: str
    side: str # BUY or SELL
    price: float
    quantity: int
    fee: float
    strategy_id: Optional[int] = None

class Position(BaseModel):
    symbol: str
    quantity: int
    avg_cost: float
    name: Optional[str] = None
    current_price: float = 0
    market_value: float = 0
    profit: float = 0
    profit_percent: float = 0

class Order(BaseModel):
    id: int
    symbol: str
    side: str
    price: float
    quantity: int
    fee: float
    created_at: datetime


class StrategyCreate(BaseModel):
    name: str
    type: str
    prompt: Optional[str] = ""
    is_ai: bool = False
    model_id: Optional[str] = None
    run_interval_minutes: Optional[int] = 1440
    auto_run_enabled: Optional[bool] = False
    universe_type: Optional[str] = None
    universe_symbols: Optional[str] = ""
    initial_capital: Optional[float] = 100000
    objectives: Optional[Dict] = None
    constraints: Optional[Dict] = None
    params: Optional[Dict] = None
    optimization: Optional[Dict] = None


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    model_id: Optional[str] = None
    run_interval_minutes: Optional[int] = None
    auto_run_enabled: Optional[bool] = None
    universe_type: Optional[str] = None
    universe_symbols: Optional[str] = None
    initial_capital: Optional[float] = None
    objectives: Optional[Dict] = None
    constraints: Optional[Dict] = None
    params: Optional[Dict] = None
    optimization: Optional[Dict] = None


def format_symbol(symbol: str) -> str:
    return symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")


def normalize_symbol(symbol: str) -> str:
    text = format_symbol(symbol)
    if not text:
        return symbol.upper()
    if text.startswith(("6", "9", "5")):
        suffix = ".SH"
    elif text.startswith(("0", "2", "3", "1")):
        suffix = ".SZ"
    else:
        suffix = ".BJ"
    return f"{text}{suffix}"


def safe_float(value) -> float:
    try:
        if value is None:
            return 0
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in ("", "--", "None", "nan"):
                return 0
            return float(text)
        num = float(value)
        if num != num or num == float("inf") or num == float("-inf"):
            return 0
        return num
    except Exception:
        return 0


def _serialize_ts(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds).isoformat()
        except Exception:
            return None
    return str(value)


def _ensure_spot_cache_dir() -> None:
    try:
        SPOT_CACHE_PATH.parent.mkdir(exist_ok=True)
    except Exception:
        pass


def _load_spot_cache() -> Dict:
    global _spot_cache
    if _spot_cache is not None:
        return _spot_cache
    _spot_cache = {
        "last_update_ts": 0,
        "last_update_date": "",
        "prices": {},
        "names": {}
    }
    _ensure_spot_cache_dir()
    if SPOT_CACHE_PATH.exists():
        try:
            with open(SPOT_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _spot_cache.update(data)
        except Exception:
            pass
    return _spot_cache


def _save_spot_cache(cache: Dict) -> None:
    _ensure_spot_cache_dir()
    try:
        with open(SPOT_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def _is_trading_time() -> bool:
    try:
        from trading_time import is_trading_time as _is_trading_time_cn
        return _is_trading_time_cn()
    except Exception:
        now = datetime.now().time()
        for start, end in TRADING_HOURS:
            if start <= now <= end:
                return True
        return False


def _filter_spot_cache(cache: Dict, codes: Set[str]) -> tuple[Dict[str, float], Dict[str, str]]:
    price_by_code: Dict[str, float] = {}
    name_by_code: Dict[str, str] = {}
    if not cache or not codes:
        return price_by_code, name_by_code
    prices = cache.get("prices", {})
    names = cache.get("names", {})
    for code in codes:
        price = safe_float(prices.get(code))
        if price > 0:
            price_by_code[code] = price
        name = names.get(code)
        if name:
            name_by_code[code] = name
    return price_by_code, name_by_code


def _get_manual_strategy_id(conn) -> int:
    row = conn.execute("SELECT id FROM paper_strategies WHERE type = 'manual'").fetchone()
    if row:
        return int(row[0])
    # Fallback create
    conn.execute(
        "INSERT INTO paper_strategies (name, type, prompt, is_ai, is_builtin) VALUES (?, ?, ?, ?, ?)",
        ("手工策略", "manual", "", False, True)
    )
    row = conn.execute("SELECT id FROM paper_strategies WHERE type = 'manual'").fetchone()
    return int(row[0]) if row else 1


def _get_initial_capital(conn, strategy_id: int) -> float:
    row = conn.execute("SELECT initial_capital FROM paper_strategies WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        return 100000.0
    value = row[0]
    try:
        return float(value) if value is not None else 100000.0
    except Exception:
        return 100000.0


def _get_cash_balance(conn, strategy_id: int) -> float:
    initial = _get_initial_capital(conn, strategy_id)
    rows = conn.execute(
        "SELECT side, price, quantity, fee FROM paper_orders WHERE strategy_id = ?",
        (strategy_id,)
    ).fetchall()
    cash = float(initial)
    for side, price, qty, fee in rows:
        amount = (price or 0) * (qty or 0)
        fee_val = fee or 0
        if str(side).upper() == "BUY":
            cash -= amount + fee_val
        elif str(side).upper() == "SELL":
            cash += amount - fee_val
    return cash

def get_name_map(conn, codes: Set[str]) -> Dict[str, str]:
    if not codes:
        return {}
    placeholders = ",".join(["?"] * len(codes))
    rows = conn.execute(
        f"SELECT code, name FROM symbols WHERE code IN ({placeholders})",
        list(codes)
    ).fetchall()
    return {str(code): name for code, name in rows if name}


def get_spot_maps(codes: Set[str]) -> tuple[Dict[str, float], Dict[str, str]]:
    price_by_code: Dict[str, float] = {}
    name_by_code: Dict[str, str] = {}
    if not codes:
        return price_by_code, name_by_code
    cache = _load_spot_cache()
    cached_prices, cached_names = _filter_spot_cache(cache, codes)

    if not _is_trading_time():
        return cached_prices, cached_names

    last_ts = cache.get("last_update_ts") or 0
    if last_ts and (datetime.now().timestamp() - last_ts) < SPOT_CACHE_TTL_SECONDS:
        return cached_prices, cached_names

    if should_skip_remote():
        print("[Backoff] Skip spot data fetch")
        return cached_prices, cached_names

    has_data = False
    try:
        throttle(scope="akshare_spot")
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            subset = df[df["代码"].isin(codes)]
            for _, row in subset.iterrows():
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue
                price_by_code[code] = safe_float(row.get("最新价"))
                name = row.get("名称")
                if name:
                    name_by_code[code] = str(name)
            if not subset.empty:
                has_data = True
    except Exception as e:
        print(f"[paper] Error fetching stock spot: {e}")
        record_failure(f"spot_stock_failed: {e}")

    try:
        throttle(scope="akshare_spot")
        df = ak.fund_etf_spot_em()
        if df is not None and not df.empty:
            subset = df[df["代码"].isin(codes)]
            for _, row in subset.iterrows():
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue
                price_by_code[code] = safe_float(row.get("最新价"))
                name = row.get("名称")
                if name:
                    name_by_code[code] = str(name)
            if not subset.empty:
                has_data = True
    except Exception as e:
        print(f"[paper] Error fetching ETF spot: {e}")
        record_failure(f"spot_etf_failed: {e}")

    if has_data:
        cache_prices = cache.get("prices", {})
        cache_names = cache.get("names", {})
        cache_prices.update(price_by_code)
        cache_names.update(name_by_code)
        cache["prices"] = cache_prices
        cache["names"] = cache_names
        cache["last_update_ts"] = datetime.now().timestamp()
        cache["last_update_date"] = date.today().strftime("%Y%m%d")
        _save_spot_cache(cache)
        record_success()

    merged_prices = {**cached_prices, **price_by_code}
    merged_names = {**cached_names, **name_by_code}
    return merged_prices, merged_names


def get_last_close(symbol: str) -> float:
    try:
        from cache import get_klines_with_cache
        klines, _ = get_klines_with_cache(symbol, period="daily", limit=1)
        if klines:
            return safe_float(klines[-1].get("close"))
    except Exception as e:
        print(f"[paper] Error fetching cached klines for {symbol}: {e}")
    return 0

# Helper to get current price (reuse logic from main.py or call internal function)
# For simplicity, we'll implement a basic fetcher here or use the one from main via import? 
# Circular imports might be an issue. Let's redefine a simple fetcher or import specific logic.
def get_current_price(symbol: str) -> float:
    try:
        if should_skip_remote():
            return 0
        code = format_symbol(symbol)
        is_etf = code.startswith(("15", "16", "5"))
        if is_etf:
            throttle(scope="akshare_spot")
            df = ak.fund_etf_spot_em()
            row = df[df["代码"] == code]
            return safe_float(row.iloc[0]["最新价"]) if not row.empty else 0
        else:
            throttle(scope="akshare_spot")
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == code]
            return safe_float(row.iloc[0]["最新价"]) if not row.empty else 0
    except:
        return 0


@router.get("/paper/strategies")
def list_strategies():
    conn = get_connection()
    try:
        _get_manual_strategy_id(conn)
        rows = conn.execute(
            "SELECT s.id, s.name, s.type, s.prompt, s.is_ai, s.is_builtin, s.model_id, s.run_interval_minutes, "
            "s.universe_type, s.universe_symbols, s.initial_capital, s.auto_run_enabled, s.last_run_at, s.created_at, "
            "s.objectives_json, s.constraints_json, s.params_json, s.optimization_json, "
            "s.last_optimized_at, s.last_optimization_score, s.last_optimization_summary, "
            "CASE WHEN r.cnt > 0 THEN TRUE ELSE FALSE END AS is_running "
            "FROM paper_strategies s "
            "LEFT JOIN (SELECT strategy_id, COUNT(*) AS cnt FROM paper_strategy_runs WHERE status = 'running' GROUP BY strategy_id) r "
            "ON s.id = r.strategy_id "
            "ORDER BY s.id"
        ).fetchall()
        data = []
        for r in rows:
            data.append({
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "prompt": r[3] or "",
                "is_ai": bool(r[4]),
                "is_builtin": bool(r[5]),
                "model_id": r[6],
                "run_interval_minutes": r[7],
                "universe_type": r[8],
                "universe_symbols": r[9] or "",
                "initial_capital": r[10] or 100000,
                "auto_run_enabled": bool(r[11]) if r[11] is not None else False,
                "last_run_at": r[12],
                "created_at": r[13],
                "objectives": normalize_objectives(json.loads(r[14]) if r[14] else None),
                "constraints": normalize_constraints(json.loads(r[15]) if r[15] else None),
                "params": normalize_params(json.loads(r[16]) if r[16] else None),
                "optimization": normalize_optimization(json.loads(r[17]) if r[17] else None),
                "last_optimized_at": r[18],
                "last_optimization_score": r[19],
                "last_optimization_summary": r[20],
                "is_running": bool(r[21]) if r[21] is not None else False
            })
        return {"data": data}
    finally:
        conn.close()


@router.post("/paper/strategies")
def create_strategy(strategy: StrategyCreate):
    conn = get_connection()
    try:
        name = strategy.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Strategy name is required")
        typ = strategy.type.strip() if strategy.type else "custom"
        prompt = strategy.prompt or ""
        is_ai = bool(strategy.is_ai)
        model_id = strategy.model_id
        run_interval = strategy.run_interval_minutes or 1440
        auto_run_enabled = bool(strategy.auto_run_enabled)
        universe_type = strategy.universe_type or ("watchlist" if is_ai else "custom")
        universe_symbols = strategy.universe_symbols or ""
        initial_capital = strategy.initial_capital or 100000
        objectives = normalize_objectives(strategy.objectives)
        constraints = normalize_constraints(strategy.constraints)
        params = normalize_params(strategy.params)
        optimization = normalize_optimization(strategy.optimization)
        res = conn.execute(
            "INSERT INTO paper_strategies (name, type, prompt, is_ai, is_builtin, model_id, run_interval_minutes, auto_run_enabled, universe_type, universe_symbols, initial_capital, objectives_json, constraints_json, params_json, optimization_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                typ,
                prompt,
                is_ai,
                False,
                model_id,
                run_interval,
                auto_run_enabled,
                universe_type,
                universe_symbols,
                initial_capital,
                json.dumps(objectives, ensure_ascii=False),
                json.dumps(constraints, ensure_ascii=False),
                json.dumps(params, ensure_ascii=False),
                json.dumps(optimization, ensure_ascii=False)
            )
        )
        return {"id": int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else None}
    finally:
        conn.close()


@router.put("/paper/strategies/{strategy_id}")
def update_strategy(strategy_id: int, strategy: StrategyUpdate):
    conn = get_connection()
    try:
        updates = []
        params = []
        if strategy.name is not None:
            updates.append("name = ?")
            params.append(strategy.name.strip())
        if strategy.prompt is not None:
            updates.append("prompt = ?")
            params.append(strategy.prompt)
        if strategy.model_id is not None:
            updates.append("model_id = ?")
            params.append(strategy.model_id)
        if strategy.run_interval_minutes is not None:
            updates.append("run_interval_minutes = ?")
            params.append(strategy.run_interval_minutes)
        if strategy.auto_run_enabled is not None:
            updates.append("auto_run_enabled = ?")
            params.append(bool(strategy.auto_run_enabled))
        if strategy.universe_type is not None:
            updates.append("universe_type = ?")
            params.append(strategy.universe_type)
        if strategy.universe_symbols is not None:
            updates.append("universe_symbols = ?")
            params.append(strategy.universe_symbols)
        if strategy.initial_capital is not None:
            updates.append("initial_capital = ?")
            params.append(strategy.initial_capital)
        if strategy.objectives is not None:
            updates.append("objectives_json = ?")
            params.append(json.dumps(normalize_objectives(strategy.objectives), ensure_ascii=False))
        if strategy.constraints is not None:
            updates.append("constraints_json = ?")
            params.append(json.dumps(normalize_constraints(strategy.constraints), ensure_ascii=False))
        if strategy.params is not None:
            updates.append("params_json = ?")
            params.append(json.dumps(normalize_params(strategy.params), ensure_ascii=False))
        if strategy.optimization is not None:
            updates.append("optimization_json = ?")
            params.append(json.dumps(normalize_optimization(strategy.optimization), ensure_ascii=False))
        if updates:
            params.append(strategy_id)
            conn.execute(
                f"UPDATE paper_strategies SET {', '.join(updates)} WHERE id = ?",
                params
            )
        return {"success": True}
    finally:
        conn.close()

@router.get("/paper/strategies/{strategy_id}/runs")
def get_strategy_runs(strategy_id: int, limit: int = Query(30)):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, status, symbols, symbol_count, action_count, model_id, run_interval_minutes, details, error, started_at, finished_at "
            "FROM paper_strategy_runs WHERE strategy_id = ? ORDER BY id DESC LIMIT ?",
            (strategy_id, limit)
        ).fetchall()
        data = []
        for r in rows:
            data.append({
                "id": r[0],
                "status": r[1],
                "symbols": r[2] or "",
                "symbol_count": r[3] or 0,
                "action_count": r[4] or 0,
                "model_id": r[5],
                "run_interval_minutes": r[6],
                "details": r[7],
                "error": r[8],
                "started_at": r[9],
                "finished_at": r[10]
            })
        return {"data": data}
    finally:
        conn.close()

@router.post("/paper/strategies/{strategy_id}/optimize")
def optimize_strategy_now(strategy_id: int):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, prompt, objectives_json, constraints_json, params_json, optimization_json, initial_capital, universe_type, universe_symbols "
            "FROM paper_strategies WHERE id = ?",
            (strategy_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
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
        strategy["symbols"] = fetch_symbols_for_strategy(conn, strategy)
        result = optimize_strategy(conn, strategy)
        return {"success": True, "data": result}
    finally:
        conn.close()


@router.get("/paper/auto_status")
def get_auto_status():
    from trading_time import auto_run_status
    allowed, reason, now_iso = auto_run_status()
    return {
        "auto_run_allowed": allowed,
        "reason": reason,
        "now": now_iso
    }


@router.post("/paper/strategies/{strategy_id}/run")
def run_strategy(strategy_id: int):
    from paper_strategy_runner import run_strategy_now
    result = run_strategy_now(strategy_id)
    if result.get("error"):
        status = 409 if result["error"] == "strategy_running" else 404
        raise HTTPException(status_code=status, detail=result["error"])
    return {"success": True, "data": result}

@router.post("/paper/order")
def place_order(order: OrderCreate):
    conn = get_connection()
    try:
        strategy_id = order.strategy_id or _get_manual_strategy_id(conn)
        order_symbol = normalize_symbol(order.symbol)
        side = order.side.upper()
        if side == 'BUY':
            required = (order.price or 0) * (order.quantity or 0) + (order.fee or 0)
            cash = _get_cash_balance(conn, strategy_id)
            if required > cash:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient cash balance. Available: {cash:.2f}, required: {required:.2f}"
                )
        # 1. Record Order
        conn.execute("""
            INSERT INTO paper_orders (symbol, side, price, quantity, fee, strategy_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (order_symbol, order.side, order.price, order.quantity, order.fee, strategy_id))
        
        # 2. Update Position
        # Check existing
        row = conn.execute(
            "SELECT quantity, avg_cost, symbol FROM paper_positions WHERE strategy_id = ? AND symbol = ?",
            (strategy_id, order_symbol)
        ).fetchone()
        if row is None:
            code = format_symbol(order_symbol)
            row = conn.execute(
                "SELECT quantity, avg_cost, symbol FROM paper_positions WHERE strategy_id = ? AND REPLACE(REPLACE(REPLACE(symbol,'.',''),'SH',''),'SZ','') = ?",
                (strategy_id, code)
            ).fetchone()
        
        if side == 'BUY':
            if row:
                old_qty = row[0]
                old_cost = row[1]
                existing_symbol = row[2]
                new_qty = old_qty + order.quantity
                # Weighted Average: (OldTotal + NewTotal + Fee) / NewQty? 
                # Usually Fee is sunk cost, but user wants "overall holding cost".
                # Standard Avg Cost = (OldQty * OldAvg + NewQty * NewPrice + Fee) / NewQty
                total_cost = (old_qty * old_cost) + (order.quantity * order.price) + order.fee
                new_avg = total_cost / new_qty
                
                conn.execute(
                    "UPDATE paper_positions SET quantity = ?, avg_cost = ? WHERE strategy_id = ? AND symbol = ?",
                    (new_qty, new_avg, strategy_id, existing_symbol)
                )
            else:
                # New Position
                total_cost = (order.quantity * order.price) + order.fee
                avg_cost = total_cost / order.quantity
                conn.execute(
                    "INSERT INTO paper_positions (strategy_id, symbol, quantity, avg_cost) VALUES (?, ?, ?, ?)",
                    (strategy_id, order_symbol, order.quantity, avg_cost)
                )
                             
        elif side == 'SELL':
            if not row:
                raise HTTPException(status_code=400, detail="No position to sell")
            
            old_qty = row[0]
            old_cost = row[1]
            existing_symbol = row[2]
            
            if old_qty < order.quantity:
                raise HTTPException(status_code=400, detail="Insufficient quantity")
            
            new_qty = old_qty - order.quantity
            
            if new_qty == 0:
                conn.execute(
                    "DELETE FROM paper_positions WHERE strategy_id = ? AND symbol = ?",
                    (strategy_id, existing_symbol)
                )
            else:
                # Selling doesn't change Avg Cost of remaining shares in most accounting methods (FIFO/Weighted defaults)
                # It just realizes P/L. Remaining shares keep same avg cost.
                conn.execute(
                    "UPDATE paper_positions SET quantity = ? WHERE strategy_id = ? AND symbol = ?",
                    (new_qty, strategy_id, existing_symbol)
                )
                
        return {"success": True}
    finally:
        conn.close()

@router.get("/paper/positions")
def get_positions(strategy_id: Optional[int] = Query(None)):
    conn = get_connection()
    try:
        sid = strategy_id or _get_manual_strategy_id(conn)
        rows = conn.execute(
            "SELECT symbol, quantity, avg_cost FROM paper_positions WHERE strategy_id = ?",
            (sid,)
        ).fetchall()
        positions = []

        codes = {format_symbol(r[0]) for r in rows}
        name_map = get_name_map(conn, codes)
        price_map, spot_name_map = get_spot_maps(codes)
        for code, name in spot_name_map.items():
            if name:
                name_map[code] = name
        
        # This loop might be slow if many positions, optimize later (batch fetch)
        for r in rows:
            sym, qty, avg = r
            code = format_symbol(sym)
            curr = price_map.get(code, 0)
            if curr <= 0:
                curr = get_last_close(sym)
            if curr <= 0:
                curr = avg
            market_val = qty * curr
            # Profit = (Current - Avg) * Qty
            # Wait, Avg Cost includes buy fees. 
            # Current P/L should probably roughly Estimate sell fee? 
            # Or just Gross P/L? User asked "Profit/Loss and Ratio". 
            # Let's show Gross P/L based on current price.
            # Realized P/L is tracked separately? For now just Floating P/L.
            profit = (curr - avg) * qty
            pct = (profit / (avg * qty)) * 100 if avg > 0 else 0
            
            positions.append({
                "symbol": sym,
                "quantity": qty,
                "avg_cost": avg,
                "name": name_map.get(code),
                "current_price": curr,
                "market_value": market_val,
                "profit": profit,
                "profit_percent": pct
            })
            
        return {"data": positions}
    finally:
        conn.close()

@router.get("/paper/orders")
def get_orders(strategy_id: Optional[int] = Query(None)):
    conn = get_connection()
    try:
        sid = strategy_id or _get_manual_strategy_id(conn)
        rows = conn.execute(
            "SELECT id, symbol, side, price, quantity, fee, created_at FROM paper_orders WHERE strategy_id = ? ORDER BY created_at DESC",
            (sid,)
        ).fetchall()
        codes = {format_symbol(r[1]) for r in rows}
        name_map = get_name_map(conn, codes)
        return {"data": [
            {
                "id": r[0],
                "symbol": r[1],
                "name": name_map.get(format_symbol(r[1])),
                "side": r[2],
                "price": r[3],
                "quantity": r[4],
                "fee": r[5],
                "created_at": _serialize_ts(r[6])
            } for r in rows
        ]}
    finally:
        conn.close()

@router.delete("/paper/reset")
def reset_account(strategy_id: Optional[int] = Query(None)):
    conn = get_connection()
    try:
        if strategy_id:
            conn.execute("DELETE FROM paper_orders WHERE strategy_id = ?", (strategy_id,))
            conn.execute("DELETE FROM paper_positions WHERE strategy_id = ?", (strategy_id,))
        else:
            conn.execute("DELETE FROM paper_orders")
            conn.execute("DELETE FROM paper_positions")
        return {"success": True}
    finally:
        conn.close()
