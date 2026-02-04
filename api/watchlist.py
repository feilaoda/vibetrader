"""
自选股管理模块 (服务端)
保存到 api/watchlist.json
"""

import json
from pathlib import Path
from typing import List, Dict, Optional
from pydantic import BaseModel

WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"

class WatchlistItem(BaseModel):
    symbol: str
    market: str  # 'ashare' | 'crypto'
    name: Optional[str] = None
    addedAt: int

def load_watchlist() -> List[Dict]:
    """加载自选股列表"""
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Watchlist] Error loading: {e}")
    return []

def save_watchlist(items: List[Dict]):
    """保存自选股列表"""
    try:
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Watchlist] Error saving: {e}")

def add_to_watchlist(item: Dict) -> List[Dict]:
    """添加"""
    items = load_watchlist()
    
    # 查重
    for i in items:
        if i["symbol"] == item["symbol"] and i["market"] == item["market"]:
            return items
            
    items.insert(0, item)  # 添加到开头
    save_watchlist(items)
    return items

def remove_from_watchlist(symbol: str, market: str) -> List[Dict]:
    """移除"""
    items = load_watchlist()
    new_items = [i for i in items if not (i["symbol"] == symbol and i["market"] == market)]
    save_watchlist(new_items)
    return new_items

def sync_watchlist(items: List[Dict]) -> List[Dict]:
    """同步完整自选股列表 (支持排序)"""
    save_watchlist(items)
    return items
