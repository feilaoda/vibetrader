from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in ("", "--", "None", "nan", "NaN"):
                return default
            value = text
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return default
        return num
    except Exception:
        return default


def normalize_daily_klines(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_date: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "").strip()
        if len(day) == 8 and day.isdigit():
            day = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        if not day:
            continue
        close = safe_float(row.get("close"))
        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))
        if close <= 0 or high <= 0 or low <= 0:
            continue
        by_date[day] = {
            "date": day[:10],
            "open": safe_float(row.get("open"), close),
            "high": high,
            "low": low,
            "close": close,
            "volume": safe_float(row.get("volume")),
        }
    return [by_date[d] for d in sorted(by_date)]


def build_weekly_klines(daily: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for row in daily:
        try:
            dt = date.fromisoformat(str(row.get("date")))
        except Exception:
            continue
        year, week, _ = dt.isocalendar()
        key = (year, week)
        if key not in groups:
            groups[key] = {
                "date": row.get("date"),
                "open": safe_float(row.get("open"), row.get("close")),
                "high": safe_float(row.get("high")),
                "low": safe_float(row.get("low")),
                "close": safe_float(row.get("close")),
                "volume": safe_float(row.get("volume")),
            }
            continue
        target = groups[key]
        target["date"] = row.get("date")
        target["high"] = max(safe_float(target.get("high")), safe_float(row.get("high")))
        target["low"] = min(safe_float(target.get("low")), safe_float(row.get("low")))
        target["close"] = safe_float(row.get("close"))
        target["volume"] = safe_float(target.get("volume")) + safe_float(row.get("volume"))
    return [groups[key] for key in sorted(groups)]


def calculate_kdj(rows: Sequence[Dict[str, Any]], period: int = 9) -> List[Dict[str, float]]:
    if period <= 1:
        raise ValueError("kdj period must be greater than 1")
    result: List[Dict[str, float]] = []
    k = 50.0
    d = 50.0
    for idx, row in enumerate(rows):
        start = max(0, idx - period + 1)
        window = rows[start:idx + 1]
        highest = max(safe_float(x.get("high")) for x in window)
        lowest = min(safe_float(x.get("low")) for x in window)
        close = safe_float(row.get("close"))
        rsv = 50.0 if highest <= lowest else (close - lowest) / (highest - lowest) * 100.0
        k = (2.0 / 3.0) * k + (1.0 / 3.0) * rsv
        d = (2.0 / 3.0) * d + (1.0 / 3.0) * k
        j = 3.0 * k - 2.0 * d
        result.append({"k": k, "d": d, "j": j, "rsv": rsv})
    return result


def moving_average(rows: Sequence[Dict[str, Any]], window: int, end_index: int) -> Optional[float]:
    if window <= 0 or end_index < window - 1:
        return None
    subset = rows[end_index - window + 1:end_index + 1]
    closes = [safe_float(row.get("close")) for row in subset]
    if len(closes) != window or any(v <= 0 for v in closes):
        return None
    return sum(closes) / float(window)


def build_indicator_rows(symbol: str, daily_rows: Sequence[Dict[str, Any]], kdj_period: int = 9) -> List[Dict[str, Any]]:
    daily = normalize_daily_klines(daily_rows)
    if not daily:
        return []
    daily_kdj = calculate_kdj(daily, kdj_period)
    weekly = build_weekly_klines(daily)
    weekly_kdj = calculate_kdj(weekly, kdj_period) if weekly else []
    weekly_by_key: Dict[Tuple[int, int], Dict[str, float]] = {}
    for idx, row in enumerate(weekly):
        if idx >= len(weekly_kdj):
            continue
        try:
            dt = date.fromisoformat(str(row.get("date")))
            year, week, _ = dt.isocalendar()
            weekly_by_key[(year, week)] = weekly_kdj[idx]
        except Exception:
            continue

    rows: List[Dict[str, Any]] = []
    for idx, row in enumerate(daily):
        week_value = None
        try:
            dt = date.fromisoformat(str(row.get("date")))
            year, week, _ = dt.isocalendar()
            week_value = weekly_by_key.get((year, week))
        except Exception:
            week_value = None

        day_value = daily_kdj[idx]
        rows.append({
            "symbol": symbol.upper(),
            "date": row.get("date"),
            "close": safe_float(row.get("close")),
            "ma30": moving_average(daily, 30, idx),
            "ma60": moving_average(daily, 60, idx),
            "kdj_period": kdj_period,
            "daily_k": day_value.get("k"),
            "daily_d": day_value.get("d"),
            "daily_j": day_value.get("j"),
            "weekly_k": week_value.get("k") if week_value else None,
            "weekly_d": week_value.get("d") if week_value else None,
            "weekly_j": week_value.get("j") if week_value else None,
            "source": "computed",
        })
    return rows
