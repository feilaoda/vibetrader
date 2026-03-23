import { useEffect, useMemo, useState } from "react";
import { Button } from "@react-spectrum/s2";

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || "http://localhost:8000";

type ScreeningRun = {
    id: number;
    status: string;
    model_id: string;
    universe: string;
    total?: number;
    processed?: number;
    started_at?: string;
    finished_at?: string;
    params?: Record<string, unknown>;
};

type ScreeningResult = {
    symbol: string;
    action: string;
    score: number;
    reason: string;
    model_id?: string;
    raw?: Record<string, any>;
    rule_action?: string;
    rule_stage?: string;
    rule_rr?: number;
    rule_rr_up?: number;
    rule_rr_down?: number;
    rule_rr_threshold?: number;
    rule_confidence?: number;
    rule_trend_score?: number;
    rule_structure_score?: number;
    rule_volume_score?: number;
    rule_rr_score?: number;
    rule_total_score?: number;
    rule_risk_gates?: string;
    ai_action?: string;
    ai_reason?: string;
    ai_risk?: string;
    ai_model?: string;
};

type LLMModel = {
    id: string;
    name: string;
};

type ScreeningStats = {
    total?: number;
    action_counts?: Record<string, number>;
    rule_action_counts?: Record<string, number>;
    rule_stage_counts?: Record<string, number>;
    ai_action_counts?: Record<string, number>;
    risk_gate_counts?: Record<string, number>;
    score_hist?: Record<string, number>;
    rule_score_hist?: Record<string, number>;
    avg_score?: number;
    avg_rule_score?: number;
};

export function ScreeningPage() {
    const [runs, setRuns] = useState<ScreeningRun[]>([]);
    const [selectedRun, setSelectedRun] = useState<number | null>(null);
    const [results, setResults] = useState<ScreeningResult[]>([]);
    const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
    const [actionFilter, setActionFilter] = useState("");
    const [search, setSearch] = useState("");
    const [summary, setSummary] = useState<Record<string, number>>({});
    const [stats, setStats] = useState<ScreeningStats | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [deletingRunId, setDeletingRunId] = useState<number | null>(null);
    const [symbolNames, setSymbolNames] = useState<Record<string, string>>({});
    const [engine, setEngine] = useState("fusion");
    const [aiEnabled, setAiEnabled] = useState(true);
    const [engineBeforeDisable, setEngineBeforeDisable] = useState("fusion");
    const [universe, setUniverse] = useState("watchlist");
    const [market, setMarket] = useState("all");
    const [limit, setLimit] = useState("200");
    const [ruleMinScore, setRuleMinScore] = useState("60");
    const [aiTopK, setAiTopK] = useState("20");
    const [useWeb, setUseWeb] = useState(false);
    const [models, setModels] = useState<LLMModel[]>([]);
    const [selectedModel, setSelectedModel] = useState("");
    const [runMsg, setRunMsg] = useState("");
    const [runLoading, setRunLoading] = useState(false);
    const [indicatorFilterText, setIndicatorFilterText] = useState("");
    const [indicatorFilterStatus, setIndicatorFilterStatus] = useState("all");
    const [indicatorOnlyAbnormal, setIndicatorOnlyAbnormal] = useState(false);
    const [onlyIndicatorAbnormalRows, setOnlyIndicatorAbnormalRows] = useState(false);
    const appBaseUrl = (import.meta as unknown as { env: Record<string, string> }).env?.BASE_URL || "/";

    const detectMarket = (symbol: string) => {
        const upper = (symbol || "").toUpperCase();
        if (upper.endsWith(".SZ") || upper.endsWith(".SH")) return "ashare";
        if (upper.endsWith(".US")) return "us";
        return "crypto";
    };

    const buildSymbolLink = (symbol: string) => {
        const base = appBaseUrl.endsWith("/") ? appBaseUrl : `${appBaseUrl}/`;
        const root = base || "/";
        const market = detectMarket(symbol);
        return `${root}?symbol=${encodeURIComponent(symbol)}&market=${market}`;
    };

    const resolveSymbolNames = (items: ScreeningResult[]) => {
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
        const limit = Math.min(50, toFetch.length);
        toFetch.slice(0, limit).forEach(symbol => {
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
    };

    const loadRuns = () => {
        fetch(`${API_BASE_URL}/api/screening/runs?limit=50`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data?.data) ? data.data : [];
                setRuns(items);
                if (!selectedRun && items.length > 0) {
                    setSelectedRun(items[0].id);
                }
            })
            .catch(err => {
                console.error(err);
                setError("加载筛选任务失败");
            });
    };

    const deleteRun = (runId: number) => {
        if (!window.confirm(`删除 Run #${runId} 吗？此操作会删除对应的全部筛选结果。`)) return;
        setDeletingRunId(runId);
        fetch(`${API_BASE_URL}/api/screening/runs/${runId}`, { method: "DELETE" })
            .then(res => {
                if (!res.ok) throw new Error("delete failed");
                return res.json();
            })
            .then(() => {
                const nextRuns = runs.filter(run => run.id !== runId);
                setRuns(nextRuns);
                if (selectedRun === runId) {
                    const nextId = nextRuns.length > 0 ? nextRuns[0].id : null;
                    setSelectedRun(nextId);
                    setResults([]);
                    setSummary({});
                    setStats(null);
                }
            })
            .catch(err => {
                console.error(err);
                setError("删除任务失败");
            })
            .finally(() => setDeletingRunId(null));
    };

    const loadSummary = (runId: number) => {
        fetch(`${API_BASE_URL}/api/screening/summary?run_id=${runId}`)
            .then(res => res.json())
            .then(data => setSummary(data?.data || {}))
            .catch(() => setSummary({}));
    };

    const loadStats = (runId: number) => {
        fetch(`${API_BASE_URL}/api/screening/stats?run_id=${runId}`)
            .then(res => res.json())
            .then(data => setStats(data?.data || {}))
            .catch(() => setStats(null));
    };

    const loadResults = (runId: number, action: string) => {
        setLoading(true);
        setError("");
        const params = new URLSearchParams();
        params.set("run_id", String(runId));
        if (action) params.set("action", action);
        params.set("limit", "500");
        fetch(`${API_BASE_URL}/api/screening/results?${params.toString()}`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data?.data) ? data.data : [];
                setResults(items);
                resolveSymbolNames(items);
            })
            .catch(err => {
                console.error(err);
                setError("加载筛选结果失败");
            })
            .finally(() => setLoading(false));
    };

    const deleteFailedResults = (runId: number) => {
        if (!window.confirm("只删除该任务中的 ERROR/NO_DATA/UNKNOWN 结果吗？")) return;
        setError("");
        fetch(`${API_BASE_URL}/api/screening/results?run_id=${runId}&actions=ERROR,NO_DATA,UNKNOWN`, { method: "DELETE" })
            .then(res => {
                if (!res.ok) throw new Error("delete failed");
                return res.json();
            })
            .then(() => {
                loadSummary(runId);
                loadResults(runId, actionFilter);
            })
            .catch(err => {
                console.error(err);
                setError("删除失败结果失败");
            });
    };

    const selectedRunMeta = runs.find(r => r.id === selectedRun);
    const aiEngine = engine === "ai" || engine === "fusion";
    const isRunning = selectedRunMeta?.status === "running";

    const formatCounts = (counts?: Record<string, number>, limit = 4) => {
        if (!counts) return "--";
        const items = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, limit);
        if (items.length === 0) return "--";
        return items.map(([k, v]) => `${k}:${v}`).join(" / ");
    };

    const formatGates = (text?: string) => {
        if (!text) return "";
        const trimmed = text.trim();
        if (!trimmed) return "";
        try {
            const parsed = JSON.parse(trimmed);
            if (Array.isArray(parsed)) {
                return parsed.slice(0, 3).join("、");
            }
        } catch {
            // ignore
        }
        return trimmed;
    };

    const clampText = (text?: string, max = 60) => {
        const value = (text || "").trim();
        if (!value) return "";
        return value.length > max ? `${value.slice(0, max)}…` : value;
    };

    const formatThreshold = (up?: number, down?: number, threshold?: number) => {
        const fmt = (v?: number) => (typeof v === "number" ? v.toFixed(2) : "--");
        if (typeof threshold === "number") {
            return fmt(threshold);
        }
        if (typeof up === "number" || typeof down === "number") {
            return `U:${fmt(up)} D:${fmt(down)}`;
        }
        return "--";
    };

    const summarizeIndicators = (raw?: Record<string, any>) => {
        const values = raw?.rule?.indicator_values || raw?.rule_meta?.indicator_values;
        if (!values || typeof values !== "object") return null;
        let ok = 0;
        let noData = 0;
        let disabled = 0;
        let error = 0;
        Object.values(values as Record<string, any>).forEach((entry: any) => {
            const status = entry?.status;
            if (status === "ok") ok += 1;
            else if (status === "no_data") noData += 1;
            else if (status === "disabled") disabled += 1;
            else if (status === "error") error += 1;
        });
        return { ok, noData, disabled, error, values };
    };

    useEffect(() => {
        loadRuns();
    }, []);

    useEffect(() => {
        fetch(`${API_BASE_URL}/api/config/llm_models`)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data?.models) ? data.models : [];
                setModels(list);
                if (data?.current_model && !selectedModel) {
                    setSelectedModel(data.current_model);
                } else if (!selectedModel && list.length > 0) {
                    setSelectedModel(list[0].id);
                }
            })
            .catch(() => {
                // ignore
            });
    }, []);

    useEffect(() => {
        if (!selectedRun) return;
        loadSummary(selectedRun);
        loadStats(selectedRun);
        loadResults(selectedRun, actionFilter);
        setExpandedSymbol(null);
        setIndicatorFilterText("");
        setIndicatorFilterStatus("all");
        setIndicatorOnlyAbnormal(false);
        setOnlyIndicatorAbnormalRows(false);
    }, [selectedRun, actionFilter]);

    useEffect(() => {
        if (!isRunning || !selectedRun) return;
        const timer = window.setInterval(() => {
            loadRuns();
            loadSummary(selectedRun);
            loadStats(selectedRun);
            loadResults(selectedRun, actionFilter);
        }, 4000);
        return () => window.clearInterval(timer);
    }, [isRunning, selectedRun, actionFilter]);

    const filteredResults = useMemo(() => {
        const text = search.trim().toUpperCase();
        const baseFiltered = results.filter(item => {
            if (!text) return true;
            return item.symbol.toUpperCase().includes(text) || (item.reason || "").toUpperCase().includes(text);
        });
        if (!onlyIndicatorAbnormalRows) return baseFiltered;
        return baseFiltered.filter(item => {
            const summary = summarizeIndicators(item.raw);
            if (!summary) return false;
            return (summary.noData + summary.error) > 0;
        });
    }, [results, search, onlyIndicatorAbnormalRows]);

    useEffect(() => {
        if (!aiEnabled) {
            setEngineBeforeDisable(engine);
            setEngine("rule");
        } else if (engine === "rule") {
            setEngine(engineBeforeDisable || "fusion");
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [aiEnabled]);

    const startRun = async () => {
        setRunMsg("");
        setRunLoading(true);
        try {
            const payload: Record<string, unknown> = {
                engine: aiEnabled ? engine : "rule",
                universe,
                market,
                use_web: useWeb,
                limit: Number(limit) || undefined,
                rule_min_score: Number(ruleMinScore) || 60,
                ai_top_k: Number(aiTopK) || 0,
            };
            if (aiEnabled && aiEngine && selectedModel) {
                payload.model = selectedModel;
            }
            const res = await fetch(`${API_BASE_URL}/api/screening/run`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!res.ok) throw new Error("start failed");
            const data = await res.json();
            const runId = data?.run_id;
            setRunMsg(runId ? `任务已启动 (#${runId})` : "任务已启动");
            loadRuns();
            if (runId) {
                setSelectedRun(runId);
                loadSummary(runId);
                loadResults(runId, actionFilter);
            }
        } catch (err) {
            console.error(err);
            setRunMsg("启动失败");
        } finally {
            setRunLoading(false);
        }
    };

    return (
        <div style={{ background: "#f4f6fb", minHeight: "100vh", padding: "24px" }}>
            <div style={{ maxWidth: "1200px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "16px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                        <div style={{ fontWeight: 700, fontSize: "18px" }}>AI 批量筛选</div>
                        <div style={{ fontSize: "12px", color: "#666", marginTop: "4px" }}>
                            独立筛选结果，不写入聊天记忆。
                        </div>
                    </div>
                    <Button onPress={() => loadRuns()}>刷新</Button>
                </div>

                <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", padding: "12px" }}>
                    <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>AI荐股任务</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "center" }}>
                        <label style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px" }}>
                            <input type="checkbox" checked={aiEnabled} onChange={(e) => setAiEnabled(e.target.checked)} />
                            启用AI
                        </label>
                        <select value={engine} onChange={(e) => setEngine(e.target.value)} style={{ padding: "6px 8px", fontSize: "12px" }} disabled={!aiEnabled}>
                            <option value="fusion">融合</option>
                            <option value="rule">Rule</option>
                            <option value="ai">AI</option>
                        </select>
                        <select value={universe} onChange={(e) => setUniverse(e.target.value)} style={{ padding: "6px 8px", fontSize: "12px" }}>
                            <option value="watchlist">自选股</option>
                            <option value="all">全市场</option>
                        </select>
                        <select value={market} onChange={(e) => setMarket(e.target.value)} style={{ padding: "6px 8px", fontSize: "12px" }}>
                            <option value="all">全市场</option>
                            <option value="ashare">A股</option>
                            <option value="etf">ETF</option>
                            <option value="us">美股</option>
                            <option value="crypto">加密</option>
                        </select>
                        <input
                            value={limit}
                            onChange={(e) => setLimit(e.target.value)}
                            placeholder="扫描数量"
                            style={{ padding: "6px 8px", fontSize: "12px", width: "90px" }}
                        />
                        <input
                            value={ruleMinScore}
                            onChange={(e) => setRuleMinScore(e.target.value)}
                            placeholder="Rule最低分"
                            style={{ padding: "6px 8px", fontSize: "12px", width: "90px" }}
                        />
                        <input
                            value={aiTopK}
                            onChange={(e) => setAiTopK(e.target.value)}
                            placeholder="AI处理数"
                            style={{ padding: "6px 8px", fontSize: "12px", width: "90px" }}
                            disabled={!aiEnabled || !aiEngine}
                        />
                        <label style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px" }}>
                            <input type="checkbox" checked={useWeb} onChange={(e) => setUseWeb(e.target.checked)} disabled={!aiEnabled || !aiEngine} />
                            web搜索
                        </label>
                        <select
                            value={selectedModel}
                            onChange={(e) => setSelectedModel(e.target.value)}
                            style={{ padding: "6px 8px", fontSize: "12px", minWidth: "160px" }}
                            disabled={!aiEnabled || !aiEngine}
                        >
                            {models.length === 0 && <option value="">默认模型</option>}
                            {models.map(m => (
                                <option key={m.id} value={m.id}>{m.name}</option>
                            ))}
                        </select>
                        <Button onPress={startRun} isDisabled={runLoading}>
                            {runLoading ? "启动中..." : "开始荐股"}
                        </Button>
                        {runMsg && <span style={{ fontSize: "12px", color: "#666" }}>{runMsg}</span>}
                    </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: "12px" }}>
                    <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", padding: "12px", height: "fit-content" }}>
                        <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>筛选任务</div>
                        <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "420px", overflowY: "auto" }}>
                            {runs.map(run => (
                                <button
                                    key={run.id}
                                    onClick={() => setSelectedRun(run.id)}
                                    style={{
                                        textAlign: "left",
                                        padding: "8px",
                                        borderRadius: "8px",
                                        border: run.id === selectedRun ? "1px solid #007acc" : "1px solid #e5e7ef",
                                        background: run.id === selectedRun ? "#e8f3ff" : "#f8f9fd",
                                        fontSize: "12px",
                                        cursor: "pointer"
                                    }}
                                >
                                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                                        <div style={{ fontWeight: 600 }}>Run #{run.id}</div>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                deleteRun(run.id);
                                            }}
                                            disabled={deletingRunId === run.id}
                                            style={{
                                                border: "none",
                                                background: "transparent",
                                                color: "#c00",
                                                fontSize: "12px",
                                                cursor: "pointer"
                                            }}
                                            title="删除该任务"
                                        >
                                            {deletingRunId === run.id ? "删除中..." : "删除"}
                                        </button>
                                    </div>
                                    <div style={{ color: "#666" }}>{run.universe} · {run.model_id}</div>
                                    <div style={{ color: "#999" }}>
                                        {run.processed ?? 0}/{run.total ?? 0} · {run.status}
                                    </div>
                                </button>
                            ))}
                        </div>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                        <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", padding: "12px" }}>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center" }}>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    任务: {selectedRunMeta ? `#${selectedRunMeta.id} (${selectedRunMeta.universe})` : "--"}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    状态: {selectedRunMeta?.status || "--"}{selectedRunMeta?.status === "running" ? " (自动刷新中)" : ""}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    进度: {selectedRunMeta?.processed ?? 0} / {selectedRunMeta?.total ?? 0}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    BUY: {summary.BUY || 0} / WATCH: {summary.WATCH || 0} / SKIP: {summary.SKIP || 0}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    Rule均分: {stats?.avg_rule_score ?? "--"} / 总均分: {stats?.avg_score ?? "--"}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    评分分布: {formatCounts(stats?.rule_score_hist, 4)}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    阶段: {formatCounts(stats?.rule_stage_counts, 4)}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    风险闸门: {formatCounts(stats?.risk_gate_counts, 4)}
                                </div>
                                <div style={{ marginLeft: "auto", display: "flex", gap: "8px", alignItems: "center" }}>
                                    <Button variant="secondary" onPress={() => selectedRun && deleteFailedResults(selectedRun)} isDisabled={!selectedRun}>
                                        清理失败
                                    </Button>
                                    <select
                                        value={actionFilter}
                                        onChange={(e) => setActionFilter(e.target.value)}
                                        style={{ padding: "6px 8px", fontSize: "12px" }}
                                    >
                                        <option value="">全部</option>
                                        <option value="BUY">BUY</option>
                                        <option value="WATCH">WATCH</option>
                                        <option value="SKIP">SKIP</option>
                                        <option value="NO_DATA">NO_DATA</option>
                                        <option value="ERROR">ERROR</option>
                                    </select>
                                    <input
                                        value={search}
                                        onChange={(e) => setSearch(e.target.value)}
                                        placeholder="搜索代码/理由"
                                        style={{ padding: "6px 8px", fontSize: "12px" }}
                                    />
                                    <label style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px", color: "#555" }}>
                                        <input
                                            type="checkbox"
                                            checked={onlyIndicatorAbnormalRows}
                                            onChange={(e) => setOnlyIndicatorAbnormalRows(e.target.checked)}
                                        />
                                        只看指标异常
                                    </label>
                                </div>
                            </div>
                        </div>

                        <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", overflowX: "auto" }}>
                            <div style={{ display: "grid", gridTemplateColumns: "200px 80px 80px 110px 120px 220px 160px 1fr 1fr", padding: "10px 12px", background: "#f7f8fb", fontSize: "12px", color: "#666", minWidth: "1320px" }}>
                                <div>Symbol</div>
                                <div>Action</div>
                                <div>Score</div>
                                <div>阈值</div>
                                <div>Model</div>
                                <div>Rule</div>
                                <div>指标</div>
                                <div>AI</div>
                                <div>Reason</div>
                            </div>
                            {loading && <div style={{ padding: "12px", fontSize: "12px", color: "#666" }}>加载中...</div>}
                            {error && <div style={{ padding: "12px", fontSize: "12px", color: "#c00" }}>{error}</div>}
                            {!loading && filteredResults.map(item => {
                                const indicatorSummary = summarizeIndicators(item.raw);
                                const indicatorText = indicatorSummary
                                    ? `OK ${indicatorSummary.ok} / ND ${indicatorSummary.noData} / DIS ${indicatorSummary.disabled}${indicatorSummary.error ? ` / ERR ${indicatorSummary.error}` : ""}`
                                    : "--";
                                const isExpanded = expandedSymbol === item.symbol;
                                const indicatorEntries = indicatorSummary
                                    ? Object.entries(indicatorSummary.values || {}).map(([id, data]: [string, any]) => ({
                                        id,
                                        status: data?.status || "unknown",
                                        value: data?.value,
                                        reason: data?.reason,
                                        weight: data?.weight,
                                        scoreContribution: data?.score_contribution,
                                    }))
                                    : [];
                                const filteredIndicators = indicatorEntries.filter((entry) => {
                                    const text = indicatorFilterText.trim().toLowerCase();
                                    const hitText = !text || entry.id.toLowerCase().includes(text);
                                    const hitStatus = indicatorFilterStatus === "all" || entry.status === indicatorFilterStatus;
                                    const hitAbnormal = !indicatorOnlyAbnormal || (entry.status !== "ok" && entry.status !== "disabled");
                                    return hitText && hitStatus && hitAbnormal;
                                });

                                return (
                                    <div key={item.symbol}>
                                        <div style={{ display: "grid", gridTemplateColumns: "200px 80px 80px 110px 120px 220px 160px 1fr 1fr", padding: "10px 12px", borderTop: "1px solid #f0f0f0", fontSize: "12px", minWidth: "1320px" }}>
                                            <div>
                                                <a
                                                    href={buildSymbolLink(item.symbol)}
                                                    target="_blank"
                                                    rel="noreferrer"
                                                    style={{ color: "#007acc", textDecoration: "none" }}
                                                >
                                                    {item.symbol}{symbolNames[item.symbol] ? ` ${symbolNames[item.symbol]}` : ""}
                                                </a>
                                            </div>
                                            <div>{item.action}</div>
                                            <div>{item.score ?? 0}</div>
                                            <div style={{ color: "#555" }}>{formatThreshold(item.rule_rr_up, item.rule_rr_down, item.rule_rr_threshold)}</div>
                                            <div style={{ color: "#666" }}>{item.model_id || selectedRunMeta?.model_id || "--"}</div>
                                            <div style={{ color: "#555", fontSize: "11px", lineHeight: 1.4 }}>
                                                <div>{item.rule_action || "--"} · {item.rule_stage || "--"}</div>
                                                <div>RR {item.rule_rr ?? "--"} · Score {item.rule_total_score ?? item.score ?? "--"}</div>
                                                {formatGates(item.rule_risk_gates) && <div>Gate: {formatGates(item.rule_risk_gates)}</div>}
                                            </div>
                                            <div style={{ color: "#555", fontSize: "11px", lineHeight: 1.4 }}>
                                                <button
                                                    onClick={() => {
                                                        const next = isExpanded ? null : item.symbol;
                                                        setExpandedSymbol(next);
                                                        if (!isExpanded) {
                                                            setIndicatorFilterText("");
                                                            setIndicatorFilterStatus("all");
                                                            setIndicatorOnlyAbnormal(false);
                                                        }
                                                    }}
                                                    style={{
                                                        background: "transparent",
                                                        border: "none",
                                                        color: "#007acc",
                                                        cursor: indicatorSummary ? "pointer" : "default",
                                                        padding: 0,
                                                        fontSize: "11px",
                                                    }}
                                                    disabled={!indicatorSummary}
                                                >
                                                    {indicatorText}
                                                </button>
                                            </div>
                                            <div style={{ color: "#555", fontSize: "11px", lineHeight: 1.4 }}>
                                                <div>{item.ai_action || "--"}</div>
                                                {clampText(item.ai_reason, 80) && <div>{clampText(item.ai_reason, 80)}</div>}
                                                {clampText(item.ai_risk, 60) && <div>Risk: {clampText(item.ai_risk, 60)}</div>}
                                            </div>
                                            <div style={{ color: "#555" }}>{item.reason}</div>
                                        </div>
                                        {isExpanded && indicatorSummary && (
                                            <div style={{ padding: "8px 12px", background: "#f9fafc", borderTop: "1px dashed #eef0f4", fontSize: "11px" }}>
                                                <div style={{ fontWeight: 600, marginBottom: "6px" }}>指标值明细</div>
                                                <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "8px" }}>
                                                    <input
                                                        value={indicatorFilterText}
                                                        onChange={(e) => setIndicatorFilterText(e.target.value)}
                                                        placeholder="搜索指标ID"
                                                        style={{ padding: "4px 6px", fontSize: "11px" }}
                                                    />
                                                    <select
                                                        value={indicatorFilterStatus}
                                                        onChange={(e) => setIndicatorFilterStatus(e.target.value)}
                                                        style={{ padding: "4px 6px", fontSize: "11px" }}
                                                    >
                                                        <option value="all">全部状态</option>
                                                        <option value="ok">ok</option>
                                                        <option value="no_data">no_data</option>
                                                        <option value="disabled">disabled</option>
                                                        <option value="error">error</option>
                                                    </select>
                                                    <label style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "11px", color: "#555" }}>
                                                        <input
                                                            type="checkbox"
                                                            checked={indicatorOnlyAbnormal}
                                                            onChange={(e) => setIndicatorOnlyAbnormal(e.target.checked)}
                                                        />
                                                        只看异常
                                                    </label>
                                                    <div style={{ color: "#888" }}>{filteredIndicators.length} 条</div>
                                                </div>
                                                <div style={{ display: "grid", gridTemplateColumns: "220px 80px 1fr 140px", gap: "8px" }}>
                                                    <div style={{ fontWeight: 600, color: "#666" }}>ID</div>
                                                    <div style={{ fontWeight: 600, color: "#666" }}>状态</div>
                                                    <div style={{ fontWeight: 600, color: "#666" }}>值</div>
                                                    <div style={{ fontWeight: 600, color: "#666" }}>权重/贡献</div>
                                                </div>
                                                {filteredIndicators.map(entry => (
                                                    <div key={entry.id} style={{ display: "grid", gridTemplateColumns: "220px 80px 1fr 140px", gap: "8px", padding: "4px 0", borderTop: "1px solid #eef0f4" }}>
                                                        <div>{entry.id}</div>
                                                        <div>{entry.status}</div>
                                                        <div style={{ color: "#555" }}>
                                                            {entry.value === undefined || entry.value === null
                                                                ? (entry.reason ? `(${entry.reason})` : "--")
                                                                : (typeof entry.value === "object" ? JSON.stringify(entry.value) : String(entry.value))}
                                                        </div>
                                                        <div style={{ color: "#555" }}>
                                                            {entry.weight != null || entry.scoreContribution != null
                                                                ? `w:${entry.weight ?? "--"} / s:${entry.scoreContribution ?? "--"}`
                                                                : "--"}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
