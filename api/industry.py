from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Tuple, Any
from datetime import datetime, timedelta
import json
import os

from db import get_connection
from watchlist import load_watchlist
from cache import is_etf
from data_sources import DataType
from data_sources.router import fetch as router_fetch
from yahoo import fetch_quote
from us_indices import resolve_us_index_ticker, get_us_index_label, is_us_index_symbol

MARKET_INDEX_ALIASES = {
    "万得全A": "000985.SH",
    "中证全指": "000985.SH",
    "WIND全A": "000985.SH",
    "881001": "000985.SH",
    "沪深300": "000300.SH",
    "CSI300": "000300.SH",
    "中证1000": "000852.SH",
    "CSI1000": "000852.SH",
    "创业板": "399006.SZ",
    "创业板指": "399006.SZ",
    "科创50": "000688.SH",
}

MARKET_INDEX_FALLBACKS = {
    "000985.SH": ["000985.SH", "000300.SH", "510300.SH"],
    "000300.SH": ["000300.SH", "510300.SH"],
    "000852.SH": ["000852.SH", "159845.SZ", "512100.SH"],
    "399006.SZ": ["399006.SZ", "159915.SZ"],
    "000688.SH": ["000688.SH", "588000.SH"],
}

MARKET_INDEX_LABELS = {
    "000985.SH": "中证全指",
    "000300.SH": "沪深300",
    "000852.SH": "中证1000",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "510300.SH": "沪深300ETF",
    "159845.SZ": "中证1000ETF",
    "512100.SH": "中证1000ETF",
    "159915.SZ": "创业板ETF",
    "588000.SH": "科创50ETF",
}

router = APIRouter()

DEFAULT_INDUSTRY_PROFILES = [
    {
        "profile_id": "etf",
        "name": "ETF",
        "keywords": [],
        "priority": 100,
        "enabled": True,
        "config": {
            "indicators": [
                {"type": "market_thermometer", "broad_index": "000985.SH", "style_index": "000300.SH", "refresh_minutes": 5},
                {"type": "industry_anomaly", "scope": "watchlist", "min_count": 5, "top": 6, "refresh_minutes": 5},
                "index",
                "macro",
                "liquidity"
            ],
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
            "indicators": [
                {"type": "market_thermometer", "broad_index": "000985.SH", "style_index": "000300.SH", "refresh_minutes": 5},
                {"type": "industry_anomaly", "scope": "watchlist", "min_count": 5, "top": 6, "refresh_minutes": 5},
                "commodity_price",
                "dxy",
                "inventory",
                "sector_leaders"
            ],
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
            "indicators": [
                {"type": "market_thermometer", "broad_index": "000985.SH", "style_index": "000852.SH", "refresh_minutes": 5},
                {"type": "industry_anomaly", "scope": "watchlist", "min_count": 5, "top": 6, "refresh_minutes": 5},
                "rates",
                "nasdaq",
                "semis",
                "policy"
            ],
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
            "indicators": [
                {"type": "market_thermometer", "broad_index": "000985.SH", "style_index": "000300.SH", "refresh_minutes": 5},
                {"type": "industry_anomaly", "scope": "watchlist", "min_count": 5, "top": 6, "refresh_minutes": 5},
                "dividend",
                "bond_yield",
                "macro"
            ],
            "refresh_minutes": 60
        }
    },
    {
        "profile_id": "default",
        "name": "默认",
        "keywords": [],
        "priority": 0,
        "enabled": True,
        "config": {
            "indicators": [
                {"type": "market_thermometer", "broad_index": "000985.SH", "style_index": "000300.SH", "refresh_minutes": 5},
                {"type": "industry_anomaly", "scope": "watchlist", "min_count": 5, "top": 6, "refresh_minutes": 5}
            ],
            "refresh_minutes": 30
        }
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
    market_broad_index: Optional[str] = None
    market_style_index: Optional[str] = None


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
    standard = os.getenv("INDUSTRY_STANDARD", "证监会行业分类标准")
    df, _ = router_fetch(DataType.INDUSTRY, channels=["akshare"], standard=standard)
    if df is None:
        return {}
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


def _get_industry_settings(conn, symbol: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT enabled, updated_at, market_broad_index, market_style_index FROM symbol_industry_settings WHERE symbol = ?",
        (symbol,)
    ).fetchone()
    if not row:
        return {
            "enabled": False,
            "updated_at": None,
            "market_broad_index": None,
            "market_style_index": None,
        }
    return {
        "enabled": bool(row[0]),
        "updated_at": row[1],
        "market_broad_index": row[2] or None,
        "market_style_index": row[3] or None,
    }


def _set_industry_settings(
    conn,
    symbol: str,
    enabled: bool,
    market_broad_index: Optional[str] = None,
    market_style_index: Optional[str] = None,
):
    broad = _normalize_market_index_symbol(market_broad_index) if market_broad_index else None
    style = _normalize_market_index_symbol(market_style_index) if market_style_index else None
    conn.execute("DELETE FROM symbol_industry_settings WHERE symbol = ?", (symbol,))
    conn.execute(
        "INSERT INTO symbol_industry_settings (symbol, enabled, market_broad_index, market_style_index, updated_at) VALUES (?, ?, ?, ?, ?)",
        (symbol, bool(enabled), broad, style, datetime.now())
    )


def _suggest_market_indexes(symbol: str, industry: Optional[str] = None) -> Tuple[str, str]:
    sym = _normalize_symbol(symbol)
    code = _symbol_to_code(sym)
    broad = "000985.SH"
    style = "000300.SH"
    ind = (industry or "").strip()

    growth_keys = ("半导体", "电子", "计算机", "通信", "软件", "军工", "新能源", "医药", "AI", "人工智能")
    large_keys = ("银行", "保险", "煤炭", "石油", "有色", "钢铁", "公用事业", "食品饮料", "白酒", "家电")

    if code.startswith(("688", "689", "787", "588")):
        style = "000688.SH"
    elif code.startswith(("300", "301")):
        style = "399006.SZ"
    elif code in ("159915", "159949", "159952"):
        style = "399006.SZ"
    elif code in ("588000", "588080", "588060"):
        style = "000688.SH"
    elif code in ("510300", "510050", "159919", "159300"):
        style = "000300.SH"
    elif ind and any(k in ind for k in growth_keys):
        style = "000852.SH"
    elif ind and any(k in ind for k in large_keys):
        style = "000300.SH"
    elif code.startswith(("000", "001", "002", "003")):
        style = "000852.SH"
    elif code.startswith(("159", "512", "515", "516", "517", "518")):
        style = "000852.SH"

    return broad, style


def _ensure_industry_settings_defaults(conn, symbol: str, industry: Optional[str] = None) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    settings = _get_industry_settings(conn, sym)
    # Only auto-enable defaults for A-share symbols.
    is_ashare_symbol = sym.endswith((".SH", ".SZ", ".BJ"))
    if not is_ashare_symbol:
        return settings

    broad_default, style_default = _suggest_market_indexes(sym, industry)
    enabled = bool(settings.get("enabled"))
    broad = settings.get("market_broad_index")
    style = settings.get("market_style_index")
    needs_init = settings.get("updated_at") is None
    if needs_init:
        enabled = True
    broad = _normalize_market_index_symbol(broad or broad_default)
    style = _normalize_market_index_symbol(style or style_default)
    if (
        needs_init
        or broad != settings.get("market_broad_index")
        or style != settings.get("market_style_index")
        or enabled != bool(settings.get("enabled"))
    ):
        _set_industry_settings(conn, sym, enabled, broad, style)
        return {
            "enabled": enabled,
            "updated_at": datetime.now(),
            "market_broad_index": broad,
            "market_style_index": style,
        }
    return settings


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
    row = _get_industry_from_db(conn, sym)
    industry = row[1] if row else None
    settings = _ensure_industry_settings_defaults(conn, sym, industry)
    enabled = bool(settings.get("enabled"))
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
        "market_broad_index": settings.get("market_broad_index"),
        "market_style_index": settings.get("market_style_index"),
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
        from cache import load_cache, get_klines_with_cache, _should_refresh_us_cache
        cache = load_cache(symbol, "daily")
        klines = (cache or {}).get("klines", []) if cache else []
        need_refresh = False
        if is_us_index_symbol(symbol):
            try:
                need_refresh, _ = _should_refresh_us_cache(cache or {})
            except Exception:
                need_refresh = False
        # On-demand补齐：若无缓存，或美股指数缓存过期，尝试拉取最近日线
        if not klines or need_refresh:
            try:
                klines, _ = get_klines_with_cache(
                    symbol,
                    period="daily",
                    limit=5,
                    force_refresh=False,
                    include_intraday=False
                )
            except Exception:
                klines = []
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


def _get_cached_daily_change_fast(symbol: str) -> Optional[Dict[str, Any]]:
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


def _quote_from_daily_cache(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        snap = _get_cached_daily_change(symbol)
        if not snap:
            return None
        price = _safe_float(snap.get("close"))
        if price <= 0:
            return None
        prev_close = _safe_float(snap.get("prev_close"))
        change = price - prev_close if prev_close > 0 else None
        change_pct = snap.get("change_percent")
        ts = None
        try:
            if snap.get("date"):
                dt = datetime.strptime(str(snap.get("date")), "%Y-%m-%d")
                ts = int(dt.timestamp())
        except Exception:
            ts = None
        return {
            "price": price,
            "change": change,
            "changePercent": change_pct,
            "time": ts
        }
    except Exception:
        return None


def _normalize_market_index_symbol(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    alias = MARKET_INDEX_ALIASES.get(text)
    if alias:
        text = alias
    if "." in text:
        return _normalize_symbol(text)
    if text in ("000001", "000300", "000688", "000852", "000905", "000985"):
        return f"{text}.SH"
    if text.startswith("399"):
        return f"{text}.SZ"
    return _normalize_symbol(text)


def _resolve_market_index_candidates(raw: Any) -> List[str]:
    norm = _normalize_market_index_symbol(raw)
    if not norm:
        return []
    raw_candidates = MARKET_INDEX_FALLBACKS.get(norm, [norm])
    candidates: List[str] = []
    for item in raw_candidates:
        val = _normalize_market_index_symbol(item)
        if val and val not in candidates:
            candidates.append(val)
    return candidates


def _safe_mean(values: List[float]) -> Optional[float]:
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return float(sum(nums) / len(nums))


def _get_daily_klines(symbol: str, limit: int = 260) -> List[Dict[str, Any]]:
    # Fast path: use local cache only to avoid blocking AI requests on remote fetch.
    try:
        from cache import load_cache
        cache = load_cache(symbol, "daily")
        klines = (cache or {}).get("klines", []) if cache else []
        if isinstance(klines, list) and klines:
            return klines[-limit:]
    except Exception:
        pass
    # Fallback: fetch once when local cache is empty.
    try:
        from cache import get_klines_with_cache
        klines, _ = get_klines_with_cache(
            symbol,
            period="daily",
            limit=limit,
            force_refresh=False,
            include_intraday=False
        )
        if not isinstance(klines, list):
            return []
        return klines
    except Exception:
        return []


def _build_index_snapshot(symbol: str) -> Optional[Dict[str, Any]]:
    klines = _get_daily_klines(symbol, limit=260)
    if not klines:
        return None
    klines = sorted(
        klines,
        key=lambda k: (k.get("openTime") or 0, k.get("date") or "")
    )
    closes = [_safe_float(k.get("close")) for k in klines if _safe_float(k.get("close")) > 0]
    volumes = [_safe_float(k.get("volume")) for k in klines if _safe_float(k.get("volume")) > 0]
    if len(closes) < 2:
        return None

    close = closes[-1]
    prev_close = closes[-2]
    pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else None
    ma20 = _safe_mean(closes[-20:]) if len(closes) >= 20 else None
    ma60 = _safe_mean(closes[-60:]) if len(closes) >= 60 else None
    vol = volumes[-1] if volumes else 0.0
    vol_ma20 = _safe_mean(volumes[-20:]) if len(volumes) >= 20 else None
    high120 = max(closes[-120:]) if len(closes) >= 120 else max(closes)
    low120 = min(closes[-120:]) if len(closes) >= 120 else min(closes)
    band = high120 - low120
    position_ratio = ((close - low120) / band) if band > 0 else 0.5

    if ma20 and ma60:
        if close > ma20 > ma60:
            daily_trend = "多头"
        elif close < ma20 < ma60:
            daily_trend = "空头"
        else:
            daily_trend = "震荡"
    else:
        daily_trend = "震荡"

    if len(closes) >= 40:
        base = closes[-40]
        weekly_change = ((close - base) / base * 100) if base > 0 else 0.0
    else:
        weekly_change = 0.0
    if weekly_change >= 3:
        weekly_trend = "上行"
    elif weekly_change <= -3:
        weekly_trend = "下行"
    else:
        weekly_trend = "震荡"

    if position_ratio >= 0.67:
        box_position = "高位"
    elif position_ratio <= 0.33:
        box_position = "低位"
    else:
        box_position = "中位"

    # Try to overlay intraday index spot data so date/price update during trading hours.
    spot = _fetch_ashare_index_spot(symbol)
    used_source = "daily"
    if spot and _safe_float(spot.get("close")) > 0:
        close = _safe_float(spot.get("close"))
        spot_pct = spot.get("change_pct")
        if spot_pct is None and prev_close > 0:
            pct = ((close - prev_close) / prev_close * 100)
        else:
            pct = _safe_float(spot_pct, default=pct or 0.0)
        spot_vol = _safe_float(spot.get("volume"))
        if spot_vol > 0:
            vol = spot_vol
        spot_date = str(spot.get("date") or "")
        used_source = str(spot.get("source") or "spot")
    else:
        spot_date = ""

    date_text = spot_date or str(klines[-1].get("date") or "")
    # Guard against obvious bad index values (e.g., index treated as stock series).
    try:
        code = _symbol_to_code(symbol)
        if not is_etf(code):
            if (symbol in MARKET_INDEX_LABELS or symbol in MARKET_INDEX_FALLBACKS) and close > 0 and close < 100:
                print(f"[MarketThermometer] suspicious index value {symbol}: {close} source={used_source}")
                return None
    except Exception:
        pass
    latest_trade = ""
    try:
        from cache import _latest_trading_date
        latest_trade = _latest_trading_date().strftime("%Y-%m-%d")
    except Exception:
        latest_trade = ""
    stale = False
    if latest_trade:
        if not date_text or date_text < latest_trade:
            stale = True
            print(f"[MarketThermometer] stale index {symbol}: date={date_text or 'N/A'} < {latest_trade}")

    return {
        "symbol": symbol,
        "date": date_text,
        "close": close,
        "change_pct": pct,
        "ma20": ma20,
        "ma60": ma60,
        "volume": vol,
        "vol_ma20": vol_ma20,
        "daily_trend": daily_trend,
        "weekly_trend": weekly_trend,
        "box_position": box_position,
        "position_ratio": position_ratio,
        "source": used_source,
        "stale": stale,
    }


def _is_ashare_index_symbol(symbol: str) -> bool:
    sym = _normalize_symbol(symbol)
    code = _symbol_to_code(sym)
    if sym in MARKET_INDEX_LABELS or sym in MARKET_INDEX_FALLBACKS:
        return True
    if code in ("000001", "000016", "000300", "000852", "000905", "000985", "000688", "399006"):
        return True
    return False


def _fetch_ashare_index_spot(symbol: str) -> Optional[Dict[str, Any]]:
    if not _is_ashare_index_symbol(symbol):
        return None
    sym = _normalize_symbol(symbol)
    code = _symbol_to_code(sym)
    data, channel = router_fetch(
        DataType.INDEX_SPOT,
        channels=["tencent", "akshare"],
        symbol=code,
    )
    if not data:
        return None
    data["symbol"] = sym
    if channel and not data.get("source"):
        data["source"] = channel
    return data


def _resolve_index_snapshot(raw: Any) -> Optional[Dict[str, Any]]:
    candidates = _resolve_market_index_candidates(raw)
    if not candidates:
        return None
    trade_iso = ""
    try:
        from cache import _latest_trading_date
        trade_iso = _latest_trading_date().strftime("%Y-%m-%d")
    except Exception:
        trade_iso = ""
    best_snapshot: Optional[Dict[str, Any]] = None
    best_date = ""
    for cand in candidates:
        snapshot = _build_index_snapshot(cand)
        if snapshot:
            snapshot["label"] = MARKET_INDEX_LABELS.get(cand, cand)
            snap_date = str(snapshot.get("date") or "")
            if trade_iso and snap_date and snap_date >= trade_iso:
                return snapshot
            if best_snapshot is None or snap_date > best_date:
                best_snapshot = snapshot
                best_date = snap_date
    return best_snapshot


def _fetch_ashare_market_breadth() -> Optional[Dict[str, Any]]:
    data, _ = router_fetch(DataType.MARKET_BREADTH, channels=["akshare"])
    return data


def _format_ma_position(snapshot: Dict[str, Any]) -> str:
    close = _safe_float(snapshot.get("close"))
    ma20 = _safe_float(snapshot.get("ma20"))
    if close <= 0 or ma20 <= 0:
        return "位置不明"
    threshold = max(ma20 * 0.002, 0.01)
    if close > ma20 + threshold:
        return "20日均线上方"
    if close < ma20 - threshold:
        return "20日均线下方"
    return "20日均线附近"


def _format_volume_state(snapshot: Dict[str, Any]) -> str:
    vol = _safe_float(snapshot.get("volume"))
    vol_ma20 = _safe_float(snapshot.get("vol_ma20"))
    if vol <= 0 or vol_ma20 <= 0:
        return "量能未知"
    ratio = vol / vol_ma20
    if ratio >= 1.2:
        return "放量"
    if ratio <= 0.8:
        return "缩量"
    return "平量"


def _resolve_market_temperature(
    broad: Optional[Dict[str, Any]],
    breadth: Optional[Dict[str, Any]],
    turnover_baseline_trillion: float,
) -> str:
    if not broad:
        return "数据不足"
    ma20 = _safe_float(broad.get("ma20"))
    close = _safe_float(broad.get("close"))
    above_ma20 = bool(ma20 > 0 and close > ma20)
    box_position = str(broad.get("box_position") or "")
    ad_ratio = None
    turnover_trillion = None
    up_limit = 0
    down_limit = 0
    if breadth:
        adv = int(breadth.get("advancers") or 0)
        dec = int(breadth.get("decliners") or 0)
        up_limit = int(breadth.get("up_limit") or 0)
        down_limit = int(breadth.get("down_limit") or 0)
        ad_ratio = adv / max(dec, 1)
        turnover = _safe_float(breadth.get("turnover"))
        if turnover > 0:
            turnover_trillion = turnover / 1e12

    if ad_ratio is not None and turnover_trillion is not None:
        if ad_ratio >= 2.2 and turnover_trillion >= max(1.6, turnover_baseline_trillion * 1.2) and above_ma20 and box_position != "低位":
            return "沸腾期（高波动，警惕追高）"
        if ad_ratio >= 1.2 and turnover_trillion >= max(1.0, turnover_baseline_trillion * 0.9) and above_ma20:
            return "启动期（风险中等，关注主线）"
        if ad_ratio <= 0.8 and turnover_trillion <= max(0.9, turnover_baseline_trillion * 0.8) and not above_ma20:
            return "冰点期（防守优先）"
    if up_limit > 80 and down_limit < 10 and above_ma20:
        return "沸腾期（题材亢奋，注意回撤）"
    if down_limit > up_limit and not above_ma20:
        return "冰点期（抛压主导）"
    return "过渡期（分化震荡）"


def _build_market_thermometer_payload(
    symbol: str,
    broad_index: str,
    style_index: str,
    include_breadth: bool = True,
    turnover_baseline_trillion: float = 1.0,
) -> Dict[str, Any]:
    broad = _resolve_index_snapshot(broad_index)
    style = _resolve_index_snapshot(style_index)
    breadth = _fetch_ashare_market_breadth() if include_breadth else None

    lines: List[str] = ["大盘环境:"]
    if broad:
        broad_date = str(broad.get("date") or "N/A")
        broad_source = str(broad.get("source") or "daily")
        broad_stale = bool(broad.get("stale"))
        broad_suffix = "（数据滞后）" if broad_stale else ""
        broad_line = (
            f"全市场指数[{broad_date}][{broad_source}]: {broad.get('label')}({broad.get('symbol')}) "
            f"{_safe_float(broad.get('close')):.2f} "
            f"({_safe_float(broad.get('change_pct')):+.2f}%)，"
            f"{_format_ma_position(broad)}，近半年箱体{broad.get('box_position')}"
            f"{broad_suffix}"
        )
        lines.append(broad_line)
        lines.append(
            f"趋势: 日线{broad.get('daily_trend')}，周线{broad.get('weekly_trend')}"
        )
    else:
        lines.append("全市场指数: 无数据")

    if style:
        style_date = str(style.get("date") or "N/A")
        style_source = str(style.get("source") or "daily")
        style_stale = bool(style.get("stale"))
        style_suffix = "（数据滞后）" if style_stale else ""
        lines.append(
            f"风格指数[{style_date}][{style_source}]: {style.get('label')}({style.get('symbol')}) "
            f"{_safe_float(style.get('close')):.2f} "
            f"({_safe_float(style.get('change_pct')):+.2f}%)，日线{style.get('daily_trend')}{style_suffix}"
        )

    if breadth:
        turnover_trillion = _safe_float(breadth.get("turnover")) / 1e12
        adv = int(breadth.get("advancers") or 0)
        dec = int(breadth.get("decliners") or 0)
        flat = int(breadth.get("flat") or 0)
        ad_ratio = adv / max(dec, 1)
        volume_state = _format_volume_state(broad) if broad else "量能未知"
        lines.append(f"量能: 两市成交额 {turnover_trillion:.2f} 万亿（{volume_state}）")
        lines.append(f"赚钱效应: 涨跌比 {ad_ratio:.2f}:1（涨{adv} / 跌{dec} / 平{flat}）")
    elif broad:
        lines.append(f"量能: {broad.get('label')} {_format_volume_state(broad)}")
        lines.append("赚钱效应: 无数据")

    temp = _resolve_market_temperature(
        broad,
        breadth,
        turnover_baseline_trillion=max(0.1, float(turnover_baseline_trillion or 1.0))
    )
    lines.append(f"市场温度计: {temp}")

    return {
        "context": "\n".join(lines),
        "broad": broad or {},
        "style": style or {},
        "breadth": breadth or {},
        "temperature": temp,
    }


def _load_industry_mapping(conn, scope: str = "watchlist", symbols: Optional[List[str]] = None) -> Dict[str, str]:
    scope_key = (scope or "watchlist").strip().lower()
    if symbols:
        mapping: Dict[str, str] = {}
        for sym in symbols:
            sym_norm = _normalize_symbol(sym)
            row = _get_industry_from_db(conn, sym_norm)
            if row and row[1]:
                mapping[sym_norm] = row[1]
        return mapping

    if scope_key == "all":
        rows = conn.execute(
            "SELECT symbol, industry FROM symbol_industry WHERE industry IS NOT NULL AND industry <> ''"
        ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows if r and r[0] and r[1]}

    symbols = _get_watchlist_symbols()
    mapping: Dict[str, str] = {}
    for sym in symbols:
        row = _get_industry_from_db(conn, sym)
        if row and row[1]:
            mapping[sym] = row[1]
    return mapping


def _build_industry_anomaly_payload(
    scope: str = "watchlist",
    min_count: int = 5,
    top_n: int = 6,
    rs_threshold: float = 1.5,
    strong_ratio_threshold: float = 0.35,
    weak_ratio_threshold: float = 0.35,
    broad_index: str = "000985.SH",
    use_cache_only: bool = True,
) -> Dict[str, Any]:
    conn = get_connection()
    try:
        mapping = _load_industry_mapping(conn, scope=scope)
        if not mapping:
            return {"context": "行业异动: 无数据", "industries": [], "scope": scope}
        broad = _resolve_index_snapshot(broad_index) if broad_index else None
        market_change = _safe_float(broad.get("change_pct")) if broad else 0.0

        industry_symbols: Dict[str, List[str]] = {}
        for sym, industry in mapping.items():
            if not industry:
                continue
            industry_symbols.setdefault(industry, []).append(sym)

        results: List[Dict[str, Any]] = []
        for industry, symbols in industry_symbols.items():
            changes: List[float] = []
            adv = dec = 0
            strong = weak = 0
            last_date = ""
            for sym in symbols:
                snap = _get_cached_daily_change_fast(sym) if use_cache_only else _get_cached_daily_change(sym)
                if not snap:
                    continue
                pct = snap.get("change_percent")
                if pct is None:
                    continue
                try:
                    pct_val = float(pct)
                except Exception:
                    continue
                changes.append(pct_val)
                if pct_val > 0:
                    adv += 1
                elif pct_val < 0:
                    dec += 1
                if pct_val >= 2.0:
                    strong += 1
                if pct_val <= -2.0:
                    weak += 1
                date = snap.get("date") or ""
                if date and date > last_date:
                    last_date = date
            count = len(changes)
            if count < min_count:
                continue
            avg_change = sum(changes) / count if count else 0.0
            sorted_changes = sorted(changes)
            mid = count // 2
            median = sorted_changes[mid] if count % 2 == 1 else (sorted_changes[mid - 1] + sorted_changes[mid]) / 2
            ad_ratio = adv / max(dec, 1)
            strong_ratio = strong / max(count, 1)
            weak_ratio = weak / max(count, 1)
            relative_strength = avg_change - market_change

            flag = "中性"
            if relative_strength >= rs_threshold or strong_ratio >= strong_ratio_threshold or ad_ratio >= 2.0:
                flag = "强势"
            elif relative_strength <= -rs_threshold or weak_ratio >= weak_ratio_threshold or ad_ratio <= 0.5:
                flag = "弱势"

            score = abs(relative_strength) + (abs(ad_ratio - 1.0) * 0.6) + max(strong_ratio, weak_ratio)
            results.append({
                "industry": industry,
                "count": count,
                "advancers": adv,
                "decliners": dec,
                "avg_change": avg_change,
                "median_change": median,
                "relative_strength": relative_strength,
                "strong_ratio": strong_ratio,
                "weak_ratio": weak_ratio,
                "flag": flag,
                "score": score,
                "date": last_date,
            })

        anomalies = [r for r in results if r["flag"] in ("强势", "弱势")]
        anomalies.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = anomalies[:max(1, int(top_n or 6))]

        if not top:
            context = "行业异动: 暂无明显异动"
        else:
            parts = []
            for item in top:
                parts.append(
                    f"{item['industry']} {item['flag']} {item['avg_change']:+.2f}%"
                    f"(RS {item['relative_strength']:+.2f}，{item['advancers']}/{item['decliners']})"
                )
            context = "行业异动: " + "；".join(parts)

        return {
            "context": context,
            "industries": results,
            "anomalies": top,
            "market_change": market_change,
            "scope": scope,
            "broad_index": broad_index,
        }
    finally:
        conn.close()


def build_indicator_context(symbol: str) -> str:
    conn = get_connection()
    try:
        ctx = _resolve_profile_config(conn, symbol)
        if not ctx.get("enabled"):
            return ""
        config = ctx.get("config") or {}
        specs = _parse_indicator_specs(config)
        symbol_upper = (symbol or "").upper()
        is_ashare_like = symbol_upper.endswith((".SH", ".SZ", ".BJ"))
        has_market_thermometer = any((spec.get("type") or "").strip() == "market_thermometer" for spec in specs)
        if is_ashare_like and not has_market_thermometer:
            specs = [{"type": "market_thermometer"}] + specs
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
                    if not payload:
                        # Yahoo 受限时，回退到已缓存日线（例如 SPX.US / NDX.US）
                        payload = _quote_from_daily_cache(ticker)
                    if payload:
                        lines.append(f"{label}: {_format_quote(payload)}")
                    else:
                        lines.append(f"{label}: 无数据")
            elif typ == "commodity_price":
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
            elif typ == "market_thermometer":
                broad_index = (
                    ctx.get("market_broad_index")
                    or spec.get("broad_index")
                    or spec.get("all_market_index")
                    or spec.get("index")
                    or "000985.SH"
                )
                style_index = (
                    ctx.get("market_style_index")
                    or spec.get("style_index")
                    or spec.get("style")
                    or ("000688.SH" if symbol.upper().startswith("588") else "000300.SH")
                )
                broad_index = broad_index or "000985.SH"
                style_index = style_index or ("000688.SH" if symbol.upper().startswith("588") else "000300.SH")
                include_breadth = bool(spec.get("include_breadth", True))
                turnover_baseline = _safe_float(spec.get("turnover_baseline_trillion"), default=1.0)
                broad_norm = _normalize_market_index_symbol(broad_index)
                style_norm = _normalize_market_index_symbol(style_index)
                spec_refresh = int(spec.get("refresh_minutes") or refresh_minutes)
                key = f"market_thermometer:{broad_norm}:{style_norm}:{int(include_breadth)}:{turnover_baseline:.2f}"
                # Read-through fast path: return cached payload immediately (even stale).
                # Background scheduler keeps it refreshed during trading hours.
                cached_row = _get_indicator_cache(conn, key)
                if cached_row:
                    payload_json, _, _, updated_at = cached_row
                    try:
                        cached_payload = json.loads(payload_json) if payload_json else {}
                    except Exception:
                        cached_payload = {}
                    cached_text = cached_payload.get("context") if isinstance(cached_payload, dict) else None
                    if cached_text:
                        lines.append(str(cached_text))
                        if _is_cache_fresh(updated_at, spec_refresh):
                            continue
                        # Stale cache still returned to keep chat latency low.
                        continue
                payload, cached, err = _fetch_indicator_with_cache(
                    conn,
                    key,
                    spec_refresh,
                    lambda sym=symbol, b=broad_norm or broad_index, s=style_norm or style_index, ib=include_breadth, tb=turnover_baseline: _build_market_thermometer_payload(sym, b, s, ib, tb),
                    source="tencent"
                )
                context_text = (payload or {}).get("context") if isinstance(payload, dict) else None
                if context_text:
                    lines.append(str(context_text))
                else:
                    lines.append("大盘环境: 无数据")
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
            elif typ == "industry_anomaly":
                scope = (spec.get("scope") or "watchlist").strip().lower()
                min_count = int(spec.get("min_count") or 5)
                top_n = int(spec.get("top") or spec.get("top_n") or 6)
                rs_threshold = _safe_float(spec.get("rs_threshold"), default=1.5)
                strong_ratio = _safe_float(spec.get("strong_ratio_threshold"), default=0.35)
                weak_ratio = _safe_float(spec.get("weak_ratio_threshold"), default=0.35)
                broad_index = (
                    ctx.get("market_broad_index")
                    or spec.get("broad_index")
                    or spec.get("all_market_index")
                    or spec.get("index")
                    or "000985.SH"
                )
                refresh_m = int(spec.get("refresh_minutes") or refresh_minutes)
                key = f"industry_anomaly:{scope}:{min_count}:{top_n}:{rs_threshold:.2f}:{strong_ratio:.2f}:{weak_ratio:.2f}:{broad_index}"
                payload, cached, err = _fetch_indicator_with_cache(
                    conn,
                    key,
                    refresh_m,
                    lambda sc=scope, mn=min_count, tp=top_n, rs=rs_threshold, sr=strong_ratio, wr=weak_ratio, bi=broad_index: _build_industry_anomaly_payload(
                        scope=sc,
                        min_count=mn,
                        top_n=tp,
                        rs_threshold=rs,
                        strong_ratio_threshold=sr,
                        weak_ratio_threshold=wr,
                        broad_index=bi,
                        use_cache_only=True,
                    ),
                    source="daily_cache"
                )
                context_text = (payload or {}).get("context") if isinstance(payload, dict) else None
                if context_text:
                    lines.append(str(context_text))
                else:
                    lines.append("行业异动: 无数据")
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
        settings = _ensure_industry_settings_defaults(conn, sym, industry)
    finally:
        conn.close()
    enabled = bool(settings.get("enabled"))
    enabled_at = settings.get("updated_at")
    market_broad_index = settings.get("market_broad_index")
    market_style_index = settings.get("market_style_index")
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
            "market_broad_index": market_broad_index,
            "market_style_index": market_style_index,
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


@router.get("/industry/anomalies")
def get_industry_anomalies(
    scope: str = Query("watchlist", description="watchlist | all"),
    min_count: int = Query(5, ge=1, le=200),
    top: int = Query(6, ge=1, le=50),
    rs_threshold: float = Query(1.5, ge=0.1, le=10),
    strong_ratio_threshold: float = Query(0.35, ge=0.0, le=1.0),
    weak_ratio_threshold: float = Query(0.35, ge=0.0, le=1.0),
    broad_index: str = Query("000985.SH"),
    refresh_minutes: int = Query(5, ge=1, le=120),
):
    conn = get_connection()
    try:
        key = f"industry_anomaly:{scope}:{min_count}:{top}:{rs_threshold:.2f}:{strong_ratio_threshold:.2f}:{weak_ratio_threshold:.2f}:{broad_index}"
        payload, cached, err = _fetch_indicator_with_cache(
            conn,
            key,
            refresh_minutes,
            lambda: _build_industry_anomaly_payload(
                scope=scope,
                min_count=min_count,
                top_n=top,
                rs_threshold=rs_threshold,
                strong_ratio_threshold=strong_ratio_threshold,
                weak_ratio_threshold=weak_ratio_threshold,
                broad_index=broad_index,
                use_cache_only=True,
            ),
            source="daily_cache"
        )
        return {"data": payload or {}, "cached": bool(cached), "error": err or None}
    finally:
        conn.close()


@router.get("/industry/settings/{symbol}")
def get_industry_settings(symbol: str):
    sym = _normalize_symbol(symbol)
    conn = get_connection()
    try:
        industry_row = _get_industry_from_db(conn, sym)
        industry = industry_row[1] if industry_row else None
        row = _ensure_industry_settings_defaults(conn, sym, industry)
        return {
            "data": {
                "symbol": sym,
                "enabled": bool(row.get("enabled")),
                "updated_at": row.get("updated_at"),
                "market_broad_index": row.get("market_broad_index"),
                "market_style_index": row.get("market_style_index"),
            }
        }
    finally:
        conn.close()


@router.post("/industry/settings/{symbol}")
def set_industry_settings(symbol: str, payload: IndustrySettingsUpdate):
    sym = _normalize_symbol(symbol)
    conn = get_connection()
    try:
        industry_row = _get_industry_from_db(conn, sym)
        industry = industry_row[1] if industry_row else None
        current = _ensure_industry_settings_defaults(conn, sym, industry)
        enabled = bool(payload.enabled) if payload.enabled is not None else bool(current.get("enabled"))
        market_broad_index = (
            payload.market_broad_index
            if payload.market_broad_index is not None
            else current.get("market_broad_index")
        )
        market_style_index = (
            payload.market_style_index
            if payload.market_style_index is not None
            else current.get("market_style_index")
        )
        _set_industry_settings(conn, sym, enabled, market_broad_index, market_style_index)
        return {
            "success": True,
            "data": {
                "symbol": sym,
                "enabled": enabled,
                "market_broad_index": _normalize_market_index_symbol(market_broad_index) if market_broad_index else None,
                "market_style_index": _normalize_market_index_symbol(market_style_index) if market_style_index else None,
                "updated_at": datetime.now(),
            }
        }
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
