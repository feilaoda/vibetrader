"""
VibeTrader AKShare API Backend
A股数据代理服务
"""

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from datetime import datetime, timedelta
import re
import urllib.request
import asyncio
import threading
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
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

CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else None

def _now_cn() -> datetime:
    return datetime.now(CN_TZ) if CN_TZ else datetime.now()

from actions import router as actions_router
from paper import router as paper_router
from fundamentals import router as fundamentals_router
from industry import router as industry_router
from crypto import router as crypto_router

app.include_router(actions_router, prefix="/api")
app.include_router(paper_router, prefix="/api")
app.include_router(fundamentals_router, prefix="/api")
app.include_router(industry_router, prefix="/api")
app.include_router(crypto_router, prefix="/api")

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
    elif code.startswith(("0", "2", "3", "1")):
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
        from akshare_guard import get_status
        
        ak_period = PERIOD_MAP.get(period, "daily")
        
        klines, source = get_klines_with_cache(
            symbol=symbol,
            period=ak_period,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
        
        return {
            "data": klines,
            "symbol": symbol,
            "period": period,
            "source": source,
            "volume_unit": "shares",
            "api_status": get_status(),
        }

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
        from akshare_guard import get_status
        
        ak_period = PERIOD_MAP.get(period, "daily")
        klines, source = force_sync(symbol, ak_period)
        
        return {
            "success": True,
            "data": klines,
            "symbol": symbol,
            "period": period,
            "count": len(klines),
            "source": source,
            "volume_unit": "shares",
            "api_status": get_status(),
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


# 自选股 API
from pydantic import BaseModel

class WatchlistItemModel(BaseModel):
    symbol: str
    market: str
    name: Optional[str] = None
    addedAt: Optional[int] = None

@app.get("/api/watchlist")
async def get_watchlist():
    """获取自选股列表"""
    from watchlist import load_watchlist
    return {"data": load_watchlist()}

@app.post("/api/watchlist")
async def add_watchlist(item: WatchlistItemModel):
    """添加自选股"""
    from watchlist import add_to_watchlist
    import time
    
    data = item.dict()
    if not data.get("addedAt"):
        data["addedAt"] = int(time.time() * 1000)
        
    updated_list = add_to_watchlist(data)
    return {"success": True, "data": updated_list}

@app.delete("/api/watchlist")
async def remove_watchlist(symbol: str = Query(...), market: str = Query(...)):
    """移除自选股"""
    from watchlist import remove_from_watchlist
    updated_list = remove_from_watchlist(symbol, market)
    return {"success": True, "data": updated_list}

@app.put("/api/watchlist/sync")
async def sync_watchlist_endpoint(items: list[dict]):
    """同步完整自选股列表 (覆盖)"""
    from watchlist import sync_watchlist
    updated_list = sync_watchlist(items)
    return {"success": True, "data": updated_list}

@app.post("/api/watchlist/sync_daily")
async def sync_watchlist_daily(market: str = Query("ashare", description="市场: ashare / us / crypto")):
    """手动同步所有自选股最新日线数据"""
    from watchlist import load_watchlist
    from cache import force_sync
    from akshare_guard import get_status

    items = [i for i in load_watchlist() if i.get("market") == market]
    results = []
    success_count = 0
    error_count = 0

    for item in items:
        symbol = item.get("symbol")
        if not symbol:
            continue
        try:
            klines, source = force_sync(symbol, "daily")
            results.append({
                "symbol": symbol,
                "count": len(klines),
                "source": source
            })
            success_count += 1
        except Exception as e:
            error_count += 1
            results.append({
                "symbol": symbol,
                "error": str(e)
            })

    return {
        "success": True,
        "market": market,
        "total": len(items),
        "success_count": success_count,
        "error_count": error_count,
        "results": results,
        "api_status": get_status(),
    }


# AI 分析 API
class AnalyzeRequest(BaseModel):
    symbol: str
    klines: list
    model: Optional[str] = None
    user_input: Optional[str] = None
    transient_context: Optional[str] = None
    mode: Optional[str] = None
    context_config: Optional[dict] = None


class SystemPromptCreate(BaseModel):
    name: Optional[str] = None
    prompt: str
    set_active: Optional[bool] = False
    symbol: Optional[str] = None


class SystemPromptUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    set_active: Optional[bool] = None
    symbol: Optional[str] = None

@app.post("/api/analyze")
async def analyze_stock(request: AnalyzeRequest, http_request: Request):
    """请求 AI 分析股票"""
    from llm import llm_service
    from fastapi.responses import StreamingResponse
    
    if not llm_service.is_configured():
        raise HTTPException(status_code=400, detail="LLM not configured")
        
    response = llm_service.analyze_stock(
        request.symbol,
        request.klines,
        request.model,
        request.user_input,
        request.mode or "assistant",
        request.context_config or {},
        request.transient_context
    )
    
    if isinstance(response, str):
         # 错误信息
         raise HTTPException(status_code=500, detail=response)
         
    async def iter_response():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()
        done = threading.Event()
        stop = threading.Event()

        def worker():
            try:
                for chunk in response:
                    if stop.is_set():
                        break
                    if chunk:
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                if not stop.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, f"\nError: {e}")
            finally:
                done.set()
                try:
                    if hasattr(response, "close"):
                        response.close()
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

        try:
            while True:
                if await http_request.is_disconnected():
                    stop.set()
                    break
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.2)
                    yield chunk
                except asyncio.TimeoutError:
                    if done.is_set() and queue.empty():
                        break
        finally:
            stop.set()

    return StreamingResponse(iter_response(), media_type="text/plain")

@app.get("/api/system_prompts")
async def list_system_prompts(symbol: str = Query(..., description="股票代码")):
    """获取系统提示词模板列表 + 当前标的关联模板"""
    from db import list_prompt_templates, get_symbol_prompt_template, get_prompt_template_by_name
    from prompts import DEFAULT_SYSTEM_PROMPT_NAME
    items = list_prompt_templates()
    active = get_symbol_prompt_template(symbol)
    default_tmpl = get_prompt_template_by_name(DEFAULT_SYSTEM_PROMPT_NAME)
    return {
        "data": items,
        "active_template_id": active.get("id") if active else None,
        "default_template_id": default_tmpl.get("id") if default_tmpl else None,
        "default_template_name": DEFAULT_SYSTEM_PROMPT_NAME,
    }

@app.post("/api/system_prompts")
async def create_system_prompt(payload: SystemPromptCreate):
    """创建系统提示词模板"""
    from db import create_prompt_template, set_symbol_prompt_template
    try:
        result = create_prompt_template(
            payload.name or "Untitled",
            payload.prompt,
            False
        )
        template_id = result.get("id")
        if payload.set_active and payload.symbol and template_id:
            set_symbol_prompt_template(payload.symbol, template_id)
        return {"success": True, "id": template_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/system_prompts/{prompt_id}")
async def update_system_prompt(prompt_id: int, payload: SystemPromptUpdate):
    """更新系统提示词模板"""
    from db import update_prompt_template, set_symbol_prompt_template
    try:
        update_prompt_template(
            prompt_id,
            name=payload.name,
            prompt=payload.prompt
        )
        if payload.set_active and payload.symbol:
            set_symbol_prompt_template(payload.symbol, prompt_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/system_prompts/{prompt_id}/activate")
async def activate_system_prompt(prompt_id: int, symbol: str = Query(..., description="股票代码")):
    """设置为当前标的激活模板"""
    from db import set_symbol_prompt_template
    ok = set_symbol_prompt_template(symbol, prompt_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"success": True}

@app.delete("/api/system_prompts/{prompt_id}")
async def delete_system_prompt(prompt_id: int):
    """删除系统提示词模板"""
    from db import delete_prompt_template
    delete_prompt_template(prompt_id)
    return {"success": True}

@app.get("/api/history")
async def get_history(symbol: Optional[str] = None, limit: int = 50):
    """获取聊天记录"""
    from db import get_history
    history = get_history(symbol, limit)
    return {"data": [h.dict() for h in history]}

@app.post("/api/history/{msg_id}/favorite")
async def toggle_favorite(msg_id: int):
    """收藏/取消收藏消息"""
    from db import toggle_favorite
    new_status = toggle_favorite(msg_id)
    return {"success": True, "is_favorite": new_status}

@app.get("/api/config/llm_models")
async def get_llm_models():
    """获取支持的 LLM 模型列表"""
    from config import get_llm_config
    config = get_llm_config()
    return {
        "models": config["available_models"],
        "current_model": config["model"], 
        "configured": bool(config["api_key"])
    }

@app.get("/api/llm/ping")
async def llm_ping(model: Optional[str] = Query(None, description="模型ID (可选)")):
    """诊断当前 LLM provider 与 base_url"""
    from llm import LLMService
    from config import get_llm_config, AVAILABLE_MODELS, PROVIDERS, LLM_MODEL

    target_model = model or LLM_MODEL
    llm = LLMService()
    provider = llm._get_provider_for_model(target_model)
    provider_cfg = PROVIDERS.get(provider, {})
    known_models = {m.get("id") for m in AVAILABLE_MODELS}

    config = get_llm_config()
    return {
        "model": target_model,
        "known_model": target_model in known_models,
        "provider": provider,
        "base_url": provider_cfg.get("base_url"),
        "provider_configured": bool(provider_cfg.get("api_key")),
        "current_model": config.get("model"),
        "providers": config.get("providers", {})
    }

@app.on_event("startup")
async def startup_event():
    """Start background tasks"""
    import os
    import asyncio
    from fastapi.concurrency import run_in_threadpool
    from symbols import get_all_symbols, fetch_all_symbols_remote, load_symbols_from_disk
    from paper_strategy_runner import start_strategy_scheduler
    from strategy_optimizer import start_optimizer_scheduler
    from industry_scheduler import start_industry_scheduler
    from watchlist_scheduler import start_watchlist_scheduler
    
    # Check if we need to refresh cache on startup
    df = load_symbols_from_disk()
    if df is None:
        sync_on_start = os.getenv("SYMBOLS_SYNC_ON_STARTUP", "1").lower() not in ("0", "false", "no", "off")
        if not sync_on_start:
            print("[Startup] No symbol cache found; skip remote sync (SYMBOLS_SYNC_ON_STARTUP=0)")
        else:
            print("[Startup] No symbol cache found, fetching in background...")
            async def _sync_symbols():
                try:
                    await run_in_threadpool(fetch_all_symbols_remote)
                except Exception as e:
                    print(f"[Startup] Symbol sync failed: {e}")
            asyncio.create_task(_sync_symbols())
    else:
        print(f"[Startup] Loaded {len(df)} symbols from disk cache")

    start_strategy_scheduler()
    start_optimizer_scheduler()
    start_industry_scheduler()
    start_watchlist_scheduler()


@app.get("/api/symbols")
async def search_symbols(
    q: str = Query("", description="搜索关键词"),
    limit: int = Query(50, description="返回数量限制"),
):
    """搜索股票代码 (含 ETF) - Disk Cached"""
    try:
        from symbols import get_all_symbols
        from fastapi.concurrency import run_in_threadpool
        
        # Use run_in_threadpool for disk I/O if needed, though get_all_symbols is fast usually
        df = await run_in_threadpool(get_all_symbols)
        
        if df is None or df.empty:
             return {"data": []}

        if q:
            q = q.upper()
            mask = df["symbol"].str.contains(q) | df["code"].str.contains(q) | df["name"].str.contains(q, case=False, na=False)
            df = df[mask]

        if df.empty and q:
            code = q.replace(".SH", "").replace(".SZ", "").replace("SH", "").replace("SZ", "").replace(".", "")
            if code.isdigit() and len(code) in (5, 6):
                if code.startswith(("6", "9", "5")):
                    symbol = f"{code}.SH"
                elif code.startswith(("0", "2", "3", "1")):
                    symbol = f"{code}.SZ"
                else:
                    symbol = f"{code}.BJ"
                return {"data": [{"symbol": symbol, "code": code, "name": ""}]}

        return {"data": df.head(limit).to_dict(orient="records")}

    except Exception as e:
        print(f"Error searching symbols: {e}")
        return {"data": []}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/symbols/list")
def list_symbols_api(
    q: str = Query("", description="搜索关键词"),
    limit: int = Query(200, description="返回数量限制"),
    offset: int = Query(0, description="偏移量"),
):
    """分页列出股票代码"""
    try:
        from symbols import get_all_symbols
        df = get_all_symbols()
        if df is None or df.empty:
            return {"data": [], "total": 0}
        if q:
            q = q.upper()
            mask = df["symbol"].str.contains(q) | df["code"].str.contains(q) | df["name"].str.contains(q, case=False, na=False)
            df = df[mask]
        total = int(len(df))
        if offset < 0:
            offset = 0
        if limit <= 0:
            limit = 200
        df = df.iloc[offset:offset + limit]
        return {"data": df.to_dict(orient="records"), "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        print(f"Error listing symbols: {e}")
        return {"data": [], "total": 0}


@app.get("/api/symbols/stats")
def symbols_stats_api():
    """返回符号库统计"""
    try:
        from symbols import get_all_symbols
        df = get_all_symbols()
        if df is None or df.empty:
            return {"count": 0, "sh": 0, "sz": 0, "bj": 0}
        count = int(len(df))
        sh = int((df["symbol"].str.endswith(".SH")).sum())
        sz = int((df["symbol"].str.endswith(".SZ")).sum())
        bj = int((df["symbol"].str.endswith(".BJ")).sum())
        return {"count": count, "sh": sh, "sz": sz, "bj": bj}
    except Exception as e:
        print(f"Error counting symbols: {e}")
        return {"count": 0, "sh": 0, "sz": 0, "bj": 0}


@app.get("/api/screening/runs")
def list_screening_runs_api(limit: int = Query(20, description="返回数量限制")):
    from db import list_screening_runs
    return {"data": list_screening_runs(int(limit))}


@app.get("/api/screening/runs/{run_id}")
def get_screening_run_api(run_id: int):
    from db import get_screening_run
    data = get_screening_run(run_id)
    if not data:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"data": data}


@app.delete("/api/screening/runs/{run_id}")
def delete_screening_run_api(run_id: int):
    from db import delete_screening_run, get_screening_run
    existing = get_screening_run(run_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Run not found")
    delete_screening_run(run_id)
    return {"success": True}


@app.get("/api/screening/results")
def list_screening_results_api(
    run_id: int = Query(..., description="Run ID"),
    action: Optional[str] = Query(None, description="Action filter"),
    limit: int = Query(200, description="Limit"),
    offset: int = Query(0, description="Offset"),
):
    from db import list_screening_results
    return {"data": list_screening_results(run_id, action=action, limit=limit, offset=offset)}


@app.delete("/api/screening/results")
def delete_screening_results_api(
    run_id: int = Query(..., description="Run ID"),
    actions: Optional[str] = Query(None, description="Comma separated actions, e.g. ERROR,NO_DATA"),
):
    from db import delete_screening_results
    action_list = None
    if actions:
        action_list = [a.strip() for a in actions.split(",") if a.strip()]
    deleted = delete_screening_results(run_id, action_list)
    return {"success": True, "deleted": deleted}


@app.get("/api/screening/summary")
def get_screening_summary_api(run_id: int = Query(..., description="Run ID")):
    from db import get_screening_summary
    return {"data": get_screening_summary(run_id)}


@app.get("/api/realtime/{symbol}")
async def get_realtime(symbol: str):
    """获取实时行情"""
    try:
        from us_indices import is_us_index_symbol, resolve_us_index_ticker, get_us_index_label
        from yahoo import fetch_quote

        if is_us_index_symbol(symbol):
            ticker = resolve_us_index_ticker(symbol) or symbol
            quote = fetch_quote(ticker)
            if not quote:
                return {
                    "symbol": symbol,
                    "name": get_us_index_label(ticker) or ticker,
                    "price": 0,
                    "change": 0,
                    "changePercent": 0,
                    "open": 0,
                    "high": 0,
                    "low": 0,
                    "volume": 0,
                    "amount": 0,
                    "timestamp": int(datetime.utcnow().timestamp() * 1000),
                    "source": "yahoo",
                    "stale": True,
                    "error": "yahoo_quote_failed"
                }
            ts = quote.get("time")
            try:
                ts = int(float(ts) * 1000) if ts else int(datetime.utcnow().timestamp() * 1000)
            except Exception:
                ts = int(datetime.utcnow().timestamp() * 1000)
            return {
                "symbol": symbol,
                "name": get_us_index_label(ticker) or ticker,
                "price": quote.get("price") or 0,
                "change": quote.get("change") or 0,
                "changePercent": quote.get("changePercent") or 0,
                "open": 0,
                "high": 0,
                "low": 0,
                "volume": 0,
                "amount": 0,
                "timestamp": ts,
                "source": "yahoo",
                "stale": False
            }

        code = format_symbol(symbol)
        
        is_etf = code.startswith(("15", "16", "5"))

        from akshare_guard import should_skip_remote, record_failure, record_success, get_status, throttle
        from cache import get_klines_with_cache, build_daily_kline_from_minutes

        scope = "realtime_etf" if is_etf else "realtime_stock"
        scope_tencent = "realtime_tencent_etf" if is_etf else "realtime_tencent_stock"
        today_iso = _now_cn().strftime("%Y-%m-%d")
        today_str = _now_cn().strftime("%Y%m%d")

        def _safe_float(value) -> float:
            try:
                if value is None:
                    return 0
                if isinstance(value, str):
                    text = value.strip().replace(",", "")
                    if text in ("", "--", "None", "nan"):
                        return 0
                    return float(text)
                num = float(value)
                if num != num or num in (float("inf"), float("-inf")):
                    return 0
                return num
            except Exception:
                return 0

        def fetch_tencent_realtime() -> Optional[dict]:
            if should_skip_remote(scope=scope_tencent):
                return None
            market_prefix = get_market_prefix(symbol)
            url = f"https://qt.gtimg.cn/q={market_prefix}{code}"

            def _do():
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.read()

            raw = retry_request(_do)
            if not raw:
                return None
            try:
                text = raw.decode("gbk", errors="ignore")
            except Exception:
                text = raw.decode("utf-8", errors="ignore")
            match = re.search(r'="([^"]+)"', text)
            if not match:
                return None
            parts = match.group(1).split("~")
            if len(parts) < 35:
                return None

            name = parts[1] if len(parts) > 1 else None
            price = _safe_float(parts[3] if len(parts) > 3 else 0)
            prev_close = _safe_float(parts[4] if len(parts) > 4 else 0)
            open_p = _safe_float(parts[5] if len(parts) > 5 else 0)
            volume_lot = _safe_float(parts[6] if len(parts) > 6 else 0)
            change = _safe_float(parts[31] if len(parts) > 31 else (price - prev_close))
            change_pct = _safe_float(parts[32] if len(parts) > 32 else ((change / prev_close * 100) if prev_close else 0))
            high = _safe_float(parts[33] if len(parts) > 33 else 0)
            low = _safe_float(parts[34] if len(parts) > 34 else 0)
            amount = _safe_float(parts[37] if len(parts) > 37 else 0) * 10000
            time_str = parts[30] if len(parts) > 30 else ""

            ts = int(_now_cn().timestamp() * 1000)
            if time_str and ":" in time_str:
                try:
                    dt = datetime.strptime(f"{today_iso} {time_str}", "%Y-%m-%d %H:%M:%S")
                    if CN_TZ:
                        dt = dt.replace(tzinfo=CN_TZ)
                    ts = int(dt.timestamp() * 1000)
                except Exception:
                    pass

            record_success(scope_tencent)
            return {
                "symbol": symbol,
                "name": name,
                "price": price,
                "change": change,
                "changePercent": change_pct,
                "open": open_p,
                "high": high,
                "low": low,
                "volume": volume_lot * 100,
                "amount": amount,
                "timestamp": ts,
                "source": "tencent",
                "stale": False,
                "api_status": get_status(scope_tencent),
            }

        def build_summary_from_daily() -> Optional[dict]:
            try:
                klines, _ = get_klines_with_cache(symbol, period="daily", limit=1)
            except Exception:
                return None
            if not klines:
                return None
            k = klines[-1]
            try:
                close = float(k.get("close") or 0)
                open_p = float(k.get("open") or 0)
                high = float(k.get("high") or 0)
                low = float(k.get("low") or 0)
                vol = float(k.get("volume") or 0)
            except Exception:
                return None
            change = close - open_p if open_p else 0
            change_pct = (change / open_p * 100) if open_p else 0
            ts = int(k.get("closeTime") or k.get("openTime") or (_now_cn().timestamp() * 1000))
            date_str = k.get("date")
            today_str = _now_cn().strftime("%Y-%m-%d")
            stale = bool(date_str and date_str != today_str)
            return {
                "symbol": symbol,
                "name": None,
                "price": close,
                "change": change,
                "changePercent": change_pct,
                "open": open_p,
                "high": high,
                "low": low,
                "volume": vol,
                "amount": 0,
                "timestamp": ts,
                "source": "daily_summary",
                "stale": stale,
            }

        def build_summary_from_intraday() -> Optional[dict]:
            try:
                k = build_daily_kline_from_minutes(code, today_str, force_remote=False)
            except Exception:
                return None
            if not k:
                return None
            try:
                close = float(k.get("close") or 0)
                open_p = float(k.get("open") or 0)
                high = float(k.get("high") or 0)
                low = float(k.get("low") or 0)
                vol = float(k.get("volume") or 0)
            except Exception:
                return None
            change = close - open_p if open_p else 0
            change_pct = (change / open_p * 100) if open_p else 0
            date_str = k.get("date")
            stale = bool(date_str and date_str != today_iso)
            ts = int(_now_cn().timestamp() * 1000)
            return {
                "symbol": symbol,
                "name": None,
                "price": close,
                "change": change,
                "changePercent": change_pct,
                "open": open_p,
                "high": high,
                "low": low,
                "volume": vol,
                "amount": 0,
                "timestamp": ts,
                "source": "intraday_summary",
                "stale": stale,
            }

        # Default to Tencent realtime first
        tencent_data = None
        try:
            tencent_data = fetch_tencent_realtime()
        except Exception as t_err:
            record_failure(f"realtime_tencent_failed: {t_err}", scope_tencent)
            tencent_data = None
        if tencent_data:
            return tencent_data

        if should_skip_remote(scope=scope):
            summary = build_summary_from_intraday() or build_summary_from_daily()
            if summary:
                summary["api_status"] = get_status(scope)
                summary["note"] = "realtime backoff"
                return summary
            raise HTTPException(status_code=503, detail="Realtime backoff active")

        def _ak_call(func):
            throttle(scope="akshare_realtime")
            return func()
        
        if is_etf:
            df = retry_request(lambda: _ak_call(ak.fund_etf_spot_em))
            row = df[df["代码"] == code]
        else:
            df = retry_request(lambda: _ak_call(ak.stock_zh_a_spot_em))
            row = df[df["代码"] == code]

        if row.empty:
            raise HTTPException(status_code=404, detail="股票/ETF 未找到")

        r = row.iloc[0]
        
        # Handle column name differences
        if is_etf:
            record_success(scope)
            price = _safe_float(r["最新价"] if "最新价" in r else 0)
            vol_raw = _safe_float(r["成交量"] if "成交量" in r else 0)
            amt_raw = _safe_float(r["成交额"] if "成交额" in r else 0)
            multiplier = 100
            if vol_raw > 0 and amt_raw > 0 and price > 0:
                ratio = (amt_raw / vol_raw) / price
                if ratio <= 2:
                    multiplier = 1
            return {
                "symbol": symbol,
                "name": r["名称"],
                "price": price,
                "change": _safe_float(r["涨跌额"] if "涨跌额" in r else 0),
                "changePercent": _safe_float(r["涨跌幅"] if "涨跌幅" in r else 0),
                "open": _safe_float(r["开盘价"] if "开盘价" in r else 0),
                "high": _safe_float(r["最高价"] if "最高价" in r else 0),
                "low": _safe_float(r["最低价"] if "最低价" in r else 0),
                "volume": vol_raw * multiplier,
                "amount": amt_raw,
                "timestamp": int(_now_cn().timestamp() * 1000),
                "source": "realtime",
                "stale": False,
                "api_status": get_status(scope),
            }
        else:
            record_success(scope)
            price = _safe_float(r["最新价"] if "最新价" in r else 0)
            vol_raw = _safe_float(r["成交量"] if "成交量" in r else 0)
            amt_raw = _safe_float(r["成交额"] if "成交额" in r else 0)
            multiplier = 100
            if vol_raw > 0 and amt_raw > 0 and price > 0:
                ratio = (amt_raw / vol_raw) / price
                if ratio <= 2:
                    multiplier = 1
            return {
                "symbol": symbol,
                "name": r["名称"],
                "price": price,
                "change": _safe_float(r["涨跌额"] if "涨跌额" in r else 0),
                "changePercent": _safe_float(r["涨跌幅"] if "涨跌幅" in r else 0),
                "open": _safe_float(r["今开"] if "今开" in r else 0),
                "high": _safe_float(r["最高"] if "最高" in r else 0),
                "low": _safe_float(r["最低"] if "最低" in r else 0),
                "volume": vol_raw * multiplier,
                "amount": amt_raw,
                "timestamp": int(_now_cn().timestamp() * 1000),
                "source": "realtime",
                "stale": False,
                "api_status": get_status(scope),
            }

    except HTTPException:
        raise
    except Exception as e:
        from akshare_guard import record_failure, get_status
        record_failure(f"realtime_failed: {e}", scope)
        summary = None
        try:
            summary = build_summary_from_intraday() or build_summary_from_daily()
        except Exception:
            summary = None
        if summary:
            summary["api_status"] = get_status(scope)
            summary["error"] = f"{type(e).__name__}: {e}"
            return summary
        import traceback
        print("[realtime] Error fetching realtime data")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
