from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class StrategyConfig:
    short_window: int = 20
    long_window: int = 60
    momentum_window: int = 20
    vol_window: int = 20
    max_volatility: float = 0.03
    min_momentum: float = 0.0
    top_k: int = 5


@dataclass(frozen=True)
class RiskConfig:
    max_position_weight: float = 0.10
    max_gross_exposure: float = 1.0


def _window(values: List[Optional[float]], end_idx: int, size: int) -> Optional[List[float]]:
    start = end_idx - size + 1
    if start < 0:
        return None
    chunk = values[start : end_idx + 1]
    if any(v is None for v in chunk):
        return None
    return [float(v) for v in chunk]  # type: ignore[arg-type]


def _daily_returns(prices: Iterable[float]) -> List[float]:
    seq = list(prices)
    if len(seq) < 2:
        return []
    out: List[float] = []
    for i in range(1, len(seq)):
        prev = seq[i - 1]
        curr = seq[i]
        if prev <= 0:
            return []
        out.append(curr / prev - 1.0)
    return out


def rank_candidates(
    close_by_symbol: Dict[str, List[Optional[float]]],
    date_index: int,
    strategy: StrategyConfig,
) -> List[Tuple[str, float]]:
    if strategy.short_window <= 0 or strategy.long_window <= 0:
        raise ValueError("short_window and long_window must be > 0")
    if strategy.short_window > strategy.long_window:
        raise ValueError("short_window must be <= long_window")

    scores: List[Tuple[str, float]] = []

    for symbol, series in close_by_symbol.items():
        short_prices = _window(series, date_index, strategy.short_window)
        long_prices = _window(series, date_index, strategy.long_window)
        momentum_prices = _window(series, date_index, strategy.momentum_window + 1)
        vol_prices = _window(series, date_index, strategy.vol_window + 1)

        if not short_prices or not long_prices or not momentum_prices or not vol_prices:
            continue

        short_ma = mean(short_prices)
        long_ma = mean(long_prices)
        if short_ma <= long_ma:
            continue

        momentum = momentum_prices[-1] / momentum_prices[0] - 1.0
        if momentum < strategy.min_momentum:
            continue

        volatility_returns = _daily_returns(vol_prices)
        if not volatility_returns:
            continue

        realized_vol = (sum(r * r for r in volatility_returns) / len(volatility_returns)) ** 0.5
        if realized_vol > strategy.max_volatility:
            continue

        scores.append((symbol, momentum))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


def allocate_equal_weights(
    ranked_symbols: List[Tuple[str, float]],
    top_k: int,
    risk: RiskConfig,
) -> Dict[str, float]:
    if top_k <= 0:
        return {}
    picked = ranked_symbols[:top_k]
    if not picked:
        return {}

    n = len(picked)
    raw_weight = risk.max_gross_exposure / n
    weight = min(raw_weight, risk.max_position_weight)
    return {symbol: weight for symbol, _ in picked}


def select_positions(
    close_by_symbol: Dict[str, List[Optional[float]]],
    date_index: int,
    strategy: StrategyConfig,
    risk: RiskConfig,
) -> Dict[str, float]:
    ranked = rank_candidates(close_by_symbol, date_index, strategy)
    return allocate_equal_weights(ranked, strategy.top_k, risk)
