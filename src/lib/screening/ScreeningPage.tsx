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
            })
            .catch(err => {
                console.error(err);
                setError("加载筛选结果失败");
            })
            .finally(() => setLoading(false));
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
                                    <div style={{ fontWeight: 600 }}>Run #{run.id}</div>
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
                            <div style={{ display: "grid", gridTemplateColumns: "140px 90px 90px 140px 1fr", padding: "10px 12px", background: "#f7f8fb", fontSize: "12px", color: "#666" }}>
                                <div>Symbol</div>
                                <div>Action</div>
                                <div>Score</div>
                                <div>Model</div>
                                <div>Reason</div>
                            </div>
                            {loading && <div style={{ padding: "12px", fontSize: "12px", color: "#666" }}>加载中...</div>}
                            {error && <div style={{ padding: "12px", fontSize: "12px", color: "#c00" }}>{error}</div>}
                            {!loading && filteredResults.map(item => (
                                <div key={item.symbol} style={{ display: "grid", gridTemplateColumns: "140px 90px 90px 140px 1fr", padding: "10px 12px", borderTop: "1px solid #f0f0f0", fontSize: "12px" }}>
                                    <div>{item.symbol}</div>
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
