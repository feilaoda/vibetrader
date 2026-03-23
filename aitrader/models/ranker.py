from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _std(values: Iterable[float]) -> float:
    seq = list(values)
    if not seq:
        return 0.0
    avg = mean(seq)
    return (sum((v - avg) ** 2 for v in seq) / len(seq)) ** 0.5


def _window(values: List[Optional[float]], end_idx: int, size: int) -> Optional[List[float]]:
    start = end_idx - size + 1
    if start < 0:
        return None
    chunk = values[start : end_idx + 1]
    if any(v is None for v in chunk):
        return None
    return [float(v) for v in chunk]  # type: ignore[arg-type]


def _features_for_symbol(
    close_by_symbol: Dict[str, List[Optional[float]]],
    symbol: str,
    idx: int,
) -> Optional[List[float]]:
    prices = close_by_symbol.get(symbol)
    if not prices:
        return None

    p_now = prices[idx]
    p_1 = prices[idx - 1] if idx - 1 >= 0 else None
    p_5 = prices[idx - 5] if idx - 5 >= 0 else None
    p_20 = prices[idx - 20] if idx - 20 >= 0 else None
    p_60 = prices[idx - 60] if idx - 60 >= 0 else None

    if None in (p_now, p_1, p_5, p_20, p_60):
        return None
    if float(p_1) <= 0 or float(p_5) <= 0 or float(p_20) <= 0 or float(p_60) <= 0:
        return None

    w20 = _window(prices, idx, 20)
    w60 = _window(prices, idx, 60)
    if not w20 or not w60:
        return None

    returns_20 = []
    for j in range(1, len(w20)):
        prev = w20[j - 1]
        curr = w20[j]
        if prev <= 0:
            return None
        returns_20.append(curr / prev - 1.0)

    p_now_f = float(p_now)
    p_1_f = float(p_1)
    p_5_f = float(p_5)
    p_20_f = float(p_20)
    p_60_f = float(p_60)
    ma20 = mean(w20)
    ma60 = mean(w60)

    return [
        p_now_f / p_1_f - 1.0,
        p_now_f / p_5_f - 1.0,
        p_now_f / p_20_f - 1.0,
        p_now_f / p_60_f - 1.0,
        ma20 / ma60 - 1.0 if ma60 > 0 else 0.0,
        _std(returns_20),
    ]


class _TreeNode:
    __slots__ = ("value", "feature_idx", "threshold", "left", "right")

    def __init__(
        self,
        value: float,
        feature_idx: Optional[int] = None,
        threshold: Optional[float] = None,
        left: Optional["_TreeNode"] = None,
        right: Optional["_TreeNode"] = None,
    ):
        self.value = value
        self.feature_idx = feature_idx
        self.threshold = threshold
        self.left = left
        self.right = right


class SimpleDecisionTreeRegressor:
    def __init__(self, max_depth: int = 3, min_samples_leaf: int = 20, max_thresholds_per_feature: int = 16):
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.max_thresholds_per_feature = max_thresholds_per_feature
        self.root: Optional[_TreeNode] = None

    def fit(self, x: List[List[float]], y: List[float]) -> None:
        if not x or not y or len(x) != len(y):
            raise ValueError("invalid training data")
        self.root = self._build(x, y, depth=0)

    def predict_one(self, row: List[float]) -> float:
        if self.root is None:
            return 0.0
        node = self.root
        while node.feature_idx is not None and node.threshold is not None:
            if row[node.feature_idx] <= node.threshold:
                if node.left is None:
                    break
                node = node.left
            else:
                if node.right is None:
                    break
                node = node.right
        return node.value

    def _build(self, x: List[List[float]], y: List[float], depth: int) -> _TreeNode:
        node_value = sum(y) / len(y)
        node = _TreeNode(value=node_value)

        if depth >= self.max_depth or len(x) < self.min_samples_leaf * 2:
            return node
        if max(y) - min(y) < 1e-9:
            return node

        split = self._best_split(x, y)
        if split is None:
            return node

        f_idx, threshold, left_idx, right_idx = split
        left_x = [x[i] for i in left_idx]
        left_y = [y[i] for i in left_idx]
        right_x = [x[i] for i in right_idx]
        right_y = [y[i] for i in right_idx]

        node.feature_idx = f_idx
        node.threshold = threshold
        node.left = self._build(left_x, left_y, depth + 1)
        node.right = self._build(right_x, right_y, depth + 1)
        return node

    def _best_split(
        self,
        x: List[List[float]],
        y: List[float],
    ) -> Optional[Tuple[int, float, List[int], List[int]]]:
        n = len(x)
        feature_count = len(x[0])
        best_score = float("inf")
        best: Optional[Tuple[int, float, List[int], List[int]]] = None

        for f_idx in range(feature_count):
            values = sorted(set(row[f_idx] for row in x))
            if len(values) <= 2:
                thresholds = values
            else:
                interior = values[1:-1]
                if len(interior) > self.max_thresholds_per_feature:
                    step = max(1, len(interior) // self.max_thresholds_per_feature)
                    thresholds = interior[::step][: self.max_thresholds_per_feature]
                else:
                    thresholds = interior

            for threshold in thresholds:
                left_idx = [i for i in range(n) if x[i][f_idx] <= threshold]
                right_idx = [i for i in range(n) if x[i][f_idx] > threshold]
                if len(left_idx) < self.min_samples_leaf or len(right_idx) < self.min_samples_leaf:
                    continue

                left_y = [y[i] for i in left_idx]
                right_y = [y[i] for i in right_idx]
                score = self._sse(left_y) + self._sse(right_y)
                if score < best_score:
                    best_score = score
                    best = (f_idx, threshold, left_idx, right_idx)

        return best

    @staticmethod
    def _sse(values: List[float]) -> float:
        if not values:
            return 0.0
        avg = sum(values) / len(values)
        return sum((v - avg) ** 2 for v in values)


@dataclass(frozen=True)
class AIRankerConfig:
    train_lookback_days: int = 180
    min_train_rows: int = 300
    max_train_rows: int = 1200
    max_depth: int = 3
    min_samples_leaf: int = 24
    max_thresholds_per_feature: int = 16
    candidate_pool_multiplier: int = 3
    retrain_interval_days: int = 20


class TreeRanker:
    def __init__(self, config: AIRankerConfig):
        self.config = config
        self._cached_model: Optional[SimpleDecisionTreeRegressor] = None
        self._last_train_idx: int = -10_000
        self._fallback_only: bool = True

    def rank(
        self,
        close_by_symbol: Dict[str, List[Optional[float]]],
        date_index: int,
        candidate_symbols: Sequence[str],
    ) -> List[Tuple[str, float]]:
        self._maybe_refit(close_by_symbol, date_index)
        model = self._cached_model

        scores: List[Tuple[str, float]] = []
        for symbol in candidate_symbols:
            feat = _features_for_symbol(close_by_symbol, symbol, date_index)
            if not feat:
                continue
            if model is None or self._fallback_only:
                # Keep behavior deterministic when train rows are insufficient.
                score = fallback_score(feat)
            else:
                score = model.predict_one(feat)
            scores.append((symbol, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def _maybe_refit(
        self,
        close_by_symbol: Dict[str, List[Optional[float]]],
        date_index: int,
    ) -> None:
        if self._cached_model is not None and date_index - self._last_train_idx < self.config.retrain_interval_days:
            return

        model, fallback_only = self._fit_model(close_by_symbol, date_index)
        self._cached_model = model
        self._fallback_only = fallback_only
        self._last_train_idx = date_index

    def _fit_model(
        self,
        close_by_symbol: Dict[str, List[Optional[float]]],
        date_index: int,
    ) -> Tuple[Optional[SimpleDecisionTreeRegressor], bool]:
        start_idx = max(60, date_index - self.config.train_lookback_days)
        x: List[List[float]] = []
        y: List[float] = []

        symbols = sorted(close_by_symbol.keys())
        for idx in range(start_idx, date_index):
            for symbol in symbols:
                feat = _features_for_symbol(close_by_symbol, symbol, idx)
                if not feat:
                    continue

                prices = close_by_symbol[symbol]
                p0 = prices[idx]
                p1 = prices[idx + 1] if idx + 1 < len(prices) else None
                if p0 is None or p1 is None or p0 <= 0:
                    continue

                x.append(feat)
                y.append(float(p1) / float(p0) - 1.0)

        if len(x) < self.config.min_train_rows:
            return None, True

        if len(x) > self.config.max_train_rows:
            x = x[-self.config.max_train_rows :]
            y = y[-self.config.max_train_rows :]

        tree = SimpleDecisionTreeRegressor(
            max_depth=self.config.max_depth,
            min_samples_leaf=self.config.min_samples_leaf,
            max_thresholds_per_feature=self.config.max_thresholds_per_feature,
        )
        tree.fit(x, y)
        return tree, False


def fallback_score(features: List[float]) -> float:
    ret_1, ret_5, ret_20, ret_60, ma_gap, vol_20 = features
    return (
        0.10 * ret_1
        + 0.25 * ret_5
        + 0.45 * ret_20
        + 0.20 * ret_60
        + 0.60 * ma_gap
        - 0.80 * vol_20
    )
