import os
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

FAILURE_THRESHOLD = 3
FAILURE_WINDOW_SECONDS = 600
BACKOFF_SECONDS = 1800

_states: Dict[str, Dict[str, Any]] = {}
_rate_states: Dict[str, Dict[str, Any]] = {}
_rate_lock = threading.Lock()

RATE_LIMIT_DEFAULT_SECONDS = float(os.getenv("AKSHARE_MIN_INTERVAL_SECONDS", "1.0"))


def _now() -> datetime:
    return datetime.now()


def _get_state(scope: str) -> Dict[str, Any]:
    state = _states.get(scope)
    if state is None:
        state = {
            "failure_count": 0,
            "last_failure_at": None,
            "backoff_until": None,
            "last_error": None,
            "last_error_at": None,
        }
        _states[scope] = state
    return state


def _get_rate_state(scope: str) -> Dict[str, Any]:
    state = _rate_states.get(scope)
    if state is None:
        state = {
            "next_allowed": 0.0,
            "last_call_at": None,
        }
        _rate_states[scope] = state
    return state


def _get_rate_limit_seconds(scope: str) -> float:
    env_key = f"AKSHARE_MIN_INTERVAL_{scope.upper()}"
    if env_key in os.environ:
        try:
            return float(os.getenv(env_key, "0") or 0.0)
        except Exception:
            return RATE_LIMIT_DEFAULT_SECONDS
    return RATE_LIMIT_DEFAULT_SECONDS


def throttle(scope: str = "default", min_interval: Optional[float] = None) -> float:
    """Throttle AkShare calls to avoid triggering IP bans."""
    interval = min_interval if min_interval is not None else _get_rate_limit_seconds(scope)
    if interval <= 0:
        return 0.0
    now = time.monotonic()
    with _rate_lock:
        state = _get_rate_state(scope)
        scheduled = max(now, float(state.get("next_allowed") or 0.0))
        state["next_allowed"] = scheduled + interval
    wait = scheduled - now
    if wait > 0:
        time.sleep(wait)
    with _rate_lock:
        state = _get_rate_state(scope)
        state["last_call_at"] = datetime.now()
    return max(wait, 0.0)


def _reset_state(scope: str) -> None:
    state = _get_state(scope)
    state["failure_count"] = 0
    state["last_failure_at"] = None
    state["backoff_until"] = None
    state["last_error"] = None
    state["last_error_at"] = None


def is_backoff_active(scope: str = "default") -> bool:
    state = _get_state(scope)
    backoff_until = state.get("backoff_until")
    if backoff_until is None:
        return False
    now = _now()
    if now < backoff_until:
        return True
    _reset_state(scope)
    return False


def should_skip_remote(force_remote: bool = False, scope: str = "default") -> bool:
    if force_remote:
        return False
    return is_backoff_active(scope)


def record_failure(error: str, scope: str = "default") -> bool:
    state = _get_state(scope)
    now = _now()
    last_failure_at = state.get("last_failure_at")
    if last_failure_at is not None:
        if (now - last_failure_at).total_seconds() > FAILURE_WINDOW_SECONDS:
            state["failure_count"] = 0
    state["failure_count"] += 1
    state["last_failure_at"] = now
    state["last_error"] = error
    state["last_error_at"] = now
    if state["failure_count"] >= FAILURE_THRESHOLD:
        state["backoff_until"] = now + timedelta(seconds=BACKOFF_SECONDS)
        return True
    return False


def record_success(scope: str = "default") -> None:
    _reset_state(scope)


def get_status(scope: str = "default") -> Dict[str, Any]:
    state = _get_state(scope)
    active = is_backoff_active(scope)
    rate_state = _get_rate_state(scope)
    return {
        "backoff_active": active,
        "backoff_until": state.get("backoff_until").isoformat() if state.get("backoff_until") else None,
        "failure_count": state.get("failure_count", 0),
        "last_error": state.get("last_error"),
        "last_error_at": state.get("last_error_at").isoformat() if state.get("last_error_at") else None,
        "backoff_seconds": BACKOFF_SECONDS,
        "failure_threshold": FAILURE_THRESHOLD,
        "failure_window_seconds": FAILURE_WINDOW_SECONDS,
        "scope": scope,
        "rate_limit_seconds": _get_rate_limit_seconds(scope),
        "rate_last_call_at": rate_state.get("last_call_at").isoformat() if rate_state.get("last_call_at") else None,
    }
