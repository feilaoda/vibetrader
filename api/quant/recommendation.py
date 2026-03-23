import time
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from aitrader.analysis.run_daily_kline_analysis import _build_rule_report, _fuse_rule_ai_action
from cache import get_klines_with_cache
from db import (
    add_screening_result,
    detect_aitrader_rule_market,
    get_aitrader_rule_profile,
    get_symbol_db,
    update_screening_run,
)
from llm import llm_service
from symbols import get_all_symbols
from watchlist import load_watchlist
from websearch import search_web, is_configured as web_search_configured


def _map_rule_action(action: str) -> str:
    text = (action or "").strip()
    if text == "买入":
        return "BUY"
    if text == "卖出":
        return "SKIP"
    return "WATCH"


def _map_ai_action(action: str) -> str:
    text = (action or "").strip().upper()
    if "BUY" in text:
        return "BUY"
    if "WATCH" in text:
        return "WATCH"
    if "PASS" in text or "SKIP" in text or "SELL" in text:
        return "SKIP"
    return "WATCH"


def _map_ai_action_cn(action: str) -> str:
    text = _map_ai_action(action)
    if text == "BUY":
        return "买入"
    if text == "SKIP":
        return "卖出"
    return "观望"


def _parse_ai_decision(content: str) -> Tuple[str, str, str]:
    text = (content or "").strip()
    if not text:
        return "WATCH", "", ""
    upper = text.upper()
    decision = "WATCH"
    for line in upper.splitlines():
        if "DECISION" in line:
            if "BUY" in line:
                decision = "BUY"
            elif "WATCH" in line:
                decision = "WATCH"
            elif "PASS" in line or "SKIP" in line or "SELL" in line:
                decision = "SKIP"
            break
    if decision == "WATCH":
        if "DECISION: BUY" in upper or "DECISION:[BUY" in upper or "RECOMMEND" in upper and "BUY" in upper:
            decision = "BUY"
        elif "DECISION: PASS" in upper or "DECISION:[PASS" in upper or "RECOMMEND" in upper and "PASS" in upper:
            decision = "SKIP"
    summary = text
    risk = ""
    if "REASON:" in text:
        summary = text.split("REASON:", 1)[-1].strip()
    if "RISK:" in text:
        risk = text.split("RISK:", 1)[-1].strip()
    return decision, summary.strip(), risk.strip()


def _build_news_query(symbol: str, name: Optional[str], market: str) -> str:
    base = name or symbol
    if market == "us":
        return f"{base} {symbol} stock news earnings"
    if market == "crypto":
        return f"{base} crypto news"
    return f"{base} {symbol} 新闻 业绩 重大 事件"


def _format_web_results(results: List[Dict[str, str]]) -> str:
    if not results:
        return "无"
    lines = []
    for item in results:
        title = item.get("title") or ""
        snippet = item.get("snippet") or ""
        date = item.get("date") or ""
        if date:
            lines.append(f"- {date} {title}：{snippet}".strip())
        else:
            lines.append(f"- {title}：{snippet}".strip())
    return "\n".join(lines)


def _format_rule_brief(rule_meta: Dict[str, Any]) -> str:
    if not rule_meta:
        return "Rule: 无"
    scores = rule_meta.get("scores") or {}
    total = scores.get("total_score")
    trend = scores.get("trend_score")
    structure = scores.get("structure_score")
    volume = scores.get("volume_score")
    rr_score = scores.get("rr_score")
    action = rule_meta.get("action")
    stage = rule_meta.get("stage")
    rr = rule_meta.get("rr")
    confidence = rule_meta.get("confidence")
    return (
        f"Rule: action={action} stage={stage} rr={rr} "
        f"score={total} (T/S/V/RR={trend}/{structure}/{volume}/{rr_score}) "
        f"confidence={confidence}"
    )


def _build_rule_reason(rule_meta: Dict[str, Any], action_cn: str, score: float) -> str:
    stage = rule_meta.get("stage") or ""
    rr = rule_meta.get("rr")
    profile = rule_meta.get("rule_profile") or {}
    rr_down = profile.get("rr_buy_downtrend")
    rr_up = profile.get("rr_buy_uptrend")
    weak_or_down = ("下跌" in stage) or ("偏空" in stage)
    rr_threshold = rr_down if weak_or_down else rr_up
    scores = rule_meta.get("scores") or {}
    gates = scores.get("risk_gates") or []
    reasons = []
    if action_cn != "买入":
        if rr is not None and rr_threshold:
            try:
                if float(rr) < float(rr_threshold):
                    reasons.append(f"RR<{float(rr_threshold):.2f}")
            except Exception:
                pass
        if gates:
            gates_text = "、".join(list(gates)[:3])
            if gates_text:
                reasons.append(f"闸门:{gates_text}")
    detail = "; ".join(reasons) if reasons else "规则未满足"
    rr_text = f"{rr:.2f}" if isinstance(rr, (int, float)) else str(rr)
    return f"Rule:{action_cn} stage={stage or '--'} score={score:.1f} rr={rr_text} {detail}".strip()


def _load_universe(universe: str, market: str, limit: Optional[int], symbols: Optional[List[str]]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    universe = (universe or "watchlist").strip().lower()
    market = (market or "all").strip().lower()
    if universe == "symbols" and symbols:
        for sym in symbols:
            if not sym:
                continue
            items.append({"symbol": sym.strip().upper(), "market": detect_aitrader_rule_market(sym)})
    elif universe == "all":
        df = get_all_symbols()
        if df is not None:
            try:
                for row in df.to_dict("records"):
                    sym = str(row.get("symbol") or "").strip().upper()
                    if not sym:
                        continue
                    items.append({"symbol": sym, "market": detect_aitrader_rule_market(sym), "name": row.get("name")})
            except Exception:
                pass
    else:
        for row in load_watchlist():
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            items.append({
                "symbol": sym,
                "market": (row.get("market") or detect_aitrader_rule_market(sym)),
                "name": row.get("name") or "",
            })
    if market != "all":
        items = [it for it in items if (it.get("market") or detect_aitrader_rule_market(it.get("symbol"))) == market]
    if limit and limit > 0:
        items = items[: int(limit)]
    return items


def run_recommendation_job(run_id: int, params: Dict[str, Any]) -> None:
    processed = 0
    symbols: List[Dict[str, str]] = []
    try:
        engine = (params.get("engine") or "fusion").strip().lower()
        universe = params.get("universe") or "watchlist"
        market = params.get("market") or "all"
        limit = params.get("limit")
        rule_min_score = float(params.get("rule_min_score") or 60)
        ai_top_k = int(params.get("ai_top_k") or 20)
        use_web = bool(params.get("use_web"))
        web_top_k = int(params.get("web_top_k") or 5)
        model_id = params.get("model") or llm_service.default_model
        bars = int(params.get("bars") or 200)
        sleep_sec = float(params.get("sleep_sec") or 0)
        include_pass = bool(params.get("include_pass"))

        update_screening_run(run_id, status="running", processed=0, total=0)

        symbols = _load_universe(universe, market, limit, params.get("symbols"))
        update_screening_run(run_id, total=len(symbols))

        if not symbols:
            update_screening_run(run_id, status="completed", processed=0, total=0, finished=True)
            return

        candidates: List[Dict[str, Any]] = []

        for item in symbols:
            symbol = item.get("symbol")
            if not symbol:
                continue
            try:
                klines, source = get_klines_with_cache(symbol, "daily", limit=bars)
                if not klines:
                    add_screening_result(run_id, symbol, "NO_DATA", 0.0, "no daily data", {"error": "no_data"})
                    processed += 1
                    update_screening_run(run_id, processed=processed)
                    continue

                rule_market = detect_aitrader_rule_market(symbol)
                rule_profile = get_aitrader_rule_profile(rule_market)
                rule_report, rule_meta = _build_rule_report(symbol, klines, rule_profile=rule_profile, rule_market=rule_market)

                scores = rule_meta.get("scores") or {}
                total_score = float(scores.get("total_score") or 0.0)
                rule_action_cn = rule_meta.get("action") or "观望"
                rule_action = _map_rule_action(rule_action_cn)
                profile = rule_meta.get("rule_profile") or {}
                rr_up = profile.get("rr_buy_uptrend")
                rr_down = profile.get("rr_buy_downtrend")
                weak_or_down = ("下跌" in (rule_meta.get("stage") or "")) or ("偏空" in (rule_meta.get("stage") or ""))
                rr_threshold = rr_down if weak_or_down else rr_up

                rule_metrics = {
                    "action": rule_action_cn,
                    "stage": rule_meta.get("stage"),
                    "rr": rule_meta.get("rr"),
                    "rr_up": rr_up,
                    "rr_down": rr_down,
                    "rr_threshold": rr_threshold,
                    "confidence": rule_meta.get("confidence"),
                    "scores": rule_meta.get("scores") or {},
                    "risk_gates": (rule_meta.get("scores") or {}).get("risk_gates"),
                }
                rule_reason = _build_rule_reason(rule_meta, rule_action_cn, total_score)

                candidate = {
                    "symbol": symbol,
                    "market": rule_market,
                    "name": item.get("name") or "",
                    "rule_action_cn": rule_action_cn,
                    "rule_action": rule_action,
                    "rule_report": rule_report,
                    "rule_meta": rule_meta,
                    "score": total_score,
                    "klines": klines,
                    "source": source,
                }

                if engine == "rule":
                    final_reason = rule_reason
                    add_screening_result(
                        run_id,
                        symbol,
                        rule_action,
                        total_score,
                        final_reason,
                        {"rule": rule_meta, "source": source},
                        model_id="rule",
                        rule_metrics=rule_metrics,
                    )
                    processed += 1
                    update_screening_run(run_id, processed=processed)
                    continue

                if rule_action_cn == "买入" or total_score >= rule_min_score:
                    candidates.append(candidate)
                elif include_pass:
                    add_screening_result(
                        run_id,
                        symbol,
                        "SKIP",
                        total_score,
                        f"{rule_reason} | Rule过滤",
                        {"rule": rule_meta, "source": source},
                        rule_metrics=rule_metrics,
                    )
                processed += 1
                update_screening_run(run_id, processed=processed)
            except Exception as e:
                add_screening_result(run_id, symbol, "ERROR", 0.0, f"rule error: {e}", {"error": str(e)})
                processed += 1
                update_screening_run(run_id, processed=processed)
            if sleep_sec > 0:
                time.sleep(sleep_sec)

        if engine == "rule":
            update_screening_run(run_id, status="completed", processed=processed, total=len(symbols), finished=True)
            return

        # Sort candidates by score desc
        candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        if not candidates:
            update_screening_run(run_id, status="completed", processed=processed, total=len(symbols), finished=True)
            return

        # AI phase
        can_use_ai = engine in ("ai", "fusion") and llm_service.is_configured()
        ai_limit = ai_top_k if can_use_ai and ai_top_k > 0 else 0

        for idx, candidate in enumerate(candidates):
            symbol = candidate["symbol"]
            rule_meta = candidate["rule_meta"]
            rule_action_cn = candidate["rule_action_cn"]
            rule_score = candidate["score"]
            rule_conf = float(rule_meta.get("confidence") or 0.0)

            ai_action = ""
            ai_summary = ""
            ai_risk = ""
            web_results: List[Dict[str, str]] = []
            if can_use_ai and idx < ai_limit:
                if use_web and web_search_configured():
                    name = candidate.get("name")
                    if not name:
                        row = get_symbol_db(symbol)
                        name = (row or {}).get("name") if row else ""
                    query = _build_news_query(symbol, name, candidate.get("market") or "ashare")
                    web_results = search_web(query, limit=web_top_k)
                try:
                    client = llm_service._get_client(model_id)
                    if client:
                        system_msg = llm_service.build_system_prompt(symbol)
                        rule_brief = _format_rule_brief(rule_meta)
                        kline_str = llm_service._format_kline_data(candidate["klines"], max_rows=60)
                        web_text = _format_web_results(web_results) if use_web else "无"
                        user_msg = (
                            "你是一位严格的荐股助手。请结合 Rule 评分、近期K线与(可选)新闻摘要，"
                            "输出清晰的筛选结论。\n\n"
                            f"{rule_brief}\n\n"
                            "近期K线数据:\n"
                            f"{kline_str}\n\n"
                            "新闻摘要(近7天):\n"
                            f"{web_text}\n\n"
                            "请严格按以下格式输出:\n"
                            "DECISION: BUY / WATCH / PASS\n"
                            "REASON: 60字以内理由\n"
                            "RISK: 30字以内风险点\n"
                        )
                        resp = client.chat.completions.create(
                            model=model_id,
                            messages=[
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                            temperature=0.2,
                            max_tokens=800,
                        )
                        content = resp.choices[0].message.content if resp.choices else ""
                        ai_decision, ai_summary, ai_risk = _parse_ai_decision(content or "")
                        ai_action = _map_ai_action(ai_decision)
                except Exception as e:
                    ai_action = ""
                    ai_summary = f"AI error: {e}"
                    ai_risk = ""

            final_action = candidate["rule_action"]
            final_reason = _build_rule_reason(rule_meta, rule_action_cn, rule_score)

            if engine == "ai" and ai_action:
                final_action = ai_action
                final_reason = f"AI:{ai_summary or ai_action} | {final_reason}"
            elif engine == "fusion" and ai_action:
                fused_cn, fused_reason = _fuse_rule_ai_action(rule_action_cn, _map_ai_action_cn(ai_action), rule_conf)
                final_action = _map_rule_action(fused_cn)
                if ai_summary:
                    final_reason = f"{fused_reason} AI:{ai_summary}"
                else:
                    final_reason = fused_reason
                final_reason = f"{final_reason} | {_build_rule_reason(rule_meta, rule_action_cn, rule_score)}"

            raw_json = {
                "rule": rule_meta,
                "rule_action": rule_action_cn,
                "ai_action": ai_action,
                "ai_summary": ai_summary,
                "ai_risk": ai_risk,
                "web": web_results,
                "source": candidate.get("source"),
            }

            rule_metrics = {
                "action": rule_action_cn,
                "stage": rule_meta.get("stage"),
                "rr": rule_meta.get("rr"),
                "confidence": rule_meta.get("confidence"),
                "scores": rule_meta.get("scores") or {},
                "risk_gates": (rule_meta.get("scores") or {}).get("risk_gates"),
            }
            ai_metrics = {
                "action": ai_action,
                "reason": ai_summary,
                "risk": ai_risk,
                "model": model_id if can_use_ai else "",
            }

            add_screening_result(
                run_id,
                symbol,
                final_action,
                rule_score,
                final_reason[:600],
                raw_json,
                model_id=model_id if can_use_ai else "rule",
                rule_metrics=rule_metrics,
                ai_metrics=ai_metrics,
            )

        update_screening_run(run_id, status="completed", processed=len(symbols), total=len(symbols), finished=True)
    except Exception as e:
        add_screening_result(run_id, "__RUN__", "ERROR", 0.0, f"run error: {e}", {"error": str(e)})
        update_screening_run(
            run_id,
            status="failed",
            processed=processed,
            total=len(symbols),
            finished=True,
            finished_status="failed",
        )
