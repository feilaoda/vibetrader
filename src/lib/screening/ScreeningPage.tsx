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
};

export function ScreeningPage() {
    const [runs, setRuns] = useState<ScreeningRun[]>([]);
    const [selectedRun, setSelectedRun] = useState<number | null>(null);
    const [results, setResults] = useState<ScreeningResult[]>([]);
    const [actionFilter, setActionFilter] = useState("");
    const [search, setSearch] = useState("");
    const [summary, setSummary] = useState<Record<string, number>>({});
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [deletingRunId, setDeletingRunId] = useState<number | null>(null);
    const [symbolNames, setSymbolNames] = useState<Record<string, string>>({});
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

    useEffect(() => {
        loadRuns();
    }, []);

    useEffect(() => {
        if (!selectedRun) return;
        loadSummary(selectedRun);
        loadResults(selectedRun, actionFilter);
    }, [selectedRun, actionFilter]);

    const filteredResults = useMemo(() => {
        if (!search) return results;
        const text = search.trim().toUpperCase();
        return results.filter(item => item.symbol.toUpperCase().includes(text) || (item.reason || "").toUpperCase().includes(text));
    }, [results, search]);

    const selectedRunMeta = runs.find(r => r.id === selectedRun);

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
                                    状态: {selectedRunMeta?.status || "--"}
                                </div>
                                <div style={{ fontSize: "12px", color: "#666" }}>
                                    BUY: {summary.BUY || 0} / WATCH: {summary.WATCH || 0} / SKIP: {summary.SKIP || 0}
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
                                </div>
                            </div>
                        </div>

                        <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", overflow: "hidden" }}>
                            <div style={{ display: "grid", gridTemplateColumns: "200px 90px 90px 140px 1fr", padding: "10px 12px", background: "#f7f8fb", fontSize: "12px", color: "#666" }}>
                                <div>Symbol</div>
                                <div>Action</div>
                                <div>Score</div>
                                <div>Model</div>
                                <div>Reason</div>
                            </div>
                            {loading && <div style={{ padding: "12px", fontSize: "12px", color: "#666" }}>加载中...</div>}
                            {error && <div style={{ padding: "12px", fontSize: "12px", color: "#c00" }}>{error}</div>}
                            {!loading && filteredResults.map(item => (
                                <div key={item.symbol} style={{ display: "grid", gridTemplateColumns: "200px 90px 90px 140px 1fr", padding: "10px 12px", borderTop: "1px solid #f0f0f0", fontSize: "12px" }}>
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
                                    <div style={{ color: "#666" }}>{item.model_id || selectedRunMeta?.model_id || "--"}</div>
                                    <div style={{ color: "#555" }}>{item.reason}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
