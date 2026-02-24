from typing import List, Dict, Any, Optional
from ..strategy_base import BaseStrategy

class TrendFollowingStrategy(BaseStrategy):
    """
    Classic Trend Following Strategy.
    Logic:
    1. Trend: Short MA > Long MA and Close > Long MA
    2. Breakout: Close > Recent High (N days)
    3. Momentum: Recent return > 0
    4. Exit: Stop loss, Take profit, or Close < Long MA
    """
    def generate_signal(
        self, 
        klines: List[Dict[str, Any]], 
        position: Optional[Dict[str, Any]], 
        account_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        if not klines or len(klines) < 30:
            return {
                "side": "HOLD",
                "confidence": 0.0,
                "reason": "insufficient_kline_data",
                "suggested_quantity": 0
            }

        closes = [self._safe_float(k.get("close")) for k in klines]
        highs = [self._safe_float(k.get("high")) for k in klines]
        last_close = closes[-1]
        
        if last_close <= 0:
            return {"side": "HOLD", "confidence": 0.0, "reason": "invalid_last_close"}

        # Parameters
        ma_short = int(self.params.get("ma_short") or 5)
        ma_long = int(self.params.get("ma_long") or 20)
        momentum_days = int(self.params.get("momentum_days") or 20)
        breakout_window = int(self.params.get("breakout_window") or 20)

        lookback = max(ma_long, momentum_days, breakout_window) + 1
        if len(closes) < lookback:
            return {
                "side": "HOLD", 
                "confidence": 0.0, 
                "reason": "insufficient_lookback"
            }

        # Indicators
        ma_s = self._avg(closes[-ma_short - 1:-1]) # Prev bar MA (to avoid repainting if using closed bars, but commonly we use current)
        # Wait, usually we use current bar close for signal if we are running AFTER close. 
        # If running real-time, we might use prev bar. 
        # Let's stick to using data up to index -1 (completed bars) if we assume 'klines' includes today's partial? 
        # API usually returns completed days + maybe today. 
        # Let's assume input klines includes the latest data point we want to judge on.
        # But if it's daily data and we run at night, the last kline IS the today's close.
        # Let's use the last available points.
        
        ma_s = self._avg(closes[-ma_short:])
        ma_l = self._avg(closes[-ma_long:])
        
        # Momentum
        prev_mom_price = closes[-momentum_days - 1] if len(closes) > momentum_days else closes[0]
        momentum = (last_close / prev_mom_price - 1) if prev_mom_price > 0 else 0
        
        # Breakout (excluding current bar for high check? or including?)
        # Conventionally: Close > Max(High of last N days excluding today)
        recent_high_window = highs[-breakout_window - 1:-1]
        recent_high = max(recent_high_window) if recent_high_window else highs[-1]

        # Position State
        position_qty = int(position.get("quantity") or 0) if position else 0
        avg_cost = self._safe_float(position.get("avg_cost")) if position else 0.0

        # Constraints
        stop_loss_pct = float(self.constraints.get("stop_loss_pct") or 5.0) / 100.0
        take_profit_pct = float(self.constraints.get("take_profit_pct") or 15.0) / 100.0

        # 1. Check Exit Signals if holding
        if position_qty > 0 and avg_cost > 0:
            # Stop Loss
            if last_close <= avg_cost * (1 - stop_loss_pct):
                return {
                    "side": "SELL",
                    "confidence": 0.8,
                    "reason": f"stop_loss_triggered (Close {last_close} < Cost {avg_cost} * {1-stop_loss_pct:.2f})",
                    "suggested_quantity": position_qty
                }
            # Take Profit
            if last_close >= avg_cost * (1 + take_profit_pct):
                return {
                    "side": "SELL",
                    "confidence": 0.7,
                    "reason": f"take_profit_triggered (Close {last_close} > Cost {avg_cost} * {1+take_profit_pct:.2f})",
                    "suggested_quantity": position_qty
                }
            # Trend Reversal (Exit condition)
            if last_close < ma_l:
                return {
                    "side": "SELL",
                    "confidence": 0.6,
                    "reason": "trend_reversal (Below MA Long)",
                    "suggested_quantity": position_qty
                }
            
            # Hold
            return {
                "side": "HOLD",
                "confidence": 0.5,
                "reason": "holding_position"
            }

        # 2. Check Entry Signals if flat
        trend_ok = last_close > ma_l and ma_s > ma_l
        breakout_ok = last_close >= recent_high
        momentum_ok = momentum > 0

        if trend_ok and breakout_ok and momentum_ok:
            # Sizing Logic
            cash_balance = float(account_context.get("cash_balance") or 0)
            total_equity = float(account_context.get("total_equity") or cash_balance)
            
            max_positions = int(self.constraints.get("max_positions") or 8)
            max_position_pct = float(self.constraints.get("max_position_pct") or 20.0) / 100.0
            
            # Target 1/N of equity or max_pct, whichever is smaller
            target_value = min(max_position_pct, 1.0 / max_positions) * total_equity
            
            # Adjust for lot size (100 for A-share)
            lot_size = 100
            
            # Calculate quantity
            qty = int(target_value / last_close / lot_size) * lot_size
            
            # Check cash constraint
            max_qty_cash = int(cash_balance / last_close / lot_size) * lot_size
            qty = max(0, min(qty, max_qty_cash))
            
            if qty > 0:
                return {
                    "side": "BUY",
                    "confidence": 0.75,
                    "reason": "trend_breakout_confirmed",
                    "suggested_quantity": qty
                }
            else:
                return {
                    "side": "HOLD",
                    "confidence": 0.0,
                    "reason": "insufficient_cash_for_lot"
                }

        return {
            "side": "HOLD",
            "confidence": 0.1,
            "reason": "no_signal"
        }
