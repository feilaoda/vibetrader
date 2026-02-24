from typing import List, Dict, Any, Optional
import math
from ..strategy_base import BaseStrategy

class BollingerBandSqueezeStrategy(BaseStrategy):
    """
    Bollinger Band Squeeze Strategy.
    
    Logic:
    1. Squeeze Detection: Bandwidth (Upper - Lower) / Middle is low (relative to history).
       - Or simply: Bandwidth < Threshold (e.g., 10% or Historical Low Percentile).
    2. Breakout: Close > Upper Band.
    3. Exit: Close < Middle Band (Trend Reversal).
    
    Params:
    - period: MA period (default 20).
    - std_dev: Standard Deviation multiplier (default 2.0).
    - squeeze_threshold: Bandwidth percentile or absolute value (e.g. 0.10 for 10% width).
      Here we use a simpler 'Squeeze Filter': Bandwidth must be narrower than its N-day MA * factor.
    """
    def generate_signal(
        self, 
        klines: List[Dict[str, Any]], 
        position: Optional[Dict[str, Any]], 
        account_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        period = int(self.params.get("period") or 20)
        std_dev_mult = float(self.params.get("std_dev") or 2.0)
        
        if not klines or len(klines) < period + 1:
            return {"side": "HOLD", "confidence": 0.0, "reason": "insufficient_data"}

        closes = [self._safe_float(k.get("close")) for k in klines]
        last_close = closes[-1]
        
        # Calculate Bollinger Bands
        # We need history for Bandwidth analysis to detect Squeeze
        # Let's verify Squeeze on the PREVIOUS bar (to be ready for breakout)
        # Or current bar. The breakout happens ON the squeeze or immediately after.
        
        # Calculate BB for last bar
        ma = sum(closes[-period:]) / period
        variance = sum([((x - ma) ** 2) for x in closes[-period:]]) / period
        std_dev = math.sqrt(variance)
        
        upper = ma + std_dev_mult * std_dev
        lower = ma - std_dev_mult * std_dev
        middle = ma
        
        bandwidth = (upper - lower) / middle if middle > 0 else 0
        
        # Check Squeeze Condition history (using last 20 days bandwidth to compare?)
        # A simple squeeze definition: Bandwidth reaches 6-month low.
        # Simplified: Bandwidth < 0.10 (10% fluctuation range)
        squeeze_threshold = float(self.params.get("squeeze_threshold") or 0.10)
        # Or relative squeeze: current bandwidth < average bandwidth * 0.7
        # Let's use relative for robustness across stocks.
        
        # Need historical bandwidths
        # This is expensive to calc inside loop if optimized.
        # For this logic, let's just use absolute threshold or basic relative check.
        # Let's use: (Upper - Lower) / Middle < 0.15 (15% Max width allowed for Squeeze)
        # Tighter squeeze = bigger explosion.
        is_squeezing = bandwidth < squeeze_threshold
        
        # Position State
        position_qty = int(position.get("quantity") or 0) if position else 0
        avg_cost = self._safe_float(position.get("avg_cost")) if position else 0.0
        
        stop_loss_pct = float(self.constraints.get("stop_loss_pct") or 5.0) / 100.0

        # 1. Exit Logic
        if position_qty > 0:
            # Stop Loss
            if last_close < avg_cost * (1 - stop_loss_pct):
                 return {
                    "side": "SELL",
                    "confidence": 1.0,
                    "reason": f"bb_stop_loss ({last_close} < {avg_cost*(1-stop_loss_pct):.2f})",
                    "suggested_quantity": position_qty
                }
            
            # Profit/Trend Exit: Close < Middle Band
            if last_close < middle:
                 return {
                    "side": "SELL",
                    "confidence": 0.8,
                    "reason": f"bb_trend_end ({last_close} < {middle:.2f})",
                    "suggested_quantity": position_qty
                }
            
            return {"side": "HOLD", "confidence": 0.5, "reason": "riding_trend"}

        # 2. Entry Logic
        # Condition: Breakout detected?
        # A breakout is valid if we were squeezing recently.
        # But here we just check: Close > Upper.
        # To avoid chasing high, we want: Close > Upper AND (Width < Threshold OR Previous Width < Threshold)
        # Let's check if previous bar detected squeeze.
        
        # Recalc prev bar BB
        prev_closes = closes[-period-1:-1]
        if len(prev_closes) == period:
            p_ma = sum(prev_closes) / period
            p_var = sum([((x - p_ma) ** 2) for x in prev_closes]) / period
            p_std = math.sqrt(p_var)
            p_upper = p_ma + std_dev_mult * p_std
            p_lower = p_ma - std_dev_mult * p_std
            p_middle = p_ma
            p_bw = (p_upper - p_lower) / p_middle if p_middle > 0 else 0
            
            prev_squeezing = p_bw < squeeze_threshold
            
            # Entry: Close > Upper AND (Currently Squeezing OR Previously Squeezing)
            # Actually, when it breaks out, bandwidth expands, so it might NOT be squeezing NOW.
            # So we rely on "Recently Squeezing" (e.g. Prev Bar).
            
            if last_close > upper and prev_squeezing:
                 qty = self._calculate_qty(last_close, account_context)
                 if qty > 0:
                     return {
                        "side": "BUY",
                        "confidence": 0.9,
                        "reason": f"bb_squeeze_breakout (PrevBW {p_bw:.3f} < {squeeze_threshold})",
                        "suggested_quantity": qty
                    }

        return {"side": "HOLD", "confidence": 0.1, "reason": "no_signal"}

    def _calculate_qty(self, price, account_context):
        cash_balance = float(account_context.get("cash_balance") or 0)
        # Risk management: usually fixed size or % of equity
        # Let's use max position pct
        total_equity = float(account_context.get("total_equity") or cash_balance)
        max_pos_pct = float(self.constraints.get("max_position_pct") or 20.0) / 100.0
        
        target_val = total_equity * max_pos_pct
        lot_size = 100
        qty = int(target_val / price / lot_size) * lot_size
        
        max_qty_cash = int(cash_balance / price / lot_size) * lot_size
        qty = max(0, min(qty, max_qty_cash))
        return qty
