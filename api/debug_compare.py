import akshare as ak
import pandas as pd
import time

symbol = "sz002807" 
print(f"Fetching Sina Spot data for {symbol}...")
start = time.time()

try:
    # Sina Spot
    spot = ak.stock_zh_a_spot()
    row = spot[spot['代码'] == symbol]
    end = time.time()
    print(f"\nSpot Data (Sina) in {end - start:.2f}s:")
    print(row)
    
except Exception as e:
    print(f"Sina Spot Error: {e}")
