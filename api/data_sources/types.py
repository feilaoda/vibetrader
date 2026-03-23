from enum import Enum


class DataType(str, Enum):
    KLINE_DAILY = "kline_daily"
    KLINE_MINUTE = "kline_minute"
    REALTIME = "realtime"
    INDEX_SPOT = "index_spot"
    SYMBOLS = "symbols"
    FUNDAMENTALS = "fundamentals"
    MARKET_BREADTH = "market_breadth"
    INDUSTRY = "industry"
