# Risk and Metrics

## Hard Risk Limits
- Max single position: 10% NAV
- Max gross exposure: 100% NAV (no leverage in initial phase)
- Max daily loss: 2% NAV -> trigger trading halt
- Max strategy drawdown: 12% -> reduce risk budget or disable
- Max sector exposure: 30% NAV

## Pre-Trade Checks
- symbol tradable and within universe
- liquidity threshold (ADV and spread filter)
- position/sector limits after hypothetical fill
- no duplicate/conflicting outstanding orders

## Monitoring Metrics
Return/Risk:
- CAGR
- Sharpe and Sortino
- Max Drawdown and Calmar

Behavior:
- turnover
- average holding period
- hit rate
- profit factor

Execution Quality:
- slippage vs benchmark price
- fill ratio and cancel ratio
- latency of signal-to-order and order-to-fill

## Promotion Gates
A strategy can be promoted only if all hold:
1. beats baseline on risk-adjusted metric in walk-forward test
2. remains within drawdown and turnover limits
3. keeps performance after fees/slippage stress tests
4. passes paper-trading stability window without incidents

## Degrade/Rollback Rules
Immediately downgrade or disable when:
- rolling Sharpe drops below threshold for N days
- drawdown breaches hard limit
- model drift or data-quality failures persist
- execution anomalies exceed operational threshold
