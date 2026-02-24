# Daily Quant System (日级别量化系统)

这是一个基于 `VibeTrader` 架构的日级别量化交易与回测系统。它包含数据获取、策略回测、信号扫描等功能。

## 目录结构

- `cron_daily.py`: 每日数据更新脚本。
- `backtester.py`: 回测引擎核心。
- `run_backtest_example.py`: 单个股票回测工具（支持多策略对比）。
- `run_batch_backtest.py`: 批量回测工具（针对 Watchlist）。
- `scan_opportunities.py`: 全市场机会扫描工具。
- `strategy_base.py`: 策略基类。
- `strategies/`: 具体策略实现。

---

## 策略库 (`strategies/`)

目前实现了以下策略：

1.  **TrendFollowing (趋势跟踪)**: 双均线 (MA5/MA20) + 价格突破 (20日新高) + 动量过滤。
2.  **MeanReversion (均值回归)**: RSI (14) 超买超卖 + 止损。
3.  **Turtle (海龟交易 - ATR版)**: 唐奇安通道突破 (20日新高买入, 10日新低卖出) + ATR 波动率仓位管理。
4.  **R-Breaker (日线版)**: 经典的枢轴点突破与反转策略。
5.  **Grid (动态网格)**: 基于 MA20中枢的动态网格交易，“跌买涨卖”。
6.  **FactorMomentum (多因子动量)**: 结合趋势、RSI、波动率因子的综合打分策略。
7.  **BollingerSq (布林带挤压)**: 捕捉布林带长期收口后的突破行情（波动率突破）。

---

## 工具使用说明

### 1. 每日数据更新

用于每天收盘后更新数据库和缓存中的 K 线数据。

```bash
# 更新数据 (默认跳过已存在的)
python api/quant/cron_daily.py

# 强制重新下载并更新所有数据
python api/quant/cron_daily.py --force
```

### 2. 单股回测与策略对比

对单个股票进行回测。如果不指定策略，默认运行所有策略并对比结果。

```bash
# 运行所有策略对比 (推荐)
python api/quant/run_backtest_example.py --symbol 000001.SZ

# 运行特定策略
python api/quant/run_backtest_example.py --symbol 000001.SZ --strategy turtle
```

**输出示例**:
```
Strategy         Return     FinalEquity  Sharpe   MaxDD      Trades Action   Reason                   
----------------------------------------------------------------------------------------------------
grid                3.52%    103517.96     1.41     -1.39%     20     HOLD     in_zone                  
trend               0.64%    100636.37     0.60     -1.46%     10     HOLD     no_signal                
...
```

### 3. Watchlist 批量回测

对 `watchlist` 表中的所有股票运行所有策略，生成统计报告。

```bash
python api/quant/run_batch_backtest.py
```

报告将保存到当前目录下的 logs 文件中。

### 4. 机会扫描器 (Scanner)

设定一个策略，扫描市场上的股票，寻找当前产生 **BUY** 信号的机会。

**参数**:
- `--strategy`: 策略名称 (`turtle`, `mean_reversion`, `grid`, `factor`, `trend`, `r_breaker`)
- `--source`: 扫描范围。`watchlist` (默认，仅关注列表) 或 `all` (数据库中所有 symbol)。
- `--limit`: 限制扫描数量 (用于测试)。

**示例**:

```bash
# 扫描 Watchlist 中的海龟策略买点
python api/quant/scan_opportunities.py --strategy turtle

# 扫描全市场 (所有A股) 的均值回归买点 (注意：首次运行会下载数据，速度有限制)
python api/quant/scan_opportunities.py --source all --strategy mean_reversion

# 测试全市场扫描 (前100个)
python api/quant/scan_opportunities.py --source all --strategy turtle --limit 100
```

# 5. 一步到位日级别扫描 (One-Step Workflow)

这是一个综合性脚本，自动执行：
1.  **全量扫描**: 使用指定策略 (如 `turtle`) 扫描 `symbols` 表中所有股票。
2.  **筛选**: 提取产生 **BUY** 信号的标的。
3.  **AI 复核**: 使用 LLM (Gemini/OpenAI) 对入选标的进行深度分析，剔除假突破，生成最终买入建议。

```bash
# 运行全流程 (默认: 海龟策略 + 全市场扫描 + AI复核)
python api/quant/run_daily_scan.py

# 指定策略 (如布林带挤压)
python api/quant/run_daily_scan.py --strategy bollinger

# 测试模式 (仅扫描前 100 个)
python api/quant/run_daily_scan.py --limit 100
```
这是每天收盘后最核心的工具。
