import json
import os
import urllib.request
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db import get_push_settings, update_push_settings, get_symbol_push_setting, set_symbol_push_setting

router = APIRouter()

PUSH_API_URL = os.getenv("PUSH_API_URL", "http://127.0.0.1:8787/api/push")


class PushSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    chat_id: Optional[str] = None
    token: Optional[str] = None


class PushNotify(BaseModel):
    symbol: str
    text: str


@router.get("/push/settings")
def get_push_settings_api():
    return {"data": get_push_settings()}


@router.put("/push/settings")
def update_push_settings_api(payload: PushSettingsUpdate):
    data = update_push_settings(payload.model_dump(exclude_none=True))
    return {"data": data}


@router.get("/push/symbol")
def get_push_symbol(symbol: str = Query(..., description="股票/币种")):
    return {"data": get_symbol_push_setting(symbol)}


@router.put("/push/symbol")
def set_push_symbol(symbol: str = Query(..., description="股票/币种"), enabled: bool = Query(...)):
    ok = set_symbol_push_setting(symbol, enabled)
    return {"success": ok, "data": get_symbol_push_setting(symbol)}


@router.post("/push/notify")
def push_notify(payload: PushNotify):
    settings = get_push_settings()
    if not settings.get("enabled"):
        return {"success": False, "detail": "push_disabled"}
    token = settings.get("token") or ""
    chat_id = settings.get("chat_id") or ""
    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="push_not_configured")
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text_required")
    body = json.dumps({"chatId": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        PUSH_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {"raw": raw.decode("utf-8", errors="ignore")}
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"push_failed: {e}")
