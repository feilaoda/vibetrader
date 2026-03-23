from typing import Optional, Any

from .types import DataType
from fallback_data import get_fallback_etfs


def supports(data_type: DataType) -> bool:
    return data_type in {DataType.SYMBOLS, DataType.FUNDAMENTALS, DataType.MARKET_BREADTH, DataType.INDUSTRY}


def fetch(data_type: DataType, **kwargs) -> Optional[Any]:
    if data_type == DataType.SYMBOLS:
        return {"stocks": None, "etfs": get_fallback_etfs()}
    if data_type == DataType.FUNDAMENTALS:
        return None
    if data_type == DataType.MARKET_BREADTH:
        return None
    if data_type == DataType.INDUSTRY:
        return None
    return None
