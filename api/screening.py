from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
from db import (
    list_screening_runs, 
    list_screening_results, 
    get_latest_screening_run,
    get_screening_run,
    get_screening_summary
)

router = APIRouter()

@router.get("/screening/runs", response_model=Dict[str, Any])
def get_runs(limit: int = 20):
    """Get list of historical screening runs"""
    rows = list_screening_runs(limit)
    return {"data": rows}

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
