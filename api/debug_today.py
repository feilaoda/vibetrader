import akshare as ak
import pandas as pd
from datetime import datetime

symbol = "002807"
today = datetime.now().strftime("%Y%m%d")
print(f"Fetching Today's ({today}) data for {symbol}...")

try:
    # Fetch just today
    df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=today, end_date=today, adjust="qfq")
    print("\nEastmoney (Today):")
    print(df)
    
except Exception as e:
    print(f"Error: {e}")
