import { useEffect, useState } from "react";
import { Button } from "react-aria-components";
import { useNavigate } from "react-router";

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || 'http://localhost:8000';

type IndustryTemplate = {
    profile_id: string;
    name: string;
    keywords?: string[];
    config?: Record<string, unknown>;
    priority?: number;
    enabled?: boolean;
    keywordsText: string;
    configText: string;
};

export function IndustryTemplatesPage() {
    const navigate = useNavigate();
    const [templates, setTemplates] = useState<IndustryTemplate[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [newId, setNewId] = useState("");
    const [newName, setNewName] = useState("");
    const [newKeywords, setNewKeywords] = useState("");
    const [newPriority, setNewPriority] = useState(0);
    const configExample = `{
  "indicators": {
    "commodity_price": {
      "items": [
        { "source": "LME", "symbol": "CU", "ticker": "HG=F", "label": "LME铜" }
      ]
    },
    "dxy": { "ticker": "DX-Y.NYB" },
    "sector_leaders": { "symbols": ["601899.SH", "603993.SH"] }
  },
  "refresh_minutes": 30
}`;

    const loadTemplates = () => {
        setLoading(true);
        setError("");
        fetch(`${API_BASE_URL}/api/industry/profiles`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data?.data) ? data.data : [];
                const mapped = items.map((p: any) => ({
                    ...p,
                    keywordsText: Array.isArray(p.keywords) ? p.keywords.join(",") : "",
                    configText: JSON.stringify(p.config || {}, null, 2)
                }));
                setTemplates(mapped);
            })
            .catch(err => {
                console.error(err);
                setError("模板加载失败");
            })
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        loadTemplates();
    }, []);

    const updateTemplateField = (profileId: string, key: keyof IndustryTemplate, value: any) => {
        setTemplates(prev => prev.map(t => t.profile_id === profileId ? { ...t, [key]: value } : t));
    };

    const handleSaveTemplate = async (tpl: IndustryTemplate) => {
        try {
            const keywords = tpl.keywordsText
                ? tpl.keywordsText.split(/[,，\s]+/).map(k => k.trim()).filter(Boolean)
                : [];
            let config: Record<string, unknown> = {};
            if (tpl.configText && tpl.configText.trim().length > 0) {
                config = JSON.parse(tpl.configText);
            }
            await fetch(`${API_BASE_URL}/api/industry/profiles/${encodeURIComponent(tpl.profile_id)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: tpl.name,
                    keywords,
                    config,
                    priority: Number(tpl.priority || 0),
                    enabled: !!tpl.enabled
                })
            });
            loadTemplates();
        } catch (err) {
            console.error(err);
            setError("模板保存失败（检查JSON格式）");
        }
    };

    const handleResetTemplates = async () => {
        try {
            await fetch(`${API_BASE_URL}/api/industry/profiles/reset`, { method: "POST" });
            loadTemplates();
        } catch (err) {
            console.error(err);
            setError("模板重置失败");
        }
    };

    const handleCreateTemplate = async () => {
        const id = newId.trim();
        if (!id) {
            setError("模板ID不能为空");
            return;
        }
        try {
            const keywords = newKeywords
                ? newKeywords.split(/[,，\s]+/).map(k => k.trim()).filter(Boolean)
                : [];
            await fetch(`${API_BASE_URL}/api/industry/profiles/${encodeURIComponent(id)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: newName.trim() || id,
                    keywords,
                    config: {},
                    priority: Number(newPriority || 0),
                    enabled: true
                })
            });
            setNewId("");
            setNewName("");
            setNewKeywords("");
            setNewPriority(0);
            loadTemplates();
        } catch (err) {
            console.error(err);
            setError("模板创建失败");
        }
    };

    const handleDeleteTemplate = async (profileId: string) => {
        if (!window.confirm(`确定删除模板 ${profileId} 吗？`)) return;
        try {
            await fetch(`${API_BASE_URL}/api/industry/profiles/${encodeURIComponent(profileId)}`, {
                method: "DELETE"
            });
            loadTemplates();
        } catch (err) {
            console.error(err);
            setError("模板删除失败");
        }
    };

    return (
        <div style={{ background: '#f4f6fb', minHeight: '100vh', padding: '24px' }}>
            <div style={{ maxWidth: '1100px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
                    <div>
                        <div style={{ fontWeight: 700, fontSize: '18px' }}>行业模板设置</div>
                        <div style={{ color: '#666', fontSize: '12px', marginTop: '4px' }}>
                            关键词用于匹配行业分类；模板配置用于驱动外部指标与刷新频率。
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        <Button
                            onPress={handleResetTemplates}
                            style={{ background: '#fff', border: '1px solid #ddd', padding: '8px 12px', borderRadius: '6px' }}
                        >
                            重置默认模板
                        </Button>
                        <Button
                            onPress={() => navigate('/')}
                            style={{ background: '#007acc', color: '#fff', border: 'none', padding: '8px 14px', borderRadius: '6px' }}
                        >
                            返回首页
                        </Button>
                    </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                    <div style={{ background: '#fff', border: '1px solid #e6e8ee', borderRadius: '10px', padding: '12px' }}>
                        <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '6px' }}>关键词怎么填？</div>
                        <div style={{ fontSize: '12px', color: '#666', lineHeight: '1.5' }}>
                            建议填写<strong>行业名称里的关键词</strong>，比如“有色”“半导体”“银行”“医药”等。
                            系统会用“行业包含关键词”的方式匹配模板。
                        </div>
                    </div>
                    <div style={{ background: '#fff', border: '1px solid #e6e8ee', borderRadius: '10px', padding: '12px' }}>
                        <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '6px' }}>示例</div>
                        <div style={{ fontSize: '12px', color: '#666', lineHeight: '1.5' }}>
                            强周期：有色, 钢铁, 煤炭, 化工, 石油<br />
                            成长：半导体, 电子, 计算机, 通信, 医药<br />
                            稳健：银行, 保险, 食品饮料, 公用事业
                        </div>
                    </div>
                </div>
                <div style={{ background: '#fff', border: '1px solid #e6e8ee', borderRadius: '10px', padding: '12px' }}>
                    <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '6px' }}>配置示例（JSON）</div>
                    <div style={{ fontSize: '12px', color: '#666', lineHeight: '1.5', marginBottom: '8px' }}>
                        只有配置了具体指标才会抓取；同一指标会全局缓存（DB）复用。详见 docs/industry.md
                    </div>
                    <pre style={{ margin: 0, padding: '10px', background: '#f7f8fb', borderRadius: '8px', fontSize: '11px', lineHeight: '1.5', overflowX: 'auto' }}>
                        {configExample}
                    </pre>
                </div>

                {error && <div style={{ color: '#c00', fontSize: '12px' }}>{error}</div>}
                {loading && <div style={{ color: '#666', fontSize: '12px' }}>加载中...</div>}

                <div style={{ background: '#fff', border: '1px solid #e6e8ee', borderRadius: '12px', padding: '12px' }}>
                    <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>新增模板</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '160px 160px 1fr 90px 80px', gap: '8px', alignItems: 'center' }}>
                        <input
                            type="text"
                            value={newId}
                            onChange={(e) => setNewId(e.target.value)}
                            style={{ padding: '6px 8px', fontSize: '12px' }}
                            placeholder="模板ID (唯一)"
                        />
                        <input
                            type="text"
                            value={newName}
                            onChange={(e) => setNewName(e.target.value)}
                            style={{ padding: '6px 8px', fontSize: '12px' }}
                            placeholder="模板名"
                        />
                        <input
                            type="text"
                            value={newKeywords}
                            onChange={(e) => setNewKeywords(e.target.value)}
                            style={{ padding: '6px 8px', fontSize: '12px' }}
                            placeholder="关键词，用逗号/空格分隔"
                        />
                        <input
                            type="number"
                            value={newPriority}
                            onChange={(e) => setNewPriority(Number(e.target.value || 0))}
                            style={{ padding: '6px 8px', fontSize: '12px' }}
                            title="优先级"
                        />
                        <Button
                            onPress={handleCreateTemplate}
                            style={{ background: '#007acc', color: '#fff', border: 'none', padding: '6px 10px', borderRadius: '6px', fontSize: '12px' }}
                        >
                            新增
                        </Button>
                    </div>
                </div>

                <div style={{ background: '#fff', border: '1px solid #e6e8ee', borderRadius: '12px', overflow: 'hidden' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '160px 160px 1fr 90px 90px 140px', gap: '8px', padding: '10px 12px', background: '#f7f8fb', fontSize: '12px', color: '#666' }}>
                        <div>ID</div>
                        <div>名称</div>
                        <div>关键词</div>
                        <div>优先级</div>
                        <div>启用</div>
                        <div>操作</div>
                    </div>
                    {templates.map((tpl) => (
                        <div key={tpl.profile_id} style={{ borderTop: '1px solid #f0f0f0' }}>
                            <div style={{ display: 'grid', gridTemplateColumns: '160px 160px 1fr 90px 90px 140px', gap: '8px', padding: '10px 12px', alignItems: 'center' }}>
                                <div style={{ fontSize: '12px', color: '#555' }}>{tpl.profile_id}</div>
                                <input
                                    type="text"
                                    value={tpl.name || ""}
                                    onChange={(e) => updateTemplateField(tpl.profile_id, "name", e.target.value)}
                                    style={{ padding: '6px 8px', fontSize: '12px' }}
                                    placeholder="模板名"
                                />
                                <input
                                    type="text"
                                    value={tpl.keywordsText}
                                    onChange={(e) => updateTemplateField(tpl.profile_id, "keywordsText", e.target.value)}
                                    style={{ padding: '6px 8px', fontSize: '12px' }}
                                    placeholder="关键词，用逗号/空格分隔"
                                />
                                <input
                                    type="number"
                                    value={tpl.priority ?? 0}
                                    onChange={(e) => updateTemplateField(tpl.profile_id, "priority", Number(e.target.value || 0))}
                                    style={{ padding: '6px 8px', fontSize: '12px' }}
                                    title="优先级"
                                />
                                <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                    <input
                                        type="checkbox"
                                        checked={!!tpl.enabled}
                                        onChange={(e) => updateTemplateField(tpl.profile_id, "enabled", e.target.checked)}
                                    />
                                    启用
                                </label>
                                <div style={{ display: 'flex', gap: '6px' }}>
                                    <Button
                                        onPress={() => handleSaveTemplate(tpl)}
                                        style={{ background: '#007acc', color: '#fff', border: 'none', padding: '6px 8px', borderRadius: '6px', fontSize: '12px' }}
                                    >
                                        保存
                                    </Button>
                                    <Button
                                        onPress={() => handleDeleteTemplate(tpl.profile_id)}
                                        style={{ background: '#fff3f3', border: '1px solid #e5bcbc', padding: '6px 8px', borderRadius: '6px', fontSize: '12px' }}
                                    >
                                        删除
                                    </Button>
                                </div>
                            </div>
                            <div style={{ padding: '0 12px 12px' }}>
                                <div style={{ fontSize: '11px', color: '#666', marginBottom: '4px' }}>配置（JSON）</div>
                                <textarea
                                    rows={3}
                                    value={tpl.configText}
                                    onChange={(e) => updateTemplateField(tpl.profile_id, "configText", e.target.value)}
                                    style={{ width: '100%', padding: '8px', fontFamily: 'monospace', fontSize: '12px', borderRadius: '6px', border: '1px solid #e0e0e0' }}
                                    placeholder="JSON 配置（如指标/频率等）"
                                />
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
