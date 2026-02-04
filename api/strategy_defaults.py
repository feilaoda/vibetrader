import json
from typing import Any, Dict

DEFAULT_OBJECTIVES: Dict[str, float] = {
    "annual_return_weight": 1.0,
    "max_drawdown_weight": 0.5,
    "sharpe_weight": 0.3,
    "turnover_weight": 0.1
}

DEFAULT_CONSTRAINTS: Dict[str, float] = {
    "max_drawdown_pct": 15.0,
    "max_position_pct": 20.0,
    "max_positions": 8,
    "max_daily_trades": 5,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 15.0
}

DEFAULT_PARAMS: Dict[str, float] = {
    "ma_short": 5,
    "ma_long": 20,
    "momentum_days": 20,
    "breakout_window": 20
}

DEFAULT_OPTIMIZATION: Dict[str, Any] = {
    "enabled": True,
    "auto_replace": True,
    "auto_tune_objectives": True,
    "auto_update_prompt": True,
    "eval_frequency": "daily",
    "backtest_years": 2,
    "train_months": 18,
    "val_months": 6,
    "min_improvement_pct": 5.0,
    "candidate_count": 8,
    "max_symbols": 30
}


def _merge_defaults(value: Any, default: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return default.copy()
    merged = default.copy()
    for key, val in value.items():
        merged[key] = val
    return merged


def normalize_objectives(value: Any) -> Dict[str, Any]:
    return _merge_defaults(value, DEFAULT_OBJECTIVES)


def normalize_constraints(value: Any) -> Dict[str, Any]:
    return _merge_defaults(value, DEFAULT_CONSTRAINTS)


def normalize_params(value: Any) -> Dict[str, Any]:
    return _merge_defaults(value, DEFAULT_PARAMS)


def normalize_optimization(value: Any) -> Dict[str, Any]:
    return _merge_defaults(value, DEFAULT_OPTIMIZATION)


def dumps_config(value: Any, default: Dict[str, Any]) -> str:
    merged = _merge_defaults(value, default)
    return json.dumps(merged, ensure_ascii=False)

