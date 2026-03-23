from typing import Optional, Any, Dict, List

from .types import DataType
from yahoo import fetch_chart, fetch_quote


def supports(data_type: DataType) -> bool:
    return data_type in {DataType.KLINE_DAILY, DataType.REALTIME}


def fetch(data_type: DataType, **kwargs) -> Optional[Any]:
    if data_type == DataType.KLINE_DAILY:
        ticker = kwargs.get("ticker") or kwargs.get("symbol") or ""
        start_date = kwargs.get("start")
        end_date = kwargs.get("end")
        interval = kwargs.get("interval") or "1d"
        return fetch_chart(ticker, start_date, end_date, interval=interval)
    if data_type == DataType.REALTIME:
        ticker = kwargs.get("ticker") or kwargs.get("symbol") or ""
        quote = fetch_quote(ticker)
        if not quote:
            return None
        ts = quote.get("time")
        try:
            ts = int(float(ts) * 1000) if ts else None
        except Exception:
            ts = None
        return {
            "symbol": kwargs.get("symbol") or ticker,
            "name": quote.get("name") or ticker,
            "price": quote.get("price") or 0,
            "change": quote.get("change") or 0,
            "changePercent": quote.get("changePercent") or 0,
            "open": quote.get("open") or 0,
            "high": quote.get("high") or 0,
            "low": quote.get("low") or 0,
            "volume": quote.get("volume") or 0,
            "amount": 0,
            "timestamp": ts,
            "source": "yahoo",
            "stale": False,
        }
    return None
