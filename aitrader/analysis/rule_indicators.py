from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from db import get_connection, get_rule_indicator_bucket_mapping, list_rule_indicators


def get_score_bucket_mapping() -> Dict[str, str]:
    return get_rule_indicator_bucket_mapping()


def _to_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _ma(series: List[float], window: int) -> Optional[float]:
    if window <= 0 or len(series) < window:
        return None
    part = series[-window:]
    return sum(part) / len(part)


def _ema(series: List[float], window: int) -> List[float]:
    if window <= 0 or not series:
        return []
    alpha = 2.0 / (window + 1.0)
    ema_vals: List[float] = []
    ema = series[0]
    for val in series:
        ema = alpha * val + (1 - alpha) * ema
        ema_vals.append(ema)
    return ema_vals


def _macd(series: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[List[float], List[float], List[float]]:
    if len(series) < slow:
        return [], [], []
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return dif, dea, hist


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _atr(highs: List[float], lows: List[float], closes: List[float], window: int = 14) -> Optional[float]:
    if len(highs) < 2 or len(lows) < 2 or len(closes) < 2:
        return None
    trs: List[float] = []
    for i in range(1, len(closes)):
        trs.append(_true_range(highs[i], lows[i], closes[i - 1]))
    if len(trs) < window:
        return sum(trs) / len(trs) if trs else None
    part = trs[-window:]
    return sum(part) / len(part)


def _pct_change(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def _threshold_signal(value: Optional[float], up_min: Optional[float], down_max: Optional[float]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if up_min is not None and value >= up_min:
        return {"pass": True, "value": value, "thresholds": {"up_min": up_min, "down_max": down_max}}
    if down_max is not None and value <= down_max:
        return {"pass": False, "value": value, "thresholds": {"up_min": up_min, "down_max": down_max}}
    return {"neutral": True, "value": value, "thresholds": {"up_min": up_min, "down_max": down_max}}


def _to_series(klines: List[Dict]) -> Tuple[List[str], List[float], List[float], List[float], List[float], List[float], List[Optional[float]]]:
    dates: List[str] = []
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    vols: List[float] = []
    turnover_rates: List[Optional[float]] = []
    for k in klines:
        d = str(k.get("date") or "").strip()
        o = _to_float(k.get("open"))
        h = _to_float(k.get("high"))
        l = _to_float(k.get("low"))
        c = _to_float(k.get("close"))
        v = _to_float(k.get("volume"))
        if not d or o is None or h is None or l is None or c is None:
            continue
        dates.append(d)
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        vols.append(v or 0.0)
        tr = _to_float(k.get("turnover_rate") or k.get("turnoverRate"))
        turnover_rates.append(tr)
    return dates, opens, highs, lows, closes, vols, turnover_rates


def _load_index_change_pct(index_symbol: str) -> Optional[float]:
    if not index_symbol:
        return None
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT close FROM daily_klines WHERE symbol = ? AND period = ? ORDER BY date DESC LIMIT 2",
            (index_symbol, "daily"),
        ).fetchall()
        if not rows or len(rows) < 2:
            return None
        latest = _to_float(rows[0][0])
        prev = _to_float(rows[1][0])
        return _pct_change(latest, prev)
    finally:
        conn.close()


def _build_context(klines: List[Dict], portfolio_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dates, opens, highs, lows, closes, vols, turnover_rates = _to_series(klines)
    if not closes:
        return {"dates": dates, "opens": opens, "highs": highs, "lows": lows, "closes": closes, "vols": vols}
    close_now = closes[-1]
    close_prev = closes[-2] if len(closes) > 1 else None
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    vol_ma5 = _ma(vols, 5)
    vol_ma20 = _ma(vols, 20)
    atr14 = _atr(highs, lows, closes, window=14)
    atr_pct = (atr14 / close_now * 100.0) if atr14 and close_now else None
    high60 = max(highs[-60:]) if len(highs) >= 60 else (max(highs) if highs else None)
    high20 = max(highs[-20:]) if len(highs) >= 20 else (max(highs) if highs else None)
    low20 = min(lows[-20:]) if len(lows) >= 20 else (min(lows) if lows else None)
    dif, dea, hist = _macd(closes, 12, 26, 9)
    key_resist = None
    if highs:
        window = 60 if len(highs) >= 60 else len(highs)
        if window > 1:
            key_resist = max(highs[-window:-1])
        else:
            key_resist = highs[-1]
    return {
        "dates": dates,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "vols": vols,
        "turnover_rates": turnover_rates,
        "close_now": close_now,
        "close_prev": close_prev,
        "change_pct": _pct_change(close_now, close_prev),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "vol_ma5": vol_ma5,
        "vol_ma20": vol_ma20,
        "atr14": atr14,
        "atr_pct": atr_pct,
        "high20": high20,
        "low20": low20,
        "high60": high60,
        "macd_dif": dif[-1] if dif else None,
        "macd_dea": dea[-1] if dea else None,
        "macd_hist": hist[-1] if hist else None,
        "macd_hist_series": hist,
        "key_resistance": key_resist,
        "portfolio": portfolio_context or {},
    }


def compute_rule_indicators(
    symbol: str,
    klines: List[Dict],
    market: str = "ashare",
    portfolio_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    indicators = list_rule_indicators(market)
    bucket_mapping = get_rule_indicator_bucket_mapping()
    ctx = _build_context(klines, portfolio_context)
    results: Dict[str, Dict[str, Any]] = {}

    def _set(
        indicator_id: str,
        status: str,
        value: Any = None,
        reason: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ):
        payload = {"status": status, "value": value}
        if reason:
            payload["reason"] = reason
        if extra:
            payload.update(extra)
        results[indicator_id] = payload

    def _bucket_for_indicator(item: Dict[str, Any], params: Dict[str, Any]) -> str:
        explicit = (params.get("score_bucket") or "").strip().lower()
        if explicit in ("trend", "structure", "volume", "rr", "total"):
            return explicit
        category = (item.get("category") or "").strip()
        return bucket_mapping.get(category, "total")

    for item in indicators:
        indicator_id = item.get("indicator_id")
        if not indicator_id:
            continue
        params = item.get("params") or {}
        extra_meta: Dict[str, Any] = {}
        if "weight" in params:
            extra_meta["weight"] = params.get("weight")
        if "score_contribution" in params:
            extra_meta["score_contribution"] = params.get("score_contribution")
        extra_meta["score_bucket"] = _bucket_for_indicator(item, params)
        if not item.get("enabled", True):
            _set(indicator_id, "disabled", extra=extra_meta)
            continue

        try:
            value: Any = None

            if indicator_id == "market_all_index_change_pct":
                value = _load_index_change_pct(str(params.get("index_symbol") or ""))

            elif indicator_id == "market_up_count":
                value = None

            elif indicator_id == "market_down_count":
                value = None

            elif indicator_id == "market_liquidity_crisis_trigger":
                up = results.get("market_up_count", {}).get("value")
                threshold = _to_float(params.get("threshold")) or 300
                if up is not None:
                    value = up < threshold

            elif indicator_id == "stock_current_price":
                value = ctx.get("close_now")

            elif indicator_id == "stock_return_6m":
                months = int(params.get("months") or 6)
                days = max(months * 20, 1)
                closes = ctx.get("closes") or []
                if len(closes) > days:
                    base = closes[-days - 1]
                    change = _pct_change(closes[-1], base)
                    up_min = _to_float(params.get("up_min"))
                    down_max = _to_float(params.get("down_max"))
                    value = _threshold_signal(change, up_min, down_max)

            elif indicator_id == "stock_turnover_rate":
                rates = [r for r in (ctx.get("turnover_rates") or []) if r is not None]
                value = rates[-1] if rates else None

            elif indicator_id == "stock_turnover_ratio_3m":
                months = int(params.get("months") or 3)
                days = max(months * 20, 1)
                rates = [r for r in (ctx.get("turnover_rates") or []) if r is not None]
                if rates and len(rates) > days:
                    avg = sum(rates[-days:]) / len(rates[-days:])
                    value = (rates[-1] / avg) if avg else None

            elif indicator_id in ("stock_pe_ttm", "stock_pe_percentile", "stock_pb_lf"):
                value = None

            elif indicator_id == "bias_ma20":
                ma20 = ctx.get("ma20")
                close_now = ctx.get("close_now")
                diff = _pct_change(close_now, ma20) if close_now and ma20 else None
                up_min = _to_float(params.get("up_min"))
                down_max = _to_float(params.get("down_max"))
                value = _threshold_signal(diff, up_min, down_max)

            elif indicator_id == "bias_ma60":
                ma60 = ctx.get("ma60")
                close_now = ctx.get("close_now")
                diff = _pct_change(close_now, ma60) if close_now and ma60 else None
                up_min = _to_float(params.get("up_min"))
                down_max = _to_float(params.get("down_max"))
                value = _threshold_signal(diff, up_min, down_max)

            elif indicator_id == "ma5":
                value = ctx.get("ma5")

            elif indicator_id == "ma10":
                value = ctx.get("ma10")

            elif indicator_id == "ma20":
                value = ctx.get("ma20")

            elif indicator_id == "ma60":
                value = ctx.get("ma60")

            elif indicator_id == "macd":
                hist = ctx.get("macd_hist")
                up_min = _to_float(params.get("hist_pos"))
                down_max = _to_float(params.get("hist_neg"))
                if hist is not None:
                    value = _threshold_signal(hist, up_min, down_max)

            elif indicator_id == "macd_red_days":
                hist = ctx.get("macd_hist_series") or []
                count = 0
                for v in reversed(hist):
                    if v is None or v <= 0:
                        break
                    count += 1
                if hist:
                    min_days = int(params.get("min_red_days") or 3)
                    zero_fail = int(params.get("zero_fail_max") or 0)
                    if count >= min_days:
                        value = {"pass": True, "value": count, "min_red_days": min_days, "zero_fail_max": zero_fail}
                    elif count <= zero_fail:
                        value = {"pass": False, "value": count, "min_red_days": min_days, "zero_fail_max": zero_fail}
                    else:
                        value = {"neutral": True, "value": count, "min_red_days": min_days, "zero_fail_max": zero_fail}

            elif indicator_id == "trendline_support":
                value = None

            elif indicator_id == "key_resistance":
                value = ctx.get("key_resistance")

            elif indicator_id == "volume_daily":
                vols = ctx.get("vols") or []
                value = vols[-1] if vols else None

            elif indicator_id == "volume_ma5":
                value = ctx.get("vol_ma5")

            elif indicator_id == "volume_ratio_5":
                vol_now = None
                vols = ctx.get("vols") or []
                if vols:
                    vol_now = vols[-1]
                vol_ma5 = ctx.get("vol_ma5")
                ratio = (vol_now / vol_ma5) if vol_now and vol_ma5 else None
                up_min = _to_float(params.get("up_min"))
                down_max = _to_float(params.get("down_max"))
                value = _threshold_signal(ratio, up_min, down_max)

            elif indicator_id == "turnover_relative_ratio":
                months = int(params.get("months") or 3)
                days = max(months * 20, 1)
                rates = [r for r in (ctx.get("turnover_rates") or []) if r is not None]
                if rates and len(rates) > days:
                    avg = sum(rates[-days:]) / len(rates[-days:])
                    value = (rates[-1] / avg) if avg else None

            elif indicator_id == "next_day_volume_ratio":
                value = None

            elif indicator_id == "sector_index_change_pct":
                value = None

            elif indicator_id == "sector_strong_count":
                value = None

            elif indicator_id == "relative_strength_sector":
                value = None

            elif indicator_id == "sector_rank":
                value = None

            elif indicator_id == "low_volume_sideways":
                vol_ma5 = ctx.get("vol_ma5")
                vol_ma20 = ctx.get("vol_ma20")
                atr_pct = ctx.get("atr_pct")
                vol_ratio_max = _to_float(params.get("vol_ratio_max")) or 0.8
                atr_pct_max = _to_float(params.get("atr_pct_max")) or 2.0
                if vol_ma5 is not None and vol_ma20:
                    value = (vol_ma5 <= vol_ma20 * vol_ratio_max) and (atr_pct is not None and atr_pct <= atr_pct_max)

            elif indicator_id == "low_volatility":
                atr14 = ctx.get("atr14")
                closes = ctx.get("closes") or []
                highs = ctx.get("highs") or []
                lows = ctx.get("lows") or []
                ratio_max = _to_float(params.get("atr_ratio_max")) or 0.7
                window = int(params.get("window") or 60)
                if atr14 and len(closes) > window:
                    trs = []
                    for i in range(1, len(closes)):
                        trs.append(_true_range(highs[i], lows[i], closes[i - 1]))
                    avg_tr = sum(trs[-window:]) / len(trs[-window:]) if len(trs) >= window else None
                    if avg_tr:
                        value = atr14 <= avg_tr * ratio_max

            elif indicator_id == "catalyst_window":
                value = None

            elif indicator_id == "daily_change_pct":
                change = ctx.get("change_pct")
                up_min = _to_float(params.get("up_min"))
                down_max = _to_float(params.get("down_max"))
                value = _threshold_signal(change, up_min, down_max)

            elif indicator_id == "close_above_key_resistance":
                close_now = ctx.get("close_now")
                resist = ctx.get("key_resistance")
                if close_now is not None and resist is not None:
                    value = close_now > resist

            elif indicator_id == "two_day_hold_above":
                closes = ctx.get("closes") or []
                resist = ctx.get("key_resistance")
                if resist is not None and len(closes) >= 2:
                    value = closes[-1] > resist and closes[-2] > resist

            elif indicator_id == "mild_breakout_days":
                closes = ctx.get("closes") or []
                resist = ctx.get("key_resistance")
                window = int(params.get("window") or 3)
                min_days = int(params.get("min_days") or 2)
                if resist is not None and len(closes) >= window:
                    recent = closes[-window:]
                    count = sum(1 for c in recent if c > resist)
                    value = {"count": count, "window": window, "min_days": min_days, "pass": count >= min_days}

            elif indicator_id == "breakdown_short_support":
                close_now = ctx.get("close_now")
                ma5 = ctx.get("ma5")
                ma10 = ctx.get("ma10")
                if close_now is not None and (ma5 or ma10):
                    value = (ma5 is not None and close_now < ma5) or (ma10 is not None and close_now < ma10)

            elif indicator_id == "volume_breakdown_ma20":
                close_now = ctx.get("close_now")
                ma20 = ctx.get("ma20")
                vol_ma5 = ctx.get("vol_ma5")
                vols = ctx.get("vols") or []
                vol_now = vols[-1] if vols else None
                ratio = _to_float(params.get("volume_ratio")) or 1.2
                if close_now is not None and ma20 and vol_ma5 and vol_now:
                    value = (vol_now > vol_ma5 * ratio) and (close_now < ma20)

            elif indicator_id == "price_near_cost":
                pos = (ctx.get("portfolio") or {}).get("position") or {}
                avg_cost = _to_float(pos.get("avg_cost"))
                close_now = ctx.get("close_now")
                threshold = _to_float(params.get("threshold_pct")) or 2.0
                if avg_cost and close_now:
                    diff_pct = abs(close_now - avg_cost) / avg_cost * 100.0
                    value = {"diff_pct": diff_pct, "threshold_pct": threshold, "pass": diff_pct <= threshold}

            elif indicator_id == "first_target":
                value = ctx.get("key_resistance")

            elif indicator_id == "trailing_stop":
                highs = ctx.get("highs") or []
                trail_pct = _to_float(params.get("trail_pct")) or 0.05
                window = 20 if len(highs) >= 20 else len(highs)
                if highs and window:
                    peak = max(highs[-window:])
                    value = peak * (1.0 - trail_pct)

            elif indicator_id == "days_to_earnings":
                value = None

            elif indicator_id == "days_to_holiday":
                value = None

            else:
                _set(indicator_id, "unimplemented", extra=extra_meta)
                continue

            if value is None:
                _set(indicator_id, "no_data", extra=extra_meta)
            else:
                _set(indicator_id, "ok", value, extra=extra_meta)
        except Exception as exc:
            _set(indicator_id, "error", reason=str(exc), extra=extra_meta)

    return results
