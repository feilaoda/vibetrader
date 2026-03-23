from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Literal, Optional
import sys

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from cache import get_klines_with_cache, _latest_trading_date
from llm import LLMService
from db import (
    add_aitrader_analysis_history,
    detect_aitrader_rule_market,
    get_paper_portfolio_context,
    get_aitrader_rule_profile,
    get_aitrader_analysis_history,
    list_aitrader_analysis_history,
    list_aitrader_rule_profiles,
    list_rule_indicators,
    get_rule_indicator_bucket_mapping,
    normalize_aitrader_rule_market,
    upsert_aitrader_rule_profile,
    upsert_rule_indicator_bucket_mapping,
    upsert_rule_indicator_setting,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aitrader.analysis.run_daily_kline_analysis import (  # noqa: E402
    ANALYSIS_PROMPT,
    _build_fusion_report,
    _build_rule_report,
    _build_snapshot_text,
    _slice_bars,
    _sort_and_dedupe_daily,
)

router = APIRouter()


class AITraderAnalyzeRequest(BaseModel):
    symbol: str
    engine: Literal["rule", "llm", "fusion"] = "rule"
    bars: int = 365
    end_date: Optional[str] = None
    model: Optional[str] = None
    mode: Literal["assistant", "chat"] = "assistant"
    force_refresh: bool = False
    save_history: bool = False
    strategy_id: Optional[int] = None
    use_portfolio_context: bool = True


class AITraderRuleProfileUpdateRequest(BaseModel):
    market: Literal["ashare", "etf", "us", "crypto"] = "ashare"
    rr_buy_downtrend: Optional[float] = None
    rr_buy_uptrend: Optional[float] = None
    require_close_above_ma20_downtrend: Optional[bool] = None
    buy_position_downtrend: Optional[str] = None
    watch_position_downtrend: Optional[str] = None
    buy_position_uptrend: Optional[str] = None
    watch_position_uptrend: Optional[str] = None
    watch_position_range: Optional[str] = None


class RuleIndicatorUpdateRequest(BaseModel):
    market: Literal["ashare", "etf", "us", "crypto"] = "ashare"
    enabled: Optional[bool] = None
    params: Optional[Dict[str, Any]] = None


class RuleIndicatorBucketUpdateRequest(BaseModel):
    bucket: str


def _parse_end_date(text: Optional[str]) -> date:
    value = (text or "").strip()
    if not value:
        return _latest_trading_date()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _fmt_ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _collect_text(response: object) -> str:
    if isinstance(response, str):
        return response
    chunks = []
    for chunk in response:
        if chunk:
            chunks.append(chunk)
    return "".join(chunks).strip()


def _analyze_sync(request: AITraderAnalyzeRequest) -> Dict[str, Any]:
    symbol = (request.symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if request.bars < 60:
        raise HTTPException(status_code=400, detail="bars must be >= 60")
    if request.bars > 1200:
        raise HTTPException(status_code=400, detail="bars must be <= 1200")

    end_d = _parse_end_date(request.end_date)
    fetch_start = end_d - timedelta(days=max(420, request.bars * 3))

    klines, source = get_klines_with_cache(
        symbol=symbol,
        period="daily",
        start_date=_fmt_ymd(fetch_start),
        end_date=_fmt_ymd(end_d),
        limit=max(request.bars * 2, request.bars + 120),
        force_refresh=bool(request.force_refresh),
        include_intraday=False,
    )
    klines = _slice_bars(_sort_and_dedupe_daily(klines), request.bars)
    if not klines:
        raise HTTPException(status_code=404, detail=f"no daily klines for {symbol}")

    first_date = str(klines[0].get("date") or "")
    last_date = str(klines[-1].get("date") or "")
    snapshot_text, snapshot_obj = _build_snapshot_text(klines)

    analysis_text = ""
    analysis_meta: Dict[str, Any] = {}

    if request.engine == "rule":
        profile_market = detect_aitrader_rule_market(symbol)
        profile = get_aitrader_rule_profile(profile_market)
        portfolio_context = (
            get_paper_portfolio_context(symbol=symbol, strategy_id=request.strategy_id)
            if request.use_portfolio_context
            else None
        )
        analysis_text, analysis_meta = _build_rule_report(
            symbol,
            klines,
            rule_profile=profile,
            rule_market=profile_market,
            portfolio_context=portfolio_context,
        )
    elif request.engine == "fusion":
        profile_market = detect_aitrader_rule_market(symbol)
        profile = get_aitrader_rule_profile(profile_market)
        portfolio_context = (
            get_paper_portfolio_context(symbol=symbol, strategy_id=request.strategy_id)
            if request.use_portfolio_context
            else None
        )
        llm = LLMService()
        analysis_text, analysis_meta = _build_fusion_report(
            symbol=symbol,
            klines=klines,
            rule_market=profile_market,
            rule_profile=profile,
            portfolio_context=portfolio_context,
            snapshot_text=snapshot_text or "",
            llm=llm,
            model=(request.model or None),
            mode=request.mode,
        )
    else:
        llm = LLMService()
        if not llm.is_configured():
            raise HTTPException(status_code=400, detail="LLM not configured")
        user_input = ANALYSIS_PROMPT.format(
            symbol=symbol,
            start_date=first_date,
            end_date=last_date,
            bars=len(klines),
        )
        context_config = {
            "enable_memory": False,
            "enable_retrieval": False,
            "disable_history": True,
            "history_limit": 0,
            "recent_limit": 0,
            "save_history": bool(request.save_history),
            "disable_indicator_context": True,
            "kline_rows_assistant": len(klines),
            "kline_rows_chat": len(klines),
        }
        response = llm.analyze_stock(
            symbol=symbol,
            klines=klines,
            model=(request.model or None),
            user_input=user_input,
            mode=request.mode,
            context_config=context_config,
            transient_context=snapshot_text or None,
        )
        analysis_text = _collect_text(response)
        if not analysis_text:
            raise HTTPException(status_code=502, detail="empty llm analysis")
        if analysis_text.lower().startswith("error"):
            raise HTTPException(status_code=502, detail=analysis_text)

    record_id = 0
    try:
        record_id = add_aitrader_analysis_history(
            symbol=symbol,
            engine=request.engine,
            model_id=request.model or "",
            bars=len(klines),
            start_date=first_date,
            end_date=last_date,
            source=source,
            analysis=analysis_text,
            analysis_meta=analysis_meta,
            snapshot=snapshot_obj,
        )
    except Exception as e:
        print(f"[AITrader] save history failed: {e}")

    return {
        "symbol": symbol,
        "engine": request.engine,
        "model": request.model or "",
        "source": source,
        "bars": len(klines),
        "range": {"start": first_date, "end": last_date},
        "snapshot": snapshot_obj,
        "analysis_meta": analysis_meta,
        "analysis": analysis_text,
        "record_id": record_id,
    }


@router.post("/aitrader/analyze")
async def analyze_aitrader(request: AITraderAnalyzeRequest):
    data = await run_in_threadpool(_analyze_sync, request)
    return {"data": data}


@router.get("/aitrader/history")
async def get_aitrader_history(symbol: str = "", limit: int = 50):
    data = await run_in_threadpool(list_aitrader_analysis_history, symbol, limit)
    return {"data": data}


@router.get("/aitrader/history/{record_id}")
async def get_aitrader_history_detail(record_id: int):
    item = await run_in_threadpool(get_aitrader_analysis_history, record_id)
    if not item:
        raise HTTPException(status_code=404, detail="history not found")
    return {"data": item}


@router.get("/aitrader/rule_profiles")
async def get_aitrader_rule_profiles():
    data = await run_in_threadpool(list_aitrader_rule_profiles)
    return {"data": data}


@router.get("/aitrader/rule_profile")
async def get_aitrader_rule_profile_api(market: str = "ashare"):
    market_norm = normalize_aitrader_rule_market(market)
    data = await run_in_threadpool(get_aitrader_rule_profile, market_norm)
    return {"data": data}


@router.post("/aitrader/rule_profile")
async def update_aitrader_rule_profile_api(request: AITraderRuleProfileUpdateRequest):
    payload = request.model_dump(exclude_unset=True)
    market = payload.pop("market", request.market)
    data = await run_in_threadpool(upsert_aitrader_rule_profile, market, payload)
    return {"data": data}


@router.get("/aitrader/indicators")
async def get_rule_indicators_api(market: str = "ashare"):
    market_norm = normalize_aitrader_rule_market(market)
    data = await run_in_threadpool(list_rule_indicators, market_norm)
    mapping = await run_in_threadpool(get_rule_indicator_bucket_mapping)
    return {
        "data": data,
        "meta": {
            "score_bucket_mapping": mapping,
        },
    }


@router.get("/aitrader/indicator_buckets")
async def get_rule_indicator_buckets_api():
    data = await run_in_threadpool(get_rule_indicator_bucket_mapping)
    return {"data": data}


@router.put("/aitrader/indicator_buckets/{category}")
async def update_rule_indicator_bucket_api(category: str, request: RuleIndicatorBucketUpdateRequest):
    bucket = (request.bucket or "").strip()
    ok = await run_in_threadpool(upsert_rule_indicator_bucket_mapping, category, bucket)
    if not ok:
        raise HTTPException(status_code=400, detail="invalid bucket mapping")
    return {"success": True}


@router.put("/aitrader/indicators/{indicator_id}")
async def update_rule_indicator_api(indicator_id: str, request: RuleIndicatorUpdateRequest):
    payload = request.model_dump(exclude_unset=True)
    market = payload.pop("market", request.market)
    enabled = payload.get("enabled")
    params = payload.get("params")
    market_norm = normalize_aitrader_rule_market(market)
    ok = await run_in_threadpool(upsert_rule_indicator_setting, indicator_id, market_norm, enabled, params)
    if not ok:
        raise HTTPException(status_code=404, detail="indicator not found")
    return {"success": True}
