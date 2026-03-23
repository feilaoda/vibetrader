from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
from db import get_connection
from watchlist import load_watchlist
from akshare_guard import should_skip_remote, record_failure, record_success
from cache import is_etf
from trading_time import now_cn
from data_sources import DataType
from data_sources.router import fetch as router_fetch

router = APIRouter()


class FundamentalsSync(BaseModel):
    symbols: Optional[List[str]] = None
    force: Optional[bool] = False


def _safe_float(value) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in ("", "--", "None", "nan"):
                return 0.0
            return float(text)
        return float(value)
    except Exception:
        return 0.0


def _pick_column(df, candidates: List[str]) -> Optional[str]:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper()


def _symbol_to_code(symbol: str) -> str:
    return symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")


def _get_watchlist_symbols() -> List[str]:
    items = load_watchlist()
    symbols = []
    for item in items:
        if item.get("market") != "ashare":
            continue
        sym = item.get("symbol")
        if sym:
            symbols.append(sym.upper())
    return list(dict.fromkeys(symbols))


def _sync_fundamentals(symbols: List[str], force: bool = False) -> Dict[str, Any]:
    if should_skip_remote(force_remote=force, scope="fundamentals"):
        raise HTTPException(status_code=429, detail="fundamentals_backoff")

    stock_codes = []
    etf_codes = []
    for sym in symbols:
        code = _symbol_to_code(sym)
        if is_etf(code):
            etf_codes.append(code)
        else:
            stock_codes.append(code)

    data, channel = router_fetch(DataType.FUNDAMENTALS, channels=["akshare"], symbols=symbols)
    if not data:
        today = now_cn().strftime("%Y-%m-%d")
        record_failure("fundamentals_fetch_failed", scope="fundamentals")
        return {"date": today, "count": 0, "note": "no_data"}
    stock_df = data.get("stocks")
    etf_df = data.get("etfs")
    record_success(scope="fundamentals")

    today = now_cn().strftime("%Y-%m-%d")
    conn = get_connection()
    try:
        inserted = 0
        for sym in symbols:
            code = _symbol_to_code(sym)
            row = None
            source = "eastmoney"
            if stock_df is not None and code in stock_codes:
                row = stock_df[stock_df["代码"] == code]
            elif etf_df is not None and code in etf_codes:
                row = etf_df[etf_df["代码"] == code]

            if row is None or row.empty:
                continue

            data = row.iloc[0].to_dict()
            pe_col = _pick_column(row, ["市盈率-动态", "市盈率", "PE", "pe"])
            pb_col = _pick_column(row, ["市净率", "PB", "pb"])
            mcap_col = _pick_column(row, ["总市值", "总市值(元)", "总市值(亿元)"])
            fcap_col = _pick_column(row, ["流通市值", "流通市值(元)", "流通市值(亿元)"])

            market_cap = _safe_float(data.get(mcap_col)) if mcap_col else 0.0
            float_market_cap = _safe_float(data.get(fcap_col)) if fcap_col else 0.0
            pe_ttm = _safe_float(data.get(pe_col)) if pe_col else 0.0
            pb = _safe_float(data.get(pb_col)) if pb_col else 0.0

            conn.execute(
                "DELETE FROM symbol_fundamentals_daily WHERE symbol = ? AND date = ?",
                (sym, today)
            )
            conn.execute(
                "INSERT INTO symbol_fundamentals_daily (symbol, date, market_cap, float_market_cap, pe_ttm, pb, source, metrics_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sym,
                    today,
                    market_cap,
                    float_market_cap,
                    pe_ttm,
                    pb,
                    source,
                    json.dumps(data, ensure_ascii=False),
                    datetime.now()
                )
            )
            inserted += 1

        return {"date": today, "count": inserted}
    finally:
        conn.close()


@router.get("/fundamentals/{symbol}")
def get_fundamentals(symbol: str, date: Optional[str] = Query(None)):
    sym = _normalize_symbol(symbol)
    conn = get_connection()
    try:
        if date:
            row = conn.execute(
                "SELECT symbol, date, market_cap, float_market_cap, pe_ttm, pb, source, metrics_json, updated_at FROM symbol_fundamentals_daily WHERE symbol = ? AND date = ?",
                (sym, date)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT symbol, date, market_cap, float_market_cap, pe_ttm, pb, source, metrics_json, updated_at FROM symbol_fundamentals_daily WHERE symbol = ? ORDER BY date DESC LIMIT 1",
                (sym,)
            ).fetchone()
        if not row:
            return {"data": None}
        return {
            "data": {
                "symbol": row[0],
                "date": row[1],
                "market_cap": row[2],
                "float_market_cap": row[3],
                "pe_ttm": row[4],
                "pb": row[5],
                "source": row[6],
                "metrics": json.loads(row[7]) if row[7] else {},
                "updated_at": row[8]
            }
        }
    finally:
        conn.close()


@router.post("/fundamentals/sync")
def sync_fundamentals(payload: FundamentalsSync):
    symbols = payload.symbols or []
    if not symbols:
        symbols = _get_watchlist_symbols()
    if not symbols:
        return {"date": now_cn().strftime("%Y-%m-%d"), "count": 0}
    result = _sync_fundamentals(symbols, force=bool(payload.force))
    return {"success": True, "data": result}
