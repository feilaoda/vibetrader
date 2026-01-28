"""
VibeTrader AKShare API Backend
A股数据代理服务
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from datetime import datetime, timedelta
import akshare as ak
import time

def retry_request(func, max_retries=3, delay=1):
    """带重试的请求包装器"""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[API] Retry {attempt + 1}/{max_retries} after error: {e}")
                time.sleep(delay)
            else:
                raise e

app = FastAPI(title="VibeTrader A股 API", version="1.0.0")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 时间周期映射
PERIOD_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "daily",
    "1D": "daily",
    "1w": "weekly",
    "1W": "weekly",
    "1M": "monthly",
}


def format_symbol(symbol: str) -> str:
    """
    格式化股票代码
    输入: 600519, 600519.SH, sh600519
    输出: 纯数字代码 600519
    """
    symbol = symbol.upper().replace("SH", "").replace("SZ", "").replace(".", "")
    return symbol


def get_market_prefix(symbol: str) -> str:
    """判断股票市场前缀 (上海/深圳)"""
    code = format_symbol(symbol)
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith(("0", "2", "3")):
        return "sz"
    elif code.startswith("8") or code.startswith("4"):
        return "bj"  # 北交所
    return "sh"


@app.get("/api/klines/{symbol}")
async def get_klines(
    symbol: str,
    period: str = Query("1d", description="K线周期: 1m, 5m, 15m, 30m, 60m, 1d, 1w, 1M"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYYMMDD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYYMMDD"),
    limit: int = Query(1000, description="数据条数限制"),
):
    """获取K线数据（使用缓存）"""
    try:
        from cache import get_klines_with_cache
        
        ak_period = PERIOD_MAP.get(period, "daily")
        
        klines = get_klines_with_cache(
            symbol=symbol,
            period=ak_period,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
        
        return {"data": klines, "symbol": symbol, "period": period}

    except Exception as e:
        import traceback
        print(f"[API] Error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sync/{symbol}")
async def sync_klines(
    symbol: str,
    period: str = Query("1d", description="K线周期"),
):
    """手动强制同步数据"""
    try:
        from cache import force_sync
        
        ak_period = PERIOD_MAP.get(period, "daily")
        klines = force_sync(symbol, ak_period)
        
        return {
            "success": True,
            "data": klines,
            "symbol": symbol,
            "period": period,
            "count": len(klines)
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config/datasource")
async def get_datasource():
    """获取当前数据源"""
    from cache import get_data_source
    return {"data_source": get_data_source()}


@app.post("/api/config/datasource")
async def set_datasource(source: str = Query(..., description="数据源: eastmoney 或 sina")):
    """设置数据源"""
    from cache import set_data_source
    if source not in ["eastmoney", "sina"]:
        raise HTTPException(status_code=400, detail="Invalid source. Use 'eastmoney' or 'sina'")
    set_data_source(source)
    return {"success": True, "data_source": source}


@app.get("/api/symbols")
async def search_symbols(
    q: str = Query("", description="搜索关键词"),
    limit: int = Query(50, description="返回数量限制"),
):
    """搜索股票代码"""
    try:
        # 获取 A 股列表
        df = ak.stock_info_a_code_name()

        if q:
            q = q.upper()
            # 按代码或名称匹配
            mask = df["code"].str.contains(q) | df["name"].str.contains(q, case=False, na=False)
            df = df[mask]

        results = []
        for _, row in df.head(limit).iterrows():
            code = row["code"]
            # 添加市场后缀
            if code.startswith(("6", "9")):
                suffix = ".SH"
            elif code.startswith(("0", "2", "3")):
                suffix = ".SZ"
            else:
                suffix = ".BJ"

            results.append({
                "symbol": f"{code}{suffix}",
                "name": row["name"],
            })

        return {"data": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/realtime/{symbol}")
async def get_realtime(symbol: str):
    """获取实时行情"""
    try:
        code = format_symbol(symbol)
        market = get_market_prefix(code)
        full_symbol = f"{market}{code}"

        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code]

        if row.empty:
            raise HTTPException(status_code=404, detail="股票未找到")

        r = row.iloc[0]
        return {
            "symbol": symbol,
            "name": r["名称"],
            "price": float(r["最新价"]) if r["最新价"] else 0,
            "change": float(r["涨跌额"]) if r["涨跌额"] else 0,
            "changePercent": float(r["涨跌幅"]) if r["涨跌幅"] else 0,
            "open": float(r["今开"]) if r["今开"] else 0,
            "high": float(r["最高"]) if r["最高"] else 0,
            "low": float(r["最低"]) if r["最低"] else 0,
            "volume": float(r["成交量"]) if r["成交量"] else 0,
            "amount": float(r["成交额"]) if r["成交额"] else 0,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
