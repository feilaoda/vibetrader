from typing import Optional, Any

from .types import DataType
from .akshare import fetch_kline_sina
from akshare_guard import akshare_disabled


def supports(data_type: DataType) -> bool:
    return data_type in {DataType.KLINE_DAILY}


def enabled(data_type: DataType | None = None) -> bool:
    if data_type not in {DataType.KLINE_DAILY}:
        return False
    return not akshare_disabled()


def fetch(data_type: DataType, **kwargs) -> Optional[Any]:
    if akshare_disabled():
        return None
    if data_type == DataType.KLINE_DAILY:
        code = (kwargs.get("symbol") or kwargs.get("code") or "").upper().replace("SH", "").replace("SZ", "").replace(".", "")
        period = kwargs.get("period") or "daily"
        fetch_start = kwargs.get("start")
        fetch_end = kwargs.get("end")
        return fetch_kline_sina(code, period, fetch_start, fetch_end)
    return None
