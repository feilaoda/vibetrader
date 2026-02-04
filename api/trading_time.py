from datetime import datetime, timedelta, time as dtime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else None

TRADING_HOURS = [
    (dtime(9, 30), dtime(11, 30)),
    (dtime(13, 0), dtime(15, 0)),
]
STOP_AUTO_RUN_AFTER_MINUTES = 5


def now_cn() -> datetime:
    return datetime.now(CN_TZ) if CN_TZ else datetime.now()

def is_trading_day(now: datetime | None = None) -> bool:
    current = now or now_cn()
    return current.weekday() < 5


def is_trading_time(now: datetime | None = None) -> bool:
    current_dt = now or now_cn()
    if not is_trading_day(current_dt):
        return False
    current = current_dt.time()
    for start, end in TRADING_HOURS:
        if start <= current <= end:
            return True
    return False


def should_auto_run(now: datetime | None = None) -> bool:
    current = now or now_cn()
    if not is_trading_day(current):
        return False
    if is_trading_time(current):
        return True
    close_dt = datetime.combine(current.date(), dtime(15, 0), tzinfo=current.tzinfo) if current.tzinfo else datetime.combine(current.date(), dtime(15, 0))
    close_with_buffer = close_dt + timedelta(minutes=STOP_AUTO_RUN_AFTER_MINUTES)
    return close_dt <= current <= close_with_buffer


def auto_run_status(now: datetime | None = None) -> tuple[bool, str, str]:
    current = now or now_cn()
    if not is_trading_day(current):
        return False, "non_trading_day", current.isoformat()
    if is_trading_time(current):
        return True, "trading_time", current.isoformat()
    close_dt = datetime.combine(current.date(), dtime(15, 0), tzinfo=current.tzinfo) if current.tzinfo else datetime.combine(current.date(), dtime(15, 0))
    close_with_buffer = close_dt + timedelta(minutes=STOP_AUTO_RUN_AFTER_MINUTES)
    if close_dt <= current <= close_with_buffer:
        return True, "post_close_buffer", current.isoformat()
    return False, "after_hours", current.isoformat()
