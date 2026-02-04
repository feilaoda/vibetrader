import json
from typing import Any, Dict, List, Optional


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _avg(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def compute_baseline_signal(
    klines: List[Dict[str, Any]],
    position: Optional[Dict[str, Any]],
    params: Dict[str, Any],
    constraints: Dict[str, Any],
    account_context: Dict[str, Any]
) -> Dict[str, Any]:
    if not klines or len(klines) < 30:
        return {
            "side": "HOLD",
            "confidence": 0.0,
            "reason": "insufficient_kline_data",
            "suggested_quantity": 0
        }

    closes = [_safe_float(k.get("close")) for k in klines]
    highs = [_safe_float(k.get("high")) for k in klines]
    last_close = closes[-1]
    if last_close <= 0:
        return {
            "side": "HOLD",
            "confidence": 0.0,
            "reason": "invalid_last_close",
            "suggested_quantity": 0
        }

    ma_short = int(params.get("ma_short") or 5)
    ma_long = int(params.get("ma_long") or 20)
    momentum_days = int(params.get("momentum_days") or 20)
    breakout_window = int(params.get("breakout_window") or 20)

    lookback = max(ma_long, momentum_days, breakout_window) + 1
    if len(closes) < lookback:
        return {
            "side": "HOLD",
            "confidence": 0.0,
            "reason": "insufficient_lookback",
            "suggested_quantity": 0
        }

    ma_s = _avg(closes[-ma_short - 1:-1])
    ma_l = _avg(closes[-ma_long - 1:-1])
    momentum = closes[-1] / closes[-momentum_days - 1] - 1 if closes[-momentum_days - 1] > 0 else 0
    recent_high = max(highs[-breakout_window - 1:-1]) if len(highs) >= breakout_window + 1 else max(highs[:-1])

    position_qty = int(position.get("quantity") or 0) if position else 0
    avg_cost = _safe_float(position.get("avg_cost")) if position else 0.0

    stop_loss_pct = float(constraints.get("stop_loss_pct") or 5.0) / 100.0
    take_profit_pct = float(constraints.get("take_profit_pct") or 15.0) / 100.0

    if position_qty > 0 and avg_cost > 0:
        if last_close <= avg_cost * (1 - stop_loss_pct):
            return {
                "side": "SELL",
                "confidence": 0.7,
                "reason": "stop_loss_triggered",
                "suggested_quantity": position_qty
            }
        if last_close >= avg_cost * (1 + take_profit_pct):
            return {
                "side": "SELL",
                "confidence": 0.6,
                "reason": "take_profit_triggered",
                "suggested_quantity": position_qty
            }
        if last_close < ma_l:
            return {
                "side": "SELL",
                "confidence": 0.55,
                "reason": "below_ma_long",
                "suggested_quantity": position_qty
            }
        return {
            "side": "HOLD",
            "confidence": 0.4,
            "reason": "hold_position",
            "suggested_quantity": 0
        }

    trend_ok = last_close > ma_l and ma_s > ma_l
    breakout_ok = last_close >= recent_high
    momentum_ok = momentum > 0

    if trend_ok and breakout_ok and momentum_ok:
        cash_balance = float(account_context.get("cash_balance") or 0)
        total_equity = float(account_context.get("total_equity") or cash_balance)
        max_positions = int(constraints.get("max_positions") or 8)
        max_position_pct = float(constraints.get("max_position_pct") or 20.0) / 100.0
        target_value = min(max_position_pct, 1.0 / max_positions) * total_equity
        lot = 100
        qty = int(target_value / last_close / lot) * lot
        max_qty = int(cash_balance / last_close / lot) * lot
        qty = max(0, min(qty, max_qty))
        return {
            "side": "BUY",
            "confidence": 0.65,
            "reason": "trend_breakout_momentum",
            "suggested_quantity": qty
        }

    return {
        "side": "HOLD",
        "confidence": 0.25,
        "reason": "no_signal",
        "suggested_quantity": 0
    }


def serialize_baseline(baseline: Dict[str, Any]) -> str:
    try:
        return json.dumps(baseline, ensure_ascii=False)
    except Exception:
        return str(baseline)

