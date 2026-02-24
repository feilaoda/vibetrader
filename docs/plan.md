# 实施计划 - 日级别量化系统

目标是将现有系统升级为一个健壮的日级别量化交易系统。这涉及系统化地管理数据更新、规范化策略定义（不仅仅是 LLM 提示词），并增加回测能力。

## 拟议变更

### 1. 数据更新 (每日任务)
**目标**: 确保所有跟踪标的的数据是最新的且一致的。

#### [NEW] `api/quant/cron_daily.py`
-   一个独立的脚本，每日运行（例如通过 crontab 或调度器）。
-   从数据库 `watchlist` 表和活跃策略中加载所有代码。
-   对每个代码调用 `cache.force_sync(symbol, "daily")`。
-   报告成功/失败统计。

### 2. 策略引擎 (基于类)
**目标**: 规范化策略逻辑，以便更好地复用和回测。

#### [NEW] `api/quant/strategy_base.py`
-   `BaseStrategy` 抽象基类。
-   方法: `on_bar(bar)`, `generate_signal(klines, position)`。

#### [NEW] `api/quant/strategies/trend_following.py`
-   将 `strategy_rules.py` 中的逻辑实现为一个类。
-   参数: `ma_short`, `ma_long`, `stop_loss_pct` 等。

#### [NEW] `api/quant/strategies/mean_reversion.py`
-   基于 RSI 或布林带的均值回归策略。
-   在超卖时买入，超买时卖出。

#### [MODIFY] `api/strategy_rules.py`
-   重构以使用新的 `TrendFollowingStrategy`，或者保留为向后兼容的包装器。
-   (可选) 如果我们想完全替换它，可以更新 `paper_strategy_runner.py` 来使用该类。目前，我将保持简单，首先使用新结构进行回测。

### 3. 回测模块
**目标**: 在历史数据上验证策略。

#### [NEW] `api/quant/backtester.py`
-   类 `Backtester`。
-   输入: `strategy_class`, `symbol`, `start_date`, `end_date`, `initial_capital`。
-   逻辑:
    -   加载缓存的 K 线数据。
    -   逐日迭代。
    -   调用策略。
    -   模拟执行（含滑点/费用）。
    -   跟踪盈亏 (P&L)。
-   输出: 绩效报告 (夏普比率, 最大回撤, 总回报)。

## 验证计划

### 自动化测试
-   **数据获取**: 运行 `python api/quant/cron_daily.py --dry-run` (我会添加 try-run 标志) 来查看它是否正确识别了要更新的代码。
-   **回测**: 在已知代码（例如 `000001.SZ`）上运行简单的回测，使用 `TrendFollowingStrategy`，检查是否产生交易。

### 人工验证
-   **策略检查**: 我将在几个代码上运行回测器，并针对手动计算的均线交叉验证信号。
