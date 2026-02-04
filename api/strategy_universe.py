from typing import Any, Dict, List
from watchlist import load_watchlist


def parse_symbol_list(raw_text: str) -> List[str]:
    if not raw_text:
        return []
    parts = [p.strip() for p in raw_text.replace("\n", " ").replace("\t", " ").replace(";", " ").replace(",", " ").split(" ") if p.strip()]
    symbols = []
    for part in parts:
        if part:
            symbols.append(part.upper())
    return list(dict.fromkeys(symbols))


def fetch_symbols_for_strategy(conn, strategy: Dict[str, Any]) -> List[str]:
    universe_type = (strategy.get("universe_type") or "watchlist").lower()
    if universe_type == "watchlist":
        items = load_watchlist()
        symbols = []
        for item in items:
            if item.get("market") != "ashare":
                continue
            symbol = item.get("symbol")
            if symbol:
                symbols.append(symbol.upper())
        symbols = list(dict.fromkeys(symbols))
        selected = parse_symbol_list(strategy.get("universe_symbols") or "")
        if selected:
            selected_set = set(selected)
            return [s for s in symbols if s in selected_set]
        return symbols
    return parse_symbol_list(strategy.get("universe_symbols") or "")

