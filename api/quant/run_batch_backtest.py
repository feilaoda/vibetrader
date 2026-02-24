import sys
import os
import argparse
import pandas as pd
from datetime import datetime

# Add api directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir)) # .../api
if parent_dir not in sys.path:
    sys.path.append(os.path.join(parent_dir, 'api'))

try:
    from db import get_connection
    from quant.backtester import Backtester
    from quant.strategies.trend_following import TrendFollowingStrategy
    from quant.strategies.mean_reversion import MeanReversionStrategy
    from quant.strategies.turtle import TurtleStrategy
    from quant.strategies.r_breaker import RBreakerStrategy
    from quant.strategies.grid import GridStrategy
    from quant.strategies.factor_momentum import FactorMomentumStrategy
    from quant.strategies.bollinger_squeeze import BollingerBandSqueezeStrategy
except ImportError:
    # If run from api root
    sys.path.append(os.getcwd())
    from db import get_connection
    from quant.backtester import Backtester
    from quant.strategies.trend_following import TrendFollowingStrategy
    from quant.strategies.mean_reversion import MeanReversionStrategy
    from quant.strategies.turtle import TurtleStrategy
    from quant.strategies.r_breaker import RBreakerStrategy
    from quant.strategies.grid import GridStrategy
    from quant.strategies.factor_momentum import FactorMomentumStrategy
    from quant.strategies.bollinger_squeeze import BollingerBandSqueezeStrategy

def get_watchlist_symbols():
    conn = get_connection()
    try:
        # Get symbols from watchlist table
        rows = conn.execute("SELECT symbol, market FROM watchlist").fetchall()
        symbols = []
        for r in rows:
            if r[0]:
                symbols.append(r[0].upper())
        return sorted(list(set(symbols)))
    except Exception as e:
        print(f"Error loading watchlist: {e}")
        return []
    finally:
        conn.close()

def run_batch_backtest(start_date="20240101"):
    symbols = get_watchlist_symbols()
    if not symbols:
        print("No symbols in watchlist.")
        return

    print(f"Found {len(symbols)} symbols in watchlist.")
    
    strategies = [
        {
            "name": "TrendFollowing",
            "cls": TrendFollowingStrategy,
            "params": {"ma_short": 5, "ma_long": 20, "breakout_window": 20},
            "constraints": {"stop_loss_pct": 5, "take_profit_pct": 15}
        },
        {
            "name": "MeanReversion",
            "cls": MeanReversionStrategy,
            "params": {"rsi_period": 14, "rsi_low": 30, "rsi_high": 70},
            "constraints": {"stop_loss_pct": 5, "take_profit_pct": 10}
        },
        {
            "name": "Turtle(ATR)",
            "cls": TurtleStrategy,
            "params": {"entry_window": 20, "exit_window": 10, "atr_period": 14},
            "constraints": {"risk_per_trade": 0.01}
        },
        {
            "name": "R-Breaker",
            "cls": RBreakerStrategy,
            "params": {},
            "constraints": {"stop_loss_pct": 3, "max_position_pct": 20}
        },
        {
            "name": "Grid(MA)",
            "cls": GridStrategy,
            "params": {"grid_pct": 1.0, "grid_limit": 10, "center_ma_period": 20},
            "constraints": {"per_grid_pct": 5}
        },
        {
            "name": "FactorMomentum",
            "cls": FactorMomentumStrategy,
            "params": {"buy_threshold": 65, "sell_threshold": 45},
            "constraints": {}
        },
        {
            "name": "BollingerSq",
            "cls": BollingerBandSqueezeStrategy,
            "params": {"period": 20, "std_dev": 2.0, "squeeze_threshold": 0.15},
            "constraints": {"stop_loss_pct": 5, "max_position_pct": 20}
        }
    ]
    
    results = []

    for symbol in symbols:
        print(f"\nProcessing {symbol}...")
        for strat in strategies:
            try:
                bt = Backtester(
                    strat["cls"], 
                    symbol=symbol, 
                    start_date=start_date,
                    initial_capital=100000,
                    params=strat["params"],
                    constraints=strat["constraints"]
                )
                bt.run()
                
                # Extract metrics manually to avoid printing full report
                if not bt.equity_curve:
                    continue
                    
                df = pd.DataFrame(bt.equity_curve)
                df['returns'] = df['equity'].pct_change()
                
                final_equity = df['equity'].iloc[-1]
                total_return = (final_equity / bt.initial_capital) - 1
                sharpe = df['returns'].mean() / df['returns'].std() * (252**0.5) if df['returns'].std() != 0 else 0
                max_drawdown = (df['equity'] / df['equity'].cummax() - 1).min()
                
                results.append({
                    "Symbol": symbol,
                    "Strategy": strat["name"],
                    "Return": total_return,
                    "FinalEquity": final_equity,
                    "Sharpe": sharpe,
                    "MaxDD": max_drawdown,
                    "Trades": len(bt.trades)
                })
            except Exception as e:
                print(f"Error backtesting {symbol} with {strat['name']}: {e}")

    # Summary
    if results:
        res_df = pd.DataFrame(results)
        # Sort by Return desc
        res_df = res_df.sort_values(by="Return", ascending=False)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(current_dir, f"backtest_report_{timestamp}.log")
        
        output_lines = []
        output_lines.append("="*80)
        output_lines.append(f"Batch Backtest Summary (Start: {start_date})")
        output_lines.append("="*80)
        
        # Print formatted table
        header = f"{'Symbol':<12} {'Strategy':<16} {'Return':<10} {'FinalEquity':<12} {'Sharpe':<8} {'MaxDD':<10} {'Trades':<6}"
        output_lines.append(header)
        output_lines.append("-" * 80)
        
        for _, row in res_df.iterrows():
            final_equity_str = f"{row['FinalEquity']:.2f}"
            line = f"{row['Symbol']:<12} {row['Strategy']:<16} {row['Return']*100:>8.2f}% {final_equity_str:>12} {row['Sharpe']:>8.2f} {row['MaxDD']*100:>9.2f}% {row['Trades']:>6}"
            output_lines.append(line)
        output_lines.append("-" * 80)
        
        # Aggr stats
        output_lines.append("\nAverage Return per Strategy:")
        avg_ret = res_df.groupby("Strategy")["Return"].mean().apply(lambda x: f"{x*100:.2f}%")
        output_lines.append(str(avg_ret))
        
        full_output = "\n".join(output_lines)
        print(full_output)
        
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(full_output)
        print(f"\n[Report Saved] {log_file}")
    else:
        print("No results generated.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Batch Backtest on Watchlist")
    parser.add_argument("--start", type=str, default="20240101", help="Start date YYYYMMDD")
    args = parser.parse_args()
    
    run_batch_backtest(args.start)
