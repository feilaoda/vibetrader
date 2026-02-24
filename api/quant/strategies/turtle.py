from typing import List, Dict, Any, Optional
from ..strategy_base import BaseStrategy

class TurtleStrategy(BaseStrategy):
    """
    Turtle Trading Strategy (Simplified ATR Breakout).
    
    Logic:
    1. Channel: Donchian Channel (High/Low of last N days). 
       - Buy if Close > Highest High of last 20 days.
       - Sell (Exit) if Close < Lowest Low of last 10 days.
    2. Sizing: Volatility based (ATR). 
       - Calculate N (ATR). 
       - Unit = Account * 1% / N.
    3. Stop Loss: 2 * N from entry price.
    """
    def generate_signal(
        self, 
        klines: List[Dict[str, Any]], 
        position: Optional[Dict[str, Any]], 
        account_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        entry_window = int(self.params.get("entry_window") or 20)
        exit_window = int(self.params.get("exit_window") or 10)
        atr_period = int(self.params.get("atr_period") or 20)
        risk_per_trade = float(self.constraints.get("risk_per_trade") or 0.01) # 1% equity risk per trade

        if not klines or len(klines) < max(entry_window, atr_period) + 1:
            return {"side": "HOLD", "confidence": 0.0, "reason": "insufficient_data"}

        closes = [self._safe_float(k.get("close")) for k in klines]
        highs = [self._safe_float(k.get("high")) for k in klines]
        lows = [self._safe_float(k.get("low")) for k in klines]
        last_close = closes[-1]
        
        # Calculate Indicators
        # 1. Donchian Channel (Shifted by 1 to avoid lookahead bias if using current close against past high)
        # Entry based on High of PREVIOUS entry_window days
        prev_highs = highs[-(entry_window+1):-1]
        breakout_high = max(prev_highs) if prev_highs else highs[-2]
        
        # Exit based on Low of PREVIOUS exit_window days
        prev_lows = lows[-(exit_window+1):-1]
        breakout_low = min(prev_lows) if prev_lows else lows[-2]
        
        # 2. ATR
        atr = self._calculate_atr(highs, lows, closes, atr_period)
        last_atr = atr[-1] if atr else last_close * 0.02 # fallback

        # Position State
        position_qty = int(position.get("quantity") or 0) if position else 0
        avg_cost = self._safe_float(position.get("avg_cost")) if position else 0.0

        # Logic
        
        # Check Exit first
        if position_qty > 0:
            # 1. Hard Stop Loss: Price < Entry - 2 * N
            stop_price = avg_cost - 2 * last_atr
            if last_close < stop_price:
                 return {
                    "side": "SELL",
                    "confidence": 0.9,
                    "reason": f"turtle_stop_loss ({last_close} < {stop_price:.2f})",
                    "suggested_quantity": position_qty
                }
            
            # 2. System Exit: Price < Low of last exit_window days
            if last_close < breakout_low:
                 return {
                    "side": "SELL",
                    "confidence": 0.8,
                    "reason": f"turtle_exit_low ({last_close} < {breakout_low})",
                    "suggested_quantity": position_qty
                }
                
            # 3. Optional: Add to position (Pyramiding) - Not implemented for simplicity base version
            
            return {"side": "HOLD", "confidence": 0.5, "reason": "holding"}

        # Check Entry
        # Buy if Close > Breakout High
        if last_close > breakout_high:
            # Sizing based on ATR
            # Unit = Equity * Risk% / ATR
            # Value = Unit * Price
            # But Unit here usually means "Number of Shares" = (Equity * Risk%) / (ATR * ContractSize)
            # A-share lot size = 100
            
            cash_balance = float(account_context.get("cash_balance") or 0)
            total_equity = float(account_context.get("total_equity") or cash_balance)
            
            # Dollar Risk = Equity * 0.01
            dollar_risk = total_equity * risk_per_trade
            
            # Stop distance = 2 * ATR
            # Loss per share = 2 * ATR
            # Shares = Dollar Risk / (2 * ATR)
            # Actually standard Turtle says 1 N stop, so risk is 1 N per unit?
            # Standard: Stop is 2N. Risk per Unit = 2N * Shares.
            # We want Risk per Unit = 1% Equity.
            # So 2N * Shares = 0.01 * Equity => Shares = (0.01 * Equity) / (2 * N)
            
            shares_raw = dollar_risk / (2 * last_atr) if last_atr > 0 else 0
            
            # Lot size adjustment
            lot_size = 100
            qty = int(shares_raw / lot_size) * lot_size
            
            # Cap by cash
            max_qty_cash = int(cash_balance / last_close / lot_size) * lot_size
            qty = max(0, min(qty, max_qty_cash))
            
            if qty > 0:
                 return {
                    "side": "BUY",
                    "confidence": 0.8,
                    "reason": f"turtle_breakout ({last_close} > {breakout_high})",
                    "suggested_quantity": qty
                }
        
        return {"side": "HOLD", "confidence": 0.1, "reason": "no_signal"}

    def _calculate_atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return []
        
        tr_list = []
        for i in range(1, len(closes)):
            h = highs[i]
            l = lows[i]
            c_prev = closes[i-1]
            
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            tr_list.append(tr)
            
        # Simple Moving Average of TR 
        # (Standard ATR uses RMA/Wilder's, but SMA is close enough for simple test)
        atrs = []
        # First ATR
        if len(tr_list) < period:
            return []
            
        sma_tr = sum(tr_list[:period]) / period
        atrs.extend([sma_tr] * period) # pad beginning roughly? Or just start appending
        # Actually we need aligned array. Let's precise.
        
        # Real calculation
        res = [0.0] * (len(closes)) 
        # ATR valid from index 'period'
        
        curr_atr = sum(tr_list[:period]) / period
        res[period] = curr_atr
        
        for i in range(period, len(tr_list)):
            # Wilder's Smoothing: ATR = (PrevATR * (n-1) + TR) / n
            curr_atr = (curr_atr * (period - 1) + tr_list[i]) / period
            res[i+1] = curr_atr
            
        return res
