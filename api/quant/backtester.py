import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Type, Dict, Any, List

# Add parent directory to path to import from api if run directly
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

try:
    from cache import get_klines_with_cache
    from quant.strategy_base import BaseStrategy
except ImportError:
    # Fallback for relative imports if run as module
    from ..cache import get_klines_with_cache
    from .strategy_base import BaseStrategy

class Backtester:
    def __init__(
        self, 
        strategy_cls: Type[BaseStrategy], 
        symbol: str, 
        start_date: str = None, 
        end_date: str = None, 
        initial_capital: float = 100000.0,
        params: Dict[str, Any] = None,
        constraints: Dict[str, Any] = None,
        commission_rate: float = 0.0003, # 3bps
        slippage: float = 0.001 # 0.1%
    ):
        self.strategy = strategy_cls(params, constraints)
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage
        
        self.cash = initial_capital
        self.position = 0
        self.avg_cost = 0.0
        self.trades = []
        self.trades = []
        self.equity_curve = []
        self.last_signal = None

    def run(self):
        # 1. Fetch Data
        klines, _ = get_klines_with_cache(
            self.symbol, 
            "daily", 
            start_date=self.start_date, 
            end_date=self.end_date, 
            limit=5000 # Fetch enough history
        )
        
        if not klines:
            print("No data found for backtest")
            return

        print(f"Backtesting {self.symbol} from {klines[0]['date']} to {klines[-1]['date']} ({len(klines)} bars)")

        # 2. Iterate
        for i in range(len(klines)):
            # Market Data available up to this point
            # Standard backtest: use Close of current bar to generate signal for NEXT bar open
            # OR use Close of current bar to execute at Close immediately?
            # Let's assume execute at Close (simpler) or Open of next?
            # To match 'paper_strategy_runner', it typically runs after market close for next day signal, 
            # OR intra-day. 
            # Let's assume we run ON Close and execute ON Close (theoretical) or Next Open.
            # For simplicity: Signal on Close[i], Execute at Close[i] (or Open[i+1]). 
            # Let's do Execute at Close[i] to align with "signal generated based on current info, actionable now".
            
            # Context for Strategy
            current_kline = klines[i]
            # Strategy needs history up to i
            history = klines[:i+1]
            
            account_context = {
                "cash_balance": self.cash,
                "total_equity": self._calculate_equity(current_kline['close']),
                "positions": [{"symbol": self.symbol, "quantity": self.position, "avg_cost": self.avg_cost}] if self.position > 0 else []
            }
            
            position_dict = {"quantity": self.position, "avg_cost": self.avg_cost} if self.position > 0 else None
            
            msg = self.strategy.generate_signal(history, position_dict, account_context)
            
            # Execute
            self._execute_signal(msg, current_kline)
            
            # Record Equity
            self.equity_curve.append({
                "date": current_kline['date'],
                "equity": self._calculate_equity(current_kline['close']),
                "cash": self.cash,
                "position": self.position,
                "close": current_kline['close']
            })
            
            # Save last signal
            if i == len(klines) - 1:
                self.last_signal = msg

    def _calculate_equity(self, price: float) -> float:
        return self.cash + self.position * price

    def _execute_signal(self, signal: Dict[str, Any], kline: Dict[str, Any]):
        side = signal.get("side")
        qty = int(signal.get("suggested_quantity") or 0)
        price = float(kline['close'])
        
        if side == "BUY" and qty > 0:
            cost = qty * price
            fee = cost * self.commission_rate
            slippage_cost = cost * self.slippage
            total_cost = cost + fee + slippage_cost
            
            if self.cash >= total_cost:
                # Update Avg Cost
                total_position_val = self.position * self.avg_cost + cost
                self.position += qty
                self.avg_cost = total_position_val / self.position
                
                self.cash -= total_cost
                self.trades.append({
                    "date": kline['date'],
                    "side": "BUY",
                    "price": price,
                    "quantity": qty,
                    "fee": fee + slippage_cost,
                    "total": total_cost
                })
        
        elif side == "SELL" and qty > 0:
            if self.position >= qty:
                proceeds = qty * price
                fee = proceeds * self.commission_rate
                slippage_cost = proceeds * self.slippage
                net_proceeds = proceeds - fee - slippage_cost
                
                pnl = (price - self.avg_cost) * qty - (fee + slippage_cost)
                
                self.cash += net_proceeds
                self.position -= qty
                if self.position == 0:
                    self.avg_cost = 0
                
                self.trades.append({
                    "date": kline['date'],
                    "side": "SELL",
                    "price": price,
                    "quantity": qty,
                    "fee": fee + slippage_cost,
                    "total": net_proceeds,
                    "pnl": pnl
                })

    def report(self):
        if not self.equity_curve:
            return "No trades or data."
            
        df = pd.DataFrame(self.equity_curve)
        df['returns'] = df['equity'].pct_change()
        
        total_return = (df['equity'].iloc[-1] / self.initial_capital) - 1
        sharpe = df['returns'].mean() / df['returns'].std() * np.sqrt(252) if df['returns'].std() != 0 else 0
        max_drawdown = (df['equity'] / df['equity'].cummax() - 1).min()
        
        print("-" * 30)
        print(f"Backtest Report: {self.symbol}")
        print(f"Initial Capital: {self.initial_capital}")
        print(f"Final Equity:    {df['equity'].iloc[-1]:.2f}")
        print(f"Total Return:    {total_return*100:.2f}%")
        print(f"Sharpe Ratio:    {sharpe:.2f}")
        print(f"Max Drawdown:    {max_drawdown*100:.2f}%")
        print(f"Total Trades:    {len(self.trades)}")
        print("-" * 30)
        
        return df

if __name__ == "__main__":
    # Example Usage
    from quant.strategies.trend_following import TrendFollowingStrategy
    
    # Run a quick test on 000001
    bt = Backtester(
        TrendFollowingStrategy, 
        symbol="000001.SZ", 
        start_date="20240101",
        params={"ma_short": 5, "ma_long": 20},
        constraints={"stop_loss_pct": 5}
    )
    bt.run()
    bt.report()
