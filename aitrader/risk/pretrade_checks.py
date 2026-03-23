from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PreTradeChecksConfig:
    max_position_weight: float = 0.10
    max_gross_exposure: float = 1.0
    max_turnover: float = 1.2
    min_price: float = 1.0
    allow_short: bool = False


def run_pretrade_checks(
    target_weights: Dict[str, float],
    previous_weights: Dict[str, float],
    close_by_symbol: Dict[str, List[Optional[float]]],
    date_index: int,
    config: PreTradeChecksConfig,
) -> Tuple[Dict[str, float], List[str]]:
    sanitized: Dict[str, float] = {}
    notes: List[str] = []

    for symbol, raw_weight in target_weights.items():
        weight = float(raw_weight)
        if not config.allow_short and weight < 0:
            notes.append(f"drop_short:{symbol}")
            continue

        series = close_by_symbol.get(symbol)
        px = series[date_index] if series and date_index < len(series) else None
        if px is None or float(px) < config.min_price:
            notes.append(f"drop_invalid_price:{symbol}")
            continue

        if abs(weight) > config.max_position_weight:
            clipped = config.max_position_weight if weight >= 0 else -config.max_position_weight
            notes.append(f"clip_position:{symbol}:{weight:.4f}->{clipped:.4f}")
            weight = clipped

        if abs(weight) > 1e-12:
            sanitized[symbol] = weight

    gross = sum(abs(w) for w in sanitized.values())
    if gross > config.max_gross_exposure and gross > 0:
        scale = config.max_gross_exposure / gross
        for symbol in list(sanitized.keys()):
            sanitized[symbol] *= scale
        notes.append(f"scale_gross:{gross:.4f}->{config.max_gross_exposure:.4f}")

    turnover = _calc_turnover(previous_weights, sanitized)
    if turnover > config.max_turnover and turnover > 0:
        ratio = config.max_turnover / turnover
        interpolated: Dict[str, float] = {}
        universe = set(previous_weights.keys()) | set(sanitized.keys())
        for symbol in universe:
            prev = previous_weights.get(symbol, 0.0)
            tgt = sanitized.get(symbol, 0.0)
            new_weight = prev + (tgt - prev) * ratio
            if abs(new_weight) > 1e-12:
                interpolated[symbol] = new_weight
        sanitized = interpolated
        notes.append(f"cap_turnover:{turnover:.4f}->{config.max_turnover:.4f}")

    return sanitized, notes


def _calc_turnover(prev_weights: Dict[str, float], target_weights: Dict[str, float]) -> float:
    symbols = set(prev_weights.keys()) | set(target_weights.keys())
    return sum(abs(target_weights.get(s, 0.0) - prev_weights.get(s, 0.0)) for s in symbols)
