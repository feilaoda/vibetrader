import React, { useState, useEffect } from 'react';
import {
    ActionButton,
    Autocomplete,
    Button,
    ButtonGroup,
    Checkbox,
    Content,
    Dialog,
    DialogTrigger,
    Divider,
    Heading,
    Menu,
    MenuItem,
    NumberField,
    Picker,
    PickerItem,
    SearchField,
    TableView,
    TableHeader,
    Column,
    TableBody,
    Row,
    Cell,
    StatusLight,
    Text,
    TextArea,
    TextField,
    Form,
    useAsyncList
} from '@react-spectrum/s2';
import Refresh from '@react-spectrum/s2/icons/Refresh';
import Delete from '@react-spectrum/s2/icons/Delete';
import Copy from '@react-spectrum/s2/icons/Copy';
import { fetchSymbolList as fetchBinanceSymbols } from "../domain/BinanaceData";
import { fetchSymbolList as fetchAShareSymbols } from "../domain/AShareData";
import { getMarket } from "../domain/DataFecther";

const API_BASE = import.meta.env.VITE_ASHARE_API_URL || '';

const DEFAULT_OBJECTIVES: StrategyObjectives = {
    annual_return_weight: 1.0,
    max_drawdown_weight: 0.5,
    sharpe_weight: 0.3,
    turnover_weight: 0.1
};

const DEFAULT_CONSTRAINTS: StrategyConstraints = {
    max_drawdown_pct: 15,
    max_position_pct: 20,
    max_positions: 8,
    max_daily_trades: 5,
    stop_loss_pct: 5,
    take_profit_pct: 15
};

const DEFAULT_PARAMS: StrategyParams = {
    ma_short: 5,
    ma_long: 20,
    momentum_days: 20,
    breakout_window: 20
};

const DEFAULT_OPTIMIZATION: StrategyOptimization = {
    enabled: true,
    auto_replace: true,
    auto_tune_objectives: true,
    auto_update_prompt: true,
    eval_frequency: 'daily',
    backtest_years: 2,
    train_months: 18,
    val_months: 6,
    min_improvement_pct: 5,
    candidate_count: 8,
    max_symbols: 30
};

interface Position {
    id?: string;
    symbol: string;
    name?: string;
    quantity: number;
    avg_cost: number;
    current_price: number;
    market_value: number;
    profit: number;
    profit_percent: number;
}

interface Order {
    id: number | string;
    symbol: string;
    name?: string;
    side: 'BUY' | 'SELL';
    price: number;
    quantity: number;
    fee: number;
    created_at: string;
}

interface Strategy {
    id: number;
    name: string;
    type: string;
    prompt: string;
    is_ai: boolean;
    is_builtin: boolean;
    model_id?: string | null;
    run_interval_minutes?: number;
    auto_run_enabled?: boolean;
    universe_type?: string | null;
    universe_symbols?: string | null;
    initial_capital?: number;
    objectives?: StrategyObjectives;
    constraints?: StrategyConstraints;
    params?: StrategyParams;
    optimization?: StrategyOptimization;
    last_optimized_at?: string;
    last_optimization_score?: number | null;
    last_optimization_summary?: string | null;
    last_run_at?: string;
    created_at?: string;
    is_running?: boolean;
}

interface StrategyObjectives {
    annual_return_weight: number;
    max_drawdown_weight: number;
    sharpe_weight: number;
    turnover_weight: number;
}

interface StrategyConstraints {
    max_drawdown_pct: number;
    max_position_pct: number;
    max_positions: number;
    max_daily_trades: number;
    stop_loss_pct: number;
    take_profit_pct: number;
}

interface StrategyParams {
    ma_short: number;
    ma_long: number;
    momentum_days: number;
    breakout_window: number;
}

interface StrategyOptimization {
    enabled: boolean;
    auto_replace: boolean;
    auto_tune_objectives: boolean;
    auto_update_prompt: boolean;
    eval_frequency: string;
    backtest_years: number;
    train_months: number;
    val_months: number;
    min_improvement_pct: number;
    candidate_count: number;
    max_symbols: number;
}

interface LLMModel {
    id: string;
    name: string;
}

interface LLMConfig {
    models: LLMModel[];
    current_model: string;
    configured: boolean;
}

interface StrategyRun {
    id: number;
    status: string;
    symbols: string;
    symbol_count: number;
    action_count: number;
    model_id?: string | null;
    run_interval_minutes?: number;
    details?: string | null;
    error?: string | null;
    started_at?: string;
    finished_at?: string;
}

interface WatchlistItem {
    symbol: string;
    market: string;
    name?: string;
}

const toSafeNumber = (value: unknown, fallback = 0): number => {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
};

const asRecord = (value: unknown): Record<string, unknown> => (
    value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
);

const normalizeStrategy = (raw: unknown): Strategy => {
    const rec = asRecord(raw);
    return {
        ...(rec as Partial<Strategy>),
        id: toSafeNumber(rec.id, 0),
        run_interval_minutes: toSafeNumber(rec.run_interval_minutes, 1440),
        auto_run_enabled: Boolean(rec.auto_run_enabled),
        initial_capital: toSafeNumber(rec.initial_capital, 100000),
        last_optimization_score: rec.last_optimization_score == null
        ? null
            : toSafeNumber(rec.last_optimization_score, 0),
    };
};

const normalizePosition = (raw: unknown, index: number): Position => {
    const rec = asRecord(raw);
    return {
        id: rec.symbol ? String(rec.symbol) : `pos-${index}`,
        symbol: String(rec.symbol || ""),
        name: rec.name ? String(rec.name) : undefined,
        quantity: toSafeNumber(rec.quantity, 0),
        avg_cost: toSafeNumber(rec.avg_cost, 0),
        current_price: toSafeNumber(rec.current_price, 0),
        market_value: toSafeNumber(rec.market_value, 0),
        profit: toSafeNumber(rec.profit, 0),
        profit_percent: toSafeNumber(rec.profit_percent, 0),
    };
};

const normalizeOrder = (raw: unknown, index: number): Order => {
    const rec = asRecord(raw);
    return {
        id: rec.id ?? `order-${index}`,
        symbol: String(rec.symbol || ""),
        name: rec.name ? String(rec.name) : undefined,
        side: String(rec.side || "BUY").toUpperCase() === "SELL" ? "SELL" : "BUY",
        price: toSafeNumber(rec.price, 0),
        quantity: toSafeNumber(rec.quantity, 0),
        fee: toSafeNumber(rec.fee, 0),
        created_at: String(rec.created_at || ""),
    };
};

export const PaperTradingPage = () => {
    const [strategies, setStrategies] = useState<Strategy[]>([]);
    const [selectedStrategyId, setSelectedStrategyId] = useState<number | null>(null);
    const [positions, setPositions] = useState<Position[]>([]);
    const [orders, setOrders] = useState<Order[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [lastUpdated, setLastUpdated] = useState<string>('');
    const [promptDraft, setPromptDraft] = useState('');
    const [isSavingPrompt, setIsSavingPrompt] = useState(false);
    const [nameDraft, setNameDraft] = useState('');
    const [modelDraft, setModelDraft] = useState('');
    const [intervalDraft, setIntervalDraft] = useState(1440);
    const [initialCapitalDraft, setInitialCapitalDraft] = useState(100000);
    const [llmConfig, setLlmConfig] = useState<LLMConfig | null>(null);
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [universeTypeDraft, setUniverseTypeDraft] = useState<'watchlist' | 'custom'>('watchlist');
    const [universeSymbolsDraft, setUniverseSymbolsDraft] = useState('');
    const [strategyRuns, setStrategyRuns] = useState<StrategyRun[]>([]);
    const [runsLoading, setRunsLoading] = useState(false);
    const [isRunningNow, setIsRunningNow] = useState(false);
    const [selectedRun, setSelectedRun] = useState<StrategyRun | null>(null);
    const [runDetailsOpen, setRunDetailsOpen] = useState(false);
    const [symbolInput, setSymbolInput] = useState('');
    const [watchlistItems, setWatchlistItems] = useState<WatchlistItem[]>([]);
    const [watchlistLoading, setWatchlistLoading] = useState(false);
    const [autoRunEnabledDraft, setAutoRunEnabledDraft] = useState(false);
    const [autoRunSaving, setAutoRunSaving] = useState(false);
    const [autoRunStatus, setAutoRunStatus] = useState<{ allowed: boolean; reason: string; now: string } | null>(null);
    const [objectivesDraft, setObjectivesDraft] = useState<StrategyObjectives>({ ...DEFAULT_OBJECTIVES });
    const [constraintsDraft, setConstraintsDraft] = useState<StrategyConstraints>({ ...DEFAULT_CONSTRAINTS });
    const [paramsDraft, setParamsDraft] = useState<StrategyParams>({ ...DEFAULT_PARAMS });
    const [optimizationDraft, setOptimizationDraft] = useState<StrategyOptimization>({ ...DEFAULT_OPTIMIZATION });
    const [isOptimizing, setIsOptimizing] = useState(false);

    // Trade Form State
    const [tradeSymbol, setTradeSymbol] = useState('');
    const [tradePrice, setTradePrice] = useState(0);
    const [tradeQty, setTradeQty] = useState(100);
    const [tradeSide, setTradeSide] = useState<'BUY' | 'SELL'>('BUY');
    const [tradeFee, setTradeFee] = useState(5);
    const [manualTradeOpen, setManualTradeOpen] = useState(false);

    const selectedStrategy = strategies.find((s) => s.id === selectedStrategyId) || null;
    const aggressiveTemplate = strategies.find((s) => s.type === 'aggressive')?.prompt || '';
    const conservativeTemplate = strategies.find((s) => s.type === 'conservative')?.prompt || '';
    const defaultModelId = llmConfig?.current_model || (llmConfig?.models?.[0]?.id || "");
    const isStrategyRunning = strategyRuns.some((r) => r.status === 'running') || !!selectedStrategy?.is_running;
    const parseSymbols = (text: string) => text
        .split(/[\s,;]+/g)
        .map((v) => v.trim().toUpperCase())
        .filter((v) => v.length > 0);
    const [customSymbols, setCustomSymbols] = useState<string[]>([]);
    const [watchlistSelected, setWatchlistSelected] = useState<string[]>([]);

    const symbolSearch = useAsyncList<{ id?: string; symbol: string; name?: string }>({
        async load({ signal, filterText }) {
            if (!filterText || filterText.trim().length < 1) {
                return { items: [] };
            }
            const market = getMarket();
            if (market === 'ashare') {
                const items = await fetchAShareSymbols(filterText, { signal });
                return { items: items.map((item) => ({ ...item, id: item.symbol })) };
            }
            const items = await fetchBinanceSymbols(filterText, { signal });
            return { items: items.map((item) => ({ ...item, id: item.symbol })) };
        }
    });

    const fetchStrategies = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/paper/strategies`);
            const data = await res.json();
            const items = Array.isArray(data.data)
                ? data.data.map((item: unknown) => normalizeStrategy(item)).filter((item: Strategy) => item.id > 0)
                : [];
            setStrategies(items);
            const hasCurrent = !!selectedStrategyId && items.some((s: Strategy) => s.id === selectedStrategyId);
            if (!hasCurrent && items.length > 0) {
                const manual = items.find((s: Strategy) => s.type === 'manual');
                const defaultId = manual ? manual.id : items[0].id;
                setSelectedStrategyId(defaultId);
                const selected = items.find((s: Strategy) => s.id === defaultId);
                setPromptDraft(selected?.prompt || '');
            }
        } catch (e) {
            console.error(e);
        }
    };

    const fetchData = async () => {
        if (!selectedStrategyId) return;
        setIsLoading(true);
        try {
            const posRes = await fetch(`${API_BASE}/api/paper/positions?strategy_id=${selectedStrategyId}`);
            const posData = await posRes.json();
            const rawPositions = Array.isArray(posData.data) ? posData.data : [];
            setPositions(rawPositions.map((p: unknown, index: number) => normalizePosition(p, index)));

            const ordRes = await fetch(`${API_BASE}/api/paper/orders?strategy_id=${selectedStrategyId}`);
            const ordData = await ordRes.json();
            const rawOrders = Array.isArray(ordData.data) ? ordData.data : [];
            setOrders(rawOrders.map((o: unknown, index: number) => normalizeOrder(o, index)));

            setLastUpdated(new Date().toLocaleTimeString());
        } catch (e) {
            console.error(e);
        } finally {
            setIsLoading(false);
        }
    };

    const fetchRuns = async () => {
        if (!selectedStrategyId) return;
        setRunsLoading(true);
        try {
            const res = await fetch(`${API_BASE}/api/paper/strategies/${selectedStrategyId}/runs?limit=30`);
            const data = await res.json();
            setStrategyRuns(Array.isArray(data.data) ? data.data : []);
        } catch (e) {
            console.error(e);
        } finally {
            setRunsLoading(false);
        }
    };

    useEffect(() => {
        fetchStrategies();
        fetch(`${API_BASE}/api/config/llm_models`)
            .then(res => res.json())
            .then(data => setLlmConfig(data))
            .catch(console.error);
        setWatchlistLoading(true);
        fetch(`${API_BASE}/api/watchlist`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data.data) ? data.data : [];
                setWatchlistItems(items);
            })
            .catch(console.error)
            .finally(() => setWatchlistLoading(false));

        const loadAutoStatus = () => {
            fetch(`${API_BASE}/api/paper/auto_status`)
                .then(res => res.json())
                .then(data => {
                    if (data && typeof data.auto_run_allowed === 'boolean') {
                        setAutoRunStatus({
                            allowed: !!data.auto_run_allowed,
                            reason: data.reason || '',
                            now: data.now || ''
                        });
                    }
                })
                .catch(() => {});
        };
        loadAutoStatus();
        const statusTimer = setInterval(loadAutoStatus, 60000);
        return () => clearInterval(statusTimer);
    }, []);

    useEffect(() => {
        if (!selectedStrategyId) return;
        if (runDetailsOpen) {
            return;
        }
        fetchData();
        fetchRuns();
        const interval = setInterval(fetchData, 10000); // Auto refresh every 10s
        return () => clearInterval(interval);
    }, [selectedStrategyId, runDetailsOpen]);

    useEffect(() => {
        if (!selectedStrategyId || !selectedStrategy?.is_ai) return;
        const running = strategyRuns.some((r) => r.status === 'running') || !!selectedStrategy?.is_running;
        if (!running && !isRunningNow) return;
        if (runDetailsOpen) {
            return;
        }
        const interval = setInterval(fetchRuns, 3000);
        return () => clearInterval(interval);
    }, [selectedStrategyId, selectedStrategy?.is_ai, isRunningNow, strategyRuns, selectedStrategy?.is_running, runDetailsOpen]);

    useEffect(() => {
        if (selectedStrategy) {
            setPromptDraft(selectedStrategy.prompt || "");
            setNameDraft(selectedStrategy.name || "");
            setModelDraft(selectedStrategy.model_id || defaultModelId || "");
            setIntervalDraft(selectedStrategy.run_interval_minutes || 1440);
            setAutoRunEnabledDraft(!!selectedStrategy.auto_run_enabled);
            setInitialCapitalDraft(selectedStrategy.initial_capital ?? 100000);
            setUniverseTypeDraft((selectedStrategy.universe_type as 'watchlist' | 'custom') || (selectedStrategy.is_ai ? 'watchlist' : 'custom'));
            setUniverseSymbolsDraft(selectedStrategy.universe_symbols || "");
            setCustomSymbols(parseSymbols(selectedStrategy.universe_symbols || ""));
            setWatchlistSelected(parseSymbols(selectedStrategy.universe_symbols || ""));
            setObjectivesDraft({ ...DEFAULT_OBJECTIVES, ...(selectedStrategy.objectives || {}) });
            setConstraintsDraft({ ...DEFAULT_CONSTRAINTS, ...(selectedStrategy.constraints || {}) });
            setParamsDraft({ ...DEFAULT_PARAMS, ...(selectedStrategy.params || {}) });
            setOptimizationDraft({ ...DEFAULT_OPTIMIZATION, ...(selectedStrategy.optimization || {}) });
            setSettingsOpen(false);
        }
    }, [selectedStrategyId, strategies, defaultModelId]);

    // Effect to auto-update fee when price/qty changes
    useEffect(() => {
        // Buy: 0.05% (min 5)
        // Sell: 1.4% (default requested by user, tax+fee included assumed)
        const amount = tradePrice * tradeQty;
        let fee = 0;
        if (tradeSide === 'BUY') {
            fee = Math.max(5, amount * 0.0005);
        } else {
            fee = amount * 0.014;
        }
        setTradeFee(parseFloat(fee.toFixed(2)));
    }, [tradePrice, tradeQty, tradeSide]);

    const handleTradeSubmit = async (): Promise<boolean> => {
        try {
            const res = await fetch(`${API_BASE}/api/paper/order`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: tradeSymbol,
                    side: tradeSide,
                    price: tradePrice,
                    quantity: tradeQty,
                    fee: tradeFee,
                    strategy_id: selectedStrategyId
                })
            });
            if (res.ok) {
                fetchData();
                alert('Order Placed!');
                return true;
            } else {
                const err = await res.json();
                alert('Trade failed: ' + err.detail);
                return false;
            }
        } catch (e) {
            alert('Error submitting trade');
            return false;
        }
    };

    const handleQuickSell = (item: Position) => {
        if (!item) return;
        setTradeSymbol(item.symbol);
        setTradeSide('SELL');
        const px = Number(item.current_price) || Number(item.avg_cost) || 0;
        setTradePrice(px);
        setTradeQty(Number(item.quantity) || 0);
        setManualTradeOpen(true);
    };

    const handleReset = async () => {
        if (!selectedStrategyId) return;
        if (!confirm("Are you sure you want to reset this strategy's data?")) return;
        await fetch(`${API_BASE}/api/paper/reset?strategy_id=${selectedStrategyId}`, { method: 'DELETE' });
        fetchData();
    }

    const handleCreateStrategy = async (payload: { name: string; type: string; prompt: string; is_ai: boolean; model_id?: string; run_interval_minutes?: number; auto_run_enabled?: boolean; universe_type?: string; universe_symbols?: string; initial_capital?: number; objectives?: StrategyObjectives; constraints?: StrategyConstraints; params?: StrategyParams; optimization?: StrategyOptimization }) => {
        try {
            const res = await fetch(`${API_BASE}/api/paper/strategies`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                const err = await res.json();
                alert('Create strategy failed: ' + (err.detail || 'unknown error'));
                return;
            }
            const data = await res.json();
            await fetchStrategies();
            if (data.id) {
                setSelectedStrategyId(Number(data.id));
            }
        } catch (e) {
            alert('Error creating strategy');
        }
    };

    const handleSavePrompt = async () => {
        if (!selectedStrategyId) return;
        setIsSavingPrompt(true);
        try {
            const res = await fetch(`${API_BASE}/api/paper/strategies/${selectedStrategyId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: nameDraft,
                    prompt: promptDraft,
                    model_id: modelDraft || defaultModelId || null,
                    run_interval_minutes: intervalDraft,
                    auto_run_enabled: autoRunEnabledDraft,
                    universe_type: universeTypeDraft,
                    universe_symbols: universeSymbolsDraft,
                    initial_capital: initialCapitalDraft,
                    objectives: objectivesDraft,
                    constraints: constraintsDraft,
                    params: paramsDraft,
                    optimization: optimizationDraft
                })
            });
            if (!res.ok) {
                const err = await res.json();
                alert('Save settings failed: ' + (err.detail || 'unknown error'));
            } else {
                await fetchStrategies();
                await fetchRuns();
            }
        } catch (e) {
            alert('Error saving prompt');
        } finally {
            setIsSavingPrompt(false);
        }
    };

    const handleOptimizeNow = async () => {
        if (!selectedStrategyId) return;
        setIsOptimizing(true);
        try {
            const res = await fetch(`${API_BASE}/api/paper/strategies/${selectedStrategyId}/optimize`, { method: 'POST' });
            if (!res.ok) {
                const err = await res.json();
                alert('Optimize failed: ' + (err.detail || 'unknown error'));
            } else {
                await fetchStrategies();
                const data = await res.json();
                if (data?.data?.status) {
                    alert(`Optimize ${data.data.status}`);
                }
            }
        } catch (e) {
            alert('Optimize failed');
        } finally {
            setIsOptimizing(false);
        }
    };

    const handleRunNow = async () => {
        if (!selectedStrategyId) return;
        setIsRunningNow(true);
        try {
            const res = await fetch(`${API_BASE}/api/paper/strategies/${selectedStrategyId}/run`, { method: 'POST' });
            if (!res.ok) {
                const err = await res.json();
                if (res.status === 409) {
                    alert('Strategy is already running.');
                } else {
                    alert('Run failed: ' + (err.detail || 'unknown error'));
                }
            } else {
                await fetchStrategies();
                await fetchRuns();
            }
        } catch (e) {
            alert('Run failed');
        } finally {
            setIsRunningNow(false);
        }
    };

    const handleAutoRunToggle = async (enabled: boolean) => {
        if (!selectedStrategyId) return;
        setAutoRunEnabledDraft(enabled);
        setAutoRunSaving(true);
        try {
            const payload: Record<string, unknown> = { auto_run_enabled: enabled };
            if (enabled) {
                payload.run_interval_minutes = 3;
            }
            const res = await fetch(`${API_BASE}/api/paper/strategies/${selectedStrategyId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                const err = await res.json();
                alert('Auto-run update failed: ' + (err.detail || 'unknown error'));
            } else {
                if (enabled) {
                    setIntervalDraft(3);
                }
                await fetchStrategies();
                await fetchRuns();
            }
        } catch (e) {
            alert('Auto-run update failed');
        } finally {
            setAutoRunSaving(false);
        }
    };

    const handleSymbolInputChange = (value: string) => {
        setSymbolInput(value);
        symbolSearch.setFilterText(value);
    };

    const addCustomSymbol = (value: string) => {
        const next = value.trim().toUpperCase();
        if (!next) return;
        const updated = Array.from(new Set([...customSymbols, next]));
        setCustomSymbols(updated);
        const text = updated.join(", ");
        setUniverseSymbolsDraft(text);
        setSymbolInput('');
        symbolSearch.setFilterText('');
    };

    const removeCustomSymbol = (symbol: string) => {
        const updated = customSymbols.filter((s) => s !== symbol);
        setCustomSymbols(updated);
        setUniverseSymbolsDraft(updated.join(", "));
    };

    const updateWatchlistSelection = (symbol: string, checked: boolean) => {
        const nextSymbol = symbol.toUpperCase();
        let updated: string[];
        if (checked) {
            updated = Array.from(new Set([...watchlistSelected, nextSymbol]));
        } else {
            updated = watchlistSelected.filter((s) => s !== nextSymbol);
        }
        setWatchlistSelected(updated);
        setUniverseSymbolsDraft(updated.join(", "));
    };

    const handleSelectAllWatchlist = () => {
        const allSymbols = watchlistItems.filter((item) => item.market === 'ashare').map((item) => item.symbol.toUpperCase());
        const unique = Array.from(new Set(allSymbols));
        setWatchlistSelected(unique);
        setUniverseSymbolsDraft(unique.join(", "));
    };

    const handleClearWatchlistSelection = () => {
        setWatchlistSelected([]);
        setUniverseSymbolsDraft('');
    };

    // Dashboard Calculations
    const totalMarketValue = positions.reduce((sum, p) => sum + (Number(p.market_value) || 0), 0);
    const initialCapital = toSafeNumber(selectedStrategy?.initial_capital, 100000);
    const cashBalance = orders.reduce((cash, o) => {
        const price = Number(o.price) || 0;
        const qty = Number(o.quantity) || 0;
        const fee = Number(o.fee) || 0;
        const amount = price * qty;
        if (o.side === 'BUY') {
            return cash - amount - fee;
        }
        if (o.side === 'SELL') {
            return cash + amount - fee;
        }
        return cash;
    }, initialCapital);
    const totalEquity = cashBalance + totalMarketValue;
    const totalProfit = totalEquity - initialCapital;
    const totalReturnPct = initialCapital > 0 ? (totalProfit / initialCapital) * 100 : 0;
    const intervalOptions = [
        { id: "1", label: "1分钟" },
        { id: "3", label: "3分钟" },
        { id: "5", label: "5分钟" },
        { id: "15", label: "15分钟" },
        { id: "30", label: "30分钟" },
        { id: "60", label: "1小时" },
        { id: "1440", label: "1天" }
    ];
    const universeOptions = [
        { id: "watchlist", label: "自选池" },
        { id: "custom", label: "自定义" }
    ];
    const customSymbolCount = customSymbols.length;
    const watchlistSymbols = watchlistItems.filter((item) => item.market === 'ashare');
    const latestRun = strategyRuns.length > 0 ? strategyRuns[0] : null;
    const formatOrderTime = (value: unknown) => {
        if (!value) return '-';
        const dt = new Date(value as string);
        if (Number.isNaN(dt.getTime())) return '-';
        return dt.toLocaleString();
    };
    const runDetailsText = (() => {
        if (!selectedRun) return '';
        const raw = selectedRun.details || '';
        if (!raw) return '';
        try {
            return JSON.stringify(JSON.parse(raw), null, 2);
        } catch {
            return raw;
        }
    })();
    const getRunProgress = (run: StrategyRun) => {
        if (!run.details) return 0;
        try {
            const parsed = JSON.parse(run.details);
            return Array.isArray(parsed) ? parsed.length : 0;
        } catch {
            return 0;
        }
    };

    return (
        <div style={{ padding: '20px', backgroundColor: '#f5f5f5', minHeight: '100vh' }}>
            <div style={{ maxWidth: '1400px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                {/* Header */}
                <div style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    backgroundColor: 'white', padding: '16px', borderRadius: '8px',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
                }}>
                    <h2 style={{ margin: 0 }}>Paper Trading Simulator</h2>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <span style={{ marginRight: '10px', fontSize: '12px', color: '#666' }}>
                            Last Updated: {lastUpdated}
                        </span>
                        <ActionButton onPress={() => fetchData()} isDisabled={isLoading || !selectedStrategyId}>
                            <Refresh />
                        </ActionButton>
                        <ActionButton onPress={handleReset} isDisabled={!selectedStrategyId}>
                            <Delete />
                        </ActionButton>
                    </div>
                </div>

                <div style={{ display: 'flex', gap: '20px' }}>
                    {/* Strategy List */}
                    <div style={{
                        width: '260px',
                        backgroundColor: 'white',
                        borderRadius: '8px',
                        padding: '16px',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '12px'
                    }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                            <h3 style={{ margin: 0 }}>Strategies</h3>
                            <CreateStrategyDialog
                                onCreate={handleCreateStrategy}
                                aggressivePrompt={aggressiveTemplate}
                                conservativePrompt={conservativeTemplate}
                                models={llmConfig?.models || []}
                                defaultModelId={defaultModelId}
                                intervalOptions={intervalOptions}
                            />
                        </div>
                        <TableView
                            aria-label="Strategies"
                            selectionMode="single"
                            selectedKeys={selectedStrategyId ? new Set([String(selectedStrategyId)]) : new Set()}
                            onSelectionChange={(keys) => {
                                if (keys === 'all') return;
                                const selected = Array.from(keys as Set<string>)[0];
                                const nextId = toSafeNumber(selected, 0);
                                if (nextId > 0) {
                                    setRunDetailsOpen(false);
                                    setSelectedRun(null);
                                    setSelectedStrategyId(nextId);
                                }
                            }}
                            density="compact"
                        >
                            <TableHeader>
                                <Column isRowHeader>Strategy</Column>
                            </TableHeader>
                            <TableBody items={strategies}>
                                {(item: Strategy) => (
                                    <Row id={String(item.id)}>
                                        <Cell>
                                            <div style={{ display: 'flex', flexDirection: 'column' }}>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                    <Text>{item.name}</Text>
                                                    {item.is_running && (
                                                        <StatusLight variant="neutral">running</StatusLight>
                                                    )}
                                                </div>
                                                <span style={{ fontSize: '11px', color: '#666' }}>
                                                    {item.is_ai ? 'AI策略' : '手工策略'} · {item.type}
                                                </span>
                                            </div>
                                        </Cell>
                                    </Row>
                                )}
                            </TableBody>
                        </TableView>
                    </div>

                    {/* Strategy Detail */}
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '20px' }}>
                        <div style={{
                            backgroundColor: 'white',
                            padding: '16px',
                            borderRadius: '8px',
                            boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
                        }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <div>
                                    <h3 style={{ margin: 0 }}>{selectedStrategy ? selectedStrategy.name : 'Select a strategy'}</h3>
                                    <div style={{ fontSize: '12px', color: '#666' }}>
                                    {selectedStrategy ? `${selectedStrategy.is_ai ? 'AI策略' : '手工策略'} · ${selectedStrategy.type}` : ''}
                                    </div>
                                </div>
                                {selectedStrategy && (
                                    <div style={{ display: 'flex', gap: '8px' }}>
                                        {selectedStrategy.is_ai && (
                                            <Button variant="primary" onPress={handleRunNow} isDisabled={isRunningNow || isStrategyRunning}>
                                                {isStrategyRunning ? 'Running...' : 'Run Now'}
                                            </Button>
                                        )}
                                        {selectedStrategy.is_ai && (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <Checkbox
                                                    isSelected={autoRunEnabledDraft}
                                                    onChange={handleAutoRunToggle}
                                                    isDisabled={autoRunSaving}
                                                >
                                                    自动执行(3m)
                                                </Checkbox>
                                                {autoRunStatus && (
                                                    <span style={{ fontSize: '11px', color: autoRunStatus.allowed ? '#2e7d32' : '#b00020' }}>
                                                        {autoRunStatus.allowed ? '可运行' : '已暂停'} {autoRunStatus.reason === 'post_close_buffer' ? '(盘后缓冲)' : (autoRunStatus.reason === 'after_hours' ? '(盘后)' : '')}
                                                    </span>
                                                )}
                                            </div>
                                        )}
                                        <Button variant="secondary" onPress={() => setSettingsOpen((prev) => !prev)}>
                                            {settingsOpen ? 'Collapse' : 'Edit'}
                                        </Button>
                                    </div>
                                )}
                            </div>
                            {selectedStrategy ? (
                                <>
                                    {!settingsOpen && (
                                        <div style={{ marginTop: '12px', fontSize: '13px', color: '#666' }}>
                                            {selectedStrategy.is_ai
                                                ? `Model: ${(llmConfig?.models || []).find(m => m.id === (modelDraft || selectedStrategy.model_id))?.name || (modelDraft || selectedStrategy.model_id || 'N/A')} · Interval: ${intervalOptions.find(o => o.id === String(intervalDraft || selectedStrategy.run_interval_minutes))?.label || '1天'} · Auto: ${autoRunEnabledDraft ? 'ON' : 'OFF'} · Universe: ${universeTypeDraft === 'watchlist' ? (watchlistSelected.length > 0 ? `自选池(${watchlistSelected.length})` : '自选池') : `自定义(${customSymbolCount})`} · Initial: ¥${toSafeNumber(selectedStrategy.initial_capital, 100000).toFixed(0)}${isStrategyRunning ? ' · Status: running' : ''}${selectedStrategy.last_run_at ? ` · Last run: ${new Date(selectedStrategy.last_run_at).toLocaleString()}` : ''}${selectedStrategy.last_optimized_at ? ` · Optimized: ${new Date(selectedStrategy.last_optimized_at).toLocaleString()}${selectedStrategy.last_optimization_score != null ? ` · Score: ${Number(selectedStrategy.last_optimization_score).toFixed(3)}` : ''}` : ''}`
                                                : `Manual strategy: trades are placed by you, no AI automation. · Initial: ¥${toSafeNumber(selectedStrategy.initial_capital, 100000).toFixed(0)}${selectedStrategy.last_run_at ? ` · Last run: ${new Date(selectedStrategy.last_run_at).toLocaleString()}` : ''}`}
                                        </div>
                                    )}
                                    {selectedStrategy.is_ai && latestRun && (
                                        <div style={{ marginTop: '8px', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: '#666' }}>
                                            <StatusLight variant={latestRun.status === 'success' ? 'positive' : (latestRun.status === 'skipped' || latestRun.status === 'running' ? 'neutral' : 'negative')}>
                                                {latestRun.status}
                                            </StatusLight>
                                            <span>Symbols: {latestRun.symbol_count ?? 0}</span>
                                            {latestRun.status === 'running' && (
                                                <span>Progress: {getRunProgress(latestRun)}/{latestRun.symbol_count ?? 0}</span>
                                            )}
                                            <span>Actions: {latestRun.action_count ?? 0}</span>
                                            {latestRun.error && <span style={{ color: '#b00020' }}>Error: {String(latestRun.error)}</span>}
                                            <Button
                                                variant="secondary"
                                                onPress={() => {
                                                    setSelectedRun(latestRun);
                                                    setRunDetailsOpen(true);
                                                }}
                                            >
                                                View latest
                                            </Button>
                                        </div>
                                    )}
                                    {settingsOpen && (
                                        <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                            <TextField
                                                label="Strategy Name"
                                                value={nameDraft}
                                                onChange={setNameDraft}
                                                placeholder="Strategy name"
                                            />
                                            <NumberField
                                                label="Initial Capital"
                                                value={initialCapitalDraft}
                                                onChange={(value) => setInitialCapitalDraft(Number(value) || 0)}
                                                formatOptions={{ style: 'decimal', minimumFractionDigits: 0 }}
                                            />
                                            {selectedStrategy.is_ai ? (
                                                <>
                                                    <Picker
                                                        label="AI Model"
                                                        selectedKey={modelDraft || defaultModelId || ""}
                                                        onSelectionChange={(key) => setModelDraft(String(key))}
                                                    >
                                                        {(llmConfig?.models || []).map((m) => (
                                                            <PickerItem id={m.id} key={m.id}>{m.name}</PickerItem>
                                                        ))}
                                                    </Picker>
                                                    <Picker
                                                        label="Run Interval"
                                                        selectedKey={String(intervalDraft)}
                                                        onSelectionChange={(key) => setIntervalDraft(Number(key))}
                                                        isDisabled={autoRunEnabledDraft}
                                                    >
                                                        {intervalOptions.map((opt) => (
                                                            <PickerItem id={opt.id} key={opt.id}>{opt.label}</PickerItem>
                                                        ))}
                                                    </Picker>
                                                    {autoRunEnabledDraft && (
                                                        <div style={{ fontSize: '12px', color: '#666' }}>
                                                            自动执行已开启，运行频率固定为 3 分钟
                                                        </div>
                                                    )}
                                                    <Picker
                                                        label="Universe"
                                                        selectedKey={universeTypeDraft}
                                                        onSelectionChange={(key) => {
                                                            const next = key === 'watchlist' ? 'watchlist' : 'custom';
                                                            setUniverseTypeDraft(next);
                                                            if (next === 'watchlist') {
                                                                setWatchlistSelected(parseSymbols(universeSymbolsDraft));
                                                            } else {
                                                                setCustomSymbols(parseSymbols(universeSymbolsDraft));
                                                            }
                                                        }}
                                                    >
                                                        {universeOptions.map((opt) => (
                                                            <PickerItem id={opt.id} key={opt.id}>{opt.label}</PickerItem>
                                                        ))}
                                                    </Picker>
                                                    {universeTypeDraft === 'watchlist' && (
                                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                                            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                                                <Checkbox
                                                                    isSelected={watchlistSelected.length === 0}
                                                                    onChange={(checked) => {
                                                                        if (checked) {
                                                                            handleClearWatchlistSelection();
                                                                        }
                                                                    }}
                                                                >
                                                                    Use all watchlist
                                                                </Checkbox>
                                                                <Button variant="secondary" onPress={handleSelectAllWatchlist} isDisabled={watchlistSymbols.length === 0}>
                                                                    Select all
                                                                </Button>
                                                                <Button variant="secondary" onPress={handleClearWatchlistSelection}>
                                                                    Clear
                                                                </Button>
                                                            </div>
                                                            <div style={{ maxHeight: '160px', overflowY: 'auto', border: '1px solid #eee', borderRadius: '6px', padding: '8px' }}>
                                                                {watchlistLoading && (
                                                                    <div style={{ fontSize: '12px', color: '#666' }}>Loading watchlist...</div>
                                                                )}
                                                                {!watchlistLoading && watchlistSymbols.length === 0 && (
                                                                    <div style={{ fontSize: '12px', color: '#666' }}>No watchlist items.</div>
                                                                )}
                                                                {!watchlistLoading && watchlistSymbols.map((item) => (
                                                                    <div key={item.symbol} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 0' }}>
                                                                        <Checkbox
                                                                            isSelected={watchlistSelected.includes(item.symbol.toUpperCase())}
                                                                            onChange={(checked) => updateWatchlistSelection(item.symbol, checked)}
                                                                        >
                                                                            {item.name ? `${item.symbol} ${item.name}` : item.symbol}
                                                                        </Checkbox>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                            <div style={{ fontSize: '12px', color: '#666' }}>
                                                                Selected: {watchlistSelected.length || watchlistSymbols.length} / {watchlistSymbols.length}
                                                            </div>
                                                        </div>
                                                    )}
                                                    {universeTypeDraft === 'custom' && (
                                                        <>
                                                            <Autocomplete inputValue={symbolInput} onInputChange={handleSymbolInputChange}>
                                                                <SearchField label="Add Symbol" placeholder="Type to search..." />
                                                                <Menu
                                                                    items={symbolSearch.items}
                                                                    selectionMode="single"
                                                                    onSelectionChange={(keys) => {
                                                                        const selected = (keys as Set<string>).values().next().value;
                                                                        if (selected) {
                                                                            addCustomSymbol(selected);
                                                                        }
                                                                    }}
                                                                >
                                                                    {(item) => (
                                                                        <MenuItem id={item.symbol} closeOnSelect={false}>
                                                                            {item.name ? `${item.symbol} ${item.name}` : item.symbol}
                                                                        </MenuItem>
                                                                    )}
                                                                </Menu>
                                                            </Autocomplete>
                                                            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                                                <Button variant="secondary" onPress={() => addCustomSymbol(symbolInput)}>Add</Button>
                                                                <span style={{ fontSize: '12px', color: '#666' }}>Total: {customSymbols.length}</span>
                                                            </div>
                                                            {customSymbols.length > 0 && (
                                                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                                                    {customSymbols.map((sym) => (
                                                                        <div key={sym} style={{ display: 'flex', alignItems: 'center', gap: '6px', backgroundColor: '#f2f2f2', borderRadius: '12px', padding: '4px 8px' }}>
                                                                            <span>{sym}</span>
                                                                            <ActionButton onPress={() => removeCustomSymbol(sym)}>
                                                                                <Delete />
                                                                            </ActionButton>
                                                                        </div>
                                                                    ))}
                                                                </div>
                                                            )}
                                                            <TextArea
                                                                label="Custom Symbols (raw)"
                                                                value={universeSymbolsDraft}
                                                                onChange={(value) => {
                                                                    setUniverseSymbolsDraft(value);
                                                                    setCustomSymbols(parseSymbols(value));
                                                                }}
                                                                placeholder="Comma/space separated: 000001.SZ, 600519.SH"
                                                            />
                                                        </>
                                                    )}
                                                    <TextArea
                                                        label="AI Prompt"
                                                        value={promptDraft}
                                                        onChange={setPromptDraft}
                                                        placeholder="Describe the strategy prompt..."
                                                    />
                                                    <div style={{ border: '1px solid #eee', borderRadius: '6px', padding: '10px', marginTop: '6px' }}>
                                                        <div style={{ fontSize: '12px', color: '#666', marginBottom: '6px' }}>Objectives (weights)</div>
                                                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px' }}>
                                                            <NumberField
                                                                label="Annual Return"
                                                                value={objectivesDraft.annual_return_weight}
                                                                onChange={(value) => setObjectivesDraft({ ...objectivesDraft, annual_return_weight: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Max Drawdown"
                                                                value={objectivesDraft.max_drawdown_weight}
                                                                onChange={(value) => setObjectivesDraft({ ...objectivesDraft, max_drawdown_weight: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Sharpe"
                                                                value={objectivesDraft.sharpe_weight}
                                                                onChange={(value) => setObjectivesDraft({ ...objectivesDraft, sharpe_weight: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Turnover"
                                                                value={objectivesDraft.turnover_weight}
                                                                onChange={(value) => setObjectivesDraft({ ...objectivesDraft, turnover_weight: Number(value) || 0 })}
                                                            />
                                                        </div>
                                                    </div>
                                                    <div style={{ border: '1px solid #eee', borderRadius: '6px', padding: '10px' }}>
                                                        <div style={{ fontSize: '12px', color: '#666', marginBottom: '6px' }}>Constraints</div>
                                                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px' }}>
                                                            <NumberField
                                                                label="Max Drawdown %"
                                                                value={constraintsDraft.max_drawdown_pct}
                                                                onChange={(value) => setConstraintsDraft({ ...constraintsDraft, max_drawdown_pct: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Max Position %"
                                                                value={constraintsDraft.max_position_pct}
                                                                onChange={(value) => setConstraintsDraft({ ...constraintsDraft, max_position_pct: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Max Positions"
                                                                value={constraintsDraft.max_positions}
                                                                onChange={(value) => setConstraintsDraft({ ...constraintsDraft, max_positions: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Max Daily Trades"
                                                                value={constraintsDraft.max_daily_trades}
                                                                onChange={(value) => setConstraintsDraft({ ...constraintsDraft, max_daily_trades: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Stop Loss %"
                                                                value={constraintsDraft.stop_loss_pct}
                                                                onChange={(value) => setConstraintsDraft({ ...constraintsDraft, stop_loss_pct: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Take Profit %"
                                                                value={constraintsDraft.take_profit_pct}
                                                                onChange={(value) => setConstraintsDraft({ ...constraintsDraft, take_profit_pct: Number(value) || 0 })}
                                                            />
                                                        </div>
                                                    </div>
                                                    <div style={{ border: '1px solid #eee', borderRadius: '6px', padding: '10px' }}>
                                                        <div style={{ fontSize: '12px', color: '#666', marginBottom: '6px' }}>Signal Params</div>
                                                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px' }}>
                                                            <NumberField
                                                                label="MA Short"
                                                                value={paramsDraft.ma_short}
                                                                onChange={(value) => setParamsDraft({ ...paramsDraft, ma_short: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="MA Long"
                                                                value={paramsDraft.ma_long}
                                                                onChange={(value) => setParamsDraft({ ...paramsDraft, ma_long: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Momentum Days"
                                                                value={paramsDraft.momentum_days}
                                                                onChange={(value) => setParamsDraft({ ...paramsDraft, momentum_days: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Breakout Window"
                                                                value={paramsDraft.breakout_window}
                                                                onChange={(value) => setParamsDraft({ ...paramsDraft, breakout_window: Number(value) || 0 })}
                                                            />
                                                        </div>
                                                    </div>
                                                    <div style={{ border: '1px solid #eee', borderRadius: '6px', padding: '10px' }}>
                                                        <div style={{ fontSize: '12px', color: '#666', marginBottom: '6px' }}>Optimization</div>
                                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center' }}>
                                                            <Checkbox
                                                                isSelected={optimizationDraft.enabled}
                                                                onChange={(checked) => setOptimizationDraft({ ...optimizationDraft, enabled: checked })}
                                                            >
                                                                Enabled
                                                            </Checkbox>
                                                            <Checkbox
                                                                isSelected={optimizationDraft.auto_replace}
                                                                onChange={(checked) => setOptimizationDraft({ ...optimizationDraft, auto_replace: checked })}
                                                            >
                                                                Auto Replace
                                                            </Checkbox>
                                                            <Checkbox
                                                                isSelected={optimizationDraft.auto_tune_objectives}
                                                                onChange={(checked) => setOptimizationDraft({ ...optimizationDraft, auto_tune_objectives: checked })}
                                                            >
                                                                Auto Tune Objectives
                                                            </Checkbox>
                                                            <Checkbox
                                                                isSelected={optimizationDraft.auto_update_prompt}
                                                                onChange={(checked) => setOptimizationDraft({ ...optimizationDraft, auto_update_prompt: checked })}
                                                            >
                                                                Auto Update Prompt
                                                            </Checkbox>
                                                        </div>
                                                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px', marginTop: '8px' }}>
                                                            <NumberField
                                                                label="Backtest Years"
                                                                value={optimizationDraft.backtest_years}
                                                                onChange={(value) => setOptimizationDraft({ ...optimizationDraft, backtest_years: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Train Months"
                                                                value={optimizationDraft.train_months}
                                                                onChange={(value) => setOptimizationDraft({ ...optimizationDraft, train_months: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Val Months"
                                                                value={optimizationDraft.val_months}
                                                                onChange={(value) => setOptimizationDraft({ ...optimizationDraft, val_months: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Min Improve %"
                                                                value={optimizationDraft.min_improvement_pct}
                                                                onChange={(value) => setOptimizationDraft({ ...optimizationDraft, min_improvement_pct: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Candidates"
                                                                value={optimizationDraft.candidate_count}
                                                                onChange={(value) => setOptimizationDraft({ ...optimizationDraft, candidate_count: Number(value) || 0 })}
                                                            />
                                                            <NumberField
                                                                label="Max Symbols"
                                                                value={optimizationDraft.max_symbols}
                                                                onChange={(value) => setOptimizationDraft({ ...optimizationDraft, max_symbols: Number(value) || 0 })}
                                                            />
                                                        </div>
                                                        <div style={{ fontSize: '11px', color: '#666', marginTop: '6px' }}>
                                                            Evaluation frequency: daily (server-side)
                                                        </div>
                                                    </div>
                                                </>
                                            ) : (
                                                <div style={{ fontSize: '13px', color: '#666' }}>
                                                    Manual strategy: trades are placed by you, no AI automation.
                                                </div>
                                            )}
                                            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                                {selectedStrategy.is_ai && (
                                                    <Button variant="secondary" onPress={() => setPromptDraft(selectedStrategy.prompt || "")}>Reset</Button>
                                                )}
                                                {selectedStrategy.is_ai && (
                                                    <Button variant="secondary" onPress={handleOptimizeNow} isDisabled={isOptimizing}>
                                                        {isOptimizing ? 'Optimizing...' : 'Optimize Now'}
                                                    </Button>
                                                )}
                                                <Button variant="accent" onPress={handleSavePrompt} isDisabled={isSavingPrompt}>
                                                    Save Settings
                                                </Button>
                                            </div>
                                        </div>
                                    )}
                                </>
                            ) : null}
                        </div>

                        {/* Dashboard Cards */}
                        <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                            <div style={{
                                backgroundColor: 'white', padding: '16px', borderRadius: '8px', flex: '1', minWidth: '200px',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                            }}>
                                <div style={{ fontSize: '14px', color: '#666' }}>Initial Capital</div>
                                <div style={{ fontSize: '24px', fontWeight: 'bold' }}>¥{initialCapital.toFixed(2)}</div>
                            </div>
                            <div style={{
                                backgroundColor: 'white', padding: '16px', borderRadius: '8px', flex: '1', minWidth: '200px',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                            }}>
                                <div style={{ fontSize: '14px', color: '#666' }}>Cash Balance</div>
                                <div style={{ fontSize: '24px', fontWeight: 'bold' }}>¥{cashBalance.toFixed(2)}</div>
                            </div>
                            <div style={{
                                backgroundColor: 'white', padding: '16px', borderRadius: '8px', flex: '1', minWidth: '200px',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                            }}>
                                <div style={{ fontSize: '14px', color: '#666' }}>Total Market Value</div>
                                <div style={{ fontSize: '24px', fontWeight: 'bold' }}>¥{totalMarketValue.toFixed(2)}</div>
                            </div>
                            <div style={{
                                backgroundColor: 'white', padding: '16px', borderRadius: '8px', flex: '1', minWidth: '200px',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                            }}>
                                <div style={{ fontSize: '14px', color: '#666' }}>Total Equity</div>
                                <div style={{ fontSize: '24px', fontWeight: 'bold' }}>¥{totalEquity.toFixed(2)}</div>
                            </div>
                            <div style={{
                                backgroundColor: 'white', padding: '16px', borderRadius: '8px', flex: '1', minWidth: '200px',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                            }}>
                                <div style={{ fontSize: '14px', color: '#666' }}>Total Profit/Loss</div>
                                <div style={{ fontSize: '24px', fontWeight: 'bold', color: totalProfit >= 0 ? '#d32f2f' : '#388e3c' }}>
                                    {totalProfit >= 0 ? '+' : ''}{totalProfit.toFixed(2)}
                                    <span style={{ fontSize: '16px', marginLeft: '8px' }}>({totalReturnPct.toFixed(2)}%)</span>
                                </div>
                            </div>
                            <div style={{
                                backgroundColor: 'white', padding: '16px', borderRadius: '8px', flex: '1', minWidth: '200px',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                            }}>
                                <div style={{ fontSize: '14px', color: '#666' }}>Positions Count</div>
                                <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{positions.length}</div>
                            </div>
                        </div>

                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <h3 style={{ margin: 0 }}>Positions</h3>
                            {selectedStrategy && !selectedStrategy.is_ai && (
                                <PaperTradingDialog
                                    tradeSymbol={tradeSymbol} setTradeSymbol={setTradeSymbol}
                                    tradeSide={tradeSide} setTradeSide={setTradeSide}
                                    tradePrice={tradePrice} setTradePrice={setTradePrice}
                                    tradeQty={tradeQty} setTradeQty={setTradeQty}
                                    tradeFee={tradeFee} setTradeFee={setTradeFee}
                                    cashBalance={cashBalance}
                                    onSubmit={handleTradeSubmit}
                                    isOpen={manualTradeOpen}
                                    setIsOpen={setManualTradeOpen}
                                    lockSymbol={manualTradeOpen && tradeSide === 'SELL' && !!tradeSymbol}
                                />
                            )}
                        </div>

                        {/* Positions Table */}
                        <div style={{ backgroundColor: 'white', borderRadius: '8px', overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
                            <TableView aria-label="Positions" density="spacious">
                                <TableHeader>
                                    <Column isRowHeader>Symbol</Column>
                                    <Column align="end">Quantity</Column>
                                    <Column align="end">Avg Cost</Column>
                                    <Column align="end">Current Price</Column>
                                    <Column align="end">Market Value</Column>
                                    <Column align="end">Profit / %</Column>
                                    {selectedStrategy && !selectedStrategy.is_ai && <Column>Action</Column>}
                                </TableHeader>
                                <TableBody items={positions}>
                                    {(item: Position) => (
                                        <Row id={item.id ?? item.symbol}>
                                            <Cell><Text>{item.name ? `${item.symbol} ${item.name}` : item.symbol}</Text></Cell>
                                            <Cell>{item.quantity}</Cell>
                                            <Cell>{item.avg_cost.toFixed(3)}</Cell>
                                            <Cell>{item.current_price.toFixed(3)}</Cell>
                                            <Cell>{item.market_value.toFixed(2)}</Cell>
                                            <Cell>
                                                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                                                    <span style={{ color: item.profit >= 0 ? '#d32f2f' : '#388e3c', fontWeight: 'bold' }}>
                                                        {item.profit >= 0 ? '+' : ''}{item.profit.toFixed(2)}
                                                    </span>
                                                    <span style={{ color: item.profit >= 0 ? '#d32f2f' : '#388e3c', fontSize: '12px' }}>
                                                        {item.profit_percent.toFixed(2)}%
                                                    </span>
                                                </div>
                                            </Cell>
                                            {selectedStrategy && !selectedStrategy.is_ai && (
                                                <Cell>
                                                    <Button
                                                        variant="negative"
                                                        onPress={() => handleQuickSell(item)}
                                                    >
                                                        Sell
                                                    </Button>
                                                </Cell>
                                            )}
                                        </Row>
                                    )}
                                </TableBody>
                            </TableView>
                        </div>

                        {/* Recent Orders */}
                        <h3 style={{ margin: 0 }}>Recent Orders</h3>
                        <div style={{ backgroundColor: 'white', borderRadius: '8px', overflow: 'hidden', maxHeight: '300px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
                            <TableView aria-label="Orders" density="compact">
                                <TableHeader>
                                    <Column isRowHeader>Time</Column>
                                    <Column>Symbol</Column>
                                    <Column>Side</Column>
                                    <Column align="end">Price</Column>
                                    <Column align="end">Qty</Column>
                                    <Column align="end">Fee</Column>
                                </TableHeader>
                                <TableBody items={orders}>
                                    {(item: Order) => (
                                        <Row id={item.id}>
                                            <Cell>{formatOrderTime(item.created_at)}</Cell>
                                            <Cell>{item.name ? `${item.symbol} ${item.name}` : item.symbol}</Cell>
                                            <Cell>
                                                <StatusLight variant={item.side === 'BUY' ? 'positive' : 'negative'}>
                                                    {item.side}
                                                </StatusLight>
                                            </Cell>
                                            <Cell>{item.price}</Cell>
                                            <Cell>{item.quantity}</Cell>
                                            <Cell>{item.fee}</Cell>
                                        </Row>
                                    )}
                                </TableBody>
                            </TableView>
                        </div>

        {selectedStrategy && selectedStrategy.is_ai && (
            <>
                <h3 style={{ margin: '16px 0 0 0' }}>Strategy Runs</h3>
                <div style={{ backgroundColor: 'white', borderRadius: '8px', overflow: 'hidden', maxHeight: '300px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
                    <TableView aria-label="Strategy Runs" density="compact">
                        <TableHeader>
                            <Column isRowHeader>Time</Column>
                            <Column>Status</Column>
                            <Column align="end">Symbols</Column>
                            <Column align="end">Actions</Column>
                            <Column>Info</Column>
                            <Column>Details</Column>
                        </TableHeader>
                        <TableBody items={strategyRuns}>
                            {(item: StrategyRun) => (
                                <Row id={item.id}>
                                    <Cell>{item.started_at ? new Date(item.started_at).toLocaleString() : '-'}</Cell>
                                                    <Cell>
                                                        <StatusLight variant={item.status === 'success' ? 'positive' : (item.status === 'skipped' || item.status === 'running' ? 'neutral' : 'negative')}>
                                                            {item.status}
                                                        </StatusLight>
                                                    </Cell>
                                                    <Cell>
                                                        {item.symbol_count ?? 0}
                                                        {item.status === 'running' && (
                                                            <div style={{ fontSize: '11px', color: '#666' }}>
                                                                {getRunProgress(item)}/{item.symbol_count ?? 0}
                                                            </div>
                                                        )}
                                                    </Cell>
                                    <Cell>{item.action_count ?? 0}</Cell>
                                    <Cell>{item.error ? String(item.error) : '-'}</Cell>
                                    <Cell>
                                        <Button
                                            variant="secondary"
                                            onPress={() => {
                                                setSelectedRun(item);
                                                setRunDetailsOpen(true);
                                            }}
                                        >
                                            View
                                        </Button>
                                    </Cell>
                                </Row>
                            )}
                        </TableBody>
                    </TableView>
                    {runsLoading && (
                        <div style={{ padding: '8px', fontSize: '12px', color: '#666' }}>Loading...</div>
                    )}
                </div>
                {selectedRun && (
                    <DialogTrigger isOpen={runDetailsOpen} onOpenChange={(open) => {
                        setRunDetailsOpen(open);
                        if (!open) {
                            setSelectedRun(null);
                        }
                    }}>
                        <Button style={{ display: 'none' }} />
                        <Dialog>
                            <Heading slot="title">Run Details</Heading>
                            <Divider />
                            <Content>
                                <div style={{ fontSize: '12px', color: '#666', marginBottom: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <span>Status: {selectedRun.status} · Symbols: {selectedRun.symbol_count ?? 0} · Actions: {selectedRun.action_count ?? 0}</span>
                                    <ActionButton onPress={() => navigator.clipboard?.writeText(runDetailsText || '')}>
                                        <Copy />
                                    </ActionButton>
                                </div>
                                <TextArea
                                    label="Details"
                                    isReadOnly
                                    value={runDetailsText}
                                />
                                {selectedRun.error && (
                                    <TextArea label="Error" isReadOnly value={String(selectedRun.error)} />
                                )}
                            </Content>
                            <ButtonGroup>
                                <Button variant="secondary" onPress={() => setRunDetailsOpen(false)}>Close</Button>
                            </ButtonGroup>
                        </Dialog>
                    </DialogTrigger>
                )}
            </>
        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

function PaperTradingDialog(props: any) {
    // We can't easily control DialogTrigger from outside in S2 without looking up docs,
    // so we'll use a hack or just standard pattern.
    // If we want to close it, we might need to rely on the fact that Submit re-renders or we just live with manual close.
    // BUT actually, let's try the standard non-function child and see if it works.
    // And for closing, we'll just not pass 'close' to onSubmit for now and let user close it,
    // OR we assume the parent re-renders and closes it? No.
    // Ideally we want: <DialogTrigger><Button /><Dialog dismissable.../></DialogTrigger>

    // Let's try this: Just a standard DialogTrigger.
    // Pass the form elements as children.
    const list = useAsyncList<{ id?: string; symbol: string; name?: string }>({
        async load({ signal, filterText }) {
            const market = getMarket();
            if (market === 'ashare') {
                const items = await fetchAShareSymbols(filterText, { signal });
                return { items: items.map((item) => ({ ...item, id: item.symbol })) };
            }
            const items = await fetchBinanceSymbols(filterText, { signal });
            return { items: items.map((item) => ({ ...item, id: item.symbol })) };
        }
    });

    const handleSymbolInput = (value: string) => {
        props.setTradeSymbol(value);
        list.setFilterText(value);
    };

    const handleSymbolSelection = (keys: unknown) => {
        const selectedSymbol = (keys as Set<string>).values().next().value;
        if (selectedSymbol) {
            props.setTradeSymbol(selectedSymbol);
            list.setFilterText(selectedSymbol);
        }
    };

    const [internalOpen, setInternalOpen] = useState(false);
    const isOpen = props.isOpen ?? internalOpen;
    const setIsOpen = props.setIsOpen ?? setInternalOpen;

    const requiredAmount = (props.tradePrice || 0) * (props.tradeQty || 0) + (props.tradeFee || 0);
    const availableCash = Number(props.cashBalance || 0);
    const insufficientCash = props.tradeSide === 'BUY' && requiredAmount > availableCash;

    return (
        <DialogTrigger isOpen={isOpen} onOpenChange={setIsOpen}>
            <Button variant="accent">New Trade</Button>
            <Dialog>
                <Heading slot="title">Place Order</Heading>
                <Divider />
                <Content>
                    <Form validationBehavior="native">
                        {props.lockSymbol ? (
                            <TextField
                                label="Symbol"
                                value={props.tradeSymbol}
                                isReadOnly
                            />
                        ) : (
                            <Autocomplete
                                inputValue={props.tradeSymbol}
                                onInputChange={handleSymbolInput}
                            >
                                <SearchField
                                    label="Symbol"
                                    autoFocus
                                    placeholder="Type to search..."
                                />
                                <Menu
                                    items={list.items}
                                    selectionMode="single"
                                    onSelectionChange={handleSymbolSelection}
                                >
                                    {(item) => (
                                        <MenuItem id={item.symbol} closeOnSelect={false}>
                                            {item.name ? `${item.symbol} ${item.name}` : item.symbol}
                                        </MenuItem>
                                    )}
                                </Menu>
                            </Autocomplete>
                        )}
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <ButtonGroup>
                                <Button variant={props.tradeSide === 'BUY' ? 'accent' : 'primary'} onPress={() => props.setTradeSide('BUY')}>Buy</Button>
                                <Button variant={props.tradeSide === 'SELL' ? 'negative' : 'primary'} onPress={() => props.setTradeSide('SELL')}>Sell</Button>
                            </ButtonGroup>
                        </div>
                        <NumberField label="Price" value={props.tradePrice} onChange={props.setTradePrice} formatOptions={{ style: 'decimal', minimumFractionDigits: 2 }} />
                        <NumberField label="Quantity" value={props.tradeQty} onChange={(v: any) => props.setTradeQty(v || 0)} />
                        <NumberField label="Fee" value={props.tradeFee} onChange={props.setTradeFee} />
                        {insufficientCash && (
                            <div style={{ fontSize: '12px', color: '#b00020' }}>
                                Insufficient cash. Available: ¥{availableCash.toFixed(2)}, required: ¥{requiredAmount.toFixed(2)}.
                            </div>
                        )}
                    </Form>
                </Content>
                <ButtonGroup>
                    <Button variant="secondary" onPress={() => setIsOpen(false)}>Cancel</Button>
                    <Button
                        variant={props.tradeSide === 'BUY' ? 'accent' : 'negative'}
                        onPress={async () => {
                            const ok = await props.onSubmit();
                            if (ok) {
                                setIsOpen(false);
                            }
                        }}
                        isDisabled={insufficientCash}
                    >
                        Submit {props.tradeSide}
                    </Button>
                </ButtonGroup>
            </Dialog>
        </DialogTrigger>
    );
}

function CreateStrategyDialog(props: {
    onCreate: (payload: { name: string; type: string; prompt: string; is_ai: boolean; model_id?: string; run_interval_minutes?: number; universe_type?: string; universe_symbols?: string; initial_capital?: number; objectives?: StrategyObjectives; constraints?: StrategyConstraints; params?: StrategyParams; optimization?: StrategyOptimization }) => void;
    aggressivePrompt?: string;
    conservativePrompt?: string;
    models: LLMModel[];
    defaultModelId: string;
    intervalOptions: { id: string; label: string }[];
}) {
    const [isOpen, setIsOpen] = useState(false);
    const [name, setName] = useState('');
    const [kind, setKind] = useState<'manual' | 'ai_custom' | 'ai_aggressive' | 'ai_conservative'>('manual');
    const [prompt, setPrompt] = useState('');
    const [modelId, setModelId] = useState('');
    const [intervalMinutes, setIntervalMinutes] = useState(1440);
    const [universeType, setUniverseType] = useState<'watchlist' | 'custom'>('watchlist');
    const [universeSymbols, setUniverseSymbols] = useState('');
    const [initialCapital, setInitialCapital] = useState(100000);

    useEffect(() => {
        if (!modelId && props.defaultModelId) {
            setModelId(props.defaultModelId);
        }
    }, [props.defaultModelId, modelId]);

    const handleKindChange = (value: string) => {
        const next = value as typeof kind;
        setKind(next);
        if (next === 'ai_aggressive') {
            setPrompt(props.aggressivePrompt || '');
        } else if (next === 'ai_conservative') {
            setPrompt(props.conservativePrompt || '');
        } else if (next === 'ai_custom') {
            setPrompt('');
        } else {
            setPrompt('');
        }
        if (next === 'manual') {
            setUniverseType('custom');
            setUniverseSymbols('');
        }
    };

    const handleSubmit = async () => {
        if (!name.trim()) {
            alert('Strategy name is required');
            return;
        }
        const is_ai = kind !== 'manual';
        const type = is_ai ? 'custom' : 'manual';
        await props.onCreate({
            name: name.trim(),
            type,
            prompt,
            is_ai,
            model_id: is_ai ? (modelId || props.defaultModelId || null) : null,
            run_interval_minutes: is_ai ? intervalMinutes : 1440,
            universe_type: is_ai ? universeType : 'custom',
            universe_symbols: is_ai ? universeSymbols : '',
            initial_capital: initialCapital,
            objectives: { ...DEFAULT_OBJECTIVES },
            constraints: { ...DEFAULT_CONSTRAINTS },
            params: { ...DEFAULT_PARAMS },
            optimization: { ...DEFAULT_OPTIMIZATION }
        });
        setName('');
        setPrompt('');
        setKind('manual');
        setModelId(props.defaultModelId || '');
        setIntervalMinutes(1440);
        setUniverseType('watchlist');
        setUniverseSymbols('');
        setInitialCapital(100000);
        setIsOpen(false);
    };

    return (
        <DialogTrigger isOpen={isOpen} onOpenChange={setIsOpen}>
            <Button variant="primary">New</Button>
            <Dialog>
                <Heading slot="title">Create Strategy</Heading>
                <Divider />
                <Content>
                    <Form validationBehavior="native">
                        <TextField label="Name" value={name} onChange={setName} placeholder="Strategy name" />
                        <NumberField
                            label="Initial Capital"
                            value={initialCapital}
                            onChange={(value) => setInitialCapital(Number(value) || 0)}
                            formatOptions={{ style: 'decimal', minimumFractionDigits: 0 }}
                        />
                        <Picker label="Type" selectedKey={kind} onSelectionChange={(key) => handleKindChange(String(key))}>
                            <PickerItem id="manual">Manual</PickerItem>
                            <PickerItem id="ai_custom">AI (Custom)</PickerItem>
                            <PickerItem id="ai_aggressive">AI (Aggressive Template)</PickerItem>
                            <PickerItem id="ai_conservative">AI (Conservative Template)</PickerItem>
                        </Picker>
                        {kind !== 'manual' && (
                            <>
                                <Picker
                                    label="AI Model"
                                    selectedKey={modelId || props.defaultModelId || ""}
                                    onSelectionChange={(key) => setModelId(String(key))}
                                >
                                    {props.models.map((m) => (
                                        <PickerItem id={m.id} key={m.id}>{m.name}</PickerItem>
                                    ))}
                                </Picker>
                                <Picker
                                    label="Run Interval"
                                    selectedKey={String(intervalMinutes)}
                                    onSelectionChange={(key) => setIntervalMinutes(Number(key))}
                                >
                                    {props.intervalOptions.map((opt) => (
                                        <PickerItem id={opt.id} key={opt.id}>{opt.label}</PickerItem>
                                    ))}
                                </Picker>
                                <Picker
                                    label="Universe"
                                    selectedKey={universeType}
                                    onSelectionChange={(key) => setUniverseType(key === 'watchlist' ? 'watchlist' : 'custom')}
                                >
                                    <PickerItem id="watchlist">自选池</PickerItem>
                                    <PickerItem id="custom">自定义</PickerItem>
                                </Picker>
                                {universeType === 'custom' && (
                                    <TextArea label="Custom Symbols" value={universeSymbols} onChange={setUniverseSymbols} placeholder="Comma/space separated: 000001.SZ, 600519.SH" />
                                )}
                                <TextArea label="Prompt" value={prompt} onChange={setPrompt} placeholder="Describe the AI strategy prompt..." />
                            </>
                        )}
                    </Form>
                </Content>
                <ButtonGroup>
                    <Button variant="secondary" onPress={() => setIsOpen(false)}>Cancel</Button>
                    <Button variant="accent" onPress={handleSubmit}>Create</Button>
                </ButtonGroup>
            </Dialog>
        </DialogTrigger>
    );
}
