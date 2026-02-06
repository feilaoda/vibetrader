from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Tuple, Any
from datetime import datetime, timedelta
import json
import os

from db import get_connection
from watchlist import load_watchlist
from cache import is_etf
from yahoo import fetch_quote
from us_indices import resolve_us_index_ticker, get_us_index_label

router = APIRouter()

DEFAULT_INDUSTRY_PROFILES = [
    {
        "profile_id": "etf",
        "name": "ETF",
        "keywords": [],
        "priority": 100,
        "enabled": True,
        "config": {
            "indicators": ["index", "macro", "liquidity"],
            "refresh_minutes": 15
        }
    },
    {
        "profile_id": "cycle",
        "name": "强周期",
        "keywords": ["有色", "钢铁", "煤炭", "化工", "石油", "建材", "采掘"],
        "priority": 80,
        "enabled": True,
        "config": {
            "indicators": ["commodity_price", "dxy", "inventory", "sector_leaders"],
            "refresh_minutes": 30
        }
    },
    {
        "profile_id": "growth",
        "name": "成长",
        "keywords": ["电子", "半导体", "计算机", "通信", "软件", "军工", "新能源", "医药"],
        "priority": 70,
        "enabled": True,
        "config": {
            "indicators": ["rates", "nasdaq", "semis", "policy"],
            "refresh_minutes": 30
        }
    },
    {
        "profile_id": "defensive",
        "name": "稳健",
        "keywords": ["食品饮料", "公用事业", "银行", "保险", "家电", "运营商"],
        "priority": 60,
        "enabled": True,
        "config": {
            "indicators": ["dividend", "bond_yield", "macro"],
            "refresh_minutes": 60
        }
    },
    {
        "profile_id": "default",
        "name": "默认",
        "keywords": [],
        "priority": 0,
        "enabled": True,
        "config": {}
    }
]


class IndustrySync(BaseModel):
    symbols: Optional[List[str]] = None
    force: Optional[bool] = False


class IndustryUpdate(BaseModel):
    industry: Optional[str] = None
    source: Optional[str] = None


class IndustryProfileUpdate(BaseModel):
    name: Optional[str] = None
    keywords: Optional[List[str]] = None
    config: Optional[Dict[str, Any]] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class IndustryProfileOverrideUpdate(BaseModel):
    profile_id: Optional[str] = None
    source: Optional[str] = None


class IndustrySettingsUpdate(BaseModel):
    enabled: Optional[bool] = None


def _normalize_symbol(symbol: str) -> str:
    sym = symbol.upper().replace("/", ".")
    if "." not in sym:
        if sym.startswith(("6", "9", "5")):
            sym = f"{sym}.SH"
        elif sym.startswith(("0", "2", "3", "1")):
            sym = f"{sym}.SZ"
        elif sym.startswith(("8", "4")):
            sym = f"{sym}.BJ"
        else:
            sym = f"{sym}.SH"
    return sym


def _symbol_to_code(symbol: str) -> str:
    return symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")


def _get_watchlist_symbols() -> List[str]:
    items = load_watchlist()
    symbols = []
    for item in items:
        if item.get("market") != "ashare":
            continue
        sym = item.get("symbol")
        if sym:
            symbols.append(_normalize_symbol(sym))
    return list(dict.fromkeys(symbols))


def _get_tushare_pro():
    try:
        import tushare as ts
    except Exception:
        raise HTTPException(status_code=500, detail="tushare_not_installed")

    token = os.getenv("TUSHARE_TOKEN") or os.getenv("TUSHARE_API_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="TUSHARE_TOKEN not configured")

    ts.set_token(token)
    return ts.pro_api()


def _fetch_tushare_industry(symbols: Optional[List[str]] = None) -> Dict[str, str]:
    pro = _get_tushare_pro()
    df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,industry")
    if df is None or df.empty:
        return {}
    mapping: Dict[str, str] = {}
    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code") or "").upper()
        industry = str(row.get("industry") or "").strip()
        if not ts_code or not industry:
            continue
        mapping[ts_code] = industry
    if symbols:
        needed = {_normalize_symbol(s) for s in symbols}
        mapping = {k: v for k, v in mapping.items() if k in needed}
    return mapping


def _code_to_symbol(code: str) -> str:
    c = str(code).strip()
    if not c:
        return ""
    if "." in c:
        return _normalize_symbol(c)
    c = c.zfill(6)
    if c.startswith(("6", "9", "5")):
        return f"{c}.SH"
    if c.startswith(("0", "2", "3", "1")):
        return f"{c}.SZ"
    if c.startswith(("8", "4")):
        return f"{c}.BJ"
    return f"{c}.SH"


def _pick_column(df, candidates: List[str]) -> Optional[str]:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _fetch_cninfo_industry(symbols: Optional[List[str]] = None) -> Dict[str, str]:
    try:
        import akshare as ak
    except Exception:
        raise HTTPException(status_code=500, detail="akshare_not_installed")

    standard = os.getenv("INDUSTRY_STANDARD", "证监会行业分类标准")
    df = ak.stock_industry_change_cninfo(symbol=standard)
    if df is None or df.empty:
        return {}

    code_col = _pick_column(df, ["证券代码", "股票代码", "代码"])
    date_col = _pick_column(df, ["变更日期", "变更时间", "更新时间"])
    # prefer mid-level then top-level industry
    industry_col = _pick_column(df, ["行业中类", "行业大类", "行业名称", "行业"])
    if not code_col or not industry_col:
        return {}

    latest: Dict[str, Dict[str, str]] = {}
    for _, row in df.iterrows():
        code_val = row.get(code_col)
        sym = _code_to_symbol(code_val)
        if not sym:
            continue
        industry = str(row.get(industry_col) or "").strip()
        if not industry:
            continue
        date_val = row.get(date_col) if date_col else None
        date_str = str(date_val) if date_val is not None else ""
        prev = latest.get(sym)
        if not prev or (date_str and date_str > prev.get("date", "")):
            latest[sym] = {"industry": industry, "date": date_str}

    if symbols:
        needed = {_normalize_symbol(s) for s in symbols}
        return {k: v["industry"] for k, v in latest.items() if k in needed}
    return {k: v["industry"] for k, v in latest.items()}


def _fetch_industry(symbols: Optional[List[str]] = None) -> Tuple[Dict[str, str], Optional[str]]:
    prefer = os.getenv("INDUSTRY_SOURCE", "").lower().strip()
    if prefer in ("cninfo", "akshare"):
        try:
            mapping = _fetch_cninfo_industry(symbols)
            if mapping:
                return mapping, "cninfo"
        except Exception:
            pass
    try:
        mapping = _fetch_tushare_industry(symbols)
        if mapping:
            return mapping, "tushare"
    except Exception:
        mapping = {}
    try:
        mapping = _fetch_cninfo_industry(symbols)
        if mapping:
            return mapping, "cninfo"
    except Exception:
        pass
    return {}, None


def _upsert_industry(conn, symbol: str, industry: str, source: str):
    conn.execute("DELETE FROM symbol_industry WHERE symbol = ?", (symbol,))
    conn.execute(
        "INSERT INTO symbol_industry (symbol, industry, source, updated_at) VALUES (?, ?, ?, ?)",
        (symbol, industry, source, datetime.now())
    )


def _get_industry_from_db(conn, symbol: str) -> Optional[Tuple[str, str, str]]:
    row = conn.execute(
        "SELECT symbol, industry, source, updated_at FROM symbol_industry WHERE symbol = ?",
        (symbol,)
    ).fetchone()
    return row


def _ensure_profiles(conn):
    row = conn.execute("SELECT COUNT(1) FROM industry_profiles").fetchone()
    count = int(row[0]) if row and row[0] is not None else 0
    if count > 0:
        return
    for profile in DEFAULT_INDUSTRY_PROFILES:
        conn.execute(
            "INSERT INTO industry_profiles (profile_id, name, keywords_json, config_json, priority, enabled, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                profile["profile_id"],
                profile["name"],
                json.dumps(profile.get("keywords", []), ensure_ascii=False),
                json.dumps(profile.get("config", {}), ensure_ascii=False),
                int(profile.get("priority", 0)),
                bool(profile.get("enabled", True)),
                datetime.now()
            )
        )


def _load_profiles(conn) -> List[Dict[str, Any]]:
    _ensure_profiles(conn)
    rows = conn.execute(
        "SELECT profile_id, name, keywords_json, config_json, priority, enabled, updated_at FROM industry_profiles"
    ).fetchall()
    profiles: List[Dict[str, Any]] = []
    for row in rows:
        keywords = []
        config = {}
        try:
            if row[2]:
                keywords = json.loads(row[2])
        except Exception:
            keywords = []
        try:
            if row[3]:
                config = json.loads(row[3])
        except Exception:
            config = {}
        profiles.append({
            "profile_id": row[0],
            "name": row[1],
            "keywords": keywords or [],
            "config": config or {},
            "priority": int(row[4] or 0),
            "enabled": bool(row[5]),
            "updated_at": row[6],
        })
    return profiles


def _get_profile_by_id(profiles: List[Dict[str, Any]], profile_id: str) -> Optional[Dict[str, Any]]:
    for p in profiles:
        if p.get("profile_id") == profile_id:
            return p
    return None


def _get_profile_override(conn, symbol: str) -> Optional[Tuple[str, str, str]]:
    row = conn.execute(
        "SELECT profile_id, source, updated_at FROM symbol_profile_override WHERE symbol = ?",
        (symbol,)
    ).fetchone()
    return row


def _set_profile_override(conn, symbol: str, profile_id: str, source: str):
    conn.execute("DELETE FROM symbol_profile_override WHERE symbol = ?", (symbol,))
    conn.execute(
        "INSERT INTO symbol_profile_override (symbol, profile_id, source, updated_at) VALUES (?, ?, ?, ?)",
        (symbol, profile_id, source, datetime.now())
    )


def _get_industry_settings(conn, symbol: str) -> Optional[Tuple[bool, str]]:
    row = conn.execute(
        "SELECT enabled, updated_at FROM symbol_industry_settings WHERE symbol = ?",
        (symbol,)
    ).fetchone()
    return row


def _set_industry_settings(conn, symbol: str, enabled: bool):
    conn.execute("DELETE FROM symbol_industry_settings WHERE symbol = ?", (symbol,))
    conn.execute(
        "INSERT INTO symbol_industry_settings (symbol, enabled, updated_at) VALUES (?, ?, ?)",
        (symbol, bool(enabled), datetime.now())
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace(",", "").strip()
            if not value:
                return default
        return float(value)
    except Exception:
        return default


def _parse_indicator_specs(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    indicators = (config or {}).get("indicators")
    specs: List[Dict[str, Any]] = []
    if isinstance(indicators, list):
        for item in indicators:
            if isinstance(item, str):
                specs.append({"type": item})
            elif isinstance(item, dict):
                typ = item.get("type") or item.get("indicator")
                if not typ and len(item) == 1:
                    typ = next(iter(item.keys()))
                    value = item.get(typ)
                    item = {"type": typ, "items": value}
                if typ:
                    spec = {"type": typ}
                    spec.update(item)
                    specs.append(spec)
    elif isinstance(indicators, dict):
        for key, value in indicators.items():
            if isinstance(value, dict):
                spec = {"type": key}
                spec.update(value)
                specs.append(spec)
            elif isinstance(value, list):
                specs.append({"type": key, "items": value})
            elif isinstance(value, bool) and value:
                specs.append({"type": key})
    return specs


def _get_indicator_cache(conn, key: str) -> Optional[Tuple[str, str, str, datetime]]:
    row = conn.execute(
        "SELECT payload_json, source, error, updated_at FROM industry_indicator_cache WHERE indicator_key = ?",
        (key,)
    ).fetchone()
    return row


def _set_indicator_cache(conn, key: str, payload: Dict[str, Any], source: str, error: Optional[str] = None):
    conn.execute("DELETE FROM industry_indicator_cache WHERE indicator_key = ?", (key,))
    conn.execute(
        "INSERT INTO industry_indicator_cache (indicator_key, payload_json, source, error, updated_at) VALUES (?, ?, ?, ?, ?)",
        (
            key,
            json.dumps(payload or {}, ensure_ascii=False),
            source,
            error or "",
            datetime.now()
        )
    )


def _is_cache_fresh(updated_at: Optional[datetime], refresh_minutes: int) -> bool:
    if not updated_at:
        return False
    if refresh_minutes <= 0:
        return False
    try:
        return datetime.now() - updated_at < timedelta(minutes=refresh_minutes)
    except Exception:
        return False


def _fetch_yahoo_quote(ticker: str) -> Optional[Dict[str, Any]]:
    return fetch_quote(ticker)


def _format_quote(payload: Dict[str, Any]) -> str:
    if not payload:
        return "N/A"
    price = payload.get("price")
    if price is None:
        return "N/A"
    ts = payload.get("time")
    time_text = ""
    try:
        if ts:
            time_text = datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        time_text = ""
    pct = payload.get("changePercent")
    if pct is not None:
        base = f"{_safe_float(price):.2f} ({_safe_float(pct):+.2f}%)"
        return f"{base} @ {time_text}" if time_text else base
    change = payload.get("change")
    if change is not None:
        base = f"{_safe_float(price):.2f} ({_safe_float(change):+.2f})"
        return f"{base} @ {time_text}" if time_text else base
    base = f"{_safe_float(price):.2f}"
    return f"{base} @ {time_text}" if time_text else base


def _resolve_profile_config(conn, symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    settings = _get_industry_settings(conn, sym)
    enabled = bool(settings[0]) if settings else False
    row = _get_industry_from_db(conn, sym)
    industry = row[1] if row else None
    profiles = _load_profiles(conn)
    override = _get_profile_override(conn, sym)
    profile_id = None
    reason = ""
    if override and override[0]:
        profile_id = override[0]
        reason = f"手动覆盖模板 {profile_id}"
    else:
        rec = _recommend_profile(sym, industry, profiles)
        profile_id = rec.get("profile")
        reason = rec.get("reason") or ""
    profile = _get_profile_by_id(profiles, profile_id) if profile_id else None
    config = profile.get("config", {}) if profile else {}
    return {
        "symbol": sym,
        "enabled": enabled,
        "industry": industry,
        "profile_id": profile_id,
        "profile_name": (profile or {}).get("name"),
        "config": config or {},
        "reason": reason
    }


def _recommend_profile(symbol: str, industry: Optional[str], profiles: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    code = _symbol_to_code(symbol)
    use_profiles = profiles or DEFAULT_INDUSTRY_PROFILES

    def _enabled(p: Dict[str, Any]) -> bool:
        return bool(p.get("enabled", True))

    if is_etf(code):
        return {"profile": "etf", "reason": "ETF 优先使用 ETF 模板"}

    if not industry:
        return {"profile": "default", "reason": "无行业信息，使用默认模板"}

    candidates = [p for p in use_profiles if _enabled(p) and p.get("profile_id") not in ("etf", "default")]
    candidates.sort(key=lambda x: int(x.get("priority", 0)), reverse=True)
    for p in candidates:
        for key in p.get("keywords", []) or []:
            if key and key in industry:
                return {"profile": p.get("profile_id"), "reason": f"行业 {industry} 命中 {p.get('name') or p.get('profile_id')} 模板"}

    return {"profile": "default", "reason": f"行业 {industry} 使用默认模板"}


def _resolve_ticker_from_mapping(source: str, symbol: str, mapping: Optional[Dict[str, str]] = None) -> Optional[str]:
    if mapping:
        key = f"{source}:{symbol}"
        if key in mapping:
            return mapping[key]
        if symbol in mapping:
            return mapping[symbol]
    return None


def _default_commodity_ticker(source: str, symbol: str) -> Optional[str]:
    key = f"{source}:{symbol}".upper()
    defaults = {
        "LME:CU": "HG=F",
        "LME:AL": "ALI=F",
        "LME:NI": "NICKEL=F",
        "LME:ZN": "ZNC=F",
        "COMEX:CU": "HG=F",
        "COMEX:GC": "GC=F",
        "COMEX:SI": "SI=F",
        "NYMEX:CL": "CL=F",
        "OIL:CL": "CL=F",
        "GOLD:GC": "GC=F",
        "SILVER:SI": "SI=F",
        "CU": "HG=F",
        "AL": "ALI=F",
        "GC": "GC=F",
        "SI": "SI=F",
        "CL": "CL=F"
    }
    return defaults.get(key)


def _fetch_indicator_with_cache(conn, key: str, refresh_minutes: int, fetcher, source: str) -> Tuple[Optional[Dict[str, Any]], bool, Optional[str]]:
    cached = _get_indicator_cache(conn, key)
    if cached:
        payload_json, cached_source, cached_error, updated_at = cached
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except Exception:
            payload = {}
        if _is_cache_fresh(updated_at, refresh_minutes):
            return payload, True, cached_error or None
    try:
        payload = fetcher()
        if payload:
            _set_indicator_cache(conn, key, payload, source)
            return payload, False, None
        _set_indicator_cache(conn, key, {}, source, error="empty_payload")
        return None, False, "empty_payload"
    except Exception as e:
        _set_indicator_cache(conn, key, {}, source, error=str(e))
        return None, False, str(e)


def _get_cached_daily_change(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        from cache import load_cache
        cache = load_cache(symbol, "daily")
        klines = (cache or {}).get("klines", []) if cache else []
        if not klines:
            return None
        def _sort_key(k):
            if k.get("openTime"):
                return k.get("openTime")
            return k.get("date") or ""
        klines = sorted(klines, key=_sort_key)
        last = klines[-1]
        prev = klines[-2] if len(klines) >= 2 else None
        close = _safe_float(last.get("close"))
        prev_close = _safe_float(prev.get("close")) if prev else 0
        pct = ((close - prev_close) / prev_close * 100) if prev_close else None
        return {
            "date": last.get("date"),
            "close": close,
            "prev_close": prev_close,
            "change_percent": pct
        }
    except Exception:
        return None


def build_indicator_context(symbol: str) -> str:
    conn = get_connection()
    try:
        ctx = _resolve_profile_config(conn, symbol)
        if not ctx.get("enabled"):
            return ""
        config = ctx.get("config") or {}
        specs = _parse_indicator_specs(config)
        if not specs:
            return ""
        refresh_minutes = int(config.get("refresh_minutes") or 30)
        lines: List[str] = []
        for spec in specs:
            typ = (spec.get("type") or "").strip()
            if not typ:
                continue
            if typ == "index":
                items = spec.get("items") or spec.get("symbols") or spec.get("tickers") or []
                if isinstance(items, dict):
                    items = [items]
                if isinstance(items, str):
                    items = [items]
                if not items:
                    items = ["^GSPC", "^IXIC"]
                for item in items:
                    if isinstance(item, dict):
                        raw = item.get("ticker") or item.get("symbol") or item.get("code") or ""
                        ticker = resolve_us_index_ticker(str(raw)) or str(raw).strip()
                        label = item.get("label") or item.get("name")
                    else:
                        raw = str(item).strip()
                        ticker = resolve_us_index_ticker(raw) or raw
                        label = None
                    if not ticker:
                        continue
                    label = label or get_us_index_label(ticker) or ticker
                    key = f"index:{ticker}"
                    payload, cached, err = _fetch_indicator_with_cache(
                        conn,
                        key,
                        int(spec.get("refresh_minutes") or refresh_minutes),
                        lambda t=ticker: _fetch_yahoo_quote(t),
                        source="yahoo"
                    )
                    if payload:
                        lines.append(f"{label}: {_format_quote(payload)}")
                    else:
                        lines.append(f"{label}: 无数据")
            if typ == "commodity_price":
                items = spec.get("items") or []
                if isinstance(items, dict):
                    items = [items]
                if not items:
                    sources = spec.get("sources") or []
                    symbols = spec.get("symbols") or []
                    mapping = spec.get("mapping") or {}
                    for src in sources:
                        for sym in symbols:
                            items.append({
                                "source": src,
                                "symbol": sym,
                                "ticker": _resolve_ticker_from_mapping(str(src), str(sym), mapping)
                            })
                mapping = spec.get("mapping") or {}
                for item in items:
                    if isinstance(item, str):
                        item = {"symbol": item}
                    source = str(item.get("source") or "YAHOO").upper()
                    symbol_name = str(item.get("symbol") or item.get("ticker") or "")
                    ticker = item.get("ticker") or _resolve_ticker_from_mapping(source, symbol_name, mapping) or _default_commodity_ticker(source, symbol_name)
                    label = item.get("label") or f"{source}:{symbol_name}" if symbol_name else source
                    if not ticker:
                        lines.append(f"商品 {label}: 未配置 ticker")
                        continue
                    key = f"commodity_price:{source}:{ticker}"
                    payload, cached, err = _fetch_indicator_with_cache(
                        conn,
                        key,
                        int(item.get("refresh_minutes") or refresh_minutes),
                        lambda t=ticker: _fetch_yahoo_quote(t),
                        source="yahoo"
                    )
                    if payload:
                        lines.append(f"商品 {label}: {_format_quote(payload)}")
                    else:
                        lines.append(f"商品 {label}: 无数据")
            elif typ == "dxy":
                ticker = spec.get("ticker") or "DX-Y.NYB"
                key = f"dxy:{ticker}"
                payload, cached, err = _fetch_indicator_with_cache(
                    conn,
                    key,
                    int(spec.get("refresh_minutes") or refresh_minutes),
                    lambda t=ticker: _fetch_yahoo_quote(t),
                    source="yahoo"
                )
                if payload:
                    lines.append(f"DXY: {_format_quote(payload)}")
                else:
                    lines.append("DXY: 无数据")
            elif typ == "sector_leaders":
                symbols = spec.get("symbols") or spec.get("items") or []
                if isinstance(symbols, str):
                    symbols = [symbols]
                leader_parts = []
                for sym in symbols:
                    sym_norm = _normalize_symbol(str(sym))
                    snap = _get_cached_daily_change(sym_norm)
                    if not snap:
                        leader_parts.append(f"{sym_norm}: 无缓存")
                        continue
                    pct = snap.get("change_percent")
                    date = snap.get("date")
                    if pct is None:
                        text = f"{sym_norm}: {snap.get('close')}"
                    else:
                        text = f"{sym_norm}: {snap.get('close')} ({pct:+.2f}%)"
                    if date:
                        text = f"{sym_norm}({date}): {text.split(': ',1)[-1]}"
                    leader_parts.append(text)
                if leader_parts:
                    lines.append("板块龙头: " + ", ".join(leader_parts))
            else:
                lines.append(f"{typ}: 未实现")
        if not lines:
            return ""
        return "\n".join(lines)
    finally:
        conn.close()


@router.get("/industry/profiles")
def list_profiles():
    conn = get_connection()
    try:
        profiles = _load_profiles(conn)
        if not profiles:
            _ensure_profiles(conn)
            profiles = _load_profiles(conn)
        if not profiles:
            profiles = [
                {
                    "profile_id": p["profile_id"],
                    "name": p["name"],
                    "keywords": p.get("keywords", []),
                    "config": p.get("config", {}),
                    "priority": p.get("priority", 0),
                    "enabled": p.get("enabled", True),
                    "updated_at": datetime.now()
                }
                for p in DEFAULT_INDUSTRY_PROFILES
            ]
        return {"data": profiles}
    finally:
        conn.close()


@router.put("/industry/profiles/{profile_id}")
def update_profile(profile_id: str, payload: IndustryProfileUpdate):
    pid = (profile_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="profile_id required")
    conn = get_connection()
    try:
        profiles = _load_profiles(conn)
        existing = _get_profile_by_id(profiles, pid)
        name = (payload.name or (existing.get("name") if existing else pid) or pid).strip()
        keywords = payload.keywords if payload.keywords is not None else (existing.get("keywords") if existing else [])
        config = payload.config if payload.config is not None else (existing.get("config") if existing else {})
        priority = payload.priority if payload.priority is not None else (existing.get("priority") if existing else 0)
        enabled = payload.enabled if payload.enabled is not None else (existing.get("enabled") if existing else True)
        conn.execute("DELETE FROM industry_profiles WHERE profile_id = ?", (pid,))
        conn.execute(
            "INSERT INTO industry_profiles (profile_id, name, keywords_json, config_json, priority, enabled, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                pid,
                name,
                json.dumps(keywords or [], ensure_ascii=False),
                json.dumps(config or {}, ensure_ascii=False),
                int(priority or 0),
                bool(enabled),
                datetime.now()
            )
        )
        return {"success": True}
    finally:
        conn.close()


@router.delete("/industry/profiles/{profile_id}")
def delete_profile(profile_id: str):
    pid = (profile_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="profile_id required")
    conn = get_connection()
    try:
        conn.execute("DELETE FROM industry_profiles WHERE profile_id = ?", (pid,))
        return {"success": True}
    finally:
        conn.close()


@router.post("/industry/profiles/reset")
def reset_profiles():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM industry_profiles")
        _ensure_profiles(conn)
        return {"success": True}
    finally:
        conn.close()


@router.get("/industry/profile")
def get_profile(symbol: str):
    sym = _normalize_symbol(symbol)
    conn = get_connection()
    try:
        row = _get_industry_from_db(conn, sym)
        industry = row[1] if row else None
        source = row[2] if row else None
        updated_at = row[3] if row else None
        profiles = _load_profiles(conn)
        override = _get_profile_override(conn, sym)
        settings = _get_industry_settings(conn, sym)
    finally:
        conn.close()
    enabled = bool(settings[0]) if settings else False
    enabled_at = settings[1] if settings else None
    override_profile = None
    override_source = None
    override_at = None
    if override and override[0]:
        override_profile = override[0]
        override_source = override[1]
        override_at = override[2]

    profile = None
    profile_info = None
    if enabled:
        if override_profile:
            profile_info = _get_profile_by_id(profiles, override_profile) if profiles else None
            if profile_info and profile_info.get("enabled", True):
                profile = {"profile": override_profile, "reason": "手工指定模板"}
            else:
                profile = _recommend_profile(sym, industry, profiles)
                profile_info = _get_profile_by_id(profiles, profile.get("profile")) if profiles else None
        else:
            profile = _recommend_profile(sym, industry, profiles)
            profile_info = _get_profile_by_id(profiles, profile.get("profile")) if profiles else None
    else:
        profile = {"profile": None, "reason": "行业数据未启用"}
    return {
        "data": {
            "symbol": sym,
            "industry": industry,
            "source": source,
            "updated_at": updated_at,
            "enabled": enabled,
            "enabled_at": enabled_at,
            "profile_override": override_profile,
            "profile_override_source": override_source,
            "profile_override_at": override_at,
            "profile_name": profile_info.get("name") if profile_info else None,
            **profile
        }
    }


@router.get("/industry/context")
def get_industry_context(symbol: str):
    sym = _normalize_symbol(symbol)
    context = build_indicator_context(sym)
    return {"data": {"symbol": sym, "context": context}}


@router.get("/industry/settings/{symbol}")
def get_industry_settings(symbol: str):
    sym = _normalize_symbol(symbol)
    conn = get_connection()
    try:
        row = _get_industry_settings(conn, sym)
        enabled = bool(row[0]) if row else False
        updated_at = row[1] if row else None
        return {"data": {"symbol": sym, "enabled": enabled, "updated_at": updated_at}}
    finally:
        conn.close()


@router.post("/industry/settings/{symbol}")
def set_industry_settings(symbol: str, payload: IndustrySettingsUpdate):
    sym = _normalize_symbol(symbol)
    enabled = bool(payload.enabled) if payload.enabled is not None else False
    conn = get_connection()
    try:
        _set_industry_settings(conn, sym, enabled)
        return {"success": True, "data": {"symbol": sym, "enabled": enabled, "updated_at": datetime.now()}}
    finally:
        conn.close()


@router.post("/industry/profile_override/{symbol}")
def set_profile_override(symbol: str, payload: IndustryProfileOverrideUpdate):
    sym = _normalize_symbol(symbol)
    profile_id = (payload.profile_id or "").strip()
    source = (payload.source or "manual").strip() or "manual"
    conn = get_connection()
    try:
        if not profile_id:
            conn.execute("DELETE FROM symbol_profile_override WHERE symbol = ?", (sym,))
            return {"success": True, "data": None}
        _set_profile_override(conn, sym, profile_id, source)
        return {"success": True, "data": {"symbol": sym, "profile_id": profile_id, "source": source, "updated_at": datetime.now()}}
    finally:
        conn.close()


@router.get("/industry/{symbol}")
def get_industry(symbol: str, refresh: bool = Query(False)):
    sym = _normalize_symbol(symbol)
    conn = get_connection()
    try:
        row = _get_industry_from_db(conn, sym)
        if row and not refresh:
            return {
                "data": {
                    "symbol": row[0],
                    "industry": row[1],
                    "source": row[2],
                    "updated_at": row[3],
                }
            }

        # fetch from remote and update if missing or forced
        mapping, source = _fetch_industry([sym])
        industry = mapping.get(sym)
        if industry:
            _upsert_industry(conn, sym, industry, source or "unknown")
            return {
                "data": {
                    "symbol": sym,
                    "industry": industry,
                    "source": source or "unknown",
                    "updated_at": datetime.now(),
                }
            }
        return {"data": None}
    finally:
        conn.close()


@router.get("/industry/list")
def list_industries():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT industry FROM symbol_industry WHERE industry IS NOT NULL AND industry <> '' ORDER BY industry"
        ).fetchall()
        industries = [r[0] for r in rows if r and r[0]]
        if not industries:
            industries = [
                "有色金属", "钢铁", "煤炭", "化工", "石油石化", "建材",
                "半导体", "电子", "计算机", "通信", "医药生物",
                "银行", "保险", "食品饮料", "公用事业", "家电",
                "新能源", "军工", "汽车", "机械设备", "房地产"
            ]
        return {"data": industries}
    finally:
        conn.close()


@router.post("/industry/{symbol}")
def set_industry(symbol: str, payload: IndustryUpdate):
    sym = _normalize_symbol(symbol)
    industry = (payload.industry or "").strip()
    source = (payload.source or "manual").strip() or "manual"
    conn = get_connection()
    try:
        if not industry:
            conn.execute("DELETE FROM symbol_industry WHERE symbol = ?", (sym,))
            return {"success": True, "data": None}
        _upsert_industry(conn, sym, industry, source)
        return {
            "success": True,
            "data": {
                "symbol": sym,
                "industry": industry,
                "source": source,
                "updated_at": datetime.now(),
            }
        }
    finally:
        conn.close()


@router.post("/industry/sync")
def sync_industry(payload: IndustrySync):
    symbols = payload.symbols or []
    if not symbols:
        symbols = _get_watchlist_symbols()
    if not symbols:
        return {"success": True, "count": 0}

    symbols = [_normalize_symbol(s) for s in symbols]
    mapping, source = _fetch_industry(symbols)
    if not mapping:
        return {"success": True, "count": 0, "missing": symbols}

    conn = get_connection()
    try:
        inserted = 0
        for sym in symbols:
            industry = mapping.get(sym)
            if not industry:
                continue
            _upsert_industry(conn, sym, industry, source or "unknown")
            inserted += 1
        missing = [s for s in symbols if s not in mapping]
        return {"success": True, "count": inserted, "missing": missing}
    finally:
        conn.close()
