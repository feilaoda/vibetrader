import os
import sys
import argparse
import logging
import time
from datetime import datetime

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
# Add parent directory to path to import from api
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

try:
    from db import get_connection
    from cache import force_sync
    from strategy_universe import fetch_symbols_for_strategy
except ImportError as e:
    print(f"Import Error: {e}")
    print("Please run this script as 'python api/quant/cron_daily.py' from project root or ensure PYTHONPATH is set.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(current_dir, 'cron_daily.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger("CronDaily")

def get_all_target_symbols(conn):
    """
    Get all unique symbols from:
    1. Watchlist (market=ashare/us)
    2. Active paper strategies
    """
    symbols = set()
    
    # 1. From Watchlist
    try:
        rows = conn.execute("SELECT symbol, market FROM watchlist").fetchall()
        for r in rows:
            if r[1] and r[1].lower() in ('ashare', 'us', 'sh', 'sz', 'bj'):
                symbols.add(r[0].upper())
        logger.info(f"Loaded {len(symbols)} symbols from Watchlist")
    except Exception as e:
        logger.error(f"Error loading watchlist: {e}")

    # 2. From Strategies
    try:
        # Select active strategies (is_ai=True and auto_run_enabled=True)
        # Note: We might want to sync for manual strategies too if they strictly follow some symbols
        strategies = conn.execute(
            "SELECT id, universe_type, universe_symbols FROM paper_strategies "
            "WHERE is_ai = TRUE AND auto_run_enabled = TRUE"
        ).fetchall()
        
        strat_symbols_count = 0
        for s in strategies:
            strat_dict = {
                "id": s[0],
                "universe_type": s[1],
                "universe_symbols": s[2]
            }
            # Use the existing helper to parse/expand universe
            strat_syms = fetch_symbols_for_strategy(conn, strat_dict)
            for sym in strat_syms:
                if sym not in symbols:
                    symbols.add(sym)
                    strat_symbols_count += 1
        
        logger.info(f"Added {strat_symbols_count} additional symbols from active strategies")
    except Exception as e:
        logger.error(f"Error loading strategy symbols: {e}")
        
    return sorted(list(symbols))

def main():
    parser = argparse.ArgumentParser(description="Daily Data Ingestion Cron Job")
    parser.add_argument("--dry-run", action="store_true", help="Scan symbols but do not fetch data")
    parser.add_argument("--force", action="store_true", help="Force refresh even if recent cache exists (handled by force_sync)")
    args = parser.parse_args()

    conn = get_connection()
    try:
        symbols = get_all_target_symbols(conn)
    finally:
        conn.close()

    if not symbols:
        logger.warning("No symbols found to sync.")
        return

    logger.info(f"Starting daily sync for {len(symbols)} symbols. Dry run: {args.dry_run}")
    
    success_count = 0
    fail_count = 0
    start_time = time.time()

    for idx, symbol in enumerate(symbols):
        try:
            if args.dry_run:
                logger.info(f"[DRY-RUN] Would sync {symbol}")
                success_count += 1
                continue

            logger.info(f"[{idx+1}/{len(symbols)}] Syncing {symbol}...")
            # force_sync calls get_klines_with_cache(..., force_refresh=True)
            # It returns (klines, source)
            klines, source = force_sync(symbol, "daily")
            
            if klines and len(klines) > 0:
                logger.info(f"  -> Success ({len(klines)} bars, source: {source})")
                success_count += 1
            else:
                logger.warning(f"  -> Empty result for {symbol}")
                fail_count += 1

            # Be nice to APIs
            time.sleep(0.5) 

        except Exception as e:
            logger.error(f"Failed to sync {symbol}: {e}")
            fail_count += 1

    elapsed = time.time() - start_time
    logger.info("="*30)
    logger.info(f"Daily Sync Completed in {elapsed:.2f}s")
    logger.info(f"Total: {len(symbols)}, Success: {success_count}, Failed: {fail_count}")
    logger.info("="*30)

if __name__ == "__main__":
    main()
