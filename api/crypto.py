import os
import json
import urllib.request
import urllib.parse
import urllib.error
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

BINANCE_URLS = [
    os.getenv("BINANCE_BASE_URL", "").strip(),
    "https://api.binance.com/api/v3",
    "https://api.binance.us/api/v3",
]
BINANCE_URLS = [u for u in BINANCE_URLS if u]


def _fetch_json(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _try_binance(path: str, params: dict | None = None):
    last_err = None
    query = urllib.parse.urlencode(params or {})
    suffix = f"{path}?{query}" if query else path
    for base in BINANCE_URLS:
        url = f"{base}{suffix}"
        try:
            data = _fetch_json(url)
            return data, base
        except urllib.error.HTTPError as e:
            last_err = f"{e.code} {e.reason}"
            # 451 or other errors -> try next base
        except Exception as e:
            last_err = str(e)
    raise HTTPException(status_code=502, detail=f"Binance proxy failed: {last_err}")


@router.get("/crypto/ping")
def crypto_ping():
    data, base = _try_binance("/ping")
    return {"success": True, "base_url": base, "data": data}


@router.get("/crypto/exchangeInfo")
def crypto_exchange_info():
    data, base = _try_binance("/exchangeInfo")
    return data


@router.get("/crypto/klines")
def crypto_klines(
    symbol: str = Query(...),
    interval: str = Query(...),
    startTime: int | None = Query(None),
    endTime: int | None = Query(None),
    limit: int | None = Query(None),
):
    params = {
        "symbol": symbol,
        "interval": interval,
    }
    if startTime is not None:
        params["startTime"] = int(startTime)
    if endTime is not None:
        params["endTime"] = int(endTime)
    if limit is not None:
        params["limit"] = int(limit)
    data, base = _try_binance("/klines", params=params)
    return data
