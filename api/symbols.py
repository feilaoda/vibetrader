
import pandas as pd
from db import save_symbols_db, get_symbols_db
from data_sources import DataType
from data_sources.router import fetch as router_fetch
from us_indices import US_INDEX_SYMBOLS


def _append_builtin_symbols(df: pd.DataFrame) -> pd.DataFrame:
    extra = pd.DataFrame(US_INDEX_SYMBOLS)
    if df is None or df.empty:
        return extra
    merged = pd.concat([df, extra], ignore_index=True)
    merged = merged.drop_duplicates(subset=["symbol"], keep="first")
    return merged

def load_symbols_from_disk():
    """Load symbols from DB (Compatibility alias)"""
    return get_symbols_db()

def save_symbols_to_disk(df):
    """Save symbols to DB (Compatibility alias)"""
    save_symbols_db(df)

def fetch_all_symbols_remote():
    """Fetch all symbols from AkShare (Stocks + ETFs)"""
    print("[Symbols] Fetching fresh data from channel router...")
    data, channel = router_fetch(DataType.SYMBOLS, channels=["local", "akshare"])
    df_stock = None
    df_etf = None
    if isinstance(data, dict):
        df_stock = data.get("stocks")
        df_etf = data.get("etfs")
    if df_stock is None:
        df_stock = pd.DataFrame(columns=["code", "name"])
    if df_etf is None:
        from fallback_data import get_fallback_etfs
        df_etf = get_fallback_etfs()
    else:
        try:
            if "代码" in df_etf.columns and "名称" in df_etf.columns:
                df_etf = df_etf[["代码", "名称"]].rename(columns={"代码": "code", "名称": "name"})
        except Exception:
            pass

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
    final_df = _append_builtin_symbols(final_df)
    
    # Save to DB
    save_symbols_to_disk(final_df)
    
    return final_df

def get_all_symbols():
    """Get all symbols (DB-First Strategy)"""
    
    # 1. Try DB Cache
    df = get_symbols_db()
    if df is not None:
        return _append_builtin_symbols(df)
        
    # 2. Fetch Remote (Blocking if no cache)
    return fetch_all_symbols_remote()
