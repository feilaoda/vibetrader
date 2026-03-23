from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else None


def now_cn() -> datetime:
    return datetime.now(CN_TZ) if CN_TZ else datetime.now()


def to_timestamp_cn(date_str: str, fmt: str) -> int:
    dt = datetime.strptime(date_str, fmt)
    if CN_TZ:
        dt = dt.replace(tzinfo=CN_TZ)
    return int(dt.timestamp() * 1000)


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in ("", "--", "None", "nan"):
                return default
            return float(text)
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return default
        return num
    except Exception:
        return default


def pick_column(df, candidates):
    for name in candidates:
        if name in df.columns:
            return name
    return None


def ymd_compact_to_iso(date_str: str | None) -> str | None:
    if not date_str:
        return None
    if "-" in date_str:
        return date_str
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str
