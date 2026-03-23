import { useEffect, useMemo, useState } from "react";
import { Button } from "@react-spectrum/s2";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || "http://localhost:8000";

type PromptTemplate = {
    id: number;
    name: string;
    prompt: string;
    params?: Record<string, unknown>;
    is_builtin?: boolean;
    created_at?: string;
    updated_at?: string;
};

type LLMModel = {
    id: string;
    name: string;
};

export function PromptLabPage() {
    const [symbol, setSymbol] = useState(() => localStorage.getItem("prompt_lab_symbol") || "");
    const [templates, setTemplates] = useState<PromptTemplate[]>([]);
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const [activeTemplateId, setActiveTemplateId] = useState<number | null>(null);
    const [name, setName] = useState("");
    const [prompt, setPrompt] = useState("");
    const [paramsText, setParamsText] = useState("{}");
    const [error, setError] = useState("");
    const [saving, setSaving] = useState(false);

    const [models, setModels] = useState<LLMModel[]>([]);
    const [selectedModel, setSelectedModel] = useState("");
    const [debugDate, setDebugDate] = useState("");
    const [debugBars, setDebugBars] = useState("365");
    const [debugInput, setDebugInput] = useState("请结合历史日线数据进行分析。");
    const [preview, setPreview] = useState("");
    const [debugOutput, setDebugOutput] = useState("");
    const [debugLoading, setDebugLoading] = useState(false);
    const [previewLoading, setPreviewLoading] = useState(false);

    const selectedTemplate = useMemo(() => templates.find(t => t.id === selectedId) || null, [templates, selectedId]);

    const loadTemplates = () => {
        const param = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
        fetch(`${API_BASE_URL}/api/system_prompts${param}`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data?.data) ? data.data : [];
                setTemplates(items);
                setActiveTemplateId(data?.active_template_id ?? null);
                if (!selectedId && items.length > 0) {
                    setSelectedId(items[0].id);
                }
            })
            .catch(() => setError("加载模板失败"));
    };

    useEffect(() => {
        fetch(`${API_BASE_URL}/api/config/llm_models`)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data?.models) ? data.models : [];
                setModels(list);
                setSelectedModel(data?.current_model || (list[0]?.id || ""));
            })
            .catch(() => {
                // ignore
            });
    }, []);

    useEffect(() => {
        loadTemplates();
    }, [symbol]);

    useEffect(() => {
        if (!selectedTemplate) return;
        setName(selectedTemplate.name || "");
        setPrompt(selectedTemplate.prompt || "");
        const params = selectedTemplate.params || {};
        setParamsText(JSON.stringify(params, null, 2));
    }, [selectedTemplate]);

    useEffect(() => {
        localStorage.setItem("prompt_lab_symbol", symbol || "");
    }, [symbol]);

    const parseParams = () => {
        if (!paramsText.trim()) return {};
        try {
            return JSON.parse(paramsText);
        } catch (e) {
            setError("参数 JSON 格式错误");
            return null;
        }
    };

    const handleSave = async () => {
        if (!selectedId) return;
        const params = parseParams();
        if (params === null) return;
        setSaving(true);
        setError("");
        try {
            const res = await fetch(`${API_BASE_URL}/api/system_prompts/${selectedId}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, prompt, params })
            });
            if (!res.ok) throw new Error("save failed");
            loadTemplates();
        } catch (e) {
            setError("保存失败");
        } finally {
            setSaving(false);
        }
    };

    const handleCreate = async () => {
        const params = parseParams();
        if (params === null) return;
        setSaving(true);
        setError("");
        try {
            const res = await fetch(`${API_BASE_URL}/api/system_prompts`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, prompt, params })
            });
            if (!res.ok) throw new Error("create failed");
            const data = await res.json();
            loadTemplates();
            if (data?.id) {
                setSelectedId(data.id);
            }
        } catch (e) {
            setError("创建失败");
        } finally {
            setSaving(false);
        }
    };

    const handleActivate = async () => {
        if (!selectedId || !symbol) return;
        setSaving(true);
        setError("");
        try {
            const res = await fetch(`${API_BASE_URL}/api/system_prompts/${selectedId}/activate?symbol=${encodeURIComponent(symbol)}`, {
                method: "POST"
            });
            if (!res.ok) throw new Error("activate failed");
            loadTemplates();
        } catch (e) {
            setError("激活失败");
        } finally {
            setSaving(false);
        }
    };

    const handleRender = async () => {
        if (!selectedId) return;
        const params = parseParams();
        if (params === null) return;
        setPreviewLoading(true);
        setPreview("");
        try {
            let klines: any[] | null = null;
            if (symbol) {
                const endDate = debugDate ? debugDate.replace(/-/g, "") : "";
                const dailyRes = await fetch(`${API_BASE_URL}/api/klines/${encodeURIComponent(symbol)}?period=1d&limit=30${endDate ? `&end_date=${endDate}` : ""}`);
                if (dailyRes.ok) {
                    const dailyJson = await dailyRes.json();
                    const rows = Array.isArray(dailyJson?.data) ? dailyJson.data : [];
                    if (rows.length > 0) {
                        klines = rows;
                    }
                }
            }
            const res = await fetch(`${API_BASE_URL}/api/system_prompts/render`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    prompt_id: selectedId,
                    prompt_text: prompt,
                    params,
                    symbol,
                    regression_date: debugDate || null,
                    klines: klines || undefined
                })
            });
            if (!res.ok) throw new Error("render failed");
            const data = await res.json();
            setPreview(data?.prompt || "");
        } catch (e) {
            setPreview("渲染失败");
        } finally {
            setPreviewLoading(false);
        }
    };

    const runDebug = async () => {
        if (!symbol || !selectedId) {
            setDebugOutput("请先选择模板并填写标的代码");
            return;
        }
        const params = parseParams();
        if (params === null) return;
        setDebugLoading(true);
        setDebugOutput("");
        try {
            const endDate = debugDate ? debugDate.replace(/-/g, "") : "";
            const limit = Math.max(1, Number(debugBars) || 365);
            const dailyRes = await fetch(`${API_BASE_URL}/api/klines/${encodeURIComponent(symbol)}?period=1d&limit=${limit}${endDate ? `&end_date=${endDate}` : ""}`);
            if (!dailyRes.ok) throw new Error("kline fetch failed");
            const dailyJson = await dailyRes.json();
            const klines = Array.isArray(dailyJson?.data) ? dailyJson.data : [];
            if (klines.length === 0) {
                setDebugOutput("未获取到日线数据");
                return;
            }

            const res = await fetch(`${API_BASE_URL}/api/analyze`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol,
                    klines,
                    model: selectedModel,
                    user_input: debugInput,
                    mode: debugDate ? "regression" : "assistant",
                    regression_date: endDate || debugDate || null,
                    prompt_id: selectedId,
                    prompt_params: params,
                    context_config: {
                        enable_memory: false,
                        enable_retrieval: false,
                        history_include_assistant: false,
                        memory_include_assistant: false,
                        disable_history: true,
                        disable_indicator_context: true,
                        save_history: false,
                        chat_use_daily: true,
                        kline_rows_chat: limit,
                        kline_rows_assistant: limit
                    }
                })
            });
            if (!res.ok || !res.body) throw new Error("analyze failed");
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let acc = "";
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                acc += decoder.decode(value, { stream: true });
                setDebugOutput(acc);
            }
            setDebugOutput(acc);
        } catch (e) {
            setDebugOutput("调试失败");
        } finally {
            setDebugLoading(false);
        }
    };

    return (
        <div style={{ background: "#f4f6fb", minHeight: "100vh", padding: "24px" }}>
            <div style={{ maxWidth: "1200px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "16px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                        <div style={{ fontWeight: 700, fontSize: "18px" }}>Prompt 实验室</div>
                        <div style={{ fontSize: "12px", color: "#666", marginTop: "4px" }}>
                            编辑模板参数并进行历史回归测试。
                        </div>
                    </div>
                    <Button onPress={loadTemplates}>刷新</Button>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: "12px" }}>
                    <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", padding: "12px", height: "fit-content" }}>
                        <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>模板列表</div>
                        <label style={{ fontSize: "12px", display: "block", marginBottom: "8px" }}>
                            当前标的（激活/回归）
                            <input
                                value={symbol}
                                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                                placeholder="000001.SZ"
                                style={{ width: "100%", padding: "6px 8px", fontSize: "12px", marginTop: "4px" }}
                            />
                        </label>
                        <select
                            value={selectedId ? String(selectedId) : ""}
                            onChange={(e) => setSelectedId(Number(e.target.value) || null)}
                            style={{ width: "100%", padding: "6px 8px", fontSize: "12px" }}
                        >
                            <option value="">请选择模板</option>
                            {templates.map(t => (
                                <option key={t.id} value={t.id}>
                                    {t.name}{t.id === activeTemplateId ? " · 当前" : ""}{t.is_builtin ? " · 内置" : ""}
                                </option>
                            ))}
                        </select>

                        <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "12px" }}>
                            <label style={{ fontSize: "12px" }}>
                                名称
                                <input
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    style={{ width: "100%", padding: "6px 8px", fontSize: "12px", marginTop: "4px" }}
                                />
                            </label>
                            <label style={{ fontSize: "12px" }}>
                                参数(JSON)
                                <textarea
                                    rows={6}
                                    value={paramsText}
                                    onChange={(e) => setParamsText(e.target.value)}
                                    style={{ width: "100%", padding: "6px 8px", fontSize: "12px", marginTop: "4px", fontFamily: "monospace" }}
                                />
                            </label>
                            <label style={{ fontSize: "12px" }}>
                                Prompt 模板
                                <textarea
                                    rows={10}
                                    value={prompt}
                                    onChange={(e) => setPrompt(e.target.value)}
                                    style={{ width: "100%", padding: "6px 8px", fontSize: "12px", marginTop: "4px", fontFamily: "monospace" }}
                                />
                            </label>
                            {error && <div style={{ fontSize: "12px", color: "#c00" }}>{error}</div>}
                            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                                <Button onPress={handleSave} isDisabled={!selectedId || saving}>保存</Button>
                                <Button onPress={handleCreate} isDisabled={saving}>新建</Button>
                                <Button onPress={handleActivate} isDisabled={!selectedId || !symbol || saving}>设为当前标的</Button>
                            </div>
                        </div>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                        <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", padding: "12px" }}>
                            <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>回归调试</div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "center" }}>
                                <label style={{ fontSize: "12px" }}>
                                    回归日期
                                    <input
                                        type="date"
                                        value={debugDate}
                                        onChange={(e) => setDebugDate(e.target.value)}
                                        style={{ marginLeft: "6px", padding: "4px 6px", fontSize: "12px" }}
                                    />
                                </label>
                                <label style={{ fontSize: "12px" }}>
                                    K线数量
                                    <input
                                        value={debugBars}
                                        onChange={(e) => setDebugBars(e.target.value)}
                                        style={{ marginLeft: "6px", padding: "4px 6px", fontSize: "12px", width: "80px" }}
                                    />
                                </label>
                                <label style={{ fontSize: "12px" }}>
                                    模型
                                    <select
                                        value={selectedModel}
                                        onChange={(e) => setSelectedModel(e.target.value)}
                                        style={{ marginLeft: "6px", padding: "4px 6px", fontSize: "12px" }}
                                    >
                                        {models.map(m => (
                                            <option key={m.id} value={m.id}>{m.name}</option>
                                        ))}
                                    </select>
                                </label>
                                <Button onPress={handleRender} isDisabled={!selectedId || previewLoading}>
                                    {previewLoading ? "渲染中..." : "预览Prompt"}
                                </Button>
                                <Button onPress={runDebug} isDisabled={!selectedId || debugLoading}>
                                    {debugLoading ? "调试中..." : "运行回归"}
                                </Button>
                            </div>
                            <label style={{ fontSize: "12px", marginTop: "8px", display: "block" }}>
                                用户问题
                                <textarea
                                    rows={3}
                                    value={debugInput}
                                    onChange={(e) => setDebugInput(e.target.value)}
                                    style={{ width: "100%", padding: "6px 8px", fontSize: "12px", marginTop: "4px" }}
                                />
                            </label>
                        </div>

                        <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", padding: "12px" }}>
                            <div style={{ fontWeight: 600, fontSize: "12px", marginBottom: "8px" }}>渲染后的 Prompt</div>
                            <pre style={{ whiteSpace: "pre-wrap", fontSize: "11px", background: "#f8f9fb", padding: "8px", borderRadius: "6px" }}>
                                {preview || "--"}
                            </pre>
                        </div>

                        <div style={{ background: "#fff", border: "1px solid #e6e8ee", borderRadius: "10px", padding: "12px" }}>
                            <div style={{ fontWeight: 600, fontSize: "12px", marginBottom: "8px" }}>调试输出</div>
                            {!debugOutput && <div style={{ fontSize: "12px", color: "#888" }}>暂无输出</div>}
                            {debugOutput && (
                                <div style={{ fontSize: "12px" }}>
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{debugOutput}</ReactMarkdown>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
