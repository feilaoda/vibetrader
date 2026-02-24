import sys
import os
import argparse
import pandas as pd

# Add api directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir)) # .../api
if parent_dir not in sys.path:
    sys.path.append(os.path.join(parent_dir, 'api'))

try:
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
    from quant.backtester import Backtester
    from quant.strategies.trend_following import TrendFollowingStrategy
    from quant.strategies.mean_reversion import MeanReversionStrategy
    from quant.strategies.turtle import TurtleStrategy
    from quant.strategies.r_breaker import RBreakerStrategy
    from quant.strategies.grid import GridStrategy
    from quant.strategies.factor_momentum import FactorMomentumStrategy
    from quant.strategies.bollinger_squeeze import BollingerBandSqueezeStrategy

STRATEGIES = {
    "trend": {
        "cls": TrendFollowingStrategy,
        "params": {"ma_short": 5, "ma_long": 20, "breakout_window": 20},
        "constraints": {"stop_loss_pct": 5, "take_profit_pct": 15}
    },
    "mean_reversion": {
        "cls": MeanReversionStrategy,
        "params": {"rsi_period": 14, "rsi_low": 30, "rsi_high": 70},
        "constraints": {"stop_loss_pct": 5, "take_profit_pct": 10}
    },
    "turtle": {
        "cls": TurtleStrategy,
        "params": {"entry_window": 20, "exit_window": 10, "atr_period": 14},
        "constraints": {"risk_per_trade": 0.01}
    },
    "r_breaker": {
        "cls": RBreakerStrategy,
        "params": {},
        "constraints": {"stop_loss_pct": 3, "max_position_pct": 20}
    },
    "grid": {
        "cls": GridStrategy,
        "params": {"grid_pct": 1.0, "grid_limit": 10, "center_ma_period": 20},
        "constraints": {"per_grid_pct": 5}
    },
    "factor": {
        "cls": FactorMomentumStrategy,
        "params": {"buy_threshold": 65, "sell_threshold": 45},
        "constraints": {}
    },
    "bollinger": {
        "cls": BollingerBandSqueezeStrategy,
        "params": {"period": 20, "std_dev": 2.0, "squeeze_threshold": 0.15},
        "constraints": {"stop_loss_pct": 5, "max_position_pct": 20}
    }
}

def run_single_strategy(symbol, strategy_name, start_date, initial_capital, show_report=True):
    conf = STRATEGIES.get(strategy_name)
    if not conf:
        print(f"Unknown strategy: {strategy_name}")
        return None

    print(f"\nRunning {strategy_name} strategy on {symbol}...")
    bt = Backtester(
        conf["cls"], 
        symbol=symbol, 
        start_date=start_date,
        initial_capital=initial_capital,
        params=conf["params"],
        constraints=conf["constraints"]
    )
    bt.run()
    if show_report:
        bt.report()
    
    # Return metrics
    if not bt.equity_curve:
        return None
        
    df = pd.DataFrame(bt.equity_curve)
    df['returns'] = df['equity'].pct_change()
    final_equity = df['equity'].iloc[-1]
    total_return = (final_equity / initial_capital) - 1
    sharpe = df['returns'].mean() / df['returns'].std() * (252**0.5) if df['returns'].std() != 0 else 0
    max_drawdown = (df['equity'] / df['equity'].cummax() - 1).min()
    
    last_action = bt.last_signal.get("side", "HOLD") if bt.last_signal else "N/A"
    reason = bt.last_signal.get("reason", "") if bt.last_signal else ""
    
    return {
        "Strategy": strategy_name,
        "Return": total_return,
        "FinalEquity": final_equity,
        "Sharpe": sharpe,
        "MaxDD": max_drawdown,
        "Trades": len(bt.trades),
        "Action": f"{last_action}", 
        "Reason": reason
    }

def main():
    parser = argparse.ArgumentParser(description="Run Backtest Example")
    parser.add_argument("--symbol", type=str, default="000001.SZ", help="Symbol to backtest")
    parser.add_argument("--strategy", type=str, default="all", help="Strategy to use or 'all'")
    parser.add_argument("--start", type=str, default="20240101", help="Start date YYYYMMDD")
    parser.add_argument("--initial", type=float, default=100000, help="Initial capital")
    args = parser.parse_args()

    if args.strategy == "all":
        results = []
        print(f"Running ALL strategies on {args.symbol} from {args.start}...")
        for name in STRATEGIES.keys():
            res = run_single_strategy(args.symbol, name, args.start, args.initial, show_report=False)
            if res:
                results.append(res)
        
        if results:
            res_df = pd.DataFrame(results)
            res_df = res_df.sort_values(by="Return", ascending=False)
            
            print("\n" + "="*110)
            print(f"Strategy Comparison: {args.symbol} (Start: {args.start})")
            print("="*110)
            print(f"{'Strategy':<16} {'Return':<10} {'FinalEquity':<12} {'Sharpe':<8} {'MaxDD':<10} {'Trades':<6} {'Action':<8} {'Reason':<25}")
            print("-" * 110)
            for _, row in res_df.iterrows():
                final_equity_str = f"{row['FinalEquity']:.2f}"
                reason_trunc = (row['Reason'][:22] + '..') if len(row['Reason']) > 22 else row['Reason']
                print(f"{row['Strategy']:<16} {row['Return']*100:>8.2f}% {final_equity_str:>12} {row['Sharpe']:>8.2f} {row['MaxDD']*100:>9.2f}% {row['Trades']:>6} {row['Action']:<8} {reason_trunc:<25}")
            print("-" * 110)
    else:
        run_single_strategy(args.symbol, args.strategy, args.start, args.initial, show_report=True)

if __name__ == "__main__":
    main()
