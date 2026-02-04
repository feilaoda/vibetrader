
import json
import time
import pandas as pd
import akshare as ak
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from db import save_symbols_db, get_symbols_db
from akshare_guard import throttle

def load_symbols_from_disk():
    """Load symbols from DB (Compatibility alias)"""
    return get_symbols_db()

def save_symbols_to_disk(df):
    """Save symbols to DB (Compatibility alias)"""
    save_symbols_db(df)

def fetch_all_symbols_remote():
    """Fetch all symbols from AkShare (Stocks + ETFs)"""
    print("[Symbols] Fetching fresh data from AkShare...")
    
    # 1. Fetch A-Shares
    try:
        throttle(scope="akshare_symbols")
        df_stock = ak.stock_info_a_code_name()
    except Exception as e:
        print(f"[Symbols] Error fetching stocks: {e}")
        df_stock = pd.DataFrame(columns=["code", "name"])

    # 2. Fetch ETFs (Try API then Fallback)
    try:
        # Try EastMoney API
        throttle(scope="akshare_symbols")
        df_etf = ak.fund_etf_spot_em()
        df_etf = df_etf[["代码", "名称"]].rename(columns={"代码": "code", "名称": "name"})
    except Exception as e:
        print(f"[Symbols] Error fetching ETFs (API): {e}")
        # Use Fallback
        from fallback_data import get_fallback_etfs
        df_etf = get_fallback_etfs()

    # 3. Merge
    df = pd.concat([df_stock, df_etf], ignore_index=True)
    
    # Add suffixes
    def add_suffix(code):
        if code.startswith(("6", "9", "5")): return ".SH"
        if code.startswith(("0", "2", "3", "1")): return ".SZ"
        return ".BJ"
        
    df["suffix"] = df["code"].apply(add_suffix)
    df["symbol"] = df["code"] + df["suffix"]
    
    # Clean up
    final_df = df[["symbol", "code", "name"]]
    
    # Save to DB
    save_symbols_to_disk(final_df)
    
    return final_df

def get_all_symbols():
    """Get all symbols (DB-First Strategy)"""
    
    # 1. Try DB Cache
    df = get_symbols_db()
    if df is not None:
        return df
        
    # 2. Fetch Remote (Blocking if no cache)
    return fetch_all_symbols_remote()
