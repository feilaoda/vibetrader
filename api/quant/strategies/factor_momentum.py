from typing import List, Dict, Any, Optional
from ..strategy_base import BaseStrategy

class FactorMomentumStrategy(BaseStrategy):
    """
    Multi-Factor Momentum Strategy.
    
    Combines 3 Factors to generate a Score (0-100):
    1. Trend Factor (40%): Price vs MA20.
    2. RSI Factor (30%): RSI value (Reversion/Momentum mix).
    3. Volatility Factor (30%): Inverse ATR (Low vol is good for steady growth).
    
    Logic:
    - Score > Buy Threshold (e.g. 70) => Buy
    - Score < Sell Threshold (e.g. 40) => Sell
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
        
        # Params
        buy_threshold = float(self.params.get("buy_threshold") or 60)
        sell_threshold = float(self.params.get("sell_threshold") or 40)
        
        # 1. Trend Factor (0-100)
        # Price position relative to MA20 and MA60
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60 if len(closes) > 60 else ma20
        
        trend_score = 50
        if last_close > ma20: trend_score += 20
        if last_close > ma60: trend_score += 10
        if ma20 > ma60: trend_score += 20
        
        # 2. RSI Factor (0-100)
        # RSI > 50 is bullish trend usually, but > 80 is overbought.
        # Let's say ideal bull rsi is 50-70.
        rsi = self._calculate_rsi(closes)[-1]
        rsi_score = 50
        if 50 < rsi < 70:
            rsi_score = 80
        elif rsi >= 70:
            rsi_score = 60 # overbought risk
        elif 30 < rsi <= 50:
            rsi_score = 40
        else: # < 30
            rsi_score = 20 # oversold risk (or accumulation?)
            
        # 3. Volatility Factor (0-100)
        # Lower volatility often implies stable trend.
        # Compare current ATR to past ATR.
        atr14 = self._calculate_atr_val(klines, 14)
        atr60 = self._calculate_atr_val(klines, 60)
        
        vol_score = 50
        if atr14 < atr60: # Volatility decreasing
            vol_score = 70
        else:
            vol_score = 30
            
        # Weighted Score
        total_score = 0.4 * trend_score + 0.3 * rsi_score + 0.3 * vol_score
        
        # Logic
        position_qty = int(position.get("quantity") or 0) if position else 0
        
        if position_qty > 0:
            # Exit
            if total_score < sell_threshold:
                 return {
                    "side": "SELL",
                    "confidence": 0.8,
                    "reason": f"score_low ({total_score:.1f} < {sell_threshold})",
                    "suggested_quantity": position_qty
                }
        else:
            # Entry
            if total_score > buy_threshold:
                cash_balance = float(account_context.get("cash_balance") or 0)
                # full pos or fixed match?
                target_val = cash_balance # simple all in for demo, or constraints governed
                lot_size = 100
                qty = int(target_val / last_close / lot_size) * lot_size
                if qty > 0:
                    return {
                        "side": "BUY",
                        "confidence": 0.7,
                        "reason": f"score_high ({total_score:.1f} > {buy_threshold})",
                        "suggested_quantity": qty
                    }
                    
        return {"side": "HOLD", "confidence": 0.1, "reason": f"score_neutral ({total_score:.1f})"}

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> List[float]:
        # Simplified RSI helper, same as MeanReversion
        if len(prices) < period + 1: return [50]*len(prices)
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsis = []
        rsis.extend([50]*period) # pad
        
        curr_gain = avg_gain
        curr_loss = avg_loss
        
        for i in range(period, len(deltas)):
            curr_gain = (curr_gain * (period - 1) + gains[i]) / period
            curr_loss = (curr_loss * (period - 1) + losses[i]) / period
            if curr_loss == 0: rsis.append(100)
            else: rsis.append(100 - (100 / (1 + curr_gain/curr_loss)))
        return rsis

    def _calculate_atr_val(self, klines, period):
        # Simplified scalar ATR calc roughly
        if len(klines) < period: return 1.0
        tr_sum = 0
        for i in range(-period, 0):
            h = float(klines[i]['high'])
            l = float(klines[i]['low'])
            c = float(klines[i]['close']) # prev close approximate
            tr_sum += (h - l)
        return tr_sum / period
