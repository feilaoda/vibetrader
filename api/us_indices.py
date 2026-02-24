from typing import Optional

US_INDEX_DEFS = [
    {"symbol": "SPX.US", "code": "SPX", "name": "标普500", "ticker": "^GSPC"},
    {"symbol": "IXIC.US", "code": "IXIC", "name": "纳指综合", "ticker": "^IXIC"},
    {"symbol": "NDX.US", "code": "NDX", "name": "纳指100", "ticker": "^NDX"},
]

US_INDEX_SYMBOLS = [
    {"symbol": item["symbol"], "code": item["code"], "name": item["name"]}
    for item in US_INDEX_DEFS
]

_TICKER_MAP = {}
_NAME_MAP = {}
for item in US_INDEX_DEFS:
    _TICKER_MAP[item["code"].upper()] = item["ticker"]
    _TICKER_MAP[item["symbol"].upper()] = item["ticker"]
    _TICKER_MAP[item["ticker"].upper()] = item["ticker"]
    _NAME_MAP[item["ticker"].upper()] = item["name"]

_ALIAS_MAP = {
    "SP500": "^GSPC",
    "GSPC": "^GSPC",
    "S&P500": "^GSPC",
    "SPX": "^GSPC",
    "NASDAQ": "^IXIC",
    "IXIC": "^IXIC",
    "NDX": "^NDX",
}
for key, val in _ALIAS_MAP.items():
    _TICKER_MAP[key] = val


def is_us_index_symbol(symbol: str) -> bool:
    if not symbol:
        return False
    sym = symbol.strip().upper()
    if not sym:
        return False
    if sym.startswith("^"):
        return True
    if sym.endswith(".IDX"):
        return True
    if sym in _TICKER_MAP:
        return True
    base = sym.split(".", 1)[0]
    if sym.endswith(".US"):
        return base in _TICKER_MAP
    return base in _TICKER_MAP and "." not in sym


def resolve_us_index_ticker(symbol: str) -> Optional[str]:
    if not symbol:
        return None
    sym = symbol.strip().upper()
    if not sym:
        return None
    if sym.startswith("^"):
        return sym
    if sym in _TICKER_MAP:
        return _TICKER_MAP.get(sym)
    base = sym.split(".", 1)[0]
    return _TICKER_MAP.get(base)


def get_us_index_label(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    key = ticker.strip().upper()
    return _NAME_MAP.get(key)
