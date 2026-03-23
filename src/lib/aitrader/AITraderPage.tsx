import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Button } from "@react-spectrum/s2";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getWatchlist, loadWatchlistFromServer, type WatchlistItem } from "../domain/Watchlist";
import "./AITraderPage.css";

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || "http://localhost:8000";

type MarketFilter = "all" | "ashare" | "us" | "crypto";
type AnalyzeEngine = "rule" | "llm" | "fusion";

type AnalysisResult = {
    symbol: string;
    engine: AnalyzeEngine;
    model?: string;
    source: string;
    bars: number;
    range?: { start?: string; end?: string };
    analysis?: string;
    analysis_meta?: Record<string, unknown>;
};

type AnalysisSection = {
    id: string;
    title: string;
    body: string;
};

type HistoryItem = {
    id: number;
    symbol: string;
    engine: AnalyzeEngine;
    model_id?: string;
    bars?: number;
    start_date?: string;
    end_date?: string;
    source?: string;
    analysis_preview?: string;
    created_at?: string;
};

type LLMModel = {
    id: string;
    name?: string;
    provider?: string;
};

type PaperStrategy = {
    id: number;
    name: string;
    type?: string;
};

type RuleProfileMarket = "ashare" | "etf" | "us" | "crypto";

type RuleProfile = {
    market: RuleProfileMarket;
    rr_buy_downtrend: number;
    rr_buy_uptrend: number;
    require_close_above_ma20_downtrend: boolean;
    buy_position_downtrend?: string;
    watch_position_downtrend?: string;
    buy_position_uptrend?: string;
    watch_position_uptrend?: string;
    watch_position_range?: string;
    updated_at?: string;
};

const marketLabel = (market: "ashare" | "us" | "crypto") => {
    if (market === "ashare") return "A股";
    if (market === "us") return "美股";
    return "Crypto";
};

const detectMarket = (symbol: string) => {
    const upper = (symbol || "").toUpperCase();
    if (upper.endsWith(".SZ") || upper.endsWith(".SH") || upper.endsWith(".BJ")) return "ashare";
    if (upper.endsWith(".US")) return "us";
    return "crypto";
};

const RULE_PROFILE_MARKETS: Array<{ value: RuleProfileMarket; label: string }> = [
    { value: "ashare", label: "A股" },
    { value: "etf", label: "ETF" },
    { value: "us", label: "美股" },
    { value: "crypto", label: "Crypto" },
];

const DEFAULT_RULE_PROFILE: RuleProfile = {
    market: "ashare",
    rr_buy_downtrend: 1.5,
    rr_buy_uptrend: 1.3,
    require_close_above_ma20_downtrend: true,
};

const detectRuleProfileMarket = (symbol: string): RuleProfileMarket => {
    const upper = (symbol || "").toUpperCase();
    if (upper.endsWith(".US")) return "us";
    if (upper.endsWith(".SZ") || upper.endsWith(".SH") || upper.endsWith(".BJ")) {
        const code = upper.split(".")[0] || "";
        if (code.startsWith("15") || code.startsWith("16") || code.startsWith("5")) {
            return "etf";
        }
        return "ashare";
    }
    return "crypto";
};

const isLikelyOpenAI = (id: string, name: string) => {
    const lowerId = (id || "").toLowerCase();
    const lowerName = (name || "").toLowerCase();
    return (
        lowerId.includes("gpt")
        || lowerId.includes("codex")
        || lowerId.startsWith("o1")
        || lowerId.startsWith("o3")
        || lowerId.startsWith("o4")
        || lowerName.includes("openai")
        || lowerName.includes("codex")
    );
};

const SECTION_HEADER_RE = /^\s*(?:#{1,6}\s*)?(\d+)[.):：、]?\s*(.+?)\s*$/;

const normalizeForCompare = (text: string) => {
    return (text || "")
        .replace(/\s+/g, "")
        .replace(/[，。；：、“”‘’`*#\-_.()/\\|[\]{}<>~!@%^&+=?]/g, "")
        .toLowerCase();
};

const extractIndicatorValues = (meta?: Record<string, unknown>) => {
    if (!meta) return null;
    const direct = (meta as any).indicator_values;
    if (direct) return direct as Record<string, any>;
    const ruleMeta = (meta as any).rule_meta;
    if (ruleMeta?.indicator_values) return ruleMeta.indicator_values as Record<string, any>;
    return null;
};

const summarizeIndicatorValues = (values?: Record<string, any> | null) => {
    if (!values || typeof values !== "object") return null;
    let ok = 0;
    let noData = 0;
    let disabled = 0;
    let error = 0;
    Object.values(values).forEach((entry: any) => {
        const status = entry?.status;
        if (status === "ok") ok += 1;
        else if (status === "no_data") noData += 1;
        else if (status === "disabled") disabled += 1;
        else if (status === "error") error += 1;
    });
    return { ok, noData, disabled, error, values };
};

const parseSections = (text?: string): AnalysisSection[] => {
    const raw = (text || "").trim();
    if (!raw) return [];
    const lines = raw.split(/\r?\n/);
    const sections: AnalysisSection[] = [];
    let current: AnalysisSection | null = null;

    const pushCurrent = () => {
        if (!current) return;
        current.body = (current.body || "").trim();
        sections.push(current);
        current = null;
    };

    lines.forEach((line) => {
        const match = line.match(SECTION_HEADER_RE);
        if (match) {
            const id = (match[1] || "").trim();
            const title = (match[2] || "").trim() || "未命名";
            pushCurrent();
            current = { id, title, body: "" };
            return;
        }
        if (!current) {
            current = { id: "0", title: "分析内容", body: line };
            return;
        }
        current.body = current.body ? `${current.body}\n${line}` : line;
    });
    pushCurrent();

    if (sections.length === 0) {
        return [{ id: "0", title: "分析内容", body: raw }];
    }
    return sections;
};

const toSectionMap = (sections: AnalysisSection[]) => {
    const map = new Map<string, AnalysisSection>();
    sections.forEach((section) => map.set(section.id, section));
    return map;
};

export function AITraderPage() {
    const navigate = useNavigate();
    const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
    const [marketFilter, setMarketFilter] = useState<MarketFilter>("all");
    const [search, setSearch] = useState("");
    const [selectedSymbol, setSelectedSymbol] = useState("");
    const [symbolNames, setSymbolNames] = useState<Record<string, string>>({});

    const [bars, setBars] = useState(365);
    const [models, setModels] = useState<LLMModel[]>([]);
    const [selectedModel, setSelectedModel] = useState("");
    const [paperStrategies, setPaperStrategies] = useState<PaperStrategy[]>([]);
    const [selectedStrategyId, setSelectedStrategyId] = useState<number>(0);

    const [ruleResult, setRuleResult] = useState<AnalysisResult | null>(null);
    const [aiResult, setAiResult] = useState<AnalysisResult | null>(null);
    const [fusionResult, setFusionResult] = useState<AnalysisResult | null>(null);
    const [loadingRule, setLoadingRule] = useState(false);
    const [loadingAI, setLoadingAI] = useState(false);
    const [loadingFusion, setLoadingFusion] = useState(false);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [historyItems, setHistoryItems] = useState<HistoryItem[]>([]);
    const [selectedHistoryId, setSelectedHistoryId] = useState<number>(0);
    const [error, setError] = useState("");
    const [indicatorFilterText, setIndicatorFilterText] = useState("");
    const [indicatorFilterStatus, setIndicatorFilterStatus] = useState("all");
    const [indicatorOnlyAbnormal, setIndicatorOnlyAbnormal] = useState(false);
    const [ruleProfiles, setRuleProfiles] = useState<Record<string, RuleProfile>>({});
    const [ruleProfileMarket, setRuleProfileMarket] = useState<RuleProfileMarket>("ashare");
    const [ruleProfileDraft, setRuleProfileDraft] = useState<RuleProfile>({ ...DEFAULT_RULE_PROFILE });
    const [ruleProfileLoading, setRuleProfileLoading] = useState(false);
    const [ruleProfileSaving, setRuleProfileSaving] = useState(false);
    const [ruleProfileMsg, setRuleProfileMsg] = useState("");
    const historyReqRef = useRef(0);

    const refreshWatchlist = () => {
        setWatchlist(getWatchlist());
    };

    useEffect(() => {
        refreshWatchlist();
        loadWatchlistFromServer();
        const handler = () => refreshWatchlist();
        window.addEventListener("watchlist-updated", handler);
        return () => window.removeEventListener("watchlist-updated", handler);
    }, []);

    useEffect(() => {
        if (selectedSymbol) return;
        if (!watchlist.length) return;
        setSelectedSymbol(watchlist[0].symbol);
    }, [watchlist, selectedSymbol]);

    useEffect(() => {
        fetch(`${API_BASE_URL}/api/config/llm_models`)
            .then(res => (res.ok ? res.json() : null))
            .then(data => {
                const items = Array.isArray(data?.models) ? data.models : [];
                setModels(items);
                const current = (data?.current_model || "") as string;
                if (items.length === 0) {
                    setSelectedModel("");
                    return;
                }
                if (current && items.some((m: LLMModel) => m.id === current)) {
                    setSelectedModel(current);
                    return;
                }
                const openai = items.find((m: LLMModel) => isLikelyOpenAI(m.id, m.name || ""));
                setSelectedModel(openai?.id || items[0].id || "");
            })
            .catch(() => {
                setModels([]);
                setSelectedModel("");
            });
    }, []);

    useEffect(() => {
        fetch(`${API_BASE_URL}/api/paper/strategies`)
            .then(res => (res.ok ? res.json() : null))
            .then(data => {
                const items = Array.isArray(data?.data) ? (data.data as PaperStrategy[]) : [];
                setPaperStrategies(items);
                if (!items.length) {
                    setSelectedStrategyId(0);
                    return;
                }
                const manual = items.find((item) => String(item.type || "").toLowerCase() === "manual");
                setSelectedStrategyId(Number(manual?.id || items[0].id || 0));
            })
            .catch(() => {
                setPaperStrategies([]);
                setSelectedStrategyId(0);
            });
    }, []);

    const loadRuleProfiles = useCallback(async () => {
        setRuleProfileLoading(true);
        try {
            const res = await fetch(`${API_BASE_URL}/api/aitrader/rule_profiles`);
            const data = await res.json().catch(() => null);
            const items = Array.isArray(data?.data) ? (data.data as RuleProfile[]) : [];
            const next: Record<string, RuleProfile> = {};
            items.forEach((item) => {
                const market = (item?.market || "").toLowerCase();
                if (!market) return;
                next[market] = {
                    ...DEFAULT_RULE_PROFILE,
                    ...item,
                    market: market as RuleProfileMarket,
                    rr_buy_downtrend: Number(item.rr_buy_downtrend ?? DEFAULT_RULE_PROFILE.rr_buy_downtrend),
                    rr_buy_uptrend: Number(item.rr_buy_uptrend ?? DEFAULT_RULE_PROFILE.rr_buy_uptrend),
                    require_close_above_ma20_downtrend: Boolean(
                        item.require_close_above_ma20_downtrend
                            ?? DEFAULT_RULE_PROFILE.require_close_above_ma20_downtrend
                    ),
                };
            });
            setRuleProfiles(next);
            setRuleProfileMsg("");
        } catch (e) {
            setRuleProfileMsg(e instanceof Error ? e.message : "Rule 参数加载失败");
        } finally {
            setRuleProfileLoading(false);
        }
    }, []);

    useEffect(() => {
        loadRuleProfiles();
    }, [loadRuleProfiles]);

    const resolveSymbolNames = useCallback((items: WatchlistItem[]) => {
        const missing = items
            .map(item => item.symbol)
            .filter(symbol => symbol && !symbolNames[symbol]);
        if (missing.length === 0) return;
        const next = { ...symbolNames };
        missing.forEach(symbol => {
            const cached = localStorage.getItem(`stock_name_${symbol}`);
            if (cached) {
                next[symbol] = cached;
            }
        });
        setSymbolNames(next);
        const toFetch = missing.filter(symbol => !next[symbol] && detectMarket(symbol) !== "crypto");
        if (toFetch.length === 0) return;
        toFetch.slice(0, 50).forEach(symbol => {
            const params = new URLSearchParams({ q: symbol, limit: "1" });
            fetch(`${API_BASE_URL}/api/symbols?${params.toString()}`)
                .then(res => (res.ok ? res.json() : null))
                .then(data => {
                    const rows = Array.isArray(data?.data) ? data.data : [];
                    const match = rows.find((row: { symbol?: string }) => row?.symbol === symbol) || rows[0];
                    const name = match?.name || "";
                    if (!name) return;
                    localStorage.setItem(`stock_name_${symbol}`, name);
                    setSymbolNames(prev => ({ ...prev, [symbol]: name }));
                })
                .catch(() => {
                    // ignore
                });
        });
    }, [symbolNames]);

    useEffect(() => {
        resolveSymbolNames(watchlist);
    }, [watchlist, resolveSymbolNames]);

    const filtered = useMemo(() => {
        const q = search.trim().toUpperCase();
        return watchlist.filter(item => {
            if (marketFilter !== "all" && item.market !== marketFilter) return false;
            if (!q) return true;
            const name = item.name || symbolNames[item.symbol] || "";
            return item.symbol.toUpperCase().includes(q) || name.includes(q);
        });
    }, [watchlist, marketFilter, search, symbolNames]);

    const selectedItem = useMemo(
        () => watchlist.find(item => item.symbol === selectedSymbol) || null,
        [watchlist, selectedSymbol]
    );

    useEffect(() => {
        if (!selectedSymbol) return;
        setRuleProfileMarket(detectRuleProfileMarket(selectedSymbol));
    }, [selectedSymbol]);

    useEffect(() => {
        const profile = ruleProfiles[ruleProfileMarket];
        if (profile) {
            setRuleProfileDraft({ ...profile });
            return;
        }
        const marketDefault = {
            ...DEFAULT_RULE_PROFILE,
            market: ruleProfileMarket,
        };
        setRuleProfileDraft(marketDefault);
    }, [ruleProfiles, ruleProfileMarket]);

    const ruleSections = useMemo(() => parseSections(ruleResult?.analysis), [ruleResult?.analysis]);
    const aiSections = useMemo(() => parseSections(aiResult?.analysis), [aiResult?.analysis]);
    const ruleSectionMap = useMemo(() => toSectionMap(ruleSections), [ruleSections]);
    const aiSectionMap = useMemo(() => toSectionMap(aiSections), [aiSections]);
    const ruleIndicatorValues = useMemo(
        () => extractIndicatorValues(ruleResult?.analysis_meta),
        [ruleResult?.analysis_meta]
    );
    const ruleIndicatorSummary = useMemo(
        () => summarizeIndicatorValues(ruleIndicatorValues),
        [ruleIndicatorValues]
    );

    useEffect(() => {
        setIndicatorFilterText("");
        setIndicatorFilterStatus("all");
        setIndicatorOnlyAbnormal(false);
    }, [ruleIndicatorValues]);

    const diffSectionIds = useMemo(() => {
        if (!ruleSections.length || !aiSections.length) return new Set<string>();
        const ids = new Set<string>([
            ...ruleSections.map(item => item.id),
            ...aiSections.map(item => item.id),
        ]);
        const changed = new Set<string>();
        ids.forEach((id) => {
            const left = ruleSectionMap.get(id)?.body || "";
            const right = aiSectionMap.get(id)?.body || "";
            if (normalizeForCompare(left) !== normalizeForCompare(right)) {
                changed.add(id);
            }
        });
        return changed;
    }, [ruleSections, aiSections, ruleSectionMap, aiSectionMap]);

    const ruleStrategyLabel = useMemo(() => {
        const meta = (ruleResult?.analysis_meta || {}) as { portfolio_plan?: { strategy_id?: number } };
        const sid = Number(meta?.portfolio_plan?.strategy_id || 0);
        return sid > 0 ? ` · 策略#${sid}` : "";
    }, [ruleResult?.analysis_meta]);

    const fetchHistoryDetailById = useCallback(async (recordId: number): Promise<AnalysisResult | null> => {
        if (!recordId) return null;
        const res = await fetch(`${API_BASE_URL}/api/aitrader/history/${recordId}`);
        const data = await res.json().catch(() => null);
        if (!res.ok) {
            throw new Error(data?.detail || "load history failed");
        }
        const item = data?.data;
        if (!item?.analysis) {
            return null;
        }
        return {
            symbol: item.symbol,
            engine: item.engine,
            model: item.model_id || "",
            source: item.source || "",
            bars: Number(item.bars || 0),
            range: { start: item.start_date, end: item.end_date },
            analysis: item.analysis,
            analysis_meta: (item.analysis_meta || {}) as Record<string, unknown>,
        };
    }, []);

    const runAnalyze = async (engine: AnalyzeEngine) => {
        if (!selectedSymbol) return;
        setError("");
        if (engine === "rule") setLoadingRule(true);
        else if (engine === "llm") setLoadingAI(true);
        else setLoadingFusion(true);
        try {
            const body: Record<string, unknown> = {
                symbol: selectedSymbol,
                engine,
                bars,
            };
            if (selectedStrategyId > 0) {
                body.strategy_id = selectedStrategyId;
            }
            if (engine === "llm" && selectedModel) {
                body.model = selectedModel;
            }
            const res = await fetch(`${API_BASE_URL}/api/aitrader/analyze`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const data = await res.json().catch(() => null);
            if (!res.ok) {
                const detail = data?.detail || `${engine} analyze failed`;
                throw new Error(String(detail));
            }
            const result = data?.data as AnalysisResult;
            if (!result?.analysis) {
                throw new Error(`${engine} result is empty`);
            }
            if (engine === "rule") setRuleResult(result);
            else if (engine === "llm") setAiResult(result);
            else setFusionResult(result);
            if (selectedSymbol) {
                loadHistory(selectedSymbol, { clearPanels: false, autoApplyLatest: false });
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            if (engine === "rule") setLoadingRule(false);
            else if (engine === "llm") setLoadingAI(false);
            else setLoadingFusion(false);
        }
    };

    const saveRuleProfile = async () => {
        setRuleProfileMsg("");
        setRuleProfileSaving(true);
        try {
            const payload: RuleProfile = {
                market: ruleProfileMarket,
                rr_buy_downtrend: Number(ruleProfileDraft.rr_buy_downtrend || 0),
                rr_buy_uptrend: Number(ruleProfileDraft.rr_buy_uptrend || 0),
                require_close_above_ma20_downtrend: Boolean(ruleProfileDraft.require_close_above_ma20_downtrend),
                buy_position_downtrend: ruleProfileDraft.buy_position_downtrend,
                watch_position_downtrend: ruleProfileDraft.watch_position_downtrend,
                buy_position_uptrend: ruleProfileDraft.buy_position_uptrend,
                watch_position_uptrend: ruleProfileDraft.watch_position_uptrend,
                watch_position_range: ruleProfileDraft.watch_position_range,
            };
            const res = await fetch(`${API_BASE_URL}/api/aitrader/rule_profile`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json().catch(() => null);
            if (!res.ok) {
                throw new Error(data?.detail || "保存 Rule 参数失败");
            }
            const saved = data?.data as RuleProfile;
            if (saved?.market) {
                const market = saved.market as RuleProfileMarket;
                setRuleProfiles((prev) => ({
                    ...prev,
                    [market]: {
                        ...DEFAULT_RULE_PROFILE,
                        ...saved,
                        market,
                    },
                }));
                setRuleProfileDraft({
                    ...DEFAULT_RULE_PROFILE,
                    ...saved,
                    market,
                });
            }
            setRuleProfileMsg("Rule 参数已保存");
        } catch (e) {
            setRuleProfileMsg(e instanceof Error ? e.message : "保存 Rule 参数失败");
        } finally {
            setRuleProfileSaving(false);
        }
    };

    const loadHistory = useCallback(async (
        symbol: string,
        options?: { clearPanels?: boolean; autoApplyLatest?: boolean }
    ) => {
        const sym = (symbol || "").trim();
        const clearPanels = options?.clearPanels ?? true;
        const autoApplyLatest = options?.autoApplyLatest ?? true;
        const reqId = ++historyReqRef.current;
        if (!sym) {
            setHistoryItems([]);
            setSelectedHistoryId(0);
            setRuleResult(null);
            setAiResult(null);
            setFusionResult(null);
            return;
        }
        if (clearPanels) {
            setRuleResult(null);
            setAiResult(null);
            setFusionResult(null);
        }
        setHistoryLoading(true);
        try {
            const res = await fetch(`${API_BASE_URL}/api/aitrader/history?symbol=${encodeURIComponent(sym)}&limit=50`);
            const data = await res.json().catch(() => null);
            const items = Array.isArray(data?.data) ? data.data : [];
            if (reqId !== historyReqRef.current) return;
            setHistoryItems(items);
            setSelectedHistoryId(items[0]?.id || 0);

            if (autoApplyLatest && items.length > 0) {
                const latestRule = items.find((item: HistoryItem) => item.engine === "rule");
                const latestAI = items.find((item: HistoryItem) => item.engine === "llm");
                const latestFusion = items.find((item: HistoryItem) => item.engine === "fusion");
                const tasks: Promise<AnalysisResult | null>[] = [];
                if (latestRule?.id) tasks.push(fetchHistoryDetailById(latestRule.id));
                if (latestAI?.id) tasks.push(fetchHistoryDetailById(latestAI.id));
                if (latestFusion?.id) tasks.push(fetchHistoryDetailById(latestFusion.id));
                if (tasks.length > 0) {
                    const loaded = await Promise.all(tasks);
                    if (reqId !== historyReqRef.current) return;
                    loaded.forEach((record) => {
                        if (!record) return;
                        if (record.engine === "rule") setRuleResult(record);
                        else if (record.engine === "llm") setAiResult(record);
                        else setFusionResult(record);
                    });
                }
            }
        } catch {
            if (reqId !== historyReqRef.current) return;
            setHistoryItems([]);
            setSelectedHistoryId(0);
            if (clearPanels) {
                setRuleResult(null);
                setAiResult(null);
                setFusionResult(null);
            }
        } finally {
            if (reqId === historyReqRef.current) setHistoryLoading(false);
        }
    }, [fetchHistoryDetailById]);

    const loadHistoryDetail = () => {
        if (!selectedHistoryId) return;
        setError("");
        fetchHistoryDetailById(selectedHistoryId)
            .then((mapped) => {
                if (!mapped?.analysis) {
                    throw new Error("history content empty");
                }
                if (mapped.engine === "rule") setRuleResult(mapped);
                else if (mapped.engine === "llm") setAiResult(mapped);
                else setFusionResult(mapped);
            })
            .catch((err) => {
                setError(err instanceof Error ? err.message : String(err));
            });
    };

    useEffect(() => {
        if (!selectedSymbol) return;
        loadHistory(selectedSymbol);
    }, [selectedSymbol, loadHistory]);

    const runCompare = async () => {
        await Promise.all([runAnalyze("rule"), runAnalyze("llm")]);
    };

    return (
        <div className="aitrader-page">
            <header className="aitrader-header">
                <div className="aitrader-title">
                    <button className="aitrader-back" onClick={() => navigate("/")}>返回首页</button>
                    <div>
                        <div className="aitrader-headline">AITrader 对比分析</div>
                        <div className="aitrader-sub">左侧选自选股，右侧分别执行 Rule 与 AI 分析并对比。</div>
                    </div>
                </div>
                <Button onPress={() => { refreshWatchlist(); loadWatchlistFromServer(); }}>刷新列表</Button>
            </header>

            <div className="aitrader-body">
                <aside className="aitrader-left">
                    <div className="aitrader-left-toolbar">
                        <input
                            className="aitrader-search"
                            placeholder="搜索 symbol 或名称"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                        <select
                            className="aitrader-select"
                            value={marketFilter}
                            onChange={(e) => setMarketFilter(e.target.value as MarketFilter)}
                        >
                            <option value="all">全部市场</option>
                            <option value="ashare">A股</option>
                            <option value="us">美股</option>
                            <option value="crypto">Crypto</option>
                        </select>
                    </div>
                    <div className="aitrader-list">
                        {filtered.length === 0 && <div className="aitrader-empty">暂无自选</div>}
                        {filtered.map(item => {
                            const name = item.name || symbolNames[item.symbol] || "";
                            const active = item.symbol === selectedSymbol;
                            return (
                                <button
                                    key={`${item.symbol}-${item.market}`}
                                    className={`aitrader-item ${active ? "active" : ""}`}
                                    onClick={() => setSelectedSymbol(item.symbol)}
                                >
                                    <div className="aitrader-item-top">
                                        <span className="aitrader-item-symbol">{item.symbol}</span>
                                        <span className="aitrader-item-market">{marketLabel(item.market)}</span>
                                    </div>
                                    <div className="aitrader-item-name">{name || "-"}</div>
                                </button>
                            );
                        })}
                    </div>
                </aside>

                <section className="aitrader-right">
                    <div className="aitrader-controls">
                        <div className="aitrader-selected">
                            标的：<strong>{selectedItem?.symbol || "--"}</strong>
                            {selectedItem && (
                                <span className="aitrader-selected-name">{selectedItem.name || symbolNames[selectedItem.symbol] || ""}</span>
                            )}
                        </div>
                        <label className="aitrader-inline">
                            Bars
                            <input
                                type="number"
                                min={60}
                                max={1200}
                                value={bars}
                                onChange={(e) => setBars(Math.max(60, Math.min(1200, Number(e.target.value || 365))))}
                            />
                        </label>
                        <label className="aitrader-inline">
                            AI模型
                            <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
                                {models.map(item => (
                                    <option key={item.id} value={item.id}>{item.name || item.id}</option>
                                ))}
                            </select>
                        </label>
                        <label className="aitrader-inline">
                            持仓策略
                            <select
                                value={selectedStrategyId || ""}
                                onChange={(e) => setSelectedStrategyId(Number(e.target.value || 0))}
                            >
                                {!paperStrategies.length && <option value="">无策略</option>}
                                {paperStrategies.map((item) => (
                                    <option key={item.id} value={item.id}>
                                        #{item.id} {item.name}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <div className="aitrader-actions">
                            <Button onPress={() => runAnalyze("rule")} isDisabled={!selectedSymbol || loadingRule}>
                                {loadingRule ? "Rule分析中..." : "Rule分析"}
                            </Button>
                            <Button onPress={() => runAnalyze("llm")} isDisabled={!selectedSymbol || loadingAI}>
                                {loadingAI ? "AI分析中..." : "AI分析"}
                            </Button>
                            <Button onPress={() => runAnalyze("fusion")} isDisabled={!selectedSymbol || loadingFusion}>
                                {loadingFusion ? "融合分析中..." : "融合分析"}
                            </Button>
                            <Button variant="secondary" onPress={runCompare} isDisabled={!selectedSymbol || loadingRule || loadingAI || loadingFusion}>
                                同时分析
                            </Button>
                        </div>
                    </div>
                    <div className="aitrader-rule-profile-row">
                        <div className="aitrader-rule-profile-title">Rule 参数配置（RULE-001）</div>
                        <label className="aitrader-inline">
                            市场
                            <select
                                value={ruleProfileMarket}
                                onChange={(e) => setRuleProfileMarket(e.target.value as RuleProfileMarket)}
                            >
                                {RULE_PROFILE_MARKETS.map((item) => (
                                    <option key={item.value} value={item.value}>{item.label}</option>
                                ))}
                            </select>
                        </label>
                        <label className="aitrader-inline">
                            下跌趋势买入R/R
                            <input
                                type="number"
                                min={0.5}
                                max={10}
                                step={0.05}
                                value={Number(ruleProfileDraft.rr_buy_downtrend || 0)}
                                onChange={(e) => setRuleProfileDraft((prev) => ({
                                    ...prev,
                                    market: ruleProfileMarket,
                                    rr_buy_downtrend: Number(e.target.value || 0),
                                }))}
                            />
                        </label>
                        <label className="aitrader-inline">
                            上行趋势买入R/R
                            <input
                                type="number"
                                min={0.5}
                                max={10}
                                step={0.05}
                                value={Number(ruleProfileDraft.rr_buy_uptrend || 0)}
                                onChange={(e) => setRuleProfileDraft((prev) => ({
                                    ...prev,
                                    market: ruleProfileMarket,
                                    rr_buy_uptrend: Number(e.target.value || 0),
                                }))}
                            />
                        </label>
                        <label className="aitrader-inline aitrader-inline-check">
                            <input
                                type="checkbox"
                                checked={Boolean(ruleProfileDraft.require_close_above_ma20_downtrend)}
                                onChange={(e) => setRuleProfileDraft((prev) => ({
                                    ...prev,
                                    market: ruleProfileMarket,
                                    require_close_above_ma20_downtrend: e.target.checked,
                                }))}
                            />
                            下跌趋势需收盘站上MA20
                        </label>
                        <div className="aitrader-rule-profile-actions">
                            <Button
                                variant="secondary"
                                onPress={saveRuleProfile}
                                isDisabled={ruleProfileSaving || ruleProfileLoading}
                            >
                                {ruleProfileSaving ? "保存中..." : "保存参数"}
                            </Button>
                            <Button
                                variant="secondary"
                                onPress={loadRuleProfiles}
                                isDisabled={ruleProfileLoading}
                            >
                                {ruleProfileLoading ? "刷新中..." : "刷新参数"}
                            </Button>
                        </div>
                        {ruleProfileMsg && <div className="aitrader-rule-profile-msg">{ruleProfileMsg}</div>}
                    </div>
                    <div className="aitrader-history-row">
                        <label className="aitrader-inline">
                            历史记录
                            <select
                                value={selectedHistoryId || ""}
                                onChange={(e) => setSelectedHistoryId(Number(e.target.value || 0))}
                            >
                                {!historyItems.length && <option value="">暂无历史</option>}
                                {historyItems.map((item) => (
                                    <option key={item.id} value={item.id}>
                                        #{item.id} [{item.engine}] {item.created_at ? String(item.created_at).slice(0, 19) : ""}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <Button variant="secondary" onPress={loadHistoryDetail} isDisabled={!selectedHistoryId || historyLoading}>
                            {historyLoading ? "加载中..." : "加载历史"}
                        </Button>
                    </div>

                    {error && <div className="aitrader-error">{error}</div>}

                    <div className="aitrader-panels">
                        <article className="aitrader-panel">
                            <div className="aitrader-panel-head">
                                <div className="aitrader-panel-title">Rule</div>
                                <div className="aitrader-panel-meta">
                                    {ruleResult ? `${ruleResult.range?.start || "--"} ~ ${ruleResult.range?.end || "--"} · ${ruleResult.source}${ruleStrategyLabel}` : "--"}
                                    {diffSectionIds.size > 0 ? ` · 差异段 ${diffSectionIds.size}` : ""}
                                </div>
                            </div>
                            <div className="aitrader-panel-content">
                                {!ruleResult?.analysis && <div className="aitrader-empty">点击“Rule分析”开始。</div>}
                                {ruleSections.map((section) => {
                                    const isDiff = diffSectionIds.has(section.id);
                                    return (
                                        <section
                                            key={`rule-${section.id}-${section.title}`}
                                            className={`aitrader-section ${isDiff ? "is-diff" : ""}`}
                                        >
                                            <div className="aitrader-section-title">
                                                <span>{section.id !== "0" ? `${section.id}. ${section.title}` : section.title}</span>
                                                {isDiff && <span className="aitrader-diff-tag">差异</span>}
                                            </div>
                                            <div className="aitrader-md">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                    {section.body || "_(空)_"}
                                                </ReactMarkdown>
                                            </div>
                                        </section>
                                    );
                                })}
                                {ruleIndicatorSummary && (() => {
                                    const entries = Object.entries(ruleIndicatorSummary.values || {}).map(([id, data]: [string, any]) => ({
                                        id,
                                        status: data?.status || "unknown",
                                        value: data?.value,
                                        reason: data?.reason,
                                        weight: data?.weight,
                                        scoreContribution: data?.score_contribution,
                                    }));
                                    const filtered = entries.filter(entry => {
                                        const key = indicatorFilterText.trim().toLowerCase();
                                        const hitText = !key || entry.id.toLowerCase().includes(key);
                                        const hitStatus = indicatorFilterStatus === "all" || entry.status === indicatorFilterStatus;
                                        const hitAbnormal = !indicatorOnlyAbnormal || (entry.status !== "ok" && entry.status !== "disabled");
                                        return hitText && hitStatus && hitAbnormal;
                                    });
                                    return (
                                        <div className="aitrader-indicators">
                                            <div className="aitrader-indicators-header">
                                                <div>
                                                    指标值 OK {ruleIndicatorSummary.ok} / ND {ruleIndicatorSummary.noData} / DIS {ruleIndicatorSummary.disabled}
                                                    {ruleIndicatorSummary.error ? ` / ERR ${ruleIndicatorSummary.error}` : ""}
                                                </div>
                                                <div className="aitrader-indicators-filter">
                                                    <input
                                                        value={indicatorFilterText}
                                                        onChange={(e) => setIndicatorFilterText(e.target.value)}
                                                        placeholder="搜索指标ID"
                                                    />
                                                    <select
                                                        value={indicatorFilterStatus}
                                                        onChange={(e) => setIndicatorFilterStatus(e.target.value)}
                                                    >
                                                        <option value="all">全部</option>
                                                        <option value="ok">ok</option>
                                                        <option value="no_data">no_data</option>
                                                        <option value="disabled">disabled</option>
                                                        <option value="error">error</option>
                                                    </select>
                                                    <label className="aitrader-indicators-toggle">
                                                        <input
                                                            type="checkbox"
                                                            checked={indicatorOnlyAbnormal}
                                                            onChange={(e) => setIndicatorOnlyAbnormal(e.target.checked)}
                                                        />
                                                        只看异常
                                                    </label>
                                                    <span>{filtered.length} 条</span>
                                                </div>
                                            </div>
                                            <div className="aitrader-indicators-table">
                                                <div className="aitrader-indicators-row head">
                                                    <div>ID</div>
                                                    <div>状态</div>
                                                    <div>值</div>
                                                    <div>权重/贡献</div>
                                                </div>
                                                {filtered.map(entry => (
                                                    <div key={entry.id} className="aitrader-indicators-row">
                                                        <div>{entry.id}</div>
                                                        <div>{entry.status}</div>
                                                        <div>
                                                            {entry.value === undefined || entry.value === null
                                                                ? (entry.reason ? `(${entry.reason})` : "--")
                                                                : (typeof entry.value === "object" ? JSON.stringify(entry.value) : String(entry.value))}
                                                        </div>
                                                        <div>
                                                            {entry.weight != null || entry.scoreContribution != null
                                                                ? `w:${entry.weight ?? "--"} / s:${entry.scoreContribution ?? "--"}`
                                                                : "--"}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    );
                                })()}
                            </div>
                        </article>

                        <article className="aitrader-panel">
                            <div className="aitrader-panel-head">
                                <div className="aitrader-panel-title">AI</div>
                                <div className="aitrader-panel-meta">
                                    {aiResult ? `${aiResult.range?.start || "--"} ~ ${aiResult.range?.end || "--"} · ${aiResult.model || "default"}` : "--"}
                                    {diffSectionIds.size > 0 ? ` · 差异段 ${diffSectionIds.size}` : ""}
                                </div>
                            </div>
                            <div className="aitrader-panel-content">
                                {!aiResult?.analysis && <div className="aitrader-empty">点击“AI分析”开始。</div>}
                                {aiSections.map((section) => {
                                    const isDiff = diffSectionIds.has(section.id);
                                    return (
                                        <section
                                            key={`ai-${section.id}-${section.title}`}
                                            className={`aitrader-section ${isDiff ? "is-diff" : ""}`}
                                        >
                                            <div className="aitrader-section-title">
                                                <span>{section.id !== "0" ? `${section.id}. ${section.title}` : section.title}</span>
                                                {isDiff && <span className="aitrader-diff-tag">差异</span>}
                                            </div>
                                            <div className="aitrader-md">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                    {section.body || "_(空)_"}
                                                </ReactMarkdown>
                                            </div>
                                        </section>
                                    );
                                })}
                            </div>
                        </article>

                        <article className="aitrader-panel">
                            <div className="aitrader-panel-head">
                                <div className="aitrader-panel-title">Fusion</div>
                                <div className="aitrader-panel-meta">
                                    {fusionResult ? `${fusionResult.range?.start || "--"} ~ ${fusionResult.range?.end || "--"} · rule+ai` : "--"}
                                </div>
                            </div>
                            <div className="aitrader-panel-content">
                                {!fusionResult?.analysis && <div className="aitrader-empty">点击“融合分析”开始。</div>}
                                {fusionResult?.analysis && (
                                    <div className="aitrader-md">
                                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                            {fusionResult.analysis}
                                        </ReactMarkdown>
                                    </div>
                                )}
                            </div>
                        </article>
                    </div>
                </section>
            </div>
        </div>
    );
}
