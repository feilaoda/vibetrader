import json
import math
import re
import urllib.request
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

from .types import DataType
from .utils import safe_float, to_timestamp_cn, now_cn, ymd_compact_to_iso, CN_TZ


def _normalize_code(code: str) -> str:
    text = str(code).strip().upper()
    text = text.replace("SH", "").replace("SZ", "").replace("BJ", "").replace(".", "")
    return text


def _get_market_prefix_from_code(code: str) -> str:
    c = _normalize_code(code)
    if c.startswith(("6", "9", "5")):
        return "sh"
    if c.startswith(("0", "2", "3", "1")):
        return "sz"
    if c.startswith(("8", "4")):
        return "bj"
    return "sh"


def _infer_tencent_multipliers(samples: List[Tuple[float, float, float]]) -> Tuple[int, int]:
    """Infer volume/amount multipliers for Tencent data."""
    candidates = [(1, 1), (1, 10000), (100, 1), (100, 10000)]
    best_score = float("inf")
    best = (100, 10000)

    valid_samples = [(v, a, p) for (v, a, p) in samples if v > 0 and a > 0 and p > 0]
    if not valid_samples:
        return best

    for vol_mul, amt_mul in candidates:
        errs = []
        for vol_raw, amt_raw, price in valid_samples:
            try:
                ratio = (amt_raw * amt_mul) / (vol_raw * vol_mul * price)
                if ratio <= 0:
                    continue
                errs.append(abs(math.log10(ratio)))
            except Exception:
                continue
        if not errs:
            continue
        errs.sort()
        score = errs[len(errs) // 2]
        if score < best_score:
            best_score = score
            best = (vol_mul, amt_mul)

    if best_score > 1.5:
        vols = sorted(v for (v, _, _) in valid_samples if v > 0)
        if vols:
            med_vol = vols[len(vols) // 2]
            return (1 if med_vol >= 5e7 else 100, 10000)
    return best


def fetch_kline_daily(code: str, fetch_start: str, fetch_end: str) -> Optional[List[Dict]]:
    code = _normalize_code(code)
    start_iso = ymd_compact_to_iso(fetch_start)
    end_iso = ymd_compact_to_iso(fetch_end)
    if not start_iso or not end_iso:
        return None
    market_prefix = _get_market_prefix_from_code(code)
    key = f"{market_prefix}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={key},day,{start_iso},{end_iso},640,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"[tencent] Error: {e}")
        return None

    node = data.get("data", {}).get(key) or {}
    series = node.get("qfqday") or node.get("day") or []
    if not series:
        return None

    parsed_rows = []
    samples: List[Tuple[float, float, float]] = []
    for row in series:
        if not row or len(row) < 6:
            continue
        date_str = row[0]
        timestamp = to_timestamp_cn(date_str, "%Y-%m-%d")
        open_price = safe_float(row[1])
        close_price = safe_float(row[2])
        high_price = safe_float(row[3])
        low_price = safe_float(row[4])
        volume_raw = safe_float(row[5])
        amount_raw = safe_float(row[6] if len(row) > 6 else 0)
        parsed_rows.append({
            "date": date_str,
            "time": None,
            "openTime": timestamp,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume_raw": volume_raw,
            "amount_raw": amount_raw,
            "closeTime": timestamp + 86400000 - 1,
        })
        if volume_raw > 0 and amount_raw > 0 and close_price > 0:
            samples.append((volume_raw, amount_raw, close_price))
    if not parsed_rows:
        return None

    if samples:
        volume_multiplier, _ = _infer_tencent_multipliers(samples)
    else:
        raw_volumes = sorted(safe_float(item.get("volume_raw")) for item in parsed_rows if safe_float(item.get("volume_raw")) > 0)
        median_raw = raw_volumes[len(raw_volumes) // 2] if raw_volumes else 0
        volume_multiplier = 1 if median_raw >= 5e7 else 100

    klines = []
    for item in parsed_rows:
        volume_raw = safe_float(item.pop("volume_raw", 0))
        item.pop("amount_raw", None)
        item["volume"] = float(volume_raw * volume_multiplier if volume_raw else 0)
        klines.append(item)
    return klines


def fetch_quote(code: str) -> Optional[Dict[str, Any]]:
    code = _normalize_code(code)
    market_prefix = _get_market_prefix_from_code(code)
    url = f"https://qt.gtimg.cn/q={market_prefix}{code}"

    def _do():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
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
    name = parts[1] if len(parts) > 1 else ""
    price = safe_float(parts[3] if len(parts) > 3 else 0)
    prev_close = safe_float(parts[4] if len(parts) > 4 else 0)
    open_p = safe_float(parts[5] if len(parts) > 5 else 0)
    volume_raw = safe_float(parts[6] if len(parts) > 6 else 0)
    amount_raw = safe_float(parts[37] if len(parts) > 37 else 0)
    high = safe_float(parts[33] if len(parts) > 33 else 0)
    low = safe_float(parts[34] if len(parts) > 34 else 0)
    price_ref = price or prev_close or open_p
    if volume_raw > 0 and amount_raw > 0 and price_ref > 0:
        volume_multiplier, amount_multiplier = _infer_tencent_multipliers([(volume_raw, amount_raw, price_ref)])
    else:
        volume_multiplier, amount_multiplier = (1 if volume_raw >= 5e7 else 100, 10000)
    time_str = parts[30] if len(parts) > 30 else ""
    ts = int(now_cn().timestamp() * 1000)
    if time_str and ":" in time_str:
        try:
            dt = datetime.strptime(f"{now_cn().strftime('%Y-%m-%d')} {time_str}", "%Y-%m-%d %H:%M:%S")
            if CN_TZ:
                dt = dt.replace(tzinfo=CN_TZ)
            ts = int(dt.timestamp() * 1000)
        except Exception:
            pass
    return {
        "name": name,
        "price": price,
        "open": open_p,
        "high": high,
        "low": low,
        "volume": volume_raw * volume_multiplier,
        "amount": amount_raw * amount_multiplier,
        "timestamp": ts,
        "prev_close": prev_close,
        "change": safe_float(parts[31] if len(parts) > 31 else (price - prev_close)),
        "changePercent": safe_float(parts[32] if len(parts) > 32 else ((price - prev_close) / prev_close * 100 if prev_close else 0)),
    }


def build_daily_from_quote(code: str, date_ymd: str) -> Optional[Dict]:
    code = _normalize_code(code)
    if not date_ymd:
        return None
    quote = fetch_quote(code)
    if not quote:
        return None
    price = safe_float(quote.get("price"))
    if price <= 0:
        return None
    open_p = safe_float(quote.get("open"))
    high = safe_float(quote.get("high"))
    low = safe_float(quote.get("low"))
    volume = safe_float(quote.get("volume"))
    date_iso = f"{date_ymd[0:4]}-{date_ymd[4:6]}-{date_ymd[6:8]}"
    timestamp = to_timestamp_cn(date_iso, "%Y-%m-%d")
    return {
        "date": date_iso,
        "time": None,
        "openTime": timestamp,
        "open": open_p,
        "high": high,
        "low": low,
        "close": price,
        "volume": volume,
        "closeTime": timestamp + 86400000 - 1,
    }


def supports(data_type: DataType) -> bool:
    return data_type in {
        DataType.KLINE_DAILY,
        DataType.REALTIME,
        DataType.INDEX_SPOT,
    }


def fetch(data_type: DataType, **kwargs) -> Optional[Any]:
    if data_type == DataType.KLINE_DAILY:
        code = kwargs.get("code") or kwargs.get("symbol") or ""
        fetch_start = kwargs.get("start")
        fetch_end = kwargs.get("end")
        if not code or not fetch_start or not fetch_end:
            return None
        return fetch_kline_daily(code, fetch_start, fetch_end)
    if data_type == DataType.REALTIME:
        code = kwargs.get("code") or kwargs.get("symbol") or ""
        if not code:
            return None
        quote = fetch_quote(code)
        if not quote:
            return None
        return {
            "symbol": kwargs.get("symbol"),
            "name": quote.get("name"),
            "price": quote.get("price") or 0,
            "change": quote.get("change") or 0,
            "changePercent": quote.get("changePercent") or 0,
            "open": quote.get("open") or 0,
            "high": quote.get("high") or 0,
            "low": quote.get("low") or 0,
            "volume": quote.get("volume") or 0,
            "amount": quote.get("amount") or 0,
            "timestamp": quote.get("timestamp") or int(now_cn().timestamp() * 1000),
            "source": "tencent",
            "stale": False,
        }
    if data_type == DataType.INDEX_SPOT:
        code = kwargs.get("code") or kwargs.get("symbol") or ""
        if not code:
            return None
        quote = fetch_quote(code)
        if not quote:
            return None
        price = safe_float(quote.get("price"))
        prev_close = safe_float(quote.get("prev_close"))
        pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else None
        ts = quote.get("timestamp") or int(now_cn().timestamp() * 1000)
        try:
            dt = datetime.fromtimestamp(int(ts) / 1000, CN_TZ) if CN_TZ else datetime.fromtimestamp(int(ts) / 1000)
            date_text = dt.strftime("%Y-%m-%d")
        except Exception:
            date_text = now_cn().strftime("%Y-%m-%d")
        return {
            "close": price,
            "change_pct": pct,
            "volume": quote.get("volume") or 0,
            "date": date_text,
            "source": "tencent_spot",
        }
    return None
