import akshare as ak
import pandas as pd

print("Testing AkShare ETF list functions...")

try:
    print("\n1. ak.fund_etf_spot_em()")
    df = ak.fund_etf_spot_em()
    print(f"Success! Count: {len(df)}")
    print(df.head(2))
except Exception as e:
    print(f"Failed: {e}")

try:
    print("\n2. ak.fund_etf_category_sina(symbol='ETF基金')")
    # Note: AkShare might not have this exact function, guessing based on pattern or checking common ones.
    # Looking at akshare, `stock_zh_a_spot_em` is for stocks.
    # Let's try searching for a working one.
    pass
except Exception as e:
    pass

# Try to find what else is available for ETF lists. 
# Commonly used: fund_etf_fund_daily_em, fund_etf_category_sina
# But for a list of ALL ETFs...

try:
    print("\n3. ak.fund_name_em() (All Funds - might be huge)")
    # df = ak.fund_name_em()
    # print(f"Success! Count: {len(df)}")
    print("Skipped (too large usually)")
except Exception as e:
    print(f"Failed: {e}")
