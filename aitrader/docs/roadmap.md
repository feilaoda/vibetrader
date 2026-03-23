# Roadmap

## Phase 0 - Foundation (1 week)
Deliverables:
- repository layout and config templates
- data schema v1 and quality checks
- metric definitions and risk gates

Exit criteria:
- one command can run data validation and produce report

## Phase 1 - Baseline Strategy (1-2 weeks)
Deliverables:
- rule-based baseline strategy (trend + volatility filter)
- event-driven backtest with realistic cost model
- baseline performance report

Exit criteria:
- baseline strategy stable across multiple walk-forward windows
- risk metrics stay within predefined limits

## Phase 2 - AI Overlay (1-2 weeks)
Deliverables:
- first AI ranker model (tree/linear model first)
- feature importance and stability diagnostics
- baseline vs AI-overlay A/B backtest report

Exit criteria:
- AI overlay improves risk-adjusted return over baseline
- turnover and drawdown do not materially degrade

## Phase 3 - Paper Trading (1 week)
Deliverables:
- paper trading execution loop
- pre-trade and intraday risk checks
- daily reconciliation and monitoring dashboard

Exit criteria:
- at least 2 weeks stable paper run
- no critical operational incidents

## Phase 4 - Controlled Live (ongoing)
Deliverables:
- small-capital live deployment
- weekly model drift and performance review
- rollback and safe-disable playbook

Exit criteria:
- strategy remains within risk budget
- performance remains consistent with paper expectation bands
