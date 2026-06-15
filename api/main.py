"""
VibeTrader AKShare API Backend
A股数据代理服务
"""

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from datetime import datetime, timedelta
import os
import re
import urllib.request
import asyncio
import threading
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
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
CN_INDEX_SH_CODES = {"000001", "000016", "000300", "000688", "000852", "000905", "000985"}
CN_INDEX_SZ_CODES = {"399001", "399005", "399006"}

def _now_cn() -> datetime:
    return datetime.now(CN_TZ) if CN_TZ else datetime.now()


def _flag_enabled(name: str, default: str = "1") -> bool:
    value = os.getenv(name, default)
    return str(value).strip().lower() not in ("0", "false", "no", "off")

from actions import router as actions_router
from paper import router as paper_router
from fundamentals import router as fundamentals_router
from industry import router as industry_router
from crypto import router as crypto_router
from push import router as push_router
from screening import router as screening_router
from aitrader_router import router as aitrader_router
from kdj_screener import router as kdj_screener_router

app.include_router(actions_router, prefix="/api")
app.include_router(paper_router, prefix="/api")
app.include_router(fundamentals_router, prefix="/api")
app.include_router(industry_router, prefix="/api")
app.include_router(crypto_router, prefix="/api")
app.include_router(push_router, prefix="/api")
app.include_router(screening_router, prefix="/api")
app.include_router(aitrader_router, prefix="/api")
app.include_router(kdj_screener_router, prefix="/api")

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
    sym = (symbol or "").upper()
    code = format_symbol(symbol)
    if sym.endswith(".SH"):
        return "sh"
    if sym.endswith(".SZ"):
        return "sz"
    if code in CN_INDEX_SH_CODES:
        return "sh"
    if code in CN_INDEX_SZ_CODES:
        return "sz"
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith(("0", "2", "3", "1")):
        return "sz"
    elif code.startswith("8") or code.startswith("4"):
        return "bj"  # 北交所
    return "sh"


def _is_us_equity_symbol(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    if not sym:
        return False
    if sym.endswith(".US"):
        return True
    if sym.endswith((".SH", ".SZ", ".BJ", ".HK", ".IDX")):
        return False
    if sym.endswith(("USDT", "USDC", "BUSD", "FDUSD", "PERP")):
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym))


def _resolve_us_ticker(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if sym.endswith(".US"):
        sym = sym[:-3]
    return re.sub(r"[^A-Z0-9.\-]", "", sym)


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
        from cache import force_sync, DAILY_PERIODS
        from akshare_guard import get_status
        
        ak_period = PERIOD_MAP.get(period, "daily")
        include_intraday = True if ak_period == "daily" else False
        klines, source = force_sync(symbol, ak_period, include_intraday=include_intraday)
        
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


class SymbolNoteCreateModel(BaseModel):
    symbol: str
    content: str
    note_date: Optional[str] = None
    is_global: Optional[bool] = False


class SymbolNoteUpdateModel(BaseModel):
    content: str
    note_date: Optional[str] = None
    is_global: Optional[bool] = False


def _normalize_note_date(raw: Optional[str]) -> str:
    if not raw:
        return _now_cn().strftime("%Y-%m-%d")
    text = str(raw).strip()
    if not text:
        return _now_cn().strftime("%Y-%m-%d")
    if re.fullmatch(r"\d{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    raise HTTPException(status_code=400, detail="Invalid note_date, use YYYY-MM-DD")

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
            klines, source = force_sync(symbol, "daily", include_intraday=True)
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


@app.get("/api/notes")
async def get_symbol_notes(
    symbol: str = Query(..., description="股票代码"),
    limit: int = Query(200, description="返回数量"),
):
    from db import list_symbol_notes
    sym = (symbol or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    rows = list_symbol_notes(sym, limit=limit)
    return {"data": rows}


@app.post("/api/notes")
async def create_symbol_note(payload: SymbolNoteCreateModel):
    from db import create_symbol_note as db_create_symbol_note
    sym = (payload.symbol or "").upper().strip()
    content = (payload.content or "").strip()
    if not sym:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    if not content:
        raise HTTPException(status_code=400, detail="Empty note content")
    note = db_create_symbol_note(sym, _normalize_note_date(payload.note_date), content, bool(payload.is_global))
    return {"success": True, "data": note}


@app.put("/api/notes/{note_id}")
async def update_symbol_note(note_id: int, payload: SymbolNoteUpdateModel):
    from db import update_symbol_note as db_update_symbol_note
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Empty note content")
    note = db_update_symbol_note(note_id, _normalize_note_date(payload.note_date), content, bool(payload.is_global))
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"success": True, "data": note}


@app.delete("/api/notes/{note_id}")
async def delete_symbol_note(note_id: int):
    from db import delete_symbol_note as db_delete_symbol_note
    ok = db_delete_symbol_note(note_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"success": True}


# AI 分析 API
class AnalyzeRequest(BaseModel):
    symbol: str
    klines: list
    model: Optional[str] = None
    user_input: Optional[str] = None
    transient_context: Optional[str] = None
    mode: Optional[str] = None
    regression_date: Optional[str] = None
    prompt_id: Optional[int] = None
    prompt_text: Optional[str] = None
    prompt_params: Optional[dict] = None
    context_config: Optional[dict] = None


class SystemPromptCreate(BaseModel):
    name: Optional[str] = None
    prompt: str
    params: Optional[dict] = None
    set_active: Optional[bool] = False
    symbol: Optional[str] = None


class SystemPromptUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    params: Optional[dict] = None
    set_active: Optional[bool] = None
    symbol: Optional[str] = None


class SystemPromptRender(BaseModel):
    prompt_id: Optional[int] = None
    prompt_text: Optional[str] = None
    params: Optional[dict] = None
    symbol: Optional[str] = None
    regression_date: Optional[str] = None
    klines: Optional[list] = None

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
        request.transient_context,
        request.regression_date,
        request.prompt_id,
        request.prompt_text,
        request.prompt_params
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
async def list_system_prompts(symbol: str = Query("", description="股票代码")):
    """获取系统提示词模板列表 + 当前标的关联模板"""
    from db import list_prompt_templates, get_symbol_prompt_template, get_prompt_template_by_name
    from prompts import DEFAULT_SYSTEM_PROMPT_NAME
    items = list_prompt_templates()
    active = get_symbol_prompt_template(symbol) if symbol else None
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
            False,
            payload.params
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
            prompt=payload.prompt,
            params=payload.params
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


@app.post("/api/system_prompts/render")
async def render_system_prompt(payload: SystemPromptRender):
    """Render prompt template with params before use."""
    from db import get_prompt_template_by_id
    from cache import load_cache
    from llm import llm_service

    prompt_text = (payload.prompt_text or "").strip()
    params = payload.params or {}
    if payload.prompt_id:
        tmpl = get_prompt_template_by_id(int(payload.prompt_id))
        if tmpl:
            prompt_text = prompt_text or (tmpl.get("prompt") or "")
            base_params = tmpl.get("params") or {}
            params = {**base_params, **(params or {})}
    if not prompt_text:
        raise HTTPException(status_code=400, detail="prompt_text required")
    needs_cys13 = False
    try:
        needs_cys13 = bool(re.search(r"{\s*(CYS13|cys13)\s*}", prompt_text))
    except Exception:
        needs_cys13 = False

    klines = payload.klines or []
    if not klines and needs_cys13 and payload.symbol:
        try:
            cache = load_cache(payload.symbol, "daily")
            if cache and cache.get("klines"):
                klines = cache.get("klines")[-30:]
        except Exception:
            klines = []

    if klines:
        try:
            params = llm_service._augment_prompt_params(params, klines)  # noqa: SLF001
        except Exception:
            pass
    rendered = llm_service.build_system_prompt(
        payload.symbol or "",
        prompt_text,
        payload.regression_date,
        params
    )
    return {"prompt": rendered}

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

@app.post("/api/memory/clear")
async def clear_memory_api(symbol: str = Query(..., description="股票代码")):
    """清空该标的的记忆摘要，不影响聊天记录"""
    from db import save_memory, get_last_history_id
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    last_id = get_last_history_id(symbol)
    save_memory(symbol, "", last_id)
    return {"success": True, "last_message_id": last_id}

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
    if _flag_enabled("INDUSTRY_SCHEDULER_ENABLED", "1"):
        start_industry_scheduler()
    else:
        print("[Startup] Industry scheduler disabled (INDUSTRY_SCHEDULER_ENABLED=0)")
    if _flag_enabled("WATCHLIST_SCHEDULER_ENABLED", "1"):
        start_watchlist_scheduler()
    else:
        print("[Startup] Watchlist scheduler disabled (WATCHLIST_SCHEDULER_ENABLED=0)")


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


@app.get("/api/symbols/ensure")
async def ensure_symbol(
    symbol: str = Query(..., description="股票代码"),
    name: str | None = Query(None, description="可选名称（手工补齐）"),
):
    """Ensure a symbol exists in DB; fill name if possible."""
    from db import get_symbol_db, upsert_symbol_db
    symbol = (symbol or "").upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    if name:
        upsert_symbol_db(symbol, name=name)
        return {"symbol": symbol, "name": name, "source": "manual"}
    existing = get_symbol_db(symbol)
    if existing and existing.get("name"):
        return {"symbol": symbol, "name": existing.get("name") or "", "source": "db"}
    try:
        data = await get_realtime(symbol)
    except Exception:
        data = None
    if isinstance(data, dict):
        rt_name = data.get("name") or ""
        if rt_name:
            upsert_symbol_db(symbol, name=rt_name)
            return {"symbol": symbol, "name": rt_name, "source": data.get("source") or "realtime"}
    return {"symbol": symbol, "name": "", "source": "unknown"}


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


@app.post("/api/symbols/backfill_from_daily_klines")
def symbols_backfill_from_daily_klines(limit: Optional[int] = Query(None, description="最多反向同步多少个，默认全部")):
    """Backfill symbols table from locally cached daily kline symbols."""
    try:
        from db import backfill_symbols_from_daily_klines

        return {"success": True, "data": backfill_symbols_from_daily_klines(limit=limit)}
    except Exception as e:
        print(f"Error backfilling symbols from daily klines: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        from db import upsert_symbol_db
        from cache import get_klines_with_cache, build_daily_kline_from_minutes, load_cache

        def _maybe_upsert_symbol(payload: dict):
            try:
                name = payload.get("name")
                if name:
                    upsert_symbol_db(payload.get("symbol") or symbol, name=name)
            except Exception:
                pass

        recent_daily_cache = {}

        def _get_recent_daily_klines(sym: str, limit: int = 3) -> list[dict]:
            cache_key = (sym, limit)
            if cache_key in recent_daily_cache:
                return recent_daily_cache[cache_key]
            try:
                cache_data = load_cache(sym, "daily") or {}
                klines = cache_data.get("klines") or []
                recent_daily_cache[cache_key] = list(klines[-limit:]) if klines else []
            except Exception:
                recent_daily_cache[cache_key] = []
            return recent_daily_cache[cache_key]

        def _payload_trade_date(payload: dict) -> str:
            date_text = str(payload.get("date") or payload.get("tradeDate") or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                return date_text
            ts = payload.get("timestamp")
            try:
                if ts:
                    dt = datetime.fromtimestamp(int(float(ts)) / 1000, CN_TZ) if CN_TZ else datetime.fromtimestamp(int(float(ts)) / 1000)
                    return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
            return _now_cn().strftime("%Y-%m-%d")

        def _attach_volume_progress(payload: Optional[dict], compare_mode: str = "intraday") -> Optional[dict]:
            if not payload:
                return payload
            try:
                current_volume = float(payload.get("volume") or 0)
            except Exception:
                current_volume = 0.0
            if current_volume <= 0:
                return payload

            klines = _get_recent_daily_klines(payload.get("symbol") or symbol, limit=3)
            if not klines:
                return payload

            trade_date = _payload_trade_date(payload)
            previous_volume = 0.0
            for row in reversed(klines):
                row_date = str(row.get("date") or "").strip()
                if not row_date:
                    continue
                if row_date < trade_date:
                    try:
                        previous_volume = float(row.get("volume") or 0)
                    except Exception:
                        previous_volume = 0.0
                    break

            if previous_volume <= 0 and compare_mode == "intraday":
                try:
                    fallback_row = klines[-1]
                    fallback_date = str(fallback_row.get("date") or "").strip()
                    if fallback_date and fallback_date != trade_date:
                        previous_volume = float(fallback_row.get("volume") or 0)
                except Exception:
                    previous_volume = 0.0

            if previous_volume <= 0:
                return payload

            payload["previousVolume"] = previous_volume
            payload["volumeVsPreviousPct"] = current_volume / previous_volume * 100
            return payload

        def _build_us_summary_from_daily(sym: str, fallback_name: str = "") -> Optional[dict]:
            try:
                klines, src = get_klines_with_cache(
                    symbol=sym,
                    period="daily",
                    limit=2,
                    force_refresh=False,
                    include_intraday=False
                )
            except Exception:
                return None
            if not klines:
                return None
            last = klines[-1]
            prev_close = 0.0
            if len(klines) >= 2:
                try:
                    prev_close = float(klines[-2].get("close") or 0)
                except Exception:
                    prev_close = 0.0
            close = float(last.get("close") or 0)
            open_p = float(last.get("open") or close)
            high = float(last.get("high") or close)
            low = float(last.get("low") or close)
            volume = float(last.get("volume") or 0)
            if prev_close > 0:
                change = close - prev_close
                change_pct = (change / prev_close * 100)
            else:
                change = close - open_p
                change_pct = (change / open_p * 100) if open_p else 0
            ts = int(last.get("closeTime") or last.get("openTime") or (datetime.utcnow().timestamp() * 1000))
            return {
                "symbol": sym,
                "name": fallback_name or sym,
                "price": close,
                "change": change,
                "changePercent": change_pct,
                "open": open_p,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": 0,
                "timestamp": ts,
                "source": f"{src}_summary" if src else "daily_summary",
                "stale": True,
            }

        if is_us_index_symbol(symbol):
            ticker = resolve_us_index_ticker(symbol) or symbol
            quote = fetch_quote(ticker)
            if not quote:
                summary = _build_us_summary_from_daily(symbol, get_us_index_label(ticker) or ticker)
                if summary:
                    summary = _attach_volume_progress(summary, compare_mode="daily")
                    _maybe_upsert_symbol(summary)
                    return summary
                payload = {
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
                payload = _attach_volume_progress(payload, compare_mode="daily")
                _maybe_upsert_symbol(payload)
                return payload
            ts = quote.get("time")
            try:
                ts = int(float(ts) * 1000) if ts else int(datetime.utcnow().timestamp() * 1000)
            except Exception:
                ts = int(datetime.utcnow().timestamp() * 1000)
            payload = {
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
            payload = _attach_volume_progress(payload, compare_mode="intraday")
            _maybe_upsert_symbol(payload)
            return payload

        upper_symbol = (symbol or "").upper()
        if _is_us_equity_symbol(upper_symbol):
            ticker = _resolve_us_ticker(upper_symbol) or upper_symbol
            quote = fetch_quote(ticker)
            if not quote:
                summary = _build_us_summary_from_daily(symbol, ticker)
                if summary:
                    summary = _attach_volume_progress(summary, compare_mode="daily")
                    _maybe_upsert_symbol(summary)
                    return summary
                payload = {
                    "symbol": symbol,
                    "name": ticker,
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
                payload = _attach_volume_progress(payload, compare_mode="daily")
                _maybe_upsert_symbol(payload)
                return payload

            ts = quote.get("time")
            try:
                ts = int(float(ts) * 1000) if ts else int(datetime.utcnow().timestamp() * 1000)
            except Exception:
                ts = int(datetime.utcnow().timestamp() * 1000)

            payload = {
                "symbol": symbol,
                "name": quote.get("name") or ticker,
                "price": quote.get("price") or 0,
                "change": quote.get("change") or 0,
                "changePercent": quote.get("changePercent") or 0,
                "open": quote.get("open") or 0,
                "high": quote.get("high") or 0,
                "low": quote.get("low") or 0,
                "volume": quote.get("volume") or 0,
                "amount": 0,
                "timestamp": ts,
                "source": "yahoo",
                "stale": False
            }
            payload = _attach_volume_progress(payload, compare_mode="intraday")
            _maybe_upsert_symbol(payload)
            return payload

        code = format_symbol(symbol)

        from data_sources import DataType
        from data_sources.router import fetch as router_fetch
        from akshare_guard import get_status
        from trading_time import is_trading_day, now_cn
        from datetime import time as dtime

        scope = "realtime"
        now_dt = now_cn()
        today_iso = now_dt.strftime("%Y-%m-%d")
        today_str = now_dt.strftime("%Y%m%d")

        def _market_phase(now) -> str:
            if not is_trading_day(now):
                return "休市"
            t = now.time()
            if t < dtime(9, 30):
                return "盘前"
            if dtime(9, 30) <= t < dtime(11, 30) or dtime(13, 0) <= t < dtime(15, 0):
                return "盘中"
            if dtime(11, 30) <= t < dtime(13, 0):
                return "午盘"
            return "盘后"

        phase = _market_phase(now_dt)
        out_of_session = phase in ("盘前", "盘后", "休市")

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

        data, channel = router_fetch(
            DataType.REALTIME,
            channels=["tencent", "akshare"],
            symbol=symbol,
            code=code,
        )
        if data:
            data["api_status"] = get_status(scope)
            data["phase"] = phase
            if phase == "午盘":
                data["note"] = "午盘休市，以上为最新成交"
            if out_of_session:
                summary = build_summary_from_daily()
                if summary:
                    summary["api_status"] = get_status(scope)
                    summary["note"] = f"{phase}，使用最近交易日行情"
                    summary["stale"] = True
                    summary["phase"] = phase
                    summary = _attach_volume_progress(summary, compare_mode="daily")
                    return summary
                data["note"] = f"{phase}，行情可能为昨收"
                data["stale"] = True
                data = _attach_volume_progress(data, compare_mode="daily")
            else:
                data = _attach_volume_progress(data, compare_mode="intraday")
            _maybe_upsert_symbol(data)
            return data

        summary = build_summary_from_intraday() or build_summary_from_daily()
        if summary:
            summary["api_status"] = get_status(scope)
            summary["note"] = "realtime_unavailable"
            summary["phase"] = phase
            compare_mode = "daily" if summary.get("source") == "daily_summary" else "intraday"
            summary = _attach_volume_progress(summary, compare_mode=compare_mode)
            return summary
        raise HTTPException(status_code=503, detail="Realtime unavailable")

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
            compare_mode = "daily" if summary.get("source") == "daily_summary" else "intraday"
            summary = _attach_volume_progress(summary, compare_mode=compare_mode)
            return summary
        import traceback
        print("[realtime] Error fetching realtime data")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/datasource/status")
async def datasource_status(probe: bool = True):
    from data_sources import get_status as _get_status
    return _get_status(probe=probe)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
