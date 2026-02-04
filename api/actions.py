from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import duckdb
from db import get_connection

router = APIRouter()

class ActionPlanBase(BaseModel):
    symbol: str
    stock_name: str
    action: str  # Buy, Sell, Watch
    time_range: str
    description: str
    reasoning: str
    original_response: Optional[str] = None
    status: str = 'pending'
    model: Optional[str] = None

class ActionPlanCreate(ActionPlanBase):
    pass

class ActionPlan(ActionPlanBase):
    id: int
    created_at: datetime
    status: str
    model: Optional[str] = None

    class Config:
        from_attributes = True

@router.post("/actions", response_model=ActionPlan)
def create_action_plan(plan: ActionPlanCreate):
    conn = get_connection()
    try:
        # Insert
        conn.execute("""
            INSERT INTO action_plans (symbol, stock_name, action, time_range, description, reasoning, original_response, status, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (plan.symbol, plan.stock_name, plan.action, plan.time_range, plan.description, plan.reasoning, plan.original_response, plan.status, plan.model))
        
        # Get extracted ID
        res = conn.execute("SELECT currval('seq_action_id')").fetchone()
        new_id = res[0]
        
        # Fetch back
        row = conn.execute("SELECT * FROM action_plans WHERE id = ?", (new_id,)).fetchone()
        
        return _map_row_to_plan(row)
    finally:
        conn.close()

@router.get("/actions/{symbol}", response_model=List[ActionPlan])
def get_actions_by_symbol(symbol: str):
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM action_plans WHERE symbol = ? ORDER BY created_at DESC", (symbol,)).fetchall()
        return [_map_row_to_plan(row) for row in rows]
    finally:
        conn.close()

@router.get("/actions", response_model=List[ActionPlan])
def get_all_actions():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM action_plans ORDER BY created_at DESC").fetchall()
        return [_map_row_to_plan(row) for row in rows]
    finally:
        conn.close()

@router.patch("/actions/{plan_id}/status", response_model=ActionPlan)
def update_action_status(plan_id: int, status: str):
    """Update action plan status"""
    conn = get_connection()
    try:
        # Check current status
        row = conn.execute("SELECT * FROM action_plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
             raise HTTPException(status_code=404, detail="Plan not found")
        
        conn.execute("UPDATE action_plans SET status = ? WHERE id = ?", (status, plan_id))
        
        # Return updated plan
        row = conn.execute("SELECT * FROM action_plans WHERE id = ?", (plan_id,)).fetchone()
        return _map_row_to_plan(row)
    finally:
        conn.close()

def _map_row_to_plan(row):
    # Check column count to handle potential schema mismatch during dev
    # Assuming standard schema for now: 
    # id, symbol, stock_name, action, time_range, description, reasoning, created_at, original_response, status, model
    
    # If migrations are running correctly, we should have 11 columns
    status = 'pending'
    if len(row) >= 10:
        status = row[9]
        
    model = None
    if len(row) >= 11:
        model = row[10]
        
    return ActionPlan(
        id=row[0],
        symbol=row[1],
        stock_name=row[2],
        action=row[3],
        time_range=row[4],
        description=row[5],
        reasoning=row[6],
        created_at=row[7],
        original_response=row[8],
        status=status,
        model=model
    )

# Extraction Logic (Stub using LLM - will implement better prompt later)
class ActionExtractRequest(BaseModel):
    symbol: str
    text: str
    model: Optional[str] = None

@router.post("/extract_action")
def extract_action(request: ActionExtractRequest):
    from llm import llm_service
    
    # Call LLM to extract
    return llm_service.extract_action_plan(request.symbol, request.text, request.model)
