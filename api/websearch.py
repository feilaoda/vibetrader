import json
import os
from typing import Dict, List, Optional
from urllib.request import Request, urlopen

from config import (
    WEB_SEARCH_API_KEY,
    WEB_SEARCH_ENDPOINT,
    WEB_SEARCH_PROVIDER,
    WEB_SEARCH_TIMEOUT,
)


def _post_json(url: str, payload: Dict, headers: Optional[Dict[str, str]] = None, timeout: int = 8) -> Dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers=headers or {}, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def is_configured(provider: Optional[str] = None) -> bool:
    provider = (provider or WEB_SEARCH_PROVIDER or "").strip().lower()
    if provider in ("custom", "proxy"):
        return bool(WEB_SEARCH_ENDPOINT)
    return bool(WEB_SEARCH_API_KEY)


def search_web(query: str, limit: int = 5, provider: Optional[str] = None) -> List[Dict[str, str]]:
    provider = (provider or WEB_SEARCH_PROVIDER or "serper").strip().lower()
    if not query:
        return []
    if not is_configured(provider):
        return []
    try:
        if provider in ("serper", "google"):
            url = WEB_SEARCH_ENDPOINT or "https://google.serper.dev/search"
            headers = {
                "Content-Type": "application/json",
                "X-API-KEY": WEB_SEARCH_API_KEY,
            }
            payload = {"q": query, "num": int(limit)}
            data = _post_json(url, payload, headers=headers, timeout=WEB_SEARCH_TIMEOUT)
            results = []
            for item in (data.get("organic") or [])[:limit]:
                results.append({
                    "title": item.get("title") or "",
                    "url": item.get("link") or "",
                    "snippet": item.get("snippet") or "",
                    "date": item.get("date") or "",
                    "source": "serper",
                })
            return results
        if provider == "tavily":
            url = WEB_SEARCH_ENDPOINT or "https://api.tavily.com/search"
            headers = {"Content-Type": "application/json"}
            payload = {
                "api_key": WEB_SEARCH_API_KEY,
                "query": query,
                "max_results": int(limit),
                "include_answer": False,
                "include_raw_content": False,
            }
            data = _post_json(url, payload, headers=headers, timeout=WEB_SEARCH_TIMEOUT)
            results = []
            for item in (data.get("results") or [])[:limit]:
                results.append({
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "snippet": item.get("content") or "",
                    "date": item.get("published_date") or "",
                    "source": "tavily",
                })
            return results
        if provider in ("custom", "proxy"):
            url = WEB_SEARCH_ENDPOINT
            if not url:
                return []
            headers = {"Content-Type": "application/json"}
            payload = {"q": query, "limit": int(limit)}
            data = _post_json(url, payload, headers=headers, timeout=WEB_SEARCH_TIMEOUT)
            items = data.get("data") if isinstance(data, dict) else data
            results = []
            for item in (items or [])[:limit]:
                results.append({
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "snippet": item.get("snippet") or item.get("content") or "",
                    "date": item.get("date") or item.get("published_date") or "",
                    "source": "custom",
                })
            return results
    except Exception as e:
        print(f"[WebSearch] error: {e}")
    return []
