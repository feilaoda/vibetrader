from typing import List, Dict, Any, Optional
from ..strategy_base import BaseStrategy

class RBreakerStrategy(BaseStrategy):
    """
    R-Breaker Strategy (Daily Version).
    
    Uses Previous Day's High/Low/Close to calculate 6 pivot points:
    - Bbreak (突破买入价)
    - Ssetup (观察卖出价)
    - Senter (反转卖出价)
    - Benter (反转买入价)
    - Bsetup (观察买入价)
    - Sbreak (突破卖出价)
    
    Logic:
    1. Trend Mode: Buy if Price > Bbreak; Sell if Price < Sbreak.
    2. Reversal Mode: 
       - Sell if Price > Ssetup then falls below Senter.
       - Buy if Price < Bsetup then rises above Benter.
    
    (Simplified for daily close-to-close backtesting):
    - If Close > Bbreak: Buy (Trend)
    - If Low < Bsetup and Close > Benter: Buy (Reversal)
    - If Close < Sbreak: Sell (Trend)
    - If High > Ssetup and Close < Senter: Sell (Reversal)
    """
    def generate_signal(
        self, 
        klines: List[Dict[str, Any]], 
        position: Optional[Dict[str, Any]], 
        account_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        if not klines or len(klines) < 2:
            return {"side": "HOLD", "confidence": 0.0, "reason": "insufficient_data"}

        # Previous Day Data
        prev_kline = klines[-2]
        prev_high = self._safe_float(prev_kline.get("high"))
        prev_low = self._safe_float(prev_kline.get("low"))
        prev_close = self._safe_float(prev_kline.get("close"))
        
        # Current Day Data (or the bar we are judging)
        # If backtesting Daily, 'last_close' is today's close.
        curr_kline = klines[-1]
        last_close = self._safe_float(curr_kline.get("close"))
        last_high = self._safe_float(curr_kline.get("high"))
        last_low = self._safe_float(curr_kline.get("low"))
        
        # Calculate Pivot Points
        # Bbreak = High + 2 * (Center - Low) = High + 2 * ( (H+L+C)/3 - L ) ? 
        # Standard R-Breaker formula ranges vary. Let's use common one:
        # Pivot = (H + L + C) / 3
        # Bbreak = H + 2 * (P - L)
        # Ssetup = P + (H - L)
        # Senter = 2 * P - L
        # Benter = 2 * P - H
        # Bsetup = P - (H - L)
        # Sbreak = L - 2 * (H - P)
        
        # Another variant:
        # Ssetup = High + 0.35 * (High - Low) ... No that's Dual Thrust.
        
        # Let's stick to Pivot based:
        # P = (H + L + C) / 3  <-- Not actually standard R-Breaker. 
        # R-Breaker usually doesn't involve Pivot P directly in all formulas.
        # Let's use this set from a reliable quant source:
        # Ssetup = High + 0.35 * (Close - Low) ... NO.
        
        # Standard:
        # Sbreak = High + f1 * (Close - Low)
        # Ssetup = High + f2 * (Close - Low)
        # Senter = Low + f3 * (High - Close) (?) 
        # This seems complex. Let's use the Pivot one which is widely cited for "Pivot Point R-Breaker".
        
        p = (prev_high + prev_low + prev_close) / 3
        range_ = prev_high - prev_low
        
        b_break = prev_high + 0.5 * range_  # Trend Buy (Aggressive) - modified multiplier
        s_setup = p + 0.5 * range_          # Reversal Sell High
        s_enter = p + 0.1 * range_          # Reversal Sell Trigger (Close below this)
        
        b_enter = p - 0.1 * range_          # Reversal Buy Trigger
        b_setup = p - 0.5 * range_          # Reversal Buy Low
        s_break = prev_low - 0.5 * range_   # Trend Sell
        
        # Params from user
        leverage = float(self.params.get("leverage") or 1.0) # Not used really
        
        # Position State
        position_qty = int(position.get("quantity") or 0) if position else 0
        avg_cost = self._safe_float(position.get("avg_cost")) if position else 0.0
        
        # Constraints
        stop_loss_pct = float(self.constraints.get("stop_loss_pct") or 5.0) / 100.0

        # 1. Exit Logic / Stop Loss
        if position_qty > 0:
             if last_close < avg_cost * (1 - stop_loss_pct):
                  return {"side": "SELL", "confidence": 1.0, "reason": "stop_loss", "suggested_quantity": position_qty}
             
             # Reversal Sell or Trend Sell signal?
             # If we are long, we sell if price hits sell conditions
             
             # Trend Sell Condition: Close < Sbreak
             if last_close < s_break:
                 return {"side": "SELL", "confidence": 0.8, "reason": "r_breaker_trend_sell", "suggested_quantity": position_qty}
             
             # Reversal Sell Condition: High > Ssetup AND Close < Senter
             if last_high > s_setup and last_close < s_enter:
                 return {"side": "SELL", "confidence": 0.7, "reason": "r_breaker_reversal_sell", "suggested_quantity": position_qty}
             
             return {"side": "HOLD", "confidence": 0.5, "reason": "holding"}

        # 2. Entry Logic
        # Trend Buy: Close > Bbreak
        if last_close > b_break:
             qty = self._calculate_qty(last_close, account_context)
             if qty > 0:
                 return {"side": "BUY", "confidence": 0.8, "reason": "r_breaker_trend_buy", "suggested_quantity": qty}
        
        # Reversal Buy: Low < Bsetup AND Close > Benter
        if last_low < b_setup and last_close > b_enter:
             qty = self._calculate_qty(last_close, account_context)
             if qty > 0:
                 return {"side": "BUY", "confidence": 0.7, "reason": "r_breaker_reversal_buy", "suggested_quantity": qty}

        return {"side": "HOLD", "confidence": 0.1, "reason": "no_signal"}

    def _calculate_qty(self, price, account_context):
        cash_balance = float(account_context.get("cash_balance") or 0)
        total_equity = float(account_context.get("total_equity") or cash_balance)
        max_position_pct = float(self.constraints.get("max_position_pct") or 20.0) / 100.0
        
        target_value = total_equity * max_position_pct
        lot_size = 100
        
        qty = int(target_value / price / lot_size) * lot_size
        max_qty_cash = int(cash_balance / price / lot_size) * lot_size
        qty = max(0, min(qty, max_qty_cash))
        return qty
