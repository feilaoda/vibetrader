from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, Any, List
from threading import Thread
from pydantic import BaseModel
from db import (
    list_screening_runs, 
    list_screening_results, 
    get_latest_screening_run,
    get_screening_run,
    get_screening_summary,
    get_screening_stats,
    create_screening_run
)
from quant.recommendation import run_recommendation_job
from config import LLM_MODEL

router = APIRouter()


class ScreeningRunRequest(BaseModel):
    engine: str = "fusion"
    universe: str = "watchlist"
    market: str = "all"
    limit: Optional[int] = None
    rule_min_score: float = 60
    ai_top_k: int = 20
    use_web: bool = False
    web_top_k: int = 5
    model: Optional[str] = None
    bars: int = 200
    sleep_sec: float = 0.0
    include_pass: bool = False
    symbols: Optional[List[str]] = None

@router.get("/screening/runs", response_model=Dict[str, Any])
def get_runs(limit: int = 20):
    """Get list of historical screening runs"""
    rows = list_screening_runs(limit)
    return {"data": rows}


@router.post("/screening/run", response_model=Dict[str, Any])
def start_screening(request: ScreeningRunRequest):
    """Start a new screening run (rule/ai/fusion)."""
    params = request.dict()
    engine = (params.get("engine") or "rule").strip().lower()
    model_id = params.get("model") or (LLM_MODEL if engine in ("ai", "fusion") else "rule")
    run_id = create_screening_run(
        model_id=model_id,
        universe=params.get("universe") or "watchlist",
        params=params,
        total=0,
    )
    worker = Thread(target=run_recommendation_job, args=(run_id, params), daemon=True)
    worker.start()
    return {"run_id": run_id}

@router.get("/screening/latest", response_model=Dict[str, Any])
def get_latest_results(action: Optional[str] = None):
    """Get results from the most recent completed run"""
    # 1. Get latest run
    # Note: get_latest_screening_run in db.py actually returns the latest *running* one? 
    # Let's check db.py implementation. 
    # db.py: "SELECT id FROM screening_runs WHERE status != 'completed' ORDER BY started_at DESC LIMIT 1"
    # That is for finding active runs. We want the latest *successful* run.
    
    # We will use list_screening_runs(1) to get the latest one
    runs = list_screening_runs(1)
    if not runs:
        return {"run": None, "results": []}
    
    last_run = runs[0]
    run_id = last_run.get("id") if isinstance(last_run, dict) else None
    if run_id is None:
        return {"run": None, "results": []}
    
    # 2. Get results
    results_rows = list_screening_results(run_id, action=action, limit=500)
    
    results = results_rows or []
    
    summary = get_screening_summary(run_id) or {}
    
    return {
        "run": {
            "id": last_run.get("id"),
            "started_at": last_run.get("started_at"),
            "status": last_run.get("status"),
            "total": last_run.get("total"),
            "processed": last_run.get("processed")
        },
        "summary": summary,
        "results": results
    }

@router.get("/screening/runs/{run_id}", response_model=Dict[str, Any])
def get_run_details(run_id: int, action: Optional[str] = None):
    """Get details and results for a specific run"""
    run_row = get_screening_run(run_id)
    if not run_row:
        raise HTTPException(status_code=404, detail="Run not found")
        
    results_rows = list_screening_results(run_id, action=action, limit=500)
    results = results_rows or []
    
    return {"run": run_row, "results": results}


@router.get("/screening/stats", response_model=Dict[str, Any])
def get_screening_stats_api(run_id: int):
    return {"data": get_screening_stats(run_id)}
