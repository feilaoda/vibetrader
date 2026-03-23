# Architecture

## System Overview
The system is split into six layers:
1. Data Layer
2. Feature Layer
3. Modeling Layer
4. Portfolio/Strategy Layer
5. Backtest/Simulation Layer
6. Live Execution + Monitoring Layer

## 1) Data Layer
Inputs:
- OHLCV bars (EOD + optional intraday)
- corporate actions (splits/dividends)
- fundamentals (optional in later phases)
- event/news sentiment (optional in later phases)

Requirements:
- deterministic symbol mapping
- timezone and trading-calendar normalization
- survivorship-bias-aware universe snapshots
- strict missing-data policy

Output contract:
- partitioned dataset by `date/symbol`
- data quality report per ingestion batch

## 2) Feature Layer
Feature groups:
- price/volume technicals (returns, volatility, momentum)
- cross-sectional factors (rank, residual momentum, liquidity)
- risk features (beta proxy, drawdown state, gap risk)

Design rules:
- all features are point-in-time correct
- each feature has a documented latency and refresh cadence
- leakage checks are required in CI/backtest validation

## 3) Modeling Layer
Model roles:
- classification/regression for next-period return ranking
- regime classifier for risk budget scaling

Model lifecycle:
- train -> validate -> register -> shadow inference -> promote
- tracked artifacts: code version, data snapshot, hyperparameters, metrics

## 4) Strategy/Portfolio Layer
Core logic:
- baseline signal generation
- AI score fusion (weighting or gating)
- portfolio construction with constraints

Portfolio constraints:
- max position weight
- sector concentration cap
- turnover cap
- cash buffer

## 5) Backtest Layer
Must include:
- realistic fees/slippage/spread assumptions
- order delay and partial fill simulation
- walk-forward evaluation (no single split overfitting)

Primary outputs:
- CAGR, Sharpe, Sortino
- Max Drawdown, Calmar
- turnover, hit rate, PnL attribution

## 6) Live Layer
Execution path:
- pre-trade risk checks
- order creation and broker adapter
- post-trade reconciliation
- real-time risk monitor and kill switch

Operational requirements:
- idempotent order handling
- audit logs for every decision and order event
- daily health report with strategy drift stats
