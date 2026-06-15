"""
自选股管理模块 (服务端)
保存到 DB (并兼容旧 watchlist.json 迁移)
"""

import json
import urllib.request
from pathlib import Path
from typing import List, Dict, Optional
from pydantic import BaseModel
from db import get_connection
from cache import is_cn_index_symbol
from symbols import CN_INDEX_SYMBOLS
from us_indices import is_us_index_symbol, get_us_index_label, resolve_us_index_ticker

WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"

class WatchlistItem(BaseModel):
    symbol: str
    market: str  # 'ashare' | 'crypto' | 'us'
    name: Optional[str] = None
    addedAt: int

def load_watchlist() -> List[Dict]:
    """加载自选股列表"""
    items = _load_watchlist_db()
    if items:
        return items
    # Migration fallback from legacy file
    legacy = _load_watchlist_file()
    if legacy:
        try:
            _save_watchlist_db(legacy)
            return _load_watchlist_db()
        except Exception:
            return legacy
    return []

def save_watchlist(items: List[Dict]):
    """保存自选股列表"""
    _save_watchlist_db(items)

def add_to_watchlist(item: Dict) -> List[Dict]:
    """添加"""
    symbol = (item.get("symbol") or "").upper()
    market = (item.get("market") or "ashare").lower()
    added_at = int(item.get("addedAt") or 0)
    if added_at <= 0:
        import time
        added_at = int(time.time() * 1000)
    name = item.get("name")

    conn = get_connection()
    try:
        if not name:
            name = _resolve_symbol_name_with_conn(conn, symbol, market)
        exists = conn.execute(
            "SELECT 1 FROM watchlist WHERE symbol = ? AND market = ?",
            (symbol, market)
        ).fetchone()
        if exists:
            if name:
                conn.execute(
                    "UPDATE watchlist SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE symbol = ? AND market = ?",
                    (name, symbol, market)
                )
            return _load_watchlist_db()

        conn.execute("UPDATE watchlist SET sort_order = COALESCE(sort_order, 0) + 1")
        conn.execute(
            "INSERT INTO watchlist (symbol, market, name, added_at, sort_order, updated_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (symbol, market, name, added_at, 0)
        )
    finally:
        conn.close()
    return _load_watchlist_db()

def remove_from_watchlist(symbol: str, market: str) -> List[Dict]:
    """移除"""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM watchlist WHERE symbol = ? AND market = ?",
            (symbol.upper(), market.lower())
        )
    finally:
        conn.close()
    return _load_watchlist_db()

def sync_watchlist(items: List[Dict]) -> List[Dict]:
    """同步完整自选股列表 (支持排序)"""
    _save_watchlist_db(items)
    return _load_watchlist_db()


def _load_watchlist_file() -> List[Dict]:
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[Watchlist] Error loading legacy file: {e}")
    return []


def _load_watchlist_db() -> List[Dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, market, name, added_at, sort_order FROM watchlist ORDER BY sort_order ASC, added_at DESC"
        ).fetchall()
        items: List[Dict] = []
        updates = []
        for row in rows or []:
            name = row[2]
            if not name:
                name = _resolve_symbol_name_with_conn(conn, row[0], row[1])
                if name:
                    updates.append((name, row[0], row[1]))
            items.append({
                "symbol": row[0],
                "market": row[1],
                "name": name,
                "addedAt": int(row[3] or 0),
            })
        if updates:
            conn.executemany(
                "UPDATE watchlist SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE symbol = ? AND market = ?",
                updates
            )
        return items
    except Exception as e:
        print(f"[Watchlist] Error loading db: {e}")
        return []
    finally:
        conn.close()


def _save_watchlist_db(items: List[Dict]) -> None:
    conn = get_connection()
    try:
        conn.execute("START TRANSACTION")
        conn.execute("DELETE FROM watchlist")
        rows = []
        for idx, item in enumerate(items or []):
            symbol = (item.get("symbol") or "").upper()
            market = (item.get("market") or "ashare").lower()
            if not symbol:
                continue
            name = item.get("name") or _resolve_symbol_name_with_conn(conn, symbol, market)
            added_at = int(item.get("addedAt") or 0)
            if added_at <= 0:
                import time
                added_at = int(time.time() * 1000)
            rows.append((symbol, market, name, added_at, idx))
        if rows:
            conn.executemany(
                "INSERT INTO watchlist (symbol, market, name, added_at, sort_order, updated_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                rows
            )
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"[Watchlist] Error saving db: {e}")
        raise
    finally:
        conn.close()


def _resolve_symbol_name_with_conn(conn, symbol: str, market: str) -> Optional[str]:
    if not symbol:
        return None
    sym = symbol.upper()
    if is_cn_index_symbol(sym):
        for item in CN_INDEX_SYMBOLS:
            if item["symbol"] == sym:
                return item["name"]
    if market in ("ashare", "us") and is_us_index_symbol(symbol):
        ticker = resolve_us_index_ticker(symbol) or symbol
        return get_us_index_label(ticker) or symbol
    try:
        row = conn.execute("SELECT name FROM symbols WHERE symbol = ? LIMIT 1", (symbol,)).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    if market != "ashare":
        return None
    return _fetch_tencent_name(symbol)


def _fetch_tencent_name(symbol: str) -> Optional[str]:
    code = symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")
    if not code:
        return None
    if code.startswith(("6", "9", "5")):
        prefix = "sh"
    elif code.startswith(("0", "2", "3", "1")):
        prefix = "sz"
    elif code.startswith(("8", "4")):
        prefix = "bj"
    else:
        prefix = "sh"
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
        try:
            text = raw.decode("gbk", errors="ignore")
        except Exception:
            text = raw.decode("utf-8", errors="ignore")
        parts = text.split("~")
        if len(parts) > 1:
            name = parts[1].strip()
            return name or None
    except Exception:
        return None
    return None
