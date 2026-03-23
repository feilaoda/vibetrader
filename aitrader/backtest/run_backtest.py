#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aitrader.data.load_prices import ensure_price_csv
from aitrader.models.ranker import AIRankerConfig, TreeRanker
from aitrader.risk.pretrade_checks import PreTradeChecksConfig, run_pretrade_checks
from aitrader.strategies.baseline_trend_vol import (
    RiskConfig,
    StrategyConfig,
    allocate_equal_weights,
    rank_candidates,
    select_positions,
)


@dataclass(frozen=True)
class ExecutionRiskConfig:
    max_daily_loss: float = 0.02
    pause_days_after_halt: int = 2


@dataclass(frozen=True)
class CostConfig:
    fee_bps: float = 4.0
    slippage_bps: float = 6.0


def _resolve_path(path_text: str) -> Path:
    p = Path(path_text)
    if p.is_absolute():
        return p
    return ROOT / p


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_price_panel(csv_path: Path) -> tuple[List[str], Dict[str, List[Optional[float]]]]:
    by_date: Dict[str, Dict[str, float]] = {}
    symbols = set()

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"date", "symbol", "close"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")

        for row in reader:
            date_key = (row.get("date") or "").strip()
            symbol = (row.get("symbol") or "").strip().upper()
            close_text = (row.get("close") or "").strip()
            if not date_key or not symbol or not close_text:
                continue
            close = float(close_text)
            if close <= 0:
                continue
            by_date.setdefault(date_key, {})[symbol] = close
            symbols.add(symbol)

    if not by_date:
        raise ValueError(f"No valid price rows found in {csv_path}")

    dates = sorted(by_date.keys())
    panel: Dict[str, List[Optional[float]]] = {
        symbol: [None] * len(dates) for symbol in sorted(symbols)
    }

    for idx, date_key in enumerate(dates):
        for symbol, close in by_date[date_key].items():
            panel[symbol][idx] = close

    return dates, panel


def _calc_turnover(prev_weights: Dict[str, float], target_weights: Dict[str, float]) -> float:
    symbols = set(prev_weights.keys()) | set(target_weights.keys())
    return sum(abs(target_weights.get(s, 0.0) - prev_weights.get(s, 0.0)) for s in symbols)


def _max_drawdown(nav_curve: List[float]) -> float:
    peak = nav_curve[0]
    worst = 0.0
    for nav in nav_curve:
        if nav > peak:
            peak = nav
        dd = nav / peak - 1.0
        if dd < worst:
            worst = dd
    return worst


def _summarize(
    nav_curve: List[float],
    daily_returns: List[float],
    turnovers: List[float],
    initial_capital: float,
) -> dict:
    n = len(daily_returns)
    nav = nav_curve[-1]

    avg_ret = sum(daily_returns) / n if n else 0.0
    var_ret = sum((r - avg_ret) ** 2 for r in daily_returns) / n if n else 0.0
    std_ret = math.sqrt(var_ret)

    annual_vol = std_ret * math.sqrt(252.0)
    sharpe = (avg_ret / std_ret) * math.sqrt(252.0) if std_ret > 0 else 0.0
    cagr = nav ** (252.0 / n) - 1.0 if n > 0 and nav > 0 else -1.0

    return {
        "trading_days": n,
        "total_return": nav - 1.0,
        "cagr": cagr,
        "annual_volatility": annual_vol,
        "sharpe": sharpe,
        "max_drawdown": _max_drawdown(nav_curve),
        "win_rate": (sum(1 for r in daily_returns if r > 0) / n) if n else 0.0,
        "avg_turnover": (sum(turnovers) / n) if n else 0.0,
        "max_daily_loss": min(daily_returns) if daily_returns else 0.0,
        "start_equity": initial_capital,
        "end_equity": initial_capital * nav,
    }


def _delta_metrics(baseline_metrics: dict, ai_metrics: dict) -> dict:
    keys = [
        "total_return",
        "cagr",
        "annual_volatility",
        "sharpe",
        "max_drawdown",
        "win_rate",
        "avg_turnover",
        "max_daily_loss",
    ]
    out: Dict[str, float] = {}
    for key in keys:
        out[key] = float(ai_metrics.get(key, 0.0)) - float(baseline_metrics.get(key, 0.0))
    return out


def _simulate_strategy(
    *,
    mode: str,
    dates: List[str],
    close_by_symbol: Dict[str, List[Optional[float]]],
    warmup: int,
    strategy: StrategyConfig,
    risk: RiskConfig,
    exec_risk: ExecutionRiskConfig,
    costs: CostConfig,
    pretrade: PreTradeChecksConfig,
    initial_capital: float,
    ai_cfg: Optional[AIRankerConfig] = None,
) -> dict:
    nav = 1.0
    nav_curve: List[float] = [nav]
    daily_returns: List[float] = []
    turnovers: List[float] = []

    prev_weights: Dict[str, float] = {}
    halt_remaining = 0
    halt_trigger_count = 0
    pretrade_adjusted_days = 0
    pretrade_note_count = 0
    selection_sizes: List[int] = []
    candidate_sizes: List[int] = []

    ranker = TreeRanker(ai_cfg) if (mode == "ai_overlay" and ai_cfg is not None) else None

    for idx in range(warmup, len(dates) - 1):
        if halt_remaining > 0:
            raw_target_weights: Dict[str, float] = {}
            candidate_count = 0
            halt_remaining -= 1
        else:
            if mode == "baseline":
                ranked = rank_candidates(close_by_symbol, idx, strategy)
                candidate_count = len(ranked)
                raw_target_weights = select_positions(close_by_symbol, idx, strategy, risk)
            elif mode == "ai_overlay":
                ranked = rank_candidates(close_by_symbol, idx, strategy)
                pool_size = max(strategy.top_k, strategy.top_k * (ai_cfg.candidate_pool_multiplier if ai_cfg else 3))
                candidate_symbols = [symbol for symbol, _ in ranked[:pool_size]]
                candidate_count = len(candidate_symbols)
                ai_ranked = ranker.rank(close_by_symbol, idx, candidate_symbols) if ranker else []
                raw_target_weights = allocate_equal_weights(ai_ranked, strategy.top_k, risk)
            else:
                raise ValueError(f"Unsupported simulation mode: {mode}")

        checked_weights, notes = run_pretrade_checks(
            raw_target_weights,
            prev_weights,
            close_by_symbol,
            idx,
            pretrade,
        )
        if notes:
            pretrade_adjusted_days += 1
            pretrade_note_count += len(notes)

        turnover = _calc_turnover(prev_weights, checked_weights)
        cost_rate = turnover * (costs.fee_bps + costs.slippage_bps) / 10_000.0

        gross_ret = 0.0
        for symbol, weight in checked_weights.items():
            p0 = close_by_symbol[symbol][idx]
            p1 = close_by_symbol[symbol][idx + 1]
            if p0 is None or p1 is None or p0 <= 0:
                continue
            gross_ret += weight * (p1 / p0 - 1.0)

        net_ret = gross_ret - cost_rate
        nav *= 1.0 + net_ret

        daily_returns.append(net_ret)
        turnovers.append(turnover)
        nav_curve.append(nav)
        selection_sizes.append(len(checked_weights))
        candidate_sizes.append(candidate_count)

        if net_ret <= -exec_risk.max_daily_loss:
            halt_remaining = max(exec_risk.pause_days_after_halt, 0)
            halt_trigger_count += 1

        prev_weights = checked_weights

    metrics = _summarize(nav_curve, daily_returns, turnovers, initial_capital)
    return {
        "name": mode,
        "metrics": metrics,
        "context": {
            "start_date": dates[warmup],
            "end_date": dates[-1],
            "halt_trigger_count": halt_trigger_count,
            "pretrade_adjusted_days": pretrade_adjusted_days,
            "pretrade_note_count": pretrade_note_count,
            "avg_selected_symbols": (sum(selection_sizes) / len(selection_sizes)) if selection_sizes else 0.0,
            "avg_candidates": (sum(candidate_sizes) / len(candidate_sizes)) if candidate_sizes else 0.0,
        },
    }


def _to_markdown(report: dict) -> str:
    lines: List[str] = [
        "# Backtest Compare Report",
        "",
        f"- generated_at_utc: {report['generated_at_utc']}",
        f"- data_csv: {report['data']['csv_path']}",
        f"- data_provider: {report['data']['provider']}",
        f"- symbols: {report['data']['symbols']}",
        f"- date_range: {report['data']['start_date']} -> {report['data']['end_date']}",
        "",
    ]

    baseline = report["runs"]["baseline"]
    ai_overlay = report["runs"].get("ai_overlay")
    bm = baseline["metrics"]
    lines.extend(
        [
            "## Baseline",
            "",
            "| metric | value |",
            "|---|---:|",
            f"| total_return | {bm['total_return']:.6f} |",
            f"| cagr | {bm['cagr']:.6f} |",
            f"| annual_volatility | {bm['annual_volatility']:.6f} |",
            f"| sharpe | {bm['sharpe']:.6f} |",
            f"| max_drawdown | {bm['max_drawdown']:.6f} |",
            f"| win_rate | {bm['win_rate']:.6f} |",
            f"| avg_turnover | {bm['avg_turnover']:.6f} |",
            "",
        ]
    )

    if ai_overlay:
        am = ai_overlay["metrics"]
        delta = report["delta_vs_baseline"]
        lines.extend(
            [
                "## AI Overlay",
                "",
                "| metric | baseline | ai_overlay | delta(ai-baseline) |",
                "|---|---:|---:|---:|",
                f"| total_return | {bm['total_return']:.6f} | {am['total_return']:.6f} | {delta['total_return']:.6f} |",
                f"| cagr | {bm['cagr']:.6f} | {am['cagr']:.6f} | {delta['cagr']:.6f} |",
                f"| annual_volatility | {bm['annual_volatility']:.6f} | {am['annual_volatility']:.6f} | {delta['annual_volatility']:.6f} |",
                f"| sharpe | {bm['sharpe']:.6f} | {am['sharpe']:.6f} | {delta['sharpe']:.6f} |",
                f"| max_drawdown | {bm['max_drawdown']:.6f} | {am['max_drawdown']:.6f} | {delta['max_drawdown']:.6f} |",
                f"| win_rate | {bm['win_rate']:.6f} | {am['win_rate']:.6f} | {delta['win_rate']:.6f} |",
                f"| avg_turnover | {bm['avg_turnover']:.6f} | {am['avg_turnover']:.6f} | {delta['avg_turnover']:.6f} |",
                "",
            ]
        )

    return "\n".join(lines)


def run(config: dict, config_path: Path) -> dict:
    data_source = config.get("data_source", {})
    trading = config.get("trading", {})
    strategy_cfg = config.get("strategy", {})
    risk_cfg = config.get("risk", {})
    cost_cfg = config.get("costs", {})
    ai_cfg_raw = config.get("ai_overlay", {})

    csv_path, data_meta = ensure_price_csv(data_source, root=ROOT)
    if not csv_path.exists():
        raise FileNotFoundError(f"Price CSV not found: {csv_path}")

    dates, close_by_symbol = _load_price_panel(csv_path)
    symbols = sorted(close_by_symbol.keys())

    strategy = StrategyConfig(
        short_window=int(strategy_cfg.get("short_window", 20)),
        long_window=int(strategy_cfg.get("long_window", 60)),
        momentum_window=int(strategy_cfg.get("momentum_window", 20)),
        vol_window=int(strategy_cfg.get("vol_window", 20)),
        max_volatility=float(strategy_cfg.get("max_volatility", 0.03)),
        min_momentum=float(strategy_cfg.get("min_momentum", 0.0)),
        top_k=int(strategy_cfg.get("top_k", 5)),
    )
    risk = RiskConfig(
        max_position_weight=float(risk_cfg.get("max_position_weight", 0.10)),
        max_gross_exposure=float(risk_cfg.get("max_gross_exposure", 1.0)),
    )
    exec_risk = ExecutionRiskConfig(
        max_daily_loss=float(risk_cfg.get("max_daily_loss", 0.02)),
        pause_days_after_halt=int(risk_cfg.get("pause_days_after_halt", 2)),
    )
    pretrade = PreTradeChecksConfig(
        max_position_weight=risk.max_position_weight,
        max_gross_exposure=risk.max_gross_exposure,
        max_turnover=float(risk_cfg.get("max_turnover", 1.2)),
        min_price=float(risk_cfg.get("min_price", 1.0)),
        allow_short=bool(risk_cfg.get("allow_short", False)),
    )
    costs = CostConfig(
        fee_bps=float(cost_cfg.get("fee_bps", 4.0)),
        slippage_bps=float(cost_cfg.get("slippage_bps", 6.0)),
    )
    ai_cfg = AIRankerConfig(
        train_lookback_days=int(ai_cfg_raw.get("train_lookback_days", 180)),
        min_train_rows=int(ai_cfg_raw.get("min_train_rows", 300)),
        max_train_rows=int(ai_cfg_raw.get("max_train_rows", 1200)),
        max_depth=int(ai_cfg_raw.get("max_depth", 3)),
        min_samples_leaf=int(ai_cfg_raw.get("min_samples_leaf", 24)),
        max_thresholds_per_feature=int(ai_cfg_raw.get("max_thresholds_per_feature", 16)),
        candidate_pool_multiplier=int(ai_cfg_raw.get("candidate_pool_multiplier", 3)),
        retrain_interval_days=int(ai_cfg_raw.get("retrain_interval_days", 20)),
    )

    base_warmup = max(strategy.long_window, strategy.momentum_window + 1, strategy.vol_window + 1, 60)
    warmup = max(base_warmup, int(ai_cfg_raw.get("warmup_override", base_warmup)))

    if len(dates) < warmup + 2:
        raise ValueError(f"Not enough rows for lookbacks. Need >= {warmup + 2} dates, got {len(dates)}")

    initial_capital = float(trading.get("initial_capital", 100_000.0))
    run_compare = bool(config.get("run_compare", True))

    baseline_run = _simulate_strategy(
        mode="baseline",
        dates=dates,
        close_by_symbol=close_by_symbol,
        warmup=warmup,
        strategy=strategy,
        risk=risk,
        exec_risk=exec_risk,
        costs=costs,
        pretrade=pretrade,
        initial_capital=initial_capital,
    )

    runs = {"baseline": baseline_run}
    delta_vs_baseline = {}
    if run_compare:
        ai_run = _simulate_strategy(
            mode="ai_overlay",
            dates=dates,
            close_by_symbol=close_by_symbol,
            warmup=warmup,
            strategy=strategy,
            risk=risk,
            exec_risk=exec_risk,
            costs=costs,
            pretrade=pretrade,
            initial_capital=initial_capital,
            ai_cfg=ai_cfg,
        )
        runs["ai_overlay"] = ai_run
        delta_vs_baseline = _delta_metrics(baseline_run["metrics"], ai_run["metrics"])

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path),
        "data": {
            "provider": data_meta.get("provider"),
            "csv_path": str(csv_path),
            "symbols": len(symbols),
            "start_date": dates[warmup],
            "end_date": dates[-1],
            "meta": data_meta,
        },
        "strategy": {
            "short_window": strategy.short_window,
            "long_window": strategy.long_window,
            "momentum_window": strategy.momentum_window,
            "vol_window": strategy.vol_window,
            "max_volatility": strategy.max_volatility,
            "min_momentum": strategy.min_momentum,
            "top_k": strategy.top_k,
        },
        "risk": {
            "max_position_weight": risk.max_position_weight,
            "max_gross_exposure": risk.max_gross_exposure,
            "max_daily_loss": exec_risk.max_daily_loss,
            "pause_days_after_halt": exec_risk.pause_days_after_halt,
            "max_turnover": pretrade.max_turnover,
            "min_price": pretrade.min_price,
        },
        "costs": {
            "fee_bps": costs.fee_bps,
            "slippage_bps": costs.slippage_bps,
        },
        "ai_overlay": {
            "enabled": run_compare,
            "train_lookback_days": ai_cfg.train_lookback_days,
            "min_train_rows": ai_cfg.min_train_rows,
            "max_train_rows": ai_cfg.max_train_rows,
            "max_depth": ai_cfg.max_depth,
            "min_samples_leaf": ai_cfg.min_samples_leaf,
            "max_thresholds_per_feature": ai_cfg.max_thresholds_per_feature,
            "retrain_interval_days": ai_cfg.retrain_interval_days,
            "candidate_pool_multiplier": ai_cfg.candidate_pool_multiplier,
        },
        "runs": runs,
        "delta_vs_baseline": delta_vs_baseline,
    }

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run baseline and AI-overlay backtest.")
    parser.add_argument(
        "--config",
        default="aitrader/config/backtest.example.json",
        help="Config JSON path",
    )
    parser.add_argument("--output-json", default=None, help="Optional JSON report output path")
    parser.add_argument("--output-md", default=None, help="Optional markdown report output path")
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")

    config = _load_json(config_path)
    report = run(config, config_path)

    report_cfg = config.get("report", {})
    output_json = _resolve_path(args.output_json or report_cfg.get("output_json", "aitrader/reports/backtest_latest.json"))
    output_md = _resolve_path(args.output_md or report_cfg.get("output_md", "aitrader/reports/backtest_latest.md"))

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    with output_json.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    with output_md.open("w", encoding="utf-8") as f:
        f.write(_to_markdown(report))

    baseline = report["runs"]["baseline"]["metrics"]
    ai_metrics = report["runs"].get("ai_overlay", {}).get("metrics")
    if ai_metrics:
        print(
            "[aitrader] compare done "
            f"baseline_sharpe={baseline['sharpe']:.3f} "
            f"ai_sharpe={ai_metrics['sharpe']:.3f} "
            f"delta_total_return={report['delta_vs_baseline']['total_return']:.4f}"
        )
    else:
        print(
            "[aitrader] baseline done "
            f"total_return={baseline['total_return']:.4f} "
            f"sharpe={baseline['sharpe']:.3f}"
        )
    print(f"[aitrader] report json: {output_json}")
    print(f"[aitrader] report md: {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
