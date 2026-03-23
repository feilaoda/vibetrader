#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from api.cache import _latest_trading_date, get_klines_with_cache  # noqa: E402
from api.llm import LLMService  # noqa: E402
from aitrader.analysis.rule_indicators import compute_rule_indicators  # noqa: E402
from db import (  # noqa: E402
    detect_aitrader_rule_market,
    get_paper_portfolio_context,
    get_aitrader_rule_profile,
    get_signal_calibration,
    upsert_signal_calibration,
)


ANALYSIS_PROMPT = """你是一位严谨的日线交易分析师。请只基于输入的日K线数据给出技术面结论，不要编造未提供的外部信息。

请严格按以下标题输出：
1) 核心结论
2) 趋势与结构
3) 量能与资金
4) 关键价位与意义
5) 情景推演（概率主观）
6) 交易计划（分类型）
7) 风险提示
8) 总结/结论/最终建议

硬性要求：
- 明确写出当前阶段判断（如：延续下跌/震荡/趋势上行）。
- 给出具体阻力位、支撑位、触发条件和失效条件。
- 给出至少一个风险回报（R/R）估算。
- 结论必须落在“买入 / 卖出 / 观望”之一，并给出仓位建议。
- 若证据不足，直接写“数据不足”，不要强行下结论。

标的: {symbol}
分析区间: {start_date} ~ {end_date}（最近 {bars} 根日K）
"""


def _parse_date(text: Optional[str]) -> date:
    value = (text or "").strip()
    if not value:
        return _latest_trading_date()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _fmt_ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _to_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _sort_and_dedupe_daily(klines: List[Dict]) -> List[Dict]:
    by_date: Dict[str, Dict] = {}
    for row in klines:
        d = str(row.get("date") or "").strip()
        if not d:
            continue
        by_date[d] = row
    out = list(by_date.values())
    out.sort(key=lambda x: str(x.get("date") or ""))
    return out


def _slice_bars(klines: List[Dict], bars: int) -> List[Dict]:
    if bars <= 0:
        return klines
    return klines[-bars:]


def _ma(series: List[float], window: int) -> Optional[float]:
    if window <= 0 or len(series) < window:
        return None
    part = series[-window:]
    return sum(part) / len(part)


def _fmt_num(v: Optional[float], digits: int = 3) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}"


def _build_snapshot_text(klines: List[Dict]) -> Tuple[str, Dict[str, object]]:
    closes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    volumes: List[float] = []
    for k in klines:
        c = _to_float(k.get("close"))
        h = _to_float(k.get("high"))
        l = _to_float(k.get("low"))
        v = _to_float(k.get("volume"))
        if c is None or h is None or l is None:
            continue
        closes.append(c)
        highs.append(h)
        lows.append(l)
        volumes.append(v or 0.0)

    if not closes:
        return "", {}

    latest = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else None
    change_pct = ((latest / prev - 1.0) * 100.0) if prev and prev > 0 else None

    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma50 = _ma(closes, 50)
    ma60 = _ma(closes, 60)

    high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    low20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    high60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    low60 = min(lows[-60:]) if len(lows) >= 60 else min(lows)

    vol_now = volumes[-1] if volumes else None
    vol5 = _ma(volumes, 5)
    vol20 = _ma(volumes, 20)
    vol_ratio_5 = (vol_now / vol5) if vol_now and vol5 and vol5 > 0 else None
    vol_ratio_20 = (vol_now / vol20) if vol_now and vol20 and vol20 > 0 else None

    snapshot = {
        "latest_close": latest,
        "change_pct": change_pct,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma50": ma50,
        "ma60": ma60,
        "high20": high20,
        "low20": low20,
        "high60": high60,
        "low60": low60,
        "volume_latest": vol_now,
        "volume_ma5": vol5,
        "volume_ma20": vol20,
        "volume_ratio_5": vol_ratio_5,
        "volume_ratio_20": vol_ratio_20,
    }

    snapshot_text = (
        "技术快照（辅助你更稳定地产出结构化结论）：\n"
        f"- 最新收盘: {_fmt_num(latest, 3)}\n"
        f"- 日涨跌幅: {_fmt_num(change_pct, 2)}%\n"
        f"- MA5/10/20/50/60: {_fmt_num(ma5, 3)} / {_fmt_num(ma10, 3)} / {_fmt_num(ma20, 3)} / {_fmt_num(ma50, 3)} / {_fmt_num(ma60, 3)}\n"
        f"- 20日高低: {_fmt_num(high20, 3)} / {_fmt_num(low20, 3)}\n"
        f"- 60日高低: {_fmt_num(high60, 3)} / {_fmt_num(low60, 3)}\n"
        f"- 最新成交量: {int(vol_now) if vol_now is not None else 'N/A'}\n"
        f"- 量比(对5日均量/20日均量): {_fmt_num(vol_ratio_5, 2)} / {_fmt_num(vol_ratio_20, 2)}"
    )
    return snapshot_text, snapshot


def _price_digits(price: float) -> int:
    if price < 10:
        return 3
    return 2


def _fmt_price(v: Optional[float], digits: int) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}"


def _to_series(klines: List[Dict]) -> Tuple[List[str], List[float], List[float], List[float], List[float], List[float]]:
    dates: List[str] = []
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    vols: List[float] = []
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
    return dates, opens, highs, lows, closes, vols


def _pct_change(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def _dedupe_levels(values: List[float], digits: int) -> List[float]:
    seen = set()
    out: List[float] = []
    for v in values:
        k = round(v, digits)
        if k in seen:
            continue
        seen.add(k)
        out.append(float(k))
    return out


def _calc_rr(close_now: float, up_target: Optional[float], down_target: Optional[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if up_target is None or down_target is None:
        return None, None, None
    up = (up_target / close_now - 1.0) * 100.0
    down = (close_now / down_target - 1.0) * 100.0
    if down <= 0:
        return up, down, None
    return up, down, up / down


def _indicator_score_bonus(indicator_values: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, float], List[str]]:
    if not indicator_values:
        return {"trend": 0.0, "structure": 0.0, "volume": 0.0, "rr": 0.0, "total": 0.0}, []
    bonuses = {"trend": 0.0, "structure": 0.0, "volume": 0.0, "rr": 0.0, "total": 0.0}
    notes: List[str] = []
    for indicator_id, entry in indicator_values.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "ok":
            continue
        contrib = entry.get("score_contribution")
        if contrib is None:
            continue
        try:
            base = float(contrib)
        except Exception:
            continue
        if abs(base) < 1e-9:
            continue
        bucket = str(entry.get("score_bucket") or "total").strip().lower()
        if bucket not in bonuses:
            bucket = "total"
        weight = entry.get("weight")
        if weight is not None:
            try:
                base *= float(weight)
            except Exception:
                pass
        value = entry.get("value")
        if value is None:
            continue
        apply = base
        if isinstance(value, dict):
            if value.get("neutral") is True:
                continue
            if "pass" in value:
                apply = base if bool(value.get("pass")) else -abs(base)
        elif isinstance(value, bool):
            apply = base if value else -abs(base)
        bonuses[bucket] += apply
        notes.append(f"{indicator_id}:{bucket}:{apply:+.2f}")
    return bonuses, notes


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _safe_std(values: List[float]) -> float:
    data = [v for v in values if isinstance(v, (int, float))]
    if len(data) < 2:
        return 0.0
    try:
        return float(statistics.stdev(data))
    except Exception:
        return 0.0


def _calc_atr(highs: List[float], lows: List[float], closes: List[float], window: int = 14) -> Optional[float]:
    if len(highs) < 2 or len(lows) < 2 or len(closes) < 2:
        return None
    tr_values: List[float] = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr_values.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(tr_values) < window:
        return None
    tail = tr_values[-window:]
    return sum(tail) / float(window)


def _calc_hist_vol(closes: List[float], window: int = 20) -> Optional[float]:
    if len(closes) < window + 1:
        return None
    log_rets: List[float] = []
    for i in range(-window, 0):
        prev = closes[i - 1]
        curr = closes[i]
        if prev <= 0 or curr <= 0:
            continue
        log_rets.append(math.log(curr / prev))
    if len(log_rets) < 5:
        return None
    return _safe_std(log_rets) * math.sqrt(252.0)


def _weighted_percentile(values: List[float], q: float) -> Optional[float]:
    data = sorted([float(v) for v in values if v is not None])
    if not data:
        return None
    idx = int(round((len(data) - 1) * _clamp(q, 0.0, 1.0)))
    return data[idx]


def _merge_zones(zones: List[Tuple[float, float]], max_gap: float = 0.0) -> List[Tuple[float, float]]:
    cleaned = [(min(a, b), max(a, b)) for a, b in zones if a is not None and b is not None]
    if not cleaned:
        return []
    cleaned.sort(key=lambda x: x[0])
    merged: List[Tuple[float, float]] = [cleaned[0]]
    for low, high in cleaned[1:]:
        last_low, last_high = merged[-1]
        if low <= last_high + max_gap:
            merged[-1] = (last_low, max(last_high, high))
        else:
            merged.append((low, high))
    return merged


def _zone_mid(zone: Tuple[float, float]) -> float:
    return (zone[0] + zone[1]) / 2.0


def _zone_text(zones: List[Tuple[float, float]], digits: int) -> str:
    if not zones:
        return "暂无清晰区间"
    parts = [f"{_fmt_price(low, digits)}~{_fmt_price(high, digits)}" for low, high in zones]
    return "、".join(parts)


def _build_volume_density_levels(
    closes: List[float],
    vols: List[float],
    atr: Optional[float],
    bins: int = 14,
) -> List[float]:
    if len(closes) < 20:
        return []
    use_closes = closes[-120:]
    use_vols = vols[-120:] if len(vols) >= len(use_closes) else [1.0] * len(use_closes)
    low_p = min(use_closes)
    high_p = max(use_closes)
    if high_p <= low_p:
        return []
    step = (high_p - low_p) / float(max(bins, 4))
    if atr and atr > 0:
        step = max(step, atr * 0.35)
    if step <= 0:
        return []
    weights = [0.0 for _ in range(bins)]
    for p, v in zip(use_closes, use_vols):
        idx = int((p - low_p) / step)
        idx = max(0, min(bins - 1, idx))
        weights[idx] += max(float(v or 0.0), 1.0)
    threshold = _weighted_percentile(weights, 0.75)
    if threshold is None:
        return []
    dense: List[float] = []
    for i, w in enumerate(weights):
        if w >= threshold and w > 0:
            dense.append(low_p + (i + 0.5) * step)
    return dense[:6]


def _build_price_zones(
    close_now: float,
    candidates: List[float],
    atr: Optional[float],
    dense_levels: List[float],
) -> List[Tuple[float, float]]:
    clean = [float(v) for v in candidates if v is not None and v > 0]
    if not clean:
        return []
    width = max((atr or 0.0) * 0.5, close_now * 0.003)
    zones = [(v - width, v + width) for v in clean]
    if dense_levels:
        dense_width = max((atr or 0.0) * 0.7, close_now * 0.004)
        zones.extend((v - dense_width, v + dense_width) for v in dense_levels)
    return _merge_zones(zones, max_gap=width * 0.4)


def _normalize_stage(stage: str) -> str:
    if stage in ("趋势上行", "震荡整理", "弱势震荡偏空", "破位后的延续下跌"):
        return stage
    return "震荡整理"


def _detect_stage_adaptive(
    closes: List[float],
    ma20: Optional[float],
    ma50: Optional[float],
    ma20_prev5: Optional[float],
    ma50_prev5: Optional[float],
    atr_pct: float,
    hv_annual: float,
) -> Tuple[str, Dict[str, float]]:
    ret20 = _pct_change(closes[-1], closes[-21]) if len(closes) >= 21 else 0.0
    ret60 = _pct_change(closes[-1], closes[-61]) if len(closes) >= 61 else 0.0
    slope20 = _pct_change(ma20, ma20_prev5) if ma20 and ma20_prev5 else 0.0
    slope50 = _pct_change(ma50, ma50_prev5) if ma50 and ma50_prev5 else 0.0

    base = 2.0
    vol_factor = 1.0 + _clamp(atr_pct / 2.5, 0.0, 1.0) + _clamp(hv_annual / 80.0, 0.0, 1.0) * 0.6
    up_th = base * vol_factor
    down_th = -base * vol_factor

    trend_bias = (ret20 or 0.0) * 0.6 + (ret60 or 0.0) * 0.4 + (slope20 or 0.0) * 0.5 + (slope50 or 0.0) * 0.3
    close_now = closes[-1]
    bearish = bool(ma20 and ma50 and close_now < ma20 < ma50)
    bullish = bool(ma20 and ma50 and close_now > ma20 > ma50)

    if bearish and trend_bias <= down_th:
        stage_now = "破位后的延续下跌"
    elif bullish and trend_bias >= up_th:
        stage_now = "趋势上行"
    elif close_now < (ma20 or close_now + 1.0) and trend_bias < 0:
        stage_now = "弱势震荡偏空"
    else:
        stage_now = "震荡整理"

    # Hysteresis: if only marginally changed, keep previous stage to reduce flip noise.
    if len(closes) >= 90:
        prev_closes = closes[:-1]
        prev_ma20 = _ma(prev_closes, 20)
        prev_ma50 = _ma(prev_closes, 50)
        prev_ma20_prev5 = _ma(prev_closes[:-5], 20) if len(prev_closes) > 25 else None
        prev_ma50_prev5 = _ma(prev_closes[:-5], 50) if len(prev_closes) > 55 else None
        prev_ret20 = _pct_change(prev_closes[-1], prev_closes[-21]) if len(prev_closes) >= 21 else 0.0
        prev_ret60 = _pct_change(prev_closes[-1], prev_closes[-61]) if len(prev_closes) >= 61 else 0.0
        prev_slope20 = _pct_change(prev_ma20, prev_ma20_prev5) if prev_ma20 and prev_ma20_prev5 else 0.0
        prev_slope50 = _pct_change(prev_ma50, prev_ma50_prev5) if prev_ma50 and prev_ma50_prev5 else 0.0
        prev_bias = (prev_ret20 or 0.0) * 0.6 + (prev_ret60 or 0.0) * 0.4 + (prev_slope20 or 0.0) * 0.5 + (prev_slope50 or 0.0) * 0.3
        prev_bearish = bool(prev_ma20 and prev_ma50 and prev_closes[-1] < prev_ma20 < prev_ma50)
        prev_bullish = bool(prev_ma20 and prev_ma50 and prev_closes[-1] > prev_ma20 > prev_ma50)
        if prev_bearish and prev_bias <= down_th:
            prev_stage = "破位后的延续下跌"
        elif prev_bullish and prev_bias >= up_th:
            prev_stage = "趋势上行"
        elif prev_closes[-1] < (prev_ma20 or prev_closes[-1] + 1.0) and prev_bias < 0:
            prev_stage = "弱势震荡偏空"
        else:
            prev_stage = "震荡整理"

        if prev_stage != stage_now and abs(trend_bias - prev_bias) < max(0.7, abs(up_th) * 0.12):
            stage_now = prev_stage

    return _normalize_stage(stage_now), {
        "trend_bias": float(trend_bias),
        "up_threshold": float(up_th),
        "down_threshold": float(down_th),
        "atr_pct": float(atr_pct),
        "hv_annual": float(hv_annual),
    }


def _format_volume(symbol: str, vol: Optional[float]) -> str:
    if vol is None:
        return "N/A"
    sym = (symbol or "").upper()
    if sym.endswith((".SH", ".SZ", ".BJ")):
        # 1万手 = 1,000,000 股
        return f"{vol / 1_000_000:.2f}万手"
    return f"{int(vol)}"


def _profile_float(profile: Dict[str, object], key: str, default: float) -> float:
    try:
        value = profile.get(key, default)
        if value is None:
            return float(default)
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return float(default)
        return num
    except Exception:
        return float(default)


def _profile_bool(profile: Dict[str, object], key: str, default: bool) -> bool:
    value = profile.get(key, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _profile_text(profile: Dict[str, object], key: str, default: str) -> str:
    value = profile.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _choose_action(
    stage: str,
    rr: Optional[float],
    close_now: float,
    ma20: Optional[float],
    profile: Dict[str, object],
) -> Tuple[str, str]:
    rr_buy_downtrend = _profile_float(profile, "rr_buy_downtrend", 1.5)
    rr_buy_uptrend = _profile_float(profile, "rr_buy_uptrend", 1.3)
    require_close_above_ma20_downtrend = _profile_bool(
        profile, "require_close_above_ma20_downtrend", True
    )
    buy_position_downtrend = _profile_text(
        profile, "buy_position_downtrend", "试错小仓(≤10%)，仅右侧确认后执行"
    )
    watch_position_downtrend = _profile_text(
        profile, "watch_position_downtrend", "空仓或轻仓(≤10%)，防守优先"
    )
    buy_position_uptrend = _profile_text(
        profile, "buy_position_uptrend", "分批建仓(10%-25%)"
    )
    watch_position_uptrend = _profile_text(
        profile, "watch_position_uptrend", "等待更优盈亏比后再进场"
    )
    watch_position_range = _profile_text(
        profile, "watch_position_range", "控制仓位，等待方向确认"
    )

    weak_or_down = ("下跌" in stage) or ("偏空" in stage)
    if weak_or_down:
        pass_ma20 = True
        if require_close_above_ma20_downtrend:
            pass_ma20 = bool(ma20 and close_now > ma20)
        if rr is not None and rr >= rr_buy_downtrend and pass_ma20:
            return "买入", buy_position_downtrend
        return "观望", watch_position_downtrend
    if "上行" in stage:
        if rr is not None and rr >= rr_buy_uptrend:
            return "买入", buy_position_uptrend
        return "观望", watch_position_uptrend
    return "观望", watch_position_range


def _decide_action_with_scores(
    stage: str,
    rr: Optional[float],
    close_now: float,
    ma20: Optional[float],
    ma50: Optional[float],
    ret20: Optional[float],
    ret60: Optional[float],
    vol_ratio5: Optional[float],
    vol_ratio20: Optional[float],
    change_pct: Optional[float],
    is_breakdown: bool,
    profile: Dict[str, object],
    confidence: float,
    indicator_bonus_by_bucket: Optional[Dict[str, float]] = None,
    indicator_notes: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    rr_buy_downtrend = _profile_float(profile, "rr_buy_downtrend", 1.5)
    rr_buy_uptrend = _profile_float(profile, "rr_buy_uptrend", 1.3)
    require_close_above_ma20_downtrend = _profile_bool(profile, "require_close_above_ma20_downtrend", True)
    buy_position_downtrend = _profile_text(profile, "buy_position_downtrend", "试错小仓(≤10%)，仅右侧确认后执行")
    watch_position_downtrend = _profile_text(profile, "watch_position_downtrend", "空仓或轻仓(≤10%)，防守优先")
    buy_position_uptrend = _profile_text(profile, "buy_position_uptrend", "分批建仓(10%-25%)")
    watch_position_uptrend = _profile_text(profile, "watch_position_uptrend", "等待更优盈亏比后再进场")
    watch_position_range = _profile_text(profile, "watch_position_range", "控制仓位，等待方向确认")

    trend_base = {
        "趋势上行": 78.0,
        "震荡整理": 52.0,
        "弱势震荡偏空": 35.0,
        "破位后的延续下跌": 20.0,
    }.get(stage, 50.0)
    trend_boost = _clamp((ret20 or 0.0) * 1.5 + (ret60 or 0.0) * 0.6, -20.0, 20.0)
    bonuses = indicator_bonus_by_bucket or {}
    trend_bonus = float(bonuses.get("trend") or 0.0)
    structure_bonus = float(bonuses.get("structure") or 0.0)
    volume_bonus = float(bonuses.get("volume") or 0.0)
    rr_bonus = float(bonuses.get("rr") or 0.0)
    total_bonus = float(bonuses.get("total") or 0.0)

    trend_score = _clamp(trend_base + trend_boost + trend_bonus, 0.0, 100.0)

    structure_score = 50.0
    if ma20 and close_now > ma20:
        structure_score += 10.0
    if ma50 and close_now > ma50:
        structure_score += 8.0
    if ma20 and ma50 and ma20 > ma50:
        structure_score += 8.0
    if is_breakdown:
        structure_score -= 22.0
    structure_score = _clamp(structure_score + structure_bonus, 0.0, 100.0)

    volume_score = 50.0
    if (change_pct or 0.0) > 0 and (vol_ratio5 or 0.0) >= 1.1:
        volume_score += 15.0
    if (change_pct or 0.0) < 0 and (vol_ratio5 or 0.0) >= 1.15:
        volume_score -= 16.0
    if (vol_ratio20 or 0.0) < 0.85:
        volume_score -= 6.0
    volume_score = _clamp(volume_score + volume_bonus, 0.0, 100.0)

    rr_score = 40.0
    if rr is not None:
        rr_score = _clamp(20.0 + rr * 32.0, 0.0, 100.0)
    rr_score = _clamp(rr_score + rr_bonus, 0.0, 100.0)

    total_score_base = (
        trend_score * 0.35 +
        structure_score * 0.25 +
        volume_score * 0.20 +
        rr_score * 0.20
    )
    total_score = _clamp(total_score_base + total_bonus, 0.0, 100.0)

    risk_gates: List[str] = []
    weak_or_down = ("下跌" in stage) or ("偏空" in stage)
    if is_breakdown and (change_pct or 0.0) < -1.0:
        risk_gates.append("破位下跌")
    if (change_pct or 0.0) < -2.0 and (vol_ratio5 or 0.0) >= 1.2:
        risk_gates.append("放量下挫")
    if confidence < 0.52:
        risk_gates.append("低置信度信号")

    if weak_or_down and require_close_above_ma20_downtrend and not bool(ma20 and close_now > ma20):
        risk_gates.append("未收复MA20")

    gate_block_buy = len(risk_gates) > 0
    buy_threshold = 66.0
    sell_threshold = 32.0
    action = "观望"
    if total_score <= sell_threshold and weak_or_down and confidence < 0.45:
        action = "卖出"
    elif total_score >= buy_threshold and not gate_block_buy:
        if weak_or_down:
            if rr is not None and rr >= rr_buy_downtrend:
                action = "买入"
        elif "上行" in stage:
            if rr is not None and rr >= rr_buy_uptrend:
                action = "买入"
        else:
            if rr is not None and rr >= rr_buy_uptrend:
                action = "买入"

    if action == "买入":
        if weak_or_down:
            position = buy_position_downtrend
        elif "上行" in stage:
            position = buy_position_uptrend
        else:
            position = "小仓试探(≤15%)"
    elif action == "卖出":
        position = "优先降仓，防守为主"
    else:
        if weak_or_down:
            position = watch_position_downtrend
        elif "上行" in stage:
            position = watch_position_uptrend
        else:
            position = watch_position_range

    scores = {
        "trend_score": round(trend_score, 2),
        "structure_score": round(structure_score, 2),
        "volume_score": round(volume_score, 2),
        "rr_score": round(rr_score, 2),
        "total_score_base": round(total_score_base, 2),
        "indicator_bonus_by_bucket": {
            "trend": round(trend_bonus, 2),
            "structure": round(structure_bonus, 2),
            "volume": round(volume_bonus, 2),
            "rr": round(rr_bonus, 2),
            "total": round(total_bonus, 2),
        },
        "total_score": round(total_score, 2),
        "buy_threshold": buy_threshold,
        "sell_threshold": sell_threshold,
        "risk_gates": risk_gates,
        "indicator_notes": indicator_notes or [],
    }
    return action, position, scores


def _lot_size(symbol: str) -> int:
    sym = (symbol or "").upper()
    if sym.endswith((".SH", ".SZ", ".BJ")):
        return 100
    return 1


def _normalize_qty(value: float, lot: int) -> int:
    if lot <= 1:
        return max(int(value), 0)
    return max(int(value // lot) * lot, 0)


def _build_portfolio_plan(
    symbol: str,
    stage: str,
    action: str,
    close_now: float,
    ma20: Optional[float],
    portfolio_context: Optional[Dict[str, object]],
    digits: int,
) -> Tuple[List[str], Dict[str, object]]:
    if not isinstance(portfolio_context, dict) or not portfolio_context:
        return ["- 持仓上下文：未接入实盘/模拟持仓数据，以下仅为通用技术计划。"], {}

    total_equity = _profile_float(portfolio_context, "total_equity", 0.0)
    cash_balance = _profile_float(portfolio_context, "cash_balance", 0.0)
    max_drawdown_pct = _profile_float(portfolio_context, "max_drawdown_pct", 15.0)
    max_position_pct = _profile_float(portfolio_context, "max_position_pct", 20.0)
    stop_loss_pct = _profile_float(portfolio_context, "stop_loss_pct", 5.0)
    max_drawdown_budget = _profile_float(portfolio_context, "max_drawdown_budget", total_equity * max_drawdown_pct / 100.0)
    strategy_name = str(portfolio_context.get("strategy_name") or "")
    strategy_id = int(_profile_float(portfolio_context, "strategy_id", 0))

    pos = portfolio_context.get("position")
    pos = pos if isinstance(pos, dict) else {}
    qty = int(_profile_float(pos, "quantity", 0))
    avg_cost = _profile_float(pos, "avg_cost", close_now)
    lot = _lot_size(symbol)
    hold_value = float(qty) * float(close_now)
    hold_pct = (hold_value / total_equity * 100.0) if total_equity > 0 else 0.0
    stop_loss_price = max(0.0, (avg_cost if avg_cost > 0 else close_now) * (1.0 - stop_loss_pct / 100.0))

    weak_stage = ("下跌" in stage) or ("偏空" in stage) or bool(ma20 and close_now < ma20)
    lines: List[str] = [
        f"- 账户视角：策略#{strategy_id}{(' ' + strategy_name) if strategy_name else ''}，总资产约 {_fmt_price(total_equity, 2)}，"
        f"可用资金 {_fmt_price(cash_balance, 2)}，最大回撤预算 {_fmt_price(max_drawdown_budget, 2)}（{max_drawdown_pct:.1f}%）。"
    ]
    plan_meta: Dict[str, object] = {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "total_equity": total_equity,
        "cash_balance": cash_balance,
        "max_drawdown_budget": max_drawdown_budget,
        "max_drawdown_pct": max_drawdown_pct,
        "max_position_pct": max_position_pct,
        "stop_loss_pct": stop_loss_pct,
        "holding_qty": qty,
        "holding_avg_cost": avg_cost,
        "holding_pct": hold_pct,
    }

    if qty > 0:
        trim_ratio = 0.0
        add_qty = 0
        if action == "买入":
            target_pct = min(max_position_pct, hold_pct + (8.0 if not weak_stage else 4.0))
            add_pct = max(target_pct - hold_pct, 0.0)
            add_budget = min(cash_balance, total_equity * add_pct / 100.0) if total_equity > 0 else 0.0
            add_qty = _normalize_qty(add_budget / close_now if close_now > 0 else 0.0, lot)
            lines.append(
                f"- 持仓诊断：当前持仓 {qty}，约占资产 {hold_pct:.1f}%。若延续信号，可按“4/3/3”分批加仓，"
                f"首批建议 {add_qty}（预计投入约 {_fmt_price(add_qty * close_now, 2)}）。"
            )
        else:
            trim_ratio = 30.0 if weak_stage else 15.0
            trim_qty = _normalize_qty(float(qty) * trim_ratio / 100.0, lot)
            if trim_qty <= 0:
                trim_qty = lot if qty >= lot else qty
            trim_qty = min(trim_qty, qty)
            remain_qty = max(qty - trim_qty, 0)
            lines.append(
                f"- 减仓计划：建议先减仓 {trim_ratio:.0f}%（约 {trim_qty}），余仓 {remain_qty} 观察；"
                "若出现放量收复关键阻力再考虑回补。"
            )
            plan_meta["trim_ratio_pct"] = trim_ratio
            plan_meta["trim_qty"] = trim_qty

        estimated_loss = max(0.0, close_now - stop_loss_price) * float(qty)
        lines.append(
            f"- 止损纪律：持仓防守位 {_fmt_price(stop_loss_price, digits)}（约 {stop_loss_pct:.1f}% 风险）；"
            f"按当前仓位测算最大回撤约 {_fmt_price(estimated_loss, 2)}。"
        )
        plan_meta["stop_loss_price"] = stop_loss_price
        plan_meta["estimated_max_loss"] = estimated_loss
        if add_qty > 0:
            plan_meta["add_qty"] = add_qty
            plan_meta["add_value"] = add_qty * close_now
    else:
        if action == "买入":
            alloc_pct = min(max_position_pct, 10.0 if weak_stage else 20.0)
            alloc_budget = min(cash_balance, total_equity * alloc_pct / 100.0) if total_equity > 0 else 0.0
            buy_qty = _normalize_qty(alloc_budget / close_now if close_now > 0 else 0.0, lot)
            buy_value = buy_qty * close_now
            stop_loss_new = max(0.0, close_now * (1.0 - stop_loss_pct / 100.0))
            max_loss = max(0.0, close_now - stop_loss_new) * buy_qty
            lines.append(
                f"- 入场计划：建议先用资产的 {alloc_pct:.1f}% 试仓（约 {_fmt_price(alloc_budget, 2)}），"
                f"参考下单数量 {buy_qty}；后续按“4/3/3”分批加码。"
            )
            lines.append(
                f"- 风控预算：试仓止损位 {_fmt_price(stop_loss_new, digits)}，该笔最大亏损约 {_fmt_price(max_loss, 2)}，"
                f"需小于组合回撤预算 {_fmt_price(max_drawdown_budget, 2)}。"
            )
            plan_meta["entry_alloc_pct"] = alloc_pct
            plan_meta["entry_qty"] = buy_qty
            plan_meta["entry_value"] = buy_value
            plan_meta["stop_loss_price"] = stop_loss_new
            plan_meta["estimated_max_loss"] = max_loss
        else:
            lines.append("- 当前无持仓：维持观望，先保留现金；仅在触发右侧确认后再按“4/3/3”分批进场。")

    return lines, plan_meta


def _score_band(score: float) -> str:
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    return "D"


def _build_signal_bucket(stage: str, score: float, rr: Optional[float]) -> str:
    rr_band = "rr_na"
    if rr is not None:
        if rr >= 2.0:
            rr_band = "rr_hi"
        elif rr >= 1.2:
            rr_band = "rr_mid"
        else:
            rr_band = "rr_low"
    stage_key = {
        "趋势上行": "up",
        "震荡整理": "range",
        "弱势震荡偏空": "weak",
        "破位后的延续下跌": "down",
    }.get(stage, "range")
    return f"{stage_key}_{_score_band(score).lower()}_{rr_band}"


def _calc_forward_metrics(closes: List[float], highs: List[float], lows: List[float], i: int, horizon: int) -> Tuple[float, float, float]:
    entry = closes[i]
    future_slice = slice(i + 1, min(i + 1 + horizon, len(closes)))
    future_closes = closes[future_slice]
    future_highs = highs[future_slice]
    future_lows = lows[future_slice]
    if not future_closes:
        return 0.0, 0.0, 0.0
    ret = (future_closes[-1] / entry - 1.0) * 100.0 if entry > 0 else 0.0
    mfe = ((max(future_highs) / entry - 1.0) * 100.0) if future_highs and entry > 0 else 0.0
    mae = ((min(future_lows) / entry - 1.0) * 100.0) if future_lows and entry > 0 else 0.0
    return ret, mfe, mae


def _estimate_symbol_calibration(
    stage: str,
    bucket: str,
    closes: List[float],
    highs: List[float],
    lows: List[float],
    horizon_days: int = 5,
) -> Dict[str, Any]:
    if len(closes) < 140:
        return {
            "bucket": bucket,
            "sample_size": 0,
            "hit_rate": 0.0,
            "avg_return": 0.0,
            "mfe": 0.0,
            "mae": 0.0,
            "confidence": 0.35,
            "horizon_days": horizon_days,
        }

    stage_key = {
        "趋势上行": "up",
        "震荡整理": "range",
        "弱势震荡偏空": "weak",
        "破位后的延续下跌": "down",
    }.get(stage, "range")

    rets: List[float] = []
    mfes: List[float] = []
    maes: List[float] = []
    for i in range(90, len(closes) - horizon_days - 1):
        c = closes[i]
        ma20_i = _ma(closes[: i + 1], 20)
        ma50_i = _ma(closes[: i + 1], 50)
        if ma20_i and ma50_i:
            if c > ma20_i > ma50_i:
                key = "up"
            elif c < ma20_i < ma50_i:
                key = "down"
            elif c < ma20_i:
                key = "weak"
            else:
                key = "range"
        else:
            key = "range"
        if key != stage_key:
            continue
        ret, mfe, mae = _calc_forward_metrics(closes, highs, lows, i, horizon_days)
        rets.append(ret)
        mfes.append(mfe)
        maes.append(mae)

    n = len(rets)
    if n == 0:
        return {
            "bucket": bucket,
            "sample_size": 0,
            "hit_rate": 0.0,
            "avg_return": 0.0,
            "mfe": 0.0,
            "mae": 0.0,
            "confidence": 0.35,
            "horizon_days": horizon_days,
        }
    hit_rate = sum(1 for r in rets if r > 0) / float(n)
    avg_ret = sum(rets) / float(n)
    mfe_avg = sum(mfes) / float(n)
    mae_avg = sum(maes) / float(n)
    sample_factor = _clamp(n / 80.0, 0.0, 1.0)
    edge = _clamp((hit_rate - 0.5) * 2.0, -1.0, 1.0)
    return_factor = _clamp(avg_ret / 3.0, -1.0, 1.0)
    confidence = _clamp(0.45 + sample_factor * 0.25 + edge * 0.2 + return_factor * 0.1, 0.05, 0.95)
    return {
        "bucket": bucket,
        "sample_size": n,
        "hit_rate": hit_rate,
        "avg_return": avg_ret,
        "mfe": mfe_avg,
        "mae": mae_avg,
        "confidence": confidence,
        "horizon_days": horizon_days,
    }


def _extract_llm_action(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return "观望"
    match = re.search(r"(买入|卖出|观望)", content)
    if match:
        return match.group(1)
    return "观望"


def _fuse_rule_ai_action(rule_action: str, ai_action: str, confidence: float) -> Tuple[str, str]:
    # Rule is hard risk floor: AI can only downgrade risk, not bypass rule veto.
    if rule_action != "买入":
        if ai_action == "买入":
            return rule_action, "Rule风控未放行，忽略AI买入建议。"
        return rule_action, "Rule与AI方向一致或更保守。"
    if confidence < 0.52:
        return "观望", "Rule置信度偏低，按融合规则降级为观望。"
    if ai_action == "卖出":
        return "观望", "AI给出卖出信号，按融合规则降风险至观望。"
    if ai_action == "观望":
        return "观望", "AI未确认买入，按融合规则先观望。"
    return "买入", "Rule通过且AI确认，维持买入但建议分批执行。"


def _build_rule_report(
    symbol: str,
    klines: List[Dict],
    rule_profile: Optional[Dict[str, object]] = None,
    rule_market: str = "",
    portfolio_context: Optional[Dict[str, object]] = None,
) -> Tuple[str, Dict[str, object]]:
    dates, opens, highs, lows, closes, vols = _to_series(klines)
    market = (rule_market or detect_aitrader_rule_market(symbol)).strip().lower()
    profile = dict(rule_profile or get_aitrader_rule_profile(market))
    if portfolio_context is None:
        try:
            portfolio_context = get_paper_portfolio_context(symbol=symbol)
        except Exception:
            portfolio_context = None
    if len(closes) < 60:
        text = (
            "1. 核心结论\n"
            "数据不足：当前可用日K少于60根，无法稳定完成8段分析。\n\n"
            "8. 总结/结论/最终建议\n"
            "建议：观望。先补齐至少60-120根连续日线再分析。"
        )
        return text, {"engine": "rule", "error": "insufficient_bars", "bars": len(closes)}

    close_now = closes[-1]
    close_prev = closes[-2]
    digits = _price_digits(close_now)
    change_pct = _pct_change(close_now, close_prev)

    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma50 = _ma(closes, 50)
    ma60 = _ma(closes, 60)
    ma20_prev5 = _ma(closes[:-5], 20) if len(closes) > 25 else None

    ret20 = _pct_change(close_now, closes[-21]) if len(closes) >= 21 else None
    ret60 = _pct_change(close_now, closes[-61]) if len(closes) >= 61 else None
    ma20_slope = _pct_change(ma20, ma20_prev5) if ma20 and ma20_prev5 else None
    ma50_prev5 = _ma(closes[:-5], 50) if len(closes) > 55 else None

    bearish_stack = bool(ma5 and ma10 and ma20 and ma50 and (ma5 < ma10 < ma20 < ma50))
    bullish_stack = bool(ma5 and ma10 and ma20 and ma50 and (ma5 > ma10 > ma20 > ma50))

    atr14 = _calc_atr(highs, lows, closes, window=14)
    atr_pct = (atr14 / close_now * 100.0) if atr14 and close_now > 0 else 0.0
    hv_annual = (_calc_hist_vol(closes, window=20) or 0.0) * 100.0
    stage, stage_info = _detect_stage_adaptive(
        closes=closes,
        ma20=ma20,
        ma50=ma50,
        ma20_prev5=ma20_prev5,
        ma50_prev5=ma50_prev5,
        atr_pct=atr_pct,
        hv_annual=hv_annual,
    )

    high20 = max(highs[-20:])
    low20 = min(lows[-20:])
    high60 = max(highs[-60:])
    low60 = min(lows[-60:])

    recent_support = min(lows[-12:-1]) if len(lows) >= 13 else low20
    recent_resist = max(highs[-12:-1]) if len(highs) >= 13 else high20
    is_breakdown = close_now < recent_support

    box_high = max(highs[-30:-8]) if len(highs) >= 38 else high20
    box_low = min(lows[-12:-2]) if len(lows) >= 14 else low20
    measured_target = None
    if box_high > box_low and close_now < box_low:
        measured_target = close_now - (box_high - box_low)
    if measured_target is None:
        measured_target = low60

    vol_now = vols[-1] if vols else None
    vol5 = _ma(vols, 5)
    vol20 = _ma(vols, 20)
    vol_ratio5 = (vol_now / vol5) if vol_now and vol5 and vol5 > 0 else None
    vol_ratio20 = (vol_now / vol20) if vol_now and vol20 and vol20 > 0 else None

    dense_levels = _build_volume_density_levels(closes, vols, atr14)
    resistance_candidates = [
        v for v in [ma5, ma10, ma20, ma50, ma60, recent_resist, high20, high60]
        if v is not None and v > close_now
    ]
    support_candidates = [
        v for v in [ma5, ma10, ma20, ma50, ma60, recent_support, low20, low60, measured_target]
        if v is not None and v < close_now
    ]
    dense_resist = [v for v in dense_levels if v > close_now]
    dense_support = [v for v in dense_levels if v < close_now]
    resistance_zones = _build_price_zones(close_now, resistance_candidates, atr14, dense_resist)
    support_zones = _build_price_zones(close_now, support_candidates, atr14, dense_support)
    resistance_zones = sorted([z for z in resistance_zones if _zone_mid(z) > close_now], key=lambda z: _zone_mid(z))[:4]
    support_zones = sorted([z for z in support_zones if _zone_mid(z) < close_now], key=lambda z: _zone_mid(z), reverse=True)[:4]

    resistance_levels = _dedupe_levels([_zone_mid(z) for z in resistance_zones], digits)[:4]
    support_levels = _dedupe_levels([_zone_mid(z) for z in support_zones], digits)[:4]

    up_target = resistance_levels[0] if resistance_levels else None
    down_target = support_levels[1] if len(support_levels) >= 2 else (support_levels[0] if support_levels else None)
    up_pct, down_pct, rr = _calc_rr(close_now, up_target, down_target)

    indicator_values = compute_rule_indicators(
        symbol=symbol,
        klines=klines,
        market=market,
        portfolio_context=portfolio_context,
    )
    indicator_bonus, indicator_notes = _indicator_score_bonus(indicator_values)

    action, position, score_meta = _decide_action_with_scores(
        stage=stage,
        rr=rr,
        close_now=close_now,
        ma20=ma20,
        ma50=ma50,
        ret20=ret20,
        ret60=ret60,
        vol_ratio5=vol_ratio5,
        vol_ratio20=vol_ratio20,
        change_pct=change_pct,
        is_breakdown=is_breakdown,
        profile=profile,
        confidence=0.60,
        indicator_bonus_by_bucket=indicator_bonus,
        indicator_notes=indicator_notes,
    )
    signal_bucket = _build_signal_bucket(stage, float(score_meta.get("total_score") or 0.0), rr)
    calibration = None
    try:
        calibration = get_signal_calibration(symbol=symbol, bucket=signal_bucket, horizon_days=5)
    except Exception:
        calibration = None
    if not calibration:
        calibration = _estimate_symbol_calibration(
            stage=stage,
            bucket=signal_bucket,
            closes=closes,
            highs=highs,
            lows=lows,
            horizon_days=5,
        )
        try:
            upsert_signal_calibration(
                symbol=symbol,
                market=market,
                bucket=signal_bucket,
                horizon_days=int(calibration.get("horizon_days") or 5),
                sample_size=int(calibration.get("sample_size") or 0),
                hit_rate=float(calibration.get("hit_rate") or 0.0),
                avg_return=float(calibration.get("avg_return") or 0.0),
                mfe=float(calibration.get("mfe") or 0.0),
                mae=float(calibration.get("mae") or 0.0),
                confidence=float(calibration.get("confidence") or 0.0),
            )
        except Exception:
            pass
    confidence = float((calibration or {}).get("confidence") or 0.35)
    action, position, score_meta = _decide_action_with_scores(
        stage=stage,
        rr=rr,
        close_now=close_now,
        ma20=ma20,
        ma50=ma50,
        ret20=ret20,
        ret60=ret60,
        vol_ratio5=vol_ratio5,
        vol_ratio20=vol_ratio20,
        change_pct=change_pct,
        is_breakdown=is_breakdown,
        profile=profile,
        confidence=confidence,
        indicator_bonus_by_bucket=indicator_bonus,
        indicator_notes=indicator_notes,
    )

    portfolio_plan_lines, portfolio_plan_meta = _build_portfolio_plan(
        symbol=symbol,
        stage=stage,
        action=action,
        close_now=close_now,
        ma20=ma20,
        portfolio_context=portfolio_context,
        digits=digits,
    )
    portfolio_plan_text = "\n".join(portfolio_plan_lines)

    if stage in ("破位后的延续下跌", "弱势震荡偏空"):
        p_base, p_alt, p_tail = 65, 28, 7
        scene1 = f"惯性下探，优先看 {_fmt_price(down_target, digits)} 附近寻支撑"
        scene2 = f"弱反弹回抽，压力位 {_fmt_price(up_target, digits)} 附近受阻"
        scene3 = "放量收复关键位并站稳，形成短反转雏形"
    elif stage == "趋势上行":
        p_base, p_alt, p_tail = 55, 35, 10
        scene1 = "沿均线震荡上行，趋势延续"
        scene2 = "回踩MA20后再上攻"
        scene3 = "跌破关键均线转入震荡"
    else:
        p_base, p_alt, p_tail = 45, 40, 15
        scene1 = "区间震荡延续，等待方向选择"
        scene2 = "向上突破并放量确认"
        scene3 = "向下破位并放量扩散"

    vol_text = "量能中性"
    if change_pct is not None and change_pct < 0 and vol_ratio5 and vol_ratio5 >= 1.1:
        vol_text = "放量下跌，主动抛压占优"
    elif change_pct is not None and change_pct > 0 and vol_ratio5 and vol_ratio5 >= 1.1:
        vol_text = "放量上涨，短线资金回流"
    elif vol_ratio5 and vol_ratio5 < 0.85:
        vol_text = "缩量运行，资金观望明显"

    resist_text = _zone_text(resistance_zones, digits)
    support_text = _zone_text(support_zones, digits)

    rr_text = "N/A"
    if rr is not None and up_pct is not None and down_pct is not None:
        rr_text = (
            f"向上到 {_fmt_price(up_target, digits)} 约 {up_pct:+.1f}%，"
            f"向下到 {_fmt_price(down_target, digits)} 约 -{abs(down_pct):.1f}%，"
            f"R/R≈{rr:.2f}:1"
        )

    confidence_pct = confidence * 100.0
    confidence_text = (
        f"置信度 {confidence_pct:.1f}%（样本 {int((calibration or {}).get('sample_size') or 0)}，"
        f"命中率 {(float((calibration or {}).get('hit_rate') or 0.0) * 100.0):.1f}%）"
    )
    bonus_text = ""
    try:
        bonus_map = score_meta.get("indicator_bonus_by_bucket") or {}
        b_trend = float(bonus_map.get("trend") or 0.0)
        b_struct = float(bonus_map.get("structure") or 0.0)
        b_vol = float(bonus_map.get("volume") or 0.0)
        b_rr = float(bonus_map.get("rr") or 0.0)
        b_total = float(bonus_map.get("total") or 0.0)
        if any(abs(v) >= 0.05 for v in (b_trend, b_struct, b_vol, b_rr, b_total)):
            bonus_text = f"（指标加权 T{b_trend:+.1f} S{b_struct:+.1f} V{b_vol:+.1f} R{b_rr:+.1f} 总{b_total:+.1f}）"
    except Exception:
        bonus_text = ""
    score_text = (
        f"评分(T/S/V/RR)= {score_meta.get('trend_score', 0):.1f}/"
        f"{score_meta.get('structure_score', 0):.1f}/"
        f"{score_meta.get('volume_score', 0):.1f}/"
        f"{score_meta.get('rr_score', 0):.1f}，总分 {score_meta.get('total_score', 0):.1f}{bonus_text}"
    )
    risk_gates = score_meta.get("risk_gates") or []
    gate_text = "、".join([str(x) for x in risk_gates]) if risk_gates else "无"

    start_d = dates[0]
    end_d = dates[-1]
    report = (
        "1. 核心结论\n"
        f"核心结论：当前仍处“{stage}”阶段。现价 {_fmt_price(close_now, digits)}，"
        f"日涨跌 {change_pct:+.2f}%（若为负值则下跌）。"
        f"在未有效收复 {_fmt_price(up_target, digits) if up_target else '关键阻力'} 前，反弹优先视作回抽而非反转。"
        f"当前 {score_text}；{confidence_text}。\n\n"
        "2. 趋势与结构\n"
        f"- 周期区间：{start_d} ~ {end_d}，最近 {len(closes)} 根日K。\n"
        f"- 均线结构：MA5/10/20/50/60="
        f"{_fmt_price(ma5, digits)}/{_fmt_price(ma10, digits)}/{_fmt_price(ma20, digits)}/{_fmt_price(ma50, digits)}/{_fmt_price(ma60, digits)}。"
        f"{'空头排列' if bearish_stack else ('多头排列' if bullish_stack else '均线交错')}"
        f"，MA20斜率(5日) {ma20_slope:+.2f}% 。\n"
        f"- 波动自适应：ATR14={_fmt_price(atr14, digits)}（{atr_pct:.2f}%），年化波动率≈{hv_annual:.2f}% ，"
        f"趋势偏置 {float(stage_info.get('trend_bias') or 0.0):+.2f}，阈值[{float(stage_info.get('down_threshold') or 0.0):+.2f}, {float(stage_info.get('up_threshold') or 0.0):+.2f}]。\n"
        f"- 结构判断：近12日支撑 {_fmt_price(recent_support, digits)}，阻力 {_fmt_price(recent_resist, digits)}，"
        f"{'已出现破位' if is_breakdown else '尚未明确破位'}；"
        f"20日高低 {_fmt_price(high20, digits)} / {_fmt_price(low20, digits)}。\n\n"
        "3. 量能与资金\n"
        f"- 最新成交量：{_format_volume(symbol, vol_now)}；量比(5日/20日)= "
        f"{_fmt_num(vol_ratio5, 2)} / {_fmt_num(vol_ratio20, 2)}。\n"
        f"- 量价特征：{vol_text}。\n"
        "- 结论：当前量能结构更支持“延续/回抽”，不支持直接反转（需放量收复关键位验证）。\n\n"
        "4. 关键价位与意义\n"
        f"- 阻力区：{resist_text}。\n"
        f"- 支撑区：{support_text}。\n"
        f"- 成交密集价位：{_zone_text([(v, v) for v in dense_levels], digits) if dense_levels else '无显著密集区'}。\n"
        f"- 风险回报：{rr_text}。\n\n"
        "5. 情景推演（概率主观）\n"
        f"- 基线（{p_base}%）：{scene1}。\n"
        f"- 次情景（{p_alt}%）：{scene2}。\n"
        f"- 低概率（{p_tail}%）：{scene3}，需“价+量”同步确认。\n\n"
        "6. 交易计划（分类型）\n"
        f"- 空仓者：{('观望' if action != '买入' else '可小仓试探')}。"
        f"仅当回踩支撑后止跌、或放量重返 {_fmt_price(up_target, digits) if up_target else '关键位'} 并站稳再考虑执行。\n"
        f"- 持仓者：若反抽至 {_fmt_price(up_target, digits) if up_target else '阻力区'} 附近可减压；"
        f"若跌破 {_fmt_price(support_levels[0], digits) if support_levels else '短支撑'} 且无法快速收回，执行纪律性减仓/止损。\n"
        "- 短线：只做确认后的右侧，不做无量抢反弹；单笔风险建议控制在1%以内。\n\n"
        f"{portfolio_plan_text}\n\n"
        "7. 风险提示\n"
        "- 趋势风险：均线未修复前，反抽后再压的概率高。\n"
        "- 假突破风险：无量收复关键位通常持续性差。\n"
        f"- 风险闸门：{gate_text}。\n"
        f"- 历史校准：{confidence_text}，当前信号桶 {signal_bucket}。\n"
        "- 事件风险：政策/行业/海外波动可引发跳空，止损与仓位优先于观点。\n\n"
        "8. 总结/结论/最终建议\n"
        f"操作建议：{action}。仓位建议：{position}。"
        f"关键观察位：阻力 {resist_text}；支撑 {support_text}。"
        "本结论仅基于技术面日线数据，不构成投资建议。"
    )

    meta = {
        "engine": "rule",
        "rule_market": market,
        "rule_profile": {
            "rr_buy_downtrend": _profile_float(profile, "rr_buy_downtrend", 1.5),
            "rr_buy_uptrend": _profile_float(profile, "rr_buy_uptrend", 1.3),
            "require_close_above_ma20_downtrend": _profile_bool(
                profile, "require_close_above_ma20_downtrend", True
            ),
        },
        "stage": stage,
        "action": action,
        "position": position,
        "close": close_now,
        "change_pct": change_pct,
        "rr": rr,
        "up_pct": up_pct,
        "down_pct": down_pct,
        "resistance_levels": resistance_levels,
        "support_levels": support_levels,
        "resistance_zones": resistance_zones,
        "support_zones": support_zones,
        "dense_levels": dense_levels,
        "atr14": atr14,
        "atr_pct": atr_pct,
        "hv_annual": hv_annual,
        "scores": score_meta,
        "signal_bucket": signal_bucket,
        "calibration": calibration or {},
        "confidence": confidence,
        "ma": {"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma50": ma50, "ma60": ma60},
        "stage_info": stage_info,
        "portfolio_plan": portfolio_plan_meta,
        "indicator_values": indicator_values,
    }
    return report, meta


def _default_output_paths(symbol: str, end_date: str) -> Tuple[Path, Path]:
    safe_symbol = symbol.upper().replace(".", "_")
    out_dir = ROOT / "aitrader" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"daily_analysis_{safe_symbol}_{end_date}.md"
    json_path = out_dir / f"daily_kline_{safe_symbol}_{end_date}.json"
    return md_path, json_path


def _collect_stream_text(response) -> str:
    if isinstance(response, str):
        return response
    chunks: List[str] = []
    for chunk in response:
        if not chunk:
            continue
        chunks.append(chunk)
        sys.stdout.write(chunk)
        sys.stdout.flush()
    return "".join(chunks)


def _collect_text(response) -> str:
    if isinstance(response, str):
        return response
    parts: List[str] = []
    for chunk in response:
        if chunk:
            parts.append(str(chunk))
    return "".join(parts)


def _build_fusion_report(
    symbol: str,
    klines: List[Dict],
    rule_market: str,
    rule_profile: Dict[str, Any],
    portfolio_context: Optional[Dict[str, object]],
    snapshot_text: str,
    llm: Optional[LLMService],
    model: Optional[str],
    mode: str,
) -> Tuple[str, Dict[str, Any]]:
    rule_text, rule_meta = _build_rule_report(
        symbol=symbol,
        klines=klines,
        rule_profile=rule_profile,
        rule_market=rule_market,
        portfolio_context=portfolio_context,
    )
    rule_action = str(rule_meta.get("action") or "观望")
    confidence = float(rule_meta.get("confidence") or 0.35)
    ai_action = "观望"
    ai_text = ""
    ai_called = False
    fuse_reason = "Rule未给出买入信号，融合层保持Rule结论。"
    fused_action = rule_action

    if rule_action == "买入" and llm is not None and llm.is_configured():
        ai_called = True
        llm_prompt = (
            f"标的: {symbol}\n"
            "你将基于日线数据做二次确认。请只输出最终动作（买入/卖出/观望）和不超过4条理由。\n"
            "若信号不充分，优先观望。"
        )
        context_config = {
            "enable_memory": False,
            "enable_retrieval": False,
            "disable_history": True,
            "history_limit": 0,
            "recent_limit": 0,
            "save_history": False,
            "disable_indicator_context": True,
            "kline_rows_assistant": len(klines),
            "kline_rows_chat": len(klines),
        }
        response = llm.analyze_stock(
            symbol=symbol,
            klines=klines,
            model=(model or None),
            user_input=llm_prompt,
            mode=mode,
            context_config=context_config,
            transient_context=snapshot_text or None,
        )
        ai_text = _collect_text(response).strip()
        ai_action = _extract_llm_action(ai_text)
        fused_action, fuse_reason = _fuse_rule_ai_action(rule_action, ai_action, confidence)
    elif rule_action == "买入" and (llm is None or not llm.is_configured()):
        fuse_reason = "LLM不可用，融合层回退到Rule结果。"

    final_position = str(rule_meta.get("position") or "")
    if fused_action != "买入":
        final_position = "轻仓或空仓，等待下一次确认"

    report = (
        "1. 融合结论\n"
        f"最终动作：{fused_action}。原因：{fuse_reason}\n\n"
        "2. Rule风控底线\n"
        f"- Rule动作：{rule_action}\n"
        f"- 评分：{json.dumps(rule_meta.get('scores') or {}, ensure_ascii=False)}\n"
        f"- 置信度：{(confidence * 100.0):.1f}%\n\n"
        "3. AI观点（仅在Rule放行后调用）\n"
        f"- AI是否调用：{'是' if ai_called else '否'}\n"
        f"- AI动作：{ai_action}\n"
        f"- AI摘要：{(ai_text[:600] + ('...' if len(ai_text) > 600 else '')) if ai_text else 'N/A'}\n\n"
        "4. 冲突处理与仓位\n"
        "- 冲突规则：Rule为硬风控底线，AI只能降风险，不能绕过Rule买入禁令。\n"
        f"- 仓位建议：{final_position}\n\n"
        "5. 最终执行建议\n"
        f"- 建议动作：{fused_action}\n"
        "- 若后续价格与量能继续确认，再考虑提高仓位。"
    )

    meta = {
        "engine": "fusion",
        "rule_action": rule_action,
        "ai_action": ai_action,
        "fused_action": fused_action,
        "fuse_reason": fuse_reason,
        "ai_called": ai_called,
        "confidence": confidence,
        "rule_meta": rule_meta,
        "ai_summary": ai_text[:1200] if ai_text else "",
    }
    return report, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate structured AI report from daily K-lines.")
    parser.add_argument("--symbol", required=True, help="Stock/ETF/Index symbol, e.g. 002050.SZ")
    parser.add_argument("--engine", default="rule", choices=["rule", "llm", "fusion"], help="Analysis engine")
    parser.add_argument("--bars", type=int, default=365, help="How many daily bars to send to AI")
    parser.add_argument("--end-date", default="", help="End date (YYYYMMDD or YYYY-MM-DD), default latest trading day")
    parser.add_argument("--model", default="", help="LLM model id, default from backend config")
    parser.add_argument("--mode", default="assistant", choices=["assistant", "chat"], help="LLM mode")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh daily data before analysis")
    parser.add_argument("--save-history", action="store_true", help="Persist chat history/memory to DB")
    parser.add_argument("--strategy-id", type=int, default=0, help="Portfolio strategy id for holdings-aware plan (0=auto)")
    parser.add_argument("--no-portfolio-context", action="store_true", help="Disable portfolio-aware trade plan")
    parser.add_argument("--output-md", default="", help="Output markdown path")
    parser.add_argument("--output-json", default="", help="Output kline json path")
    args = parser.parse_args()

    symbol = (args.symbol or "").strip().upper()
    if not symbol:
        print("symbol is required", file=sys.stderr)
        return 2
    if args.bars < 60:
        print("bars must be >= 60 for stable daily analysis", file=sys.stderr)
        return 2

    end_d = _parse_date(args.end_date)
    # Use a wider calendar window to ensure enough trading bars for A-share/US markets.
    fetch_start = end_d - timedelta(days=max(420, args.bars * 3))
    start_ymd = _fmt_ymd(fetch_start)
    end_ymd = _fmt_ymd(end_d)

    klines, source = get_klines_with_cache(
        symbol=symbol,
        period="daily",
        start_date=start_ymd,
        end_date=end_ymd,
        limit=max(args.bars * 2, args.bars + 120),
        force_refresh=bool(args.force_refresh),
        include_intraday=False,
    )
    klines = _slice_bars(_sort_and_dedupe_daily(klines), args.bars)
    if not klines:
        print(f"[aitrader] no daily klines for {symbol}", file=sys.stderr)
        return 1

    first_date = str(klines[0].get("date") or "")
    last_date = str(klines[-1].get("date") or "")
    snapshot_text, snapshot_obj = _build_snapshot_text(klines)

    print(
        f"[aitrader] symbol={symbol} bars={len(klines)} "
        f"range={first_date}->{last_date} source={source} "
        f"engine={args.engine} model={args.model or 'default'} mode={args.mode}"
    )
    if snapshot_text:
        print("[aitrader] snapshot ready")

    analysis_meta: Dict[str, object] = {}
    if args.engine == "rule":
        rule_market = detect_aitrader_rule_market(symbol)
        rule_profile = get_aitrader_rule_profile(rule_market)
        portfolio_context = None
        if not args.no_portfolio_context:
            try:
                sid = args.strategy_id if args.strategy_id and args.strategy_id > 0 else None
                portfolio_context = get_paper_portfolio_context(symbol=symbol, strategy_id=sid)
            except Exception as e:
                print(f"[aitrader] portfolio context unavailable: {e}")
        print(
            f"[aitrader] rule_profile market={rule_market} "
            f"rr_down={_profile_float(rule_profile, 'rr_buy_downtrend', 1.5):.2f} "
            f"rr_up={_profile_float(rule_profile, 'rr_buy_uptrend', 1.3):.2f}"
        )
        analysis_text, analysis_meta = _build_rule_report(
            symbol,
            klines,
            rule_profile=rule_profile,
            rule_market=rule_market,
            portfolio_context=portfolio_context,
        )
        print(analysis_text)
    elif args.engine == "fusion":
        rule_market = detect_aitrader_rule_market(symbol)
        rule_profile = get_aitrader_rule_profile(rule_market)
        portfolio_context = None
        if not args.no_portfolio_context:
            try:
                sid = args.strategy_id if args.strategy_id and args.strategy_id > 0 else None
                portfolio_context = get_paper_portfolio_context(symbol=symbol, strategy_id=sid)
            except Exception as e:
                print(f"[aitrader] portfolio context unavailable: {e}")
        llm = LLMService()
        analysis_text, analysis_meta = _build_fusion_report(
            symbol=symbol,
            klines=klines,
            rule_market=rule_market,
            rule_profile=rule_profile,
            portfolio_context=portfolio_context,
            snapshot_text=snapshot_text,
            llm=llm,
            model=(args.model or None),
            mode=args.mode,
        )
        print(analysis_text)
    else:
        llm = LLMService()
        if not llm.is_configured():
            print("[aitrader] LLM is not configured, check api/.env", file=sys.stderr)
            return 1
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
            "save_history": bool(args.save_history),
            "disable_indicator_context": True,
            "kline_rows_assistant": len(klines),
            "kline_rows_chat": len(klines),
        }
        response = llm.analyze_stock(
            symbol=symbol,
            klines=klines,
            model=(args.model or None),
            user_input=user_input,
            mode=args.mode,
            context_config=context_config,
            transient_context=snapshot_text or None,
        )
        analysis_text = _collect_stream_text(response).strip()
        if not analysis_text:
            print("[aitrader] empty analysis output", file=sys.stderr)
            return 1
        if analysis_text.lower().startswith("error"):
            print(f"[aitrader] {analysis_text}", file=sys.stderr)
            return 1
        if not analysis_text.endswith("\n"):
            print("")

    default_md, default_json = _default_output_paths(symbol, end_ymd)
    output_md = Path(args.output_md).expanduser() if args.output_md else default_md
    output_json = Path(args.output_json).expanduser() if args.output_json else default_json
    if not output_md.is_absolute():
        output_md = ROOT / output_md
    if not output_json.is_absolute():
        output_json = ROOT / output_json
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    output_md.write_text(analysis_text + ("\n" if not analysis_text.endswith("\n") else ""), encoding="utf-8")

    payload = {
        "symbol": symbol,
        "source": source,
        "engine": args.engine,
        "bars": len(klines),
        "range": {"start": first_date, "end": last_date},
        "snapshot": snapshot_obj,
        "analysis_meta": analysis_meta,
        "klines": klines,
        "analysis_markdown": str(output_md),
    }
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[aitrader] saved analysis -> {output_md}")
    print(f"[aitrader] saved daily klines -> {output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
