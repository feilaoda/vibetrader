import sys
import os
sys.path.append(os.getcwd())
from api.cache import force_sync, get_klines_with_cache

symbol = "002807.SZ"
print(f"Fetching data for {symbol}...")

try:
    # Try getting from cache first
    klines, _source = get_klines_with_cache(symbol, "daily")
    print(f"Cached records: {len(klines)}")
    if klines:
        print("Last 5 records (Cached):")
        for k in klines[-5:]:
            print(k)

    # Force sync
    print("\nForce syncing...")
    klines, source = force_sync(symbol, "daily")
    print(f" synced records: {len(klines)} from {source}")
    if klines:
        print("Last 5 records (Synced):")
        for k in klines[-5:]:
            print(k)
            
except Exception as e:
    print(f"Error: {e}")
