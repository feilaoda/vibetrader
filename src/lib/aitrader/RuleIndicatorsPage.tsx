import { useEffect, useMemo, useState } from "react";
import { Button } from "@react-spectrum/s2";
import { useNavigate } from "react-router";
import "./RuleIndicatorsPage.css";

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || "http://localhost:8000";

type RuleIndicator = {
    indicator_id: string;
    name: string;
    category: string;
    description?: string;
    formula?: string;
    data_source?: string;
    enabled: boolean;
    default_enabled?: boolean;
    params?: Record<string, unknown>;
};

type ScoreBucketMapping = Record<string, string>;

const MARKET_OPTIONS = [
    { value: "ashare", label: "A股" },
    { value: "etf", label: "ETF" },
    { value: "us", label: "美股" },
    { value: "crypto", label: "Crypto" },
];

export function RuleIndicatorsPage() {
    const navigate = useNavigate();
    const [market, setMarket] = useState("ashare");
    const [items, setItems] = useState<RuleIndicator[]>([]);
    const [search, setSearch] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [paramDrafts, setParamDrafts] = useState<Record<string, string>>({});
    const [paramSavingId, setParamSavingId] = useState<string | null>(null);
    const [scoreBucketMapping, setScoreBucketMapping] = useState<ScoreBucketMapping>({});
    const [bucketSaving, setBucketSaving] = useState<string | null>(null);
    const [mappingExpanded, setMappingExpanded] = useState(false);

    const mappingCategories = useMemo(() => {
        const set = new Set<string>();
        items.forEach(item => {
            if (item.category) set.add(item.category);
        });
        Object.keys(scoreBucketMapping || {}).forEach((key) => set.add(key));
        return Array.from(set).sort();
    }, [items, scoreBucketMapping]);

    const loadIndicators = () => {
        setLoading(true);
        setError("");
        fetch(`${API_BASE_URL}/api/aitrader/indicators?market=${encodeURIComponent(market)}`)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data?.data) ? data.data : [];
                setItems(list);
                const drafts: Record<string, string> = {};
                list.forEach((item: RuleIndicator) => {
                    drafts[item.indicator_id] = JSON.stringify(item.params || {}, null, 2);
                });
                setParamDrafts(drafts);
                const mapping = data?.meta?.score_bucket_mapping;
                if (mapping && typeof mapping === "object") {
                    setScoreBucketMapping(mapping);
                } else {
                    setScoreBucketMapping({});
                }
            })
            .catch(() => setError("加载指标失败"))
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        loadIndicators();
    }, [market]);

    const filtered = useMemo(() => {
        const key = search.trim().toLowerCase();
        if (!key) return items;
        return items.filter(item =>
            (item.name || "").toLowerCase().includes(key)
            || (item.category || "").toLowerCase().includes(key)
            || (item.indicator_id || "").toLowerCase().includes(key)
        );
    }, [items, search]);

    const handleToggle = async (indicatorId: string, enabled: boolean) => {
        const prev = items;
        setItems(items.map(item => item.indicator_id === indicatorId ? { ...item, enabled } : item));
        try {
            const res = await fetch(`${API_BASE_URL}/api/aitrader/indicators/${encodeURIComponent(indicatorId)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ market, enabled })
            });
            if (!res.ok) throw new Error("update failed");
        } catch (e) {
            setItems(prev);
            setError("保存失败");
        }
    };

    const handleSaveParams = async (indicatorId: string) => {
        const text = (paramDrafts[indicatorId] || "").trim();
        let params: Record<string, unknown> = {};
        if (text) {
            try {
                params = JSON.parse(text);
            } catch {
                setError("参数 JSON 格式错误");
                return;
            }
        }
        setParamSavingId(indicatorId);
        setError("");
        try {
            const res = await fetch(`${API_BASE_URL}/api/aitrader/indicators/${encodeURIComponent(indicatorId)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ market, params })
            });
            if (!res.ok) throw new Error("update failed");
            setItems(items.map(item => item.indicator_id === indicatorId ? { ...item, params } : item));
            setExpandedId(null);
        } catch {
            setError("保存参数失败");
        } finally {
            setParamSavingId(null);
        }
    };

    const updateParamField = (indicatorId: string, field: string, rawValue: string) => {
        const text = (paramDrafts[indicatorId] || "").trim();
        let params: Record<string, unknown> = {};
        try {
            params = text ? JSON.parse(text) : {};
        } catch {
            setError("参数 JSON 格式错误");
            return;
        }
        const num = rawValue === "" ? null : Number(rawValue);
        if (num === null || Number.isNaN(num)) {
            delete params[field];
        } else {
            params[field] = num;
        }
        setParamDrafts(prev => ({
            ...prev,
            [indicatorId]: JSON.stringify(params, null, 2)
        }));
    };

    const updateParamFieldString = (indicatorId: string, field: string, rawValue: string) => {
        const text = (paramDrafts[indicatorId] || "").trim();
        let params: Record<string, unknown> = {};
        try {
            params = text ? JSON.parse(text) : {};
        } catch {
            setError("参数 JSON 格式错误");
            return;
        }
        const value = (rawValue || "").trim();
        if (!value) {
            delete params[field];
        } else {
            params[field] = value;
        }
        setParamDrafts(prev => ({
            ...prev,
            [indicatorId]: JSON.stringify(params, null, 2)
        }));
    };

    const handleBulk = async (enabled: boolean) => {
        const prev = items;
        setItems(items.map(item => ({ ...item, enabled })));
        try {
            await Promise.all(items.map(item =>
                fetch(`${API_BASE_URL}/api/aitrader/indicators/${encodeURIComponent(item.indicator_id)}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ market, enabled })
                })
            ));
        } catch (e) {
            setItems(prev);
            setError("批量更新失败");
        }
    };

    const handleBucketUpdate = async (category: string, bucket: string) => {
        const prev = scoreBucketMapping;
        setScoreBucketMapping({ ...prev, [category]: bucket });
        setBucketSaving(category);
        setError("");
        try {
            const res = await fetch(`${API_BASE_URL}/api/aitrader/indicator_buckets/${encodeURIComponent(category)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ bucket }),
            });
            if (!res.ok) throw new Error("update failed");
        } catch {
            setScoreBucketMapping(prev);
            setError("保存评分映射失败");
        } finally {
            setBucketSaving(null);
        }
    };

    return (
        <div className="rule-indicators-page">
            <div className="rule-indicators-header">
                <div>
                    <div className="title">规则指标配置</div>
                    <div className="subtitle">关闭的指标不会参与规则计算（无数据的指标会自动空值）。</div>
                </div>
                <div className="header-actions">
                    <Button onPress={() => navigate("/")}>返回首页</Button>
                    <Button onPress={loadIndicators}>刷新</Button>
                </div>
            </div>

        <div className="rule-indicators-toolbar">
            <label>
                市场
                <select value={market} onChange={(e) => setMarket(e.target.value)}>
                        {MARKET_OPTIONS.map(opt => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                    </select>
                </label>
                <label>
                    搜索
                    <input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="名称 / 分类 / ID"
                    />
                </label>
            <div className="bulk-actions">
                <Button onPress={() => handleBulk(true)}>全启用</Button>
                <Button onPress={() => handleBulk(false)}>全关闭</Button>
            </div>
        </div>

        <div className="rule-indicators-hint">
            <div className="hint-header">
                <div>
                    <div className="hint-title">评分归属映射（分类 → 得分桶）</div>
                    <div className="hint-note">可在指标参数中使用 score_bucket 覆盖该映射。</div>
                </div>
                <button
                    className="hint-toggle"
                    onClick={() => setMappingExpanded(!mappingExpanded)}
                >
                    {mappingExpanded ? "收起" : "展开"}
                </button>
            </div>
            {mappingExpanded && (
                <div className="mapping-table">
                    <div className="mapping-header">
                        <div>分类</div>
                        <div>归属桶</div>
                    </div>
                    {mappingCategories.map(category => {
                        const value = scoreBucketMapping[category] || "total";
                        return (
                            <div key={category} className="mapping-row">
                                <div>{category}</div>
                                <div>
                                    <select
                                        value={value}
                                        onChange={(e) => handleBucketUpdate(category, e.target.value)}
                                        disabled={bucketSaving === category}
                                    >
                                        <option value="trend">趋势</option>
                                        <option value="structure">结构</option>
                                        <option value="volume">量能</option>
                                        <option value="rr">RR</option>
                                        <option value="total">总分</option>
                                    </select>
                                </div>
                            </div>
                        );
                    })}
                    {mappingCategories.length === 0 && (
                        <div className="mapping-empty">暂无映射数据</div>
                    )}
                </div>
            )}
        </div>

            {error && <div className="error">{error}</div>}
            {loading && <div className="loading">加载中...</div>}

            <div className="rule-indicators-table">
                <div className="table-header">
                    <div>开启</div>
                    <div>指标</div>
                    <div>分类</div>
                    <div>描述 / 公式</div>
                    <div>数据来源</div>
                    <div>参数</div>
                </div>
                {filtered.map(item => {
                    const isExpanded = expandedId === item.indicator_id;
                    const draftText = paramDrafts[item.indicator_id] || "";
                    let parsedParams: Record<string, unknown> | null = null;
                    let parseError = "";
                    if (draftText.trim()) {
                        try {
                            parsedParams = JSON.parse(draftText);
                        } catch {
                            parseError = "JSON 无效";
                        }
                    } else {
                        parsedParams = {};
                    }
                    const weightVal = parsedParams && typeof parsedParams.weight === "number" ? String(parsedParams.weight) : "";
                    const scoreContributionVal = parsedParams && typeof parsedParams.score_contribution === "number"
                        ? String(parsedParams.score_contribution)
                        : "";
                    const scoreBucketVal = parsedParams && typeof parsedParams.score_bucket === "string"
                        ? String(parsedParams.score_bucket)
                        : "";
                    return (
                        <div key={item.indicator_id} className="table-row">
                            <div>
                                <input
                                    type="checkbox"
                                    checked={!!item.enabled}
                                    onChange={(e) => handleToggle(item.indicator_id, e.target.checked)}
                                />
                            </div>
                            <div>
                                <div className="indicator-name">{item.name}</div>
                                <div className="indicator-id">{item.indicator_id}</div>
                            </div>
                            <div>{item.category || "--"}</div>
                            <div className="indicator-desc">
                                <div>{item.description || "--"}</div>
                                {item.formula && <div className="indicator-formula">{item.formula}</div>}
                            </div>
                            <div>{item.data_source || "--"}</div>
                            <div>
                                <button
                                    className="param-btn"
                                    onClick={() => setExpandedId(isExpanded ? null : item.indicator_id)}
                                >
                                    {isExpanded ? "收起" : "参数"}
                                </button>
                            </div>
                            {isExpanded && (
                                <div className="param-editor">
                                    <div className="param-editor-title">参数(JSON)</div>
                                    <div className="param-editor-fields">
                                        <label>
                                            权重
                                            <input
                                                type="number"
                                                step="0.1"
                                                value={weightVal}
                                                disabled={!!parseError}
                                                onChange={(e) => updateParamField(item.indicator_id, "weight", e.target.value)}
                                            />
                                        </label>
                                        <label>
                                            评分贡献
                                            <input
                                                type="number"
                                                step="0.1"
                                                value={scoreContributionVal}
                                                disabled={!!parseError}
                                                onChange={(e) => updateParamField(item.indicator_id, "score_contribution", e.target.value)}
                                            />
                                        </label>
                                        <label>
                                            评分归属
                                            <select
                                                value={scoreBucketVal}
                                                disabled={!!parseError}
                                                onChange={(e) => updateParamFieldString(item.indicator_id, "score_bucket", e.target.value)}
                                            >
                                                <option value="">自动</option>
                                                <option value="trend">趋势</option>
                                                <option value="structure">结构</option>
                                                <option value="volume">量能</option>
                                                <option value="rr">RR</option>
                                                <option value="total">总分</option>
                                            </select>
                                        </label>
                                        {parseError && <span className="param-error">{parseError}</span>}
                                    </div>
                                    <textarea
                                        rows={6}
                                        value={draftText}
                                        onChange={(e) => setParamDrafts(prev => ({ ...prev, [item.indicator_id]: e.target.value }))}
                                    />
                                    <div className="param-editor-actions">
                                        <button
                                            className="param-save"
                                            onClick={() => handleSaveParams(item.indicator_id)}
                                            disabled={paramSavingId === item.indicator_id}
                                        >
                                            {paramSavingId === item.indicator_id ? "保存中..." : "保存参数"}
                                        </button>
                                        <button className="param-cancel" onClick={() => setExpandedId(null)}>取消</button>
                                    </div>
                                </div>
                            )}
                        </div>
                    );
                })}
                {!loading && filtered.length === 0 && (
                    <div className="empty">无匹配指标</div>
                )}
            </div>
        </div>
    );
}
