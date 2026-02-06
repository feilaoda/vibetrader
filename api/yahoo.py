import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.replace(",", "").strip()
            if not text:
                return default
            return float(text)
        return float(value)
    except Exception:
        return default


def _parse_ymd(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    text = str(date_str).strip()
    if not text:
        return None
    if "-" in text:
        text = text.replace("-", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d")
    except Exception:
        return None


def fetch_quote(ticker: str) -> Optional[Dict[str, Any]]:
    if not ticker:
        return None
    safe = urllib.parse.quote(ticker, safe="")
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={safe}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            return None
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    items = data.get("quoteResponse", {}).get("result", [])
    if not items:
        return None
    q = items[0] or {}
    return {
        "ticker": ticker,
        "price": q.get("regularMarketPrice"),
        "change": q.get("regularMarketChange"),
        "changePercent": q.get("regularMarketChangePercent"),
        "time": q.get("regularMarketTime"),
        "currency": q.get("currency"),
        "source": "yahoo"
    }


def fetch_chart(
    ticker: str,
    start_date: Optional[str],
    end_date: Optional[str],
    interval: str = "1d"
) -> List[Dict[str, Any]]:
    if not ticker:
        return []

    start_dt = _parse_ymd(start_date)
    end_dt = _parse_ymd(end_date)
    if not end_dt:
        end_dt = datetime.utcnow()
    if not start_dt:
        start_dt = end_dt - timedelta(days=365)
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    start_ts = int(start_dt.replace(tzinfo=timezone.utc).timestamp())
    end_ts = int((end_dt + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())

    safe = urllib.parse.quote(ticker, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{safe}"
        f"?period1={start_ts}&period2={end_ts}&interval={interval}"
        f"&includePrePost=false&events=div%2Csplits"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            return []
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return []
    result = result[0] or {}
    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators") or {}).get("quote") or []
    if not quotes:
        return []
    quote = quotes[0] or {}
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    klines: List[Dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        if ts is None:
            continue
        try:
            open_v = opens[idx]
            high_v = highs[idx]
            low_v = lows[idx]
            close_v = closes[idx]
        except Exception:
            continue
        if open_v is None or high_v is None or low_v is None or close_v is None:
            continue
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        open_time = int(float(ts) * 1000)
        if interval == "1wk":
            close_time = open_time + 7 * 24 * 3600 * 1000 - 1
        elif interval == "1mo":
            close_time = open_time + 31 * 24 * 3600 * 1000 - 1
        else:
            close_time = open_time + 24 * 3600 * 1000 - 1
        volume = 0.0
        try:
            volume = _safe_float(volumes[idx], 0.0)
        except Exception:
            volume = 0.0
        klines.append({
            "date": dt.strftime("%Y-%m-%d"),
            "time": None,
            "openTime": open_time,
            "open": _safe_float(open_v),
            "high": _safe_float(high_v),
            "low": _safe_float(low_v),
            "close": _safe_float(close_v),
            "volume": volume,
            "closeTime": close_time,
        })
    return klines
