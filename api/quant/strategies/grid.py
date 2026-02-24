from typing import List, Dict, Any, Optional
from ..strategy_base import BaseStrategy

class GridStrategy(BaseStrategy):
    """
    Grid Trading Strategy.
    
    Logic:
    1. Define a Center Price (Base Price). Can be fixed or dynamic (e.g., MA).
    2. Create Grid Lines above and below Base Price with fixed interval (percentage).
    3. Buy when price crosses down a grid line.
    4. Sell when price crosses up a grid line.
    
    This implementation uses a Dynamic Center (MA) to adapt to trends, 
    making it a "Rolling Grid".
    """
    def __init__(self, params: Dict[str, Any] = None, constraints: Dict[str, Any] = None):
        super().__init__(params, constraints)
        self.grids = [] # List of {"price": float, "filled": bool, "type": "BUY"/"SELL"}
        self.last_grid_index = 0 # 0 is center. +1 is upper 1, -1 is lower 1.

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
        
        # Params
        grid_pct = float(self.params.get("grid_pct") or 1.0) / 100.0 # 1% grid
        grid_count = int(self.params.get("grid_limit") or 10) # 10 grids each side
        ma_period = int(self.params.get("center_ma_period") or 20)
        
        # Calculate Center Price
        if len(closes) < ma_period:
            return {"side": "HOLD", "confidence": 0.0, "reason": "insufficient_data_ma"}
            
        center_price = sum(closes[-ma_period:]) / ma_period
        
        # Position State
        position_qty = int(position.get("quantity") or 0) if position else 0
        
        # Sizing
        cash_balance = float(account_context.get("cash_balance") or 0)
        total_equity = float(account_context.get("total_equity") or cash_balance)
        
        # Per grid investment = Total Equity * Allocation / (Grid Count * 2) ?
        # Simplified: Per grid use fixed % of equity.
        per_grid_pct = float(self.constraints.get("per_grid_pct") or 2.0) / 100.0
        target_val = total_equity * per_grid_pct
        lot_size = 100
        qty_per_grid = int(target_val / last_close / lot_size) * lot_size
        
        if qty_per_grid <= 0:
             return {"side": "HOLD", "confidence": 0.0, "reason": "insufficient_cash_per_grid"}

        # Logic:
        # Determine relative position to center in terms of grid units
        # Relative = (Price - Center) / Center / GridPct
        # e.g. Price = 105, Center = 100, Grid = 1%. Relative = 5 / 100 / 0.01 = 5.
        # We are at +5 grid line.
        
        current_grid_idx = int((last_close - center_price) / center_price / grid_pct)
        
        # 1. Buy Logic (Dip)
        # If we moved DOWN from a previous higher grid index, we BUY.
        # But we need state. 
        # Making it stateless for simple backtesting:
        # We hold generic logic:
        # If Price < Center - N * Grid, we should hold N units (net).
        # Actually Grid strategy is about "Buying Low, Selling High".
        # Let's use a "Target Position" approach based on Grid Level.
        # At Center: Hold neutral position (e.g. 50% capacity? or 0?)
        # Let's say we start with 0.
        # Price drops to -1 Grid: Buy 1 unit.
        # Price drops to -2 Grid: Buy another unit (Total 2).
        # Price rises to -1 Grid: Sell 1 unit (Total 1).
        # Price rises to 0 Grid: Sell 1 unit (Total 0).
        # Price rises to +1 Grid: Short 1 unit? (We usually don't short stocks here, so Sell if we have inventory).
        
        # "Long-only Grid":
        # Assume max drop is 10 grids. We want full position at -10 grids.
        # At Center (MA), we might hold 0? Or 50%?
        # Let's assume we build position as it drops below MA.
        # Target Quantity = -current_grid_idx * qty_per_grid
        # If current_grid_idx is positive (Price > MA), Target = negative? No, 0.
        
        target_qty = 0
        if current_grid_idx < 0:
             # e.g. -1 => Buy 1 unit. -5 => Buy 5 units.
             # Cap at grid_count
             buy_units = min(abs(current_grid_idx), grid_count)
             target_qty = buy_units * qty_per_grid
        
        # Execution
        if target_qty > position_qty:
            diff = target_qty - position_qty
            # Buy difference
            can_buy_cash = int(cash_balance / last_close / lot_size) * lot_size
            buy_qty = min(diff, can_buy_cash)
            if buy_qty > 0:
                return {
                    "side": "BUY", 
                    "confidence": 0.6, 
                    "reason": f"grid_buy_level_{current_grid_idx}", 
                    "suggested_quantity": buy_qty
                }
        
        elif target_qty < position_qty:
            diff = position_qty - target_qty
            # Sell difference
            if diff > 0:
                 return {
                    "side": "SELL", 
                    "confidence": 0.6, 
                    "reason": f"grid_sell_level_{current_grid_idx}", 
                    "suggested_quantity": diff
                }
        
        return {"side": "HOLD", "confidence": 0.1, "reason": "in_zone"}
