# AI Trader Plan

`aitrader` is a self-use stock research and execution scaffold.
It combines a rule-based baseline strategy with an AI overlay path.

## Goals
- Build a reproducible research-to-live pipeline.
- Keep AI as an assist layer first, not full-autonomy execution.
- Enforce strict risk controls and measurable promotion gates.

## Scope (Phase 1)
- US equities and ETFs.
- End-of-day (EOD) and low-frequency intraday support.
- Single-account self-use workflow.

## Directory Layout
- `docs/`: architecture, roadmap, and risk definitions.
- `data/`: market data snapshots and sample data generator.
- `features/`: feature engineering logic and schema.
- `models/`: model training/inference artifacts.
- `strategies/`: strategy implementations.
- `backtest/`: backtest entry and evaluation scripts.
- `live/`: paper/live execution adapters.
- `risk/`: pre-trade checks and kill-switch rules.
- `reports/`: backtest and live reports.
- `notebooks/`: ad-hoc research notebooks.
- `config/`: config templates.

## Quick Start
1) Pull market data from Yahoo into local CSV (optional, backtest can auto-pull):

```bash
python3 aitrader/data/load_prices.py --provider yahoo --symbols SPY,QQQ,IWM,TLT,GLD --csv-path aitrader/data/market_prices.csv
```

2) Run baseline + AI overlay compare backtest with template config:

```bash
python3 aitrader/backtest/run_backtest.py --config aitrader/config/backtest.example.json
```

3) Reports will be written to:
- `aitrader/reports/backtest_latest.json`
- `aitrader/reports/backtest_latest.md`

4) Optional: generate deterministic sample data directly:

```bash
python3 aitrader/data/generate_sample_data.py --output aitrader/data/sample_prices.csv --days 320
```

5) Daily K-line AI report (structured technical analysis):

```bash
python3 aitrader/analysis/run_daily_kline_analysis.py --symbol 002050.SZ --bars 365 --engine rule
```

If you still want LLM analysis:

```bash
python3 aitrader/analysis/run_daily_kline_analysis.py --symbol 002050.SZ --bars 365 --engine llm --model deepseek-reasoner
```

Outputs:
- `aitrader/reports/daily_analysis_<SYMBOL>_<YYYYMMDD>.md`
- `aitrader/reports/daily_kline_<SYMBOL>_<YYYYMMDD>.json` (includes full daily OHLCV payload)

## Config Templates
- `aitrader/config/backtest.example.json`: data source (yahoo/local csv), baseline+AI params, costs, and hard risk checks.
- `aitrader/config/live.example.json`: paper/live execution and monitoring template.

## Promotion Principle
1. Baseline strategy must be profitable after realistic costs.
2. AI overlay must add incremental value in walk-forward tests.
3. Only strategies passing risk gates are promoted to paper/live.
