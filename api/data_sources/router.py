from typing import Optional, Any, List, Tuple, Dict
import os
from datetime import timedelta

from .types import DataType
from . import tencent, sina, akshare, yahoo, local
from akshare_guard import akshare_disabled
from .utils import now_cn


CHANNELS = {
    "tencent": tencent,
    "sina": sina,
    "akshare": akshare,
    "yahoo": yahoo,
    "local": local,
}


DEFAULT_ORDER = {
    DataType.KLINE_DAILY: ["tencent", "sina", "akshare"],
    DataType.KLINE_MINUTE: ["akshare"],
    DataType.REALTIME: ["tencent", "akshare"],
    DataType.INDEX_SPOT: ["tencent", "akshare"],
    DataType.SYMBOLS: ["local", "akshare"],
    DataType.FUNDAMENTALS: ["local", "akshare"],
    DataType.MARKET_BREADTH: ["local", "akshare"],
    DataType.INDUSTRY: ["local", "akshare"],
}

US_ORDER = {
    DataType.KLINE_DAILY: ["yahoo"],
    DataType.REALTIME: ["yahoo"],
}


def _channel_enabled(mod, data_type: DataType) -> bool:
    enabled = getattr(mod, "enabled", None)
    if callable(enabled):
        try:
            return bool(enabled(data_type))
        except TypeError:
            try:
                return bool(enabled())
            except Exception:
                return False
        except Exception:
            return False
    return True


def fetch(
    data_type: DataType,
    channels: Optional[List[str]] = None,
    **kwargs
) -> Tuple[Optional[Any], Optional[str]]:
    market = (kwargs.get("market") or "").lower().strip()
    order = channels
    if order is None:
        if market == "us":
            order = US_ORDER.get(data_type, DEFAULT_ORDER.get(data_type, []))
        else:
            order = DEFAULT_ORDER.get(data_type, [])
    for name in order:
        mod = CHANNELS.get(name)
        if mod is None:
            continue
        if not _channel_enabled(mod, data_type):
            continue
        if hasattr(mod, "supports") and not mod.supports(data_type):
            continue
        try:
            data = mod.fetch(data_type, **kwargs)
        except Exception:
            data = None
        if data is not None:
            return data, name
    return None, None


def get_status(probe: bool = True) -> Dict[str, Any]:
    def _env_flag(name: str) -> bool:
        return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")

    def _build(dt: DataType, order: List[str], market: str = "cn") -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for name in order:
            mod = CHANNELS.get(name)
            if mod is None:
                continue
            declared = bool(getattr(mod, "supports", lambda _: False)(dt))
            enabled = _channel_enabled(mod, dt)
            item: Dict[str, Any] = {
                "channel": name,
                "enabled": enabled,
                "declared": declared,
            }
            if probe:
                probe_result = _probe_channel(dt, name, declared, enabled, market)
                item["available"] = bool(probe_result.get("ok"))
                item["probe"] = probe_result
            items.append(item)
        return items

    data = {
        "akshare_disabled": akshare_disabled(),
        "env": {
            "AKSHARE_DISABLED": _env_flag("AKSHARE_DISABLED"),
            "AKSHARE_ENABLED_TYPES": os.getenv("AKSHARE_ENABLED_TYPES", ""),
            "AKSHARE_ENABLE_MINUTE": _env_flag("AKSHARE_ENABLE_MINUTE"),
            "AKSHARE_ENABLE_BREADTH": _env_flag("AKSHARE_ENABLE_BREADTH"),
            "AKSHARE_ENABLE_INDUSTRY": _env_flag("AKSHARE_ENABLE_INDUSTRY"),
        },
        "data_types": {},
    }

    for data_type in DataType:
        default_order = DEFAULT_ORDER.get(data_type, [])
        us_order = US_ORDER.get(data_type, [])
        data["data_types"][data_type.value] = {
            "default": _build(data_type, default_order, market="cn"),
            "us": _build(data_type, us_order, market="us") if us_order else [],
        }

    channels: Dict[str, Any] = {}
    for name, mod in CHANNELS.items():
        matrix: Dict[str, Any] = {}
        declared_list: List[str] = []
        enabled: List[str] = []
        available: List[str] = []
        for dt in DataType:
            sup = bool(getattr(mod, "supports", lambda _: False)(dt))
            en = _channel_enabled(mod, dt)
            avail = False
            probe_result: Optional[Dict[str, Any]] = None
            if probe and sup and en:
                params = _probe_params(dt, "cn")
                if params is None:
                    probe_result = {"ok": False, "skipped": "no_probe"}
                elif dt in {DataType.FUNDAMENTALS, DataType.MARKET_BREADTH, DataType.INDUSTRY} and name == "local":
                    probe_result = {"ok": False, "skipped": "no_local_data"}
                else:
                    probe_result = _probe_channel(dt, name, sup, en, "cn")
                avail = bool(probe_result.get("ok")) if probe_result else False
            matrix[dt.value] = {
                "declared": sup,
                "enabled": en,
                "available": avail if probe else None,
            }
            if probe_result is not None:
                matrix[dt.value]["probe"] = probe_result
            if sup:
                declared_list.append(dt.value)
            if sup and en:
                enabled.append(dt.value)
            if probe and avail:
                available.append(dt.value)
        channels[name] = {
            "declared": declared_list,
            "enabled": enabled,
            "available": available if probe else [],
            "matrix": matrix,
        }

    data["channels"] = channels
    return data


def _probe_params(dt: DataType, market: str) -> Optional[Dict[str, Any]]:
    now = now_cn()
    today = now.strftime("%Y%m%d")
    start = (now - timedelta(days=10)).strftime("%Y%m%d")
    if market == "us":
        if dt == DataType.KLINE_DAILY:
            return {"symbol": "^GSPC", "start": start, "end": today, "interval": "1d", "market": "us"}
        if dt == DataType.REALTIME:
            return {"symbol": "^GSPC", "market": "us"}
        return None
    if dt == DataType.KLINE_DAILY:
        return {"symbol": "000001.SZ", "start": start, "end": today, "period": "daily"}
    if dt == DataType.KLINE_MINUTE:
        return {"symbol": "000001.SZ", "start": today, "end": today, "period": "1"}
    if dt == DataType.REALTIME:
        return {"symbol": "000001.SZ"}
    if dt == DataType.INDEX_SPOT:
        return {"symbol": "000985.SH"}
    if dt == DataType.SYMBOLS:
        return {}
    if dt == DataType.FUNDAMENTALS:
        return {"symbols": ["000001.SZ"]}
    if dt == DataType.MARKET_BREADTH:
        return {}
    if dt == DataType.INDUSTRY:
        return {"standard": "证监会行业分类标准"}
    return None


def _probe_channel(dt: DataType, channel: str, supports: bool, enabled: bool, market: str) -> Dict[str, Any]:
    if not supports:
        return {"ok": False, "skipped": "unsupported"}
    if not enabled:
        return {"ok": False, "skipped": "disabled"}
    params = _probe_params(dt, market)
    if params is None:
        return {"ok": False, "skipped": "no_probe"}
    try:
        data, used = fetch(dt, channels=[channel], **params)
        if data is None:
            return {"ok": False, "error": "no_data"}
        if isinstance(data, list) and not data:
            return {"ok": False, "error": "empty"}
        return {"ok": True, "channel": used or channel}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
