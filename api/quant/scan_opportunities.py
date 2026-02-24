import sys
import os
import argparse
import pandas as pd
from datetime import datetime, timedelta

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

def get_all_symbols():
    conn = get_connection()
    try:
        # Get all symbols from symbols table
        rows = conn.execute("SELECT symbol FROM symbols").fetchall()
        symbols = []
        for r in rows:
            if r[0]:
                symbols.append(r[0].upper())
        # Filter for A-shares only if desired (starts with digit)
        # Assuming symbols table has US stocks too? 
        # Usually A-shares are 6 digits.
        return sorted(list(set(symbols)))
    except Exception as e:
        print(f"Error loading symbols: {e}")
        return []
    finally:
        conn.close()

def scan_opportunities(strategy_name: str, lookback_days: int = 365, source: str = "watchlist", limit: int = None):
    conf = STRATEGIES.get(strategy_name)
    if not conf:
        print(f"Unknown strategy: {strategy_name}")
        print(f"Available: {list(STRATEGIES.keys())}")
        return

    if source == "all":
        symbols = get_all_symbols()
        # Simple filter for A-shares (digit start) to avoid indices/US if mixed
        # Or just take all.
        # Let's verify if user wants only A-shares. The request said "库中所有的A股股票".
        # Filter by regex ^\d{6}
        import re
        symbols = [s for s in symbols if re.match(r'^\d{6}(\.SH|\.SZ)?$', s)]
    else:
        symbols = get_watchlist_symbols()
        
    if not symbols:
        print(f"No symbols found in {source}.")
        return

    if limit:
        symbols = symbols[:limit]

    print(f"Scanning {len(symbols)} symbols from '{source}' with '{strategy_name}' strategy...")
    print(f"Looking for BUY signals based on recent data...")
    
    # Set start date to lookback_days ago
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    
    buy_signals = []
    
    # Calculate results
    results_list = []
    
    # Progress bar simple
    total = len(symbols)
    processed = 0
    
    # Path to cache dir
    # api/quant/../__datacache__ -> api/__datacache__
    cache_dir = os.path.join(parent_dir, 'api', '__datacache__')
    
    import time
    
    for symbol in symbols:
        processed += 1
        if processed % 10 == 0:
            print(f"Progress: {processed}/{total}...", end='\r')
            
        try:
            # Smart Rate Limiting:
            # Check if cache file exists and is updated during this run
            safe_symbol = symbol.replace('.', '_')
            cache_file = os.path.join(cache_dir, f"{safe_symbol}_daily.json")
            
            mtime_before = 0
            if os.path.exists(cache_file):
                mtime_before = os.path.getmtime(cache_file)
            
            # Run backtest lightly
            bt = Backtester(
                conf["cls"], 
                symbol=symbol, 
                start_date=start_date, # Only need recent history
                initial_capital=100000,
                params=conf["params"],
                constraints=conf["constraints"]
            )
            bt.run()
            
            # Check last signal
            if bt.last_signal and bt.last_signal.get("side") == "BUY":
                last_price = bt.equity_curve[-1]['close'] if bt.equity_curve else 0.0
                record = {
                    "Symbol": symbol,
                    "Price": last_price,
                    "Reason": bt.last_signal.get("reason"),
                    "Confidence": bt.last_signal.get("confidence", 0.0)
                }
                buy_signals.append(record)
                results_list.append(record) # For return
                
                # Real-time output for found items
                print(f"  [FOUND] {symbol}: {bt.last_signal.get('reason'):<30} (Price: {last_price:.2f})")
            
            # Check if we need to sleep
            mtime_after = 0
            if os.path.exists(cache_file):
                mtime_after = os.path.getmtime(cache_file)
            
            if mtime_after > mtime_before:
                # File was updated, meaning network request likely happened
                # Sleep 1s as requested
                time.sleep(1.0)
            else:
                # Cache hit, go fast (maybe tiny sleep to yield)
                # time.sleep(0.001)
                pass
                
        except Exception as e:
            # print(f"Error scanning {symbol}: {e}")
            pass

    # Report
    print("\n" + "="*80)
    print(f"SCAN RESULTS: {strategy_name.upper()} - BUY SIGNALS ({source})")
    print("="*80)
    
    if buy_signals:
        df = pd.DataFrame(buy_signals)
        df = df.sort_values(by="Confidence", ascending=False)
        
        print(f"{'Symbol':<12} {'Price':<10} {'Confidence':<10} {'Reason':<40}")
        print("-" * 80)
        for _, row in df.iterrows():
            print(f"{row['Symbol']:<12} {row['Price']:<10.2f} {row['Confidence']:<10.2f} {row['Reason']:<40}")
        print("-" * 80)
        print(f"Total Found: {len(buy_signals)}")
    else:
        print("No BUY signals found currently.")
        
    return results_list

if __name__ == "__main__":
    try:
        from quant.log_utils import setup_logging
    except ImportError:
        # If run as script
        parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent not in sys.path: sys.path.append(parent)
        from quant.log_utils import setup_logging

    setup_logging("scan_opportunities")

    parser = argparse.ArgumentParser(description="Scan for Trading Opportunites")
    parser.add_argument("--strategy", type=str, default="turtle", help="Strategy to use")
    parser.add_argument("--days", type=int, default=365, help="Lookback days for data context")
    parser.add_argument("--source", type=str, default="watchlist", choices=["watchlist", "all"], help="Source of symbols")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of symbols to scan")
    args = parser.parse_args()
    
    scan_opportunities(args.strategy, args.days, args.source, args.limit)
