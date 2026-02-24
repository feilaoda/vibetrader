from typing import List, Dict, Any, Optional
from ..strategy_base import BaseStrategy

class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy (RSI Based).
    Logic:
    1. Buy: RSI < low_threshold (e.g. 30)
    2. Sell: RSI > high_threshold (e.g. 70) or Stop Loss
    """
    def generate_signal(
        self, 
        klines: List[Dict[str, Any]], 
        position: Optional[Dict[str, Any]], 
        account_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        if not klines or len(klines) < 30:
            return {"side": "HOLD", "confidence": 0.0, "reason": "insufficient_data"}

        closes = [self._safe_float(k.get("close")) for k in klines]
        last_close = closes[-1]
        
        # Parameters
        rsi_period = int(self.params.get("rsi_period") or 14)
        rsi_low = float(self.params.get("rsi_low") or 30)
        rsi_high = float(self.params.get("rsi_high") or 70)
        
        if len(closes) < rsi_period + 1:
            return {"side": "HOLD", "confidence": 0.0, "reason": "insufficient_lookback"}

        # Calculate RSI
        rsi = self._calculate_rsi(closes, rsi_period)
        last_rsi = rsi[-1]

        # Position State
        position_qty = int(position.get("quantity") or 0) if position else 0
        avg_cost = self._safe_float(position.get("avg_cost")) if position else 0.0
        
        # Constraints
        stop_loss_pct = float(self.constraints.get("stop_loss_pct") or 5.0) / 100.0
        take_profit_pct = float(self.constraints.get("take_profit_pct") or 10.0) / 100.0

        # 1. Exit Logic
        if position_qty > 0:
            # PnL Check
            if avg_cost > 0:
                if last_close <= avg_cost * (1 - stop_loss_pct):
                    return {
                        "side": "SELL",
                        "confidence": 0.9,
                        "reason": "stop_loss",
                        "suggested_quantity": position_qty
                    }
                # Optional: Fixed take profit, or wait for RSI Overbought
                if last_close >= avg_cost * (1 + take_profit_pct):
                     # Wait, Mean reversion often aims for RSI high, but fixed TP is safe
                     pass 

            # RSI Overbought Exit
            if last_rsi > rsi_high:
                 return {
                    "side": "SELL",
                    "confidence": 0.8,
                    "reason": f"rsi_overbought ({last_rsi:.1f} > {rsi_high})",
                    "suggested_quantity": position_qty
                }
            
            return {"side": "HOLD", "confidence": 0.5, "reason": "holding"}

        # 2. Entry Logic
        if last_rsi < rsi_low:
             # Sizing
            cash_balance = float(account_context.get("cash_balance") or 0)
            total_equity = float(account_context.get("total_equity") or cash_balance)
            max_position_pct = float(self.constraints.get("max_position_pct") or 20.0) / 100.0
            
            target_value = total_equity * max_position_pct
            lot_size = 100
            
            qty = int(target_value / last_close / lot_size) * lot_size
            max_qty_cash = int(cash_balance / last_close / lot_size) * lot_size
            qty = max(0, min(qty, max_qty_cash))
            
            if qty > 0:
                return {
                    "side": "BUY",
                    "confidence": 0.7,
                    "reason": f"rsi_oversold ({last_rsi:.1f} < {rsi_low})",
                    "suggested_quantity": qty
                }

        return {"side": "HOLD", "confidence": 0.1, "reason": "no_signal"}

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> List[float]:
        if len(prices) < period + 1:
            return []
        
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsis = []
        # Simple RS calculation for robustness (not full EMA smoothing just to keep it simple for now)
        # Or standard Wilder's smoothing
        
        # Initial RSI
        if avg_loss == 0:
            rsis.append(100)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))
            
        # Subsequent
        for i in range(period, len(deltas)):
            change = deltas[i]
            gain = change if change > 0 else 0
            loss = -change if change < 0 else 0
            
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            
            if avg_loss == 0:
                rsis.append(100)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100 - (100 / (1 + rs)))
                
        return rsis
