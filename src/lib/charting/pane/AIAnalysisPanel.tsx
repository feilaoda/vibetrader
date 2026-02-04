import { useState, useEffect, useRef } from "react";
import { Button } from 'react-aria-components';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Kline } from "../../domain/Kline";
import Close from '@react-spectrum/s2/icons/Close';
import Copy from '@react-spectrum/s2/icons/Copy';
import Star from '@react-spectrum/s2/icons/Star';
import StarFilled from '@react-spectrum/s2/icons/StarFilled';
import Send from '@react-spectrum/s2/icons/Send';
import { ActionCreationModal, type ActionPlanData } from './ActionCreationModal';

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || 'http://localhost:8000';

interface AIAnalysisPanelProps {
    symbol: string;
    klines: Kline[];
    isOpen: boolean;
    onClose: () => void;
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

interface SystemPromptItem {
    id: number;
    symbol: string;
    name: string;
    prompt: string;
    is_active: boolean;
}

interface IndustryProfile {
    symbol: string;
    industry?: string | null;
    source?: string | null;
    updated_at?: string | null;
    enabled?: boolean;
    enabled_at?: string | null;
    profile?: string;
    profile_name?: string | null;
    profile_override?: string | null;
    profile_override_source?: string | null;
    profile_override_at?: string | null;
    reason?: string;
}

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

interface ChatMessage {
    id: number;
    role: 'user' | 'assistant';
    content: string;
    timestamp: number;
    is_favorite: boolean;
    model?: string;
}

type ContextSettings = {
    enableMemory: boolean;
    enableRetrieval: boolean;
    chatUseDaily: boolean;
    showIndustryInChat: boolean;
    historyLimit: number;
    recentLimit: number;
    summaryMin: number;
    summaryStep: number;
    relevantTopK: number;
    maxMessageChars: number;
    klineRowsChat: number;
    klineRowsAssistant: number;
};

const SETTINGS_KEY = "vibetrader.ai.context.settings";
const DEFAULT_SETTINGS: ContextSettings = {
    enableMemory: true,
    enableRetrieval: true,
    chatUseDaily: true,
    showIndustryInChat: false,
    historyLimit: 60,
    recentLimit: 8,
    summaryMin: 10,
    summaryStep: 6,
    relevantTopK: 4,
    maxMessageChars: 1200,
    klineRowsChat: 365,
    klineRowsAssistant: 365
};

export function AIAnalysisPanel(props: AIAnalysisPanelProps) {
    const [config, setConfig] = useState<LLMConfig | null>(null);
    const [selectedModel, setSelectedModel] = useState<string>("");

    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [analysisMode, setAnalysisMode] = useState<'chat' | 'assistant'>('chat');
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [settingsTab, setSettingsTab] = useState<'params' | 'prompt' | 'industry'>('params');
    const [contextSettings, setContextSettings] = useState<ContextSettings>(DEFAULT_SETTINGS);
    const [input, setInput] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [autoEnabled, setAutoEnabled] = useState(false);
    const [autoLoading, setAutoLoading] = useState(false);
    const [autoError, setAutoError] = useState("");
    const [autoLastRun, setAutoLastRun] = useState<number | null>(null);
    const autoRunningRef = useRef(false);
    const latestKlinesRef = useRef<Kline[]>([]);

    const [actionModalOpen, setActionModalOpen] = useState(false);
    const [actionData, setActionData] = useState<ActionPlanData | null>(null);

    const [promptItems, setPromptItems] = useState<SystemPromptItem[]>([]);
    const [selectedPromptId, setSelectedPromptId] = useState<number | null>(null);
    const [promptName, setPromptName] = useState("");
    const [promptText, setPromptText] = useState("");
    const [promptSetActive, setPromptSetActive] = useState(false);
    const [promptLoading, setPromptLoading] = useState(false);
    const [promptError, setPromptError] = useState("");
    const [defaultPromptTemplate, setDefaultPromptTemplate] = useState("");
    const [industryProfile, setIndustryProfile] = useState<IndustryProfile | null>(null);
    const [industryError, setIndustryError] = useState("");
    const [industryOptions, setIndustryOptions] = useState<string[]>([]);
    const [industryDraft, setIndustryDraft] = useState("");
    const [profileDraft, setProfileDraft] = useState("");
    const [industryEnabled, setIndustryEnabled] = useState(false);
    const [templates, setTemplates] = useState<IndustryTemplate[]>([]);
    const [templatesLoading, setTemplatesLoading] = useState(false);
    const [templatesError, setTemplatesError] = useState("");
    const templatesResetRef = useRef(false);

    const chatEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const inputStyle = { width: '100%', padding: '6px', marginTop: '4px', boxSizing: 'border-box' as const };
    const formatVolume = (value: unknown, source?: string) => {
        const num = Number(value ?? 0);
        if (!Number.isFinite(num) || num <= 0) return 'N/A';
        if (source === 'tencent') {
            const hands = num / 100;
            if (hands >= 10000) return `${(hands / 10000).toFixed(2)}万手`;
            if (hands >= 1000) return `${hands.toFixed(0)}手`;
            return `${hands.toFixed(0)}手`;
        }
        if (num >= 1e8) return `${(num / 1e8).toFixed(2)}亿股`;
        if (num >= 1e4) return `${(num / 1e4).toFixed(2)}万股`;
        return `${num.toFixed(0)}股`;
    };

    const buildRealtimeSummary = (rt: any) => {
        const ts = rt?.timestamp ? new Date(rt.timestamp).toLocaleString() : new Date().toLocaleString();
        const volumeText = formatVolume(rt?.volume, rt?.source);
        return `当前行情(${ts}): 最新价 ${rt?.price ?? 'N/A'}，今开 ${rt?.open ?? 'N/A'}，最高 ${rt?.high ?? 'N/A'}，最低 ${rt?.low ?? 'N/A'}，成交量 ${volumeText}。`;
    };

    const buildIndustryContext = (profile: IndustryProfile | null) => {
        if (!profile?.enabled) return "";
        const parts: string[] = [];
        if (profile.industry) parts.push(`行业: ${profile.industry}`);
        const templateName = profile.profile_name || profile.profile;
        if (templateName) parts.push(`模板: ${templateName}`);
        if (profile.profile_override) parts.push(`模板覆盖: ${profile.profile_override}`);
        if (profile.source) parts.push(`行业来源: ${profile.source}`);
        if (profile.reason) parts.push(`推荐理由: ${profile.reason}`);
        if (parts.length === 0) return "";
        return parts.join("；");
    };

    const buildIndustryTransient = (profile: IndustryProfile | null) => {
        if (!profile?.enabled) return "";
        const context = buildIndustryContext(profile);
        if (!context) return "";
        return context;
    };

    const fetchIndustryIndicatorContext = async () => {
        if (!contextSettings.showIndustryInChat) return "";
        if (!industryProfile?.enabled) return "";
        try {
            const res = await fetch(`${API_BASE_URL}/api/industry/context?symbol=${encodeURIComponent(props.symbol)}`);
            if (!res.ok) return "";
            const data = await res.json();
            const ctx = data?.data?.context;
            if (!ctx || typeof ctx !== "string") return "";
            return ctx.trim();
        } catch (e) {
            return "";
        }
    };

    // Auto-scroll
    useEffect(() => {
        chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isLoading]);

    // Load config & History
    useEffect(() => {
        if (props.isOpen) {
            // Load Config
            if (!config) {
                fetch(`${API_BASE_URL}/api/config/llm_models`)
                    .then(res => res.json())
                    .then(data => {
                        setConfig(data);
                        setSelectedModel(data.current_model || (data.models.length > 0 ? data.models[0].id : ""));
                    })
                    .catch(console.error);
            }
            // Load History
            loadHistory();
            // Load System Prompts
            loadSystemPrompts();
            // Load Industry Profile
            loadIndustryProfile();
        }
    }, [props.isOpen, props.symbol]);

    useEffect(() => {
        try {
            const raw = localStorage.getItem(SETTINGS_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                setContextSettings({ ...DEFAULT_SETTINGS, ...parsed });
            }
        } catch (e) {
            // ignore
        }
    }, []);

    const persistSettings = (next: ContextSettings) => {
        setContextSettings(next);
        try {
            localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
        } catch (e) {
            // ignore
        }
    };

    useEffect(() => {
        if (Array.isArray(props.klines) && props.klines.length > 0) {
            latestKlinesRef.current = props.klines;
        }
    }, [props.klines]);

    useEffect(() => {
        if (settingsOpen) {
            setSettingsTab('params');
            loadIndustryTemplates();
            loadIndustryOptions();
            setIndustryDraft(industryProfile?.industry || "");
            setProfileDraft(industryProfile?.profile_override || "");
            setIndustryEnabled(!!industryProfile?.enabled);
        }
    }, [settingsOpen]);

    useEffect(() => {
        if (!settingsOpen) return;
        setIndustryDraft(industryProfile?.industry || "");
        setProfileDraft(industryProfile?.profile_override || "");
        setIndustryEnabled(!!industryProfile?.enabled);
    }, [industryProfile, settingsOpen]);

    const HISTORY_LIMIT = 5;

    const loadHistory = () => {
        fetch(`${API_BASE_URL}/api/history?symbol=${props.symbol}&limit=${HISTORY_LIMIT}`)
            .then(res => res.json())
            .then(data => setMessages(data.data))
            .catch(console.error);
    };

    const applyPromptSelection = (items: SystemPromptItem[]) => {
        if (!items || items.length === 0) {
            setSelectedPromptId(null);
            setPromptName("");
            setPromptText("");
            setPromptSetActive(false);
            return;
        }
        const active = items.find(p => p.is_active);
        const selected = active || items[0];
        setSelectedPromptId(selected.id);
        setPromptName(selected.name || "");
        setPromptText(selected.prompt || "");
        setPromptSetActive(!!selected.is_active);
    };

    const loadSystemPrompts = () => {
        if (!props.symbol) return;
        setPromptLoading(true);
        setPromptError("");
        fetch(`${API_BASE_URL}/api/system_prompts?symbol=${props.symbol}`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data.data) ? data.data : [];
                setPromptItems(items);
                if (typeof data.default_template === "string") {
                    setDefaultPromptTemplate(data.default_template);
                }
                applyPromptSelection(items);
            })
            .catch(err => {
                console.error(err);
                setPromptError("加载系统提示词失败");
            })
            .finally(() => setPromptLoading(false));
    };

    const loadIndustryProfile = () => {
        if (!props.symbol) return;
        setIndustryError("");
        fetch(`${API_BASE_URL}/api/industry/profile?symbol=${encodeURIComponent(props.symbol)}`)
            .then(res => res.json())
            .then(data => {
                const payload = data?.data || null;
                setIndustryProfile(payload);
                if (payload && typeof payload.enabled === "boolean") {
                    setIndustryEnabled(payload.enabled);
                }
            })
            .catch(err => {
                console.error(err);
                setIndustryError("行业模板加载失败");
            });
    };

    const loadIndustryTemplates = () => {
        setTemplatesLoading(true);
        setTemplatesError("");
        fetch(`${API_BASE_URL}/api/industry/profiles`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data?.data) ? data.data : [];
                if (items.length === 0 && !templatesResetRef.current) {
                    templatesResetRef.current = true;
                    fetch(`${API_BASE_URL}/api/industry/profiles/reset`, { method: "POST" })
                        .then(() => loadIndustryTemplates())
                        .catch(err => {
                            console.error(err);
                            setTemplatesError("模板重置失败");
                        });
                    return;
                }
                const mapped = items.map((p: any) => ({
                    ...p,
                    keywordsText: Array.isArray(p.keywords) ? p.keywords.join(",") : "",
                    configText: JSON.stringify(p.config || {}, null, 2)
                }));
                setTemplates(mapped);
            })
            .catch(err => {
                console.error(err);
                setTemplatesError("模板加载失败");
            })
            .finally(() => setTemplatesLoading(false));
    };

    const loadIndustryOptions = () => {
        fetch(`${API_BASE_URL}/api/industry/list`)
            .then(res => res.json())
            .then(data => {
                const items = Array.isArray(data?.data) ? data.data : [];
                setIndustryOptions(items);
            })
            .catch(err => {
                console.error(err);
            });
    };

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
            loadIndustryTemplates();
        } catch (err) {
            console.error(err);
            setTemplatesError("模板保存失败（检查JSON格式）");
        }
    };

    const handleResetTemplates = async () => {
        try {
            await fetch(`${API_BASE_URL}/api/industry/profiles/reset`, { method: "POST" });
            loadIndustryTemplates();
        } catch (err) {
            console.error(err);
            setTemplatesError("模板重置失败");
        }
    };

    const handleSaveIndustry = async () => {
        if (!props.symbol) return;
        try {
            await fetch(`${API_BASE_URL}/api/industry/${encodeURIComponent(props.symbol)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ industry: industryDraft.trim(), source: "manual" })
            });
            loadIndustryProfile();
            loadIndustryOptions();
        } catch (err) {
            console.error(err);
            setIndustryError("行业保存失败");
        }
    };

    const handleSaveProfileOverride = async () => {
        if (!props.symbol) return;
        try {
            await fetch(`${API_BASE_URL}/api/industry/profile_override/${encodeURIComponent(props.symbol)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ profile_id: profileDraft || "", source: "manual" })
            });
            loadIndustryProfile();
        } catch (err) {
            console.error(err);
            setIndustryError("模板保存失败");
        }
    };

    const handleToggleIndustryEnabled = async (enabled: boolean) => {
        if (!props.symbol) return;
        setIndustryEnabled(enabled);
        try {
            await fetch(`${API_BASE_URL}/api/industry/settings/${encodeURIComponent(props.symbol)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled })
            });
            loadIndustryProfile();
        } catch (err) {
            console.error(err);
            setIndustryError("行业开关保存失败");
        }
    };

    const handlePromptSelect = (value: string) => {
        if (!value) {
            setSelectedPromptId(null);
            setPromptName("");
            setPromptText("");
            setPromptSetActive(false);
            return;
        }
        const id = Number(value);
        const item = promptItems.find(p => p.id === id);
        if (!item) return;
        setSelectedPromptId(id);
        setPromptName(item.name || "");
        setPromptText(item.prompt || "");
        setPromptSetActive(!!item.is_active);
    };

    const handlePromptNew = () => {
        setSelectedPromptId(null);
        setPromptName("New Prompt");
        setPromptText(defaultPromptTemplate || "");
        setPromptSetActive(true);
        setPromptError("");
    };

    const handlePromptSave = async () => {
        if (!props.symbol) return;
        if (!promptText.trim()) {
            setPromptError("Prompt不能为空");
            return;
        }
        setPromptLoading(true);
        setPromptError("");
        const payload = {
            symbol: props.symbol,
            name: promptName.trim() || "Untitled",
            prompt: promptText.trim(),
            set_active: promptSetActive
        };
        try {
            if (selectedPromptId) {
                const updatePayload = {
                    name: payload.name,
                    prompt: payload.prompt,
                    set_active: payload.set_active
                };
                await fetch(`${API_BASE_URL}/api/system_prompts/${selectedPromptId}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(updatePayload)
                });
            } else {
                await fetch(`${API_BASE_URL}/api/system_prompts`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
            }
            loadSystemPrompts();
        } catch (err) {
            console.error(err);
            setPromptError("保存失败");
        } finally {
            setPromptLoading(false);
        }
    };

    const handlePromptActivate = async () => {
        if (!selectedPromptId) return;
        setPromptLoading(true);
        setPromptError("");
        try {
            await fetch(`${API_BASE_URL}/api/system_prompts/${selectedPromptId}/activate`, {
                method: "POST"
            });
            loadSystemPrompts();
        } catch (err) {
            console.error(err);
            setPromptError("激活失败");
        } finally {
            setPromptLoading(false);
        }
    };

    const handlePromptDelete = async () => {
        if (!selectedPromptId) return;
        setPromptLoading(true);
        setPromptError("");
        try {
            await fetch(`${API_BASE_URL}/api/system_prompts/${selectedPromptId}`, {
                method: "DELETE"
            });
            loadSystemPrompts();
        } catch (err) {
            console.error(err);
            setPromptError("删除失败");
        } finally {
            setPromptLoading(false);
        }
    };

    const streamAnalyze = async (params: {
        symbol: string;
        klines: Kline[];
        userInput: string;
        displayUserContent?: string;
        runMode: "manual" | "auto";
        analysisMode: "chat" | "assistant";
        contextSettings: ContextSettings;
        transientContext?: string;
    }) => {
        const { symbol, klines, userInput, displayUserContent, runMode, analysisMode, contextSettings, transientContext } = params;
        if (!config?.configured) return;
        if (!symbol || klines.length === 0) {
            const msg = !symbol ? "No symbol selected" : "No kline data available";
            setMessages(prev => [...prev, {
                id: Date.now(),
                role: 'assistant',
                content: `Error: ${msg}`,
                timestamp: Date.now() / 1000,
                is_favorite: false
            }]);
            return;
        }

        if (runMode === "manual") {
            setIsLoading(true);
        } else {
            setAutoLoading(true);
            setAutoError("");
            setAutoLastRun(Date.now());
        }

        const userContent = displayUserContent ?? userInput;
        if (userContent) {
            const tempParams: ChatMessage = {
                id: Date.now(),
                role: 'user',
                content: userContent,
                timestamp: Date.now() / 1000,
                is_favorite: false,
                model: selectedModel
            };
            setMessages(prev => [...prev, tempParams]);
        }

        try {
            const response = await fetch(`${API_BASE_URL}/api/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol,
                    klines,
                    model: selectedModel,
                    user_input: userInput,
                    transient_context: transientContext,
                    mode: analysisMode,
                    context_config: {
                        enable_memory: contextSettings.enableMemory,
                        enable_retrieval: contextSettings.enableRetrieval,
                        chat_use_daily: contextSettings.chatUseDaily,
                        history_limit: contextSettings.historyLimit,
                        recent_limit: contextSettings.recentLimit,
                        summary_min: contextSettings.summaryMin,
                        summary_step: contextSettings.summaryStep,
                        relevant_top_k: contextSettings.relevantTopK,
                        max_message_chars: contextSettings.maxMessageChars,
                        kline_rows_chat: contextSettings.klineRowsChat,
                        kline_rows_assistant: contextSettings.klineRowsAssistant
                    }
                })
            });

            if (!response.ok) throw new Error("Analysis failed");
            if (!response.body) throw new Error("No response body");

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let aiContent = "";

            const tempAiMsgId = Date.now() + 1;
            setMessages(prev => [...prev, {
                id: tempAiMsgId,
                role: 'assistant',
                content: "",
                timestamp: Date.now() / 1000,
                is_favorite: false,
                model: selectedModel
            }]);

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value);
                aiContent += chunk;

                setMessages(prev => {
                    const newHistory = [...prev];
                    const lastMsg = newHistory[newHistory.length - 1];
                    if (lastMsg.role === 'assistant') {
                        lastMsg.content = aiContent;
                    }
                    return newHistory;
                });
            }

        } catch (e) {
            const errMsg = `Error: ${(e as Error).message}`;
            setMessages(prev => [...prev, {
                id: Date.now(),
                role: 'assistant',
                content: errMsg,
                timestamp: Date.now() / 1000,
                is_favorite: false
            }]);
            if (runMode === "auto") {
                setAutoError((e as Error).message || "Auto evaluation failed");
            }
        } finally {
            if (runMode === "manual") {
                setIsLoading(false);
            } else {
                setAutoLoading(false);
            }
            try {
                loadHistory();
            } catch (e) {
                console.error("Error reloading history:", e);
            }
        }
    };

    const handleSend = async () => {
        if (!config?.configured) return;
        if (!input.trim() && messages.length > 0) return;

        const userInput = input.trim();
        setInput("");

        let klinesToUse = props.klines;
        let realtimeSummary = '';
        const wantDaily = analysisMode === 'assistant' || (analysisMode === 'chat' && contextSettings.chatUseDaily);
        if (wantDaily) {
            try {
                const limit = analysisMode === 'assistant'
                    ? contextSettings.klineRowsAssistant
                    : contextSettings.klineRowsChat;
                const dailyRes = await fetch(`${API_BASE_URL}/api/klines/${encodeURIComponent(props.symbol)}?period=1d&limit=${limit}`);
                if (dailyRes.ok) {
                    const dailyJson = await dailyRes.json();
                    const dailyKlines = Array.isArray(dailyJson?.data) ? dailyJson.data : [];
                    if (dailyKlines.length > 0) {
                        klinesToUse = dailyKlines;
                    }
                }
            } catch (e) {
                // fallback to current klines
            }
        }
        if (analysisMode === 'chat') {
            try {
                const realtimeRes = await fetch(`${API_BASE_URL}/api/realtime/${encodeURIComponent(props.symbol)}`);
                if (realtimeRes.ok) {
                    const rt = await realtimeRes.json();
                    if (rt && rt?.price !== undefined && !rt?.stale) {
                        realtimeSummary = buildRealtimeSummary(rt);
                    } else {
                        realtimeSummary = '当前行情获取失败或非当日数据。';
                    }
                } else {
                    realtimeSummary = '当前行情获取失败。';
                }
            } catch (e) {
                realtimeSummary = '当前行情获取失败。';
            }
        }

        const industryContext = buildIndustryTransient(industryProfile);
        const indicatorContext = await fetchIndustryIndicatorContext();
        const baseUserInput = realtimeSummary ? `${userInput}\n\n${realtimeSummary}` : userInput;

        const displayParts = [baseUserInput];
        if (contextSettings.showIndustryInChat) {
            if (indicatorContext) displayParts.push(`[行业指标]\n${indicatorContext}`);
        }
        const displayUserContent = displayParts.join("\n\n");

        await streamAnalyze({
            symbol: props.symbol,
            klines: klinesToUse,
            userInput: baseUserInput,
            displayUserContent,
            runMode: "manual",
            analysisMode,
            contextSettings,
            transientContext: industryContext
        });
    };

    const runAutoEvaluation = async () => {
        if (autoRunningRef.current || isLoading || autoLoading) return;
        if (!props.symbol || !config?.configured) return;

        autoRunningRef.current = true;
        try {
            const [dailyRes, realtimeRes] = await Promise.all([
                fetch(`${API_BASE_URL}/api/klines/${encodeURIComponent(props.symbol)}?period=1d&limit=365`),
                fetch(`${API_BASE_URL}/api/realtime/${encodeURIComponent(props.symbol)}`)
            ]);

            let dailyKlines: Kline[] = [];
            if (dailyRes.ok) {
                const dailyJson = await dailyRes.json();
                dailyKlines = Array.isArray(dailyJson?.data) ? dailyJson.data : [];
            }
            if (dailyKlines.length === 0 && latestKlinesRef.current.length > 0) {
                dailyKlines = latestKlinesRef.current;
            }

            let realtimeSummary = '';
            if (!realtimeRes.ok) {
                setAutoError('自动评估跳过：实时行情获取失败');
                return;
            }
            const rt = await realtimeRes.json();
            if (!rt || rt?.price === undefined) {
                setAutoError('自动评估跳过：实时行情数据不完整');
                return;
            }
            if (rt?.stale) {
                setAutoError('自动评估跳过：行情非当日数据');
                return;
            }
            realtimeSummary = buildRealtimeSummary(rt);

            const nowText = new Date().toLocaleString();
            const userInput = `自动评估。当前时间: ${nowText}。\n${realtimeSummary}\n请结合历史日线数据评估下一步动作（买/卖/观望），给出理由和风险提示。`;
            const industryContext = buildIndustryTransient(industryProfile);
            const indicatorContext = await fetchIndustryIndicatorContext();

            const baseDisplay = `自动评估 · ${nowText}\n${realtimeSummary}`;
            const displayParts = [baseDisplay];
            if (contextSettings.showIndustryInChat) {
                if (indicatorContext) displayParts.push(`[行业指标]\n${indicatorContext}`);
            }
            const displayUserContent = displayParts.join("\n\n");

            await streamAnalyze({
                symbol: props.symbol,
                klines: dailyKlines,
                userInput,
                displayUserContent,
                runMode: "auto",
                analysisMode: "assistant",
                contextSettings,
                transientContext: industryContext
            });
        } finally {
            autoRunningRef.current = false;
        }
    };

    useEffect(() => {
        if (!props.isOpen) return;
        if (!autoEnabled) return;
        runAutoEvaluation();
        const interval = setInterval(() => {
            if (!autoRunningRef.current) {
                runAutoEvaluation();
            }
        }, 5 * 60 * 1000);
        return () => clearInterval(interval);
    }, [autoEnabled, props.isOpen, props.symbol, selectedModel]);

    const [isExtracting, setIsExtracting] = useState(false);

    // ...

    const handleCreateAction = async (msgContent: string) => {
        setIsExtracting(true);
        try {
            // Call API to extract action using real LLM
            const res = await fetch(`${API_BASE_URL}/api/extract_action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: msgContent, symbol: props.symbol, model: selectedModel })
            });
            const data = await res.json();
            setActionData(data);
            setActionModalOpen(true);
        } catch (e) {
            console.error("Failed to extract action", e);
            setActionData({
                symbol: props.symbol,
                stock_name: "Unknown",
                action: "Watch",
                time_range: "Next few days",
                description: "Action derived from analysis",
                reasoning: "See original analysis",
                original_response: msgContent
            });
            setActionModalOpen(true);
        } finally {
            setIsExtracting(false);
        }
    };

    const handleSaveAction = async (data: ActionPlanData) => {
        try {
            await fetch(`${API_BASE_URL}/api/actions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            setActionModalOpen(false);
            // Could add a toast notification here
        } catch (e) {
            console.error("Failed to save action", e);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const toggleFavorite = async (msg: ChatMessage) => {
        // Optimistic
        setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, is_favorite: !m.is_favorite } : m));
        await fetch(`${API_BASE_URL}/api/history/${msg.id}/favorite`, { method: 'POST' });
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
    };

    if (!props.isOpen) return null;

    return (
        <div style={{
            width: '100%',
            height: '100%',
            backgroundColor: '#fff',
            borderLeft: '1px solid #e0e0e0',
            display: 'flex',
            flexDirection: 'column',
            boxShadow: '-2px 0 5px rgba(0,0,0,0.05)',
            zIndex: 10
        }}>
            {/* Header */}
            <div style={{
                padding: '10px 16px',
                borderBottom: '1px solid #e0e0e0',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                backgroundColor: '#f8f9fa'
            }}>
                <div style={{ fontWeight: 600, fontSize: '13px' }}>AI STOCK ANALYST</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <div style={{ fontSize: '11px', color: '#666', background: '#e0e0e0', padding: '2px 6px', borderRadius: '4px' }}>
                        {props.symbol}
                    </div>
                    <Button onPress={props.onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 2 }}>
                        <Close />
                    </Button>
                </div>
            </div>
            <div style={{
                padding: '6px 16px',
                borderBottom: '1px solid #eee',
                backgroundColor: '#fafafa',
                fontSize: '11px',
                color: '#666',
                display: 'flex',
                justifyContent: 'space-between',
                gap: '8px',
                flexWrap: 'wrap'
            }}>
                <div>
                    行业: <span style={{ color: '#333' }}>
                        {industryProfile?.enabled ? (industryProfile?.industry || 'N/A') : '未启用'}
                    </span>
                    {industryProfile?.enabled && industryProfile?.source ? <span style={{ color: '#999' }}> · {industryProfile.source}</span> : null}
                </div>
                <div>
                    模板: <span style={{ color: '#333' }}>{(() => {
                        if (!industryProfile?.enabled) return '未启用';
                        if (industryProfile?.profile_name) return industryProfile.profile_name;
                        const p = industryProfile?.profile || 'default';
                        if (p === 'etf') return 'ETF';
                        if (p === 'cycle') return '强周期';
                        if (p === 'growth') return '成长';
                        if (p === 'defensive') return '稳健';
                        return '默认';
                    })()}</span>
                </div>
                <div style={{ color: '#999' }}>{industryProfile?.reason || industryError}</div>
            </div>

            {/* Chat History Area */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '16px', backgroundColor: '#f0f0f0' }}>
                {messages.length === 0 && (
                    <div style={{ textAlign: 'center', color: '#888', marginTop: '40px', fontSize: '13px' }}>
                        <div style={{ fontSize: '40px', marginBottom: '16px' }}>💬</div>
                        <div>Start a conversation with the AI Analyst.<br />Ask about trends, support levels, or strategy.</div>
                        <div style={{ fontSize: '11px', marginTop: '10px', color: '#aaa' }}>
                            (Leave input empty and click Send for default full analysis)
                        </div>
                    </div>
                )}

                {messages.map((msg) => {
                    const displayContent = (msg.role === 'assistant' && msg.model)
                        ? `${msg.content}\n\n(Model: ${msg.model})`
                        : msg.content;
                    return (
                    <div key={msg.id} style={{
                        marginBottom: '16px',
                        display: 'flex',
                        justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start'
                    }}>
                        <div style={{
                            maxWidth: '90%',
                            backgroundColor: msg.role === 'user' ? '#007acc' : '#fff',
                            color: msg.role === 'user' ? '#fff' : '#333',
                            borderRadius: '8px',
                            padding: '12px',
                            boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                            fontSize: '13px',
                            lineHeight: '1.5',
                            position: 'relative'
                        }} className="message-bubble">

                            {/* Message Header (Role) */}
                            {msg.role === 'assistant' && (
                                <div style={{
                                    borderBottom: '1px solid #eee',
                                    marginBottom: '8px',
                                    paddingBottom: '4px',
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    fontSize: '11px',
                                    color: '#888'
                                }}>
                                    <span style={{ fontWeight: 'bold' }}>VibeTrader AI</span>
                                </div>
                            )}

                            {/* Content */}
                            <div className="markdown-body" style={{ textAlign: 'left', wordBreak: 'break-word' }}>
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayContent}</ReactMarkdown>
                            </div>

                            {/* Message Actions (Footer) */}
                            {msg.role === 'assistant' && (
                                <div style={{
                                    display: 'flex',
                                    justifyContent: 'flex-end',
                                    gap: '8px',
                                    marginTop: '8px',
                                    paddingTop: '6px',
                                    borderTop: '1px dashed #eee'
                                }}>
                                    <div title="Create Action Plan">
                                        <Button onPress={() => handleCreateAction(msg.content)} isDisabled={isExtracting} style={{ cursor: isExtracting ? 'wait' : 'pointer', border: 'none', background: 'transparent', color: '#666', padding: 4 }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px' }}>
                                                <Send /> <span>{isExtracting ? 'Extracting...' : 'Action'}</span>
                                            </div>
                                        </Button>
                                    </div>
                                    <div title="Favorite">
                                        <Button onPress={() => toggleFavorite(msg)} style={{ cursor: 'pointer', border: 'none', background: 'transparent', color: msg.is_favorite ? '#f1c40f' : '#ccc', padding: 4 }}>
                                            {msg.is_favorite ? <StarFilled /> : <Star />}
                                        </Button>
                                    </div>
                                    <div title="Copy">
                                        <Button onPress={() => copyToClipboard(displayContent)} style={{ cursor: 'pointer', border: 'none', background: 'transparent', color: '#ccc', padding: 4 }}>
                                            <Copy />
                                        </Button>
                                    </div>
                                </div>
                            )}

                            {/* User Timestamp */}
                            {msg.role === 'user' && (
                                <div style={{ fontSize: '10px', marginTop: '4px', opacity: 0.7, textAlign: 'right' }}>
                                    {new Date(msg.timestamp * 1000).toLocaleTimeString()}
                                </div>
                            )}
                        </div>
                    </div>
                );
                })}

                {isLoading && (
                    <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: '16px' }}>
                        <div style={{ backgroundColor: '#fff', borderRadius: '8px', padding: '12px', fontSize: '12px', color: '#666' }}>
                            Thinking... <span className="loading-dots">...</span>
                        </div>
                    </div>
                )}

                <div ref={chatEndRef} />
            </div>

            {/* Input Area (VS Code Style) */}
            <div style={{ borderTop: '1px solid #e0e0e0', padding: '12px', backgroundColor: '#fff' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>

                    <textarea
                        ref={textareaRef}
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder={isLoading ? "Thinking..." : "Ask the analyst..."}
                        disabled={isLoading || !config?.configured}
                        style={{
                            width: '100%',
                            minHeight: '60px',
                            padding: '8px',
                            borderRadius: '4px',
                            border: '1px solid #ccc',
                            fontFamily: 'inherit',
                            fontSize: '13px',
                            resize: 'vertical'
                        }}
                    />

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        {/* Model Selector */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <select
                                value={selectedModel}
                                onChange={(e) => setSelectedModel(e.target.value)}
                                style={{
                                    padding: '4px 8px',
                                    borderRadius: '2px',
                                    border: '1px solid #ddd',
                                    fontSize: '11px',
                                    backgroundColor: '#f5f5f5',
                                    maxWidth: '120px'
                                }}
                            >
                                {config?.models.map(m => (
                                    <option key={m.id} value={m.id}>{m.name}</option>
                                ))}
                            </select>

                            <select
                                value={analysisMode}
                                onChange={(e) => setAnalysisMode(e.target.value as 'chat' | 'assistant')}
                                style={{
                                    padding: '4px 8px',
                                    borderRadius: '2px',
                                    border: '1px solid #ddd',
                                    fontSize: '11px',
                                    backgroundColor: '#f5f5f5',
                                    maxWidth: '90px'
                                }}
                            >
                                <option value="chat">对话</option>
                                <option value="assistant">助手</option>
                            </select>

                            {!config?.configured && (
                                <span style={{ color: 'red', fontSize: '10px' }}>API Key Missing</span>
                            )}
                        </div>

                        {/* Send + Auto Evaluate */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: '#555' }}>
                                <input
                                    type="checkbox"
                                    checked={autoEnabled}
                                    onChange={(e) => setAutoEnabled(e.target.checked)}
                                    disabled={!config?.configured}
                                />
                                自动评估(5m)
                            </label>
                            <Button
                                onPress={() => setSettingsOpen(true)}
                                style={{
                                    background: '#f5f5f5',
                                    color: '#333',
                                    border: '1px solid #ddd',
                                    borderRadius: '4px',
                                    padding: '6px 8px',
                                    fontSize: '12px',
                                    cursor: 'pointer'
                                }}
                            >
                                ⚙︎
                            </Button>
                            <Button
                                onPress={handleSend}
                                isDisabled={isLoading || !config?.configured}
                                style={{
                                    background: isLoading ? '#ccc' : '#007acc',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '4px',
                                    padding: '6px 12px',
                                    fontSize: '12px',
                                    cursor: isLoading ? 'wait' : 'pointer',
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '4px'
                                }}
                            >
                                <span>Send</span> <Send />
                            </Button>
                        </div>
                    </div>
                </div>
            </div>

            <ActionCreationModal
                isOpen={actionModalOpen}
                initialData={actionData}
                onClose={() => setActionModalOpen(false)}
                onSave={handleSaveAction}
            />

            {settingsOpen && (
                <div style={{
                    position: 'fixed',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    backgroundColor: 'rgba(0,0,0,0.35)',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    zIndex: 1000
                }}>
                    <div style={{
                        backgroundColor: 'white',
                        padding: '16px',
                        borderRadius: '8px',
                        width: '520px',
                        maxHeight: '85vh',
                        overflowY: 'auto',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '12px'
                    }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <div style={{ fontWeight: 600, fontSize: '14px' }}>AI Context Settings</div>
                            <Button onPress={() => setSettingsOpen(false)} style={{ background: 'transparent', border: 'none', cursor: 'pointer' }}>
                                ✕
                            </Button>
                        </div>

                        <div style={{ display: 'flex', gap: '8px' }}>
                            {[
                                { id: 'params', label: '参数' },
                                { id: 'prompt', label: 'Prompt' },
                                { id: 'industry', label: '行业数据' }
                            ].map((tab) => {
                                const active = settingsTab === tab.id;
                                return (
                                    <button
                                        key={tab.id}
                                        onClick={() => setSettingsTab(tab.id as typeof settingsTab)}
                                        style={{
                                            padding: '6px 10px',
                                            fontSize: '12px',
                                            borderRadius: '6px',
                                            border: active ? '1px solid #007acc' : '1px solid #ddd',
                                            background: active ? '#e8f3ff' : '#f7f7f7',
                                            color: active ? '#007acc' : '#555',
                                            cursor: 'pointer'
                                        }}
                                    >
                                        {tab.label}
                                    </button>
                                );
                            })}
                        </div>

                        {settingsTab === 'params' && (
                            <>
                                <div style={{ display: 'flex', gap: '12px' }}>
                                    <label style={{ fontSize: '12px' }}>
                                        <input
                                            type="checkbox"
                                            checked={contextSettings.enableMemory}
                                            onChange={(e) => persistSettings({ ...contextSettings, enableMemory: e.target.checked })}
                                        /> 启用记忆摘要
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        <input
                                            type="checkbox"
                                            checked={contextSettings.enableRetrieval}
                                            onChange={(e) => persistSettings({ ...contextSettings, enableRetrieval: e.target.checked })}
                                        /> 启用相关检索
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        <input
                                            type="checkbox"
                                            checked={contextSettings.chatUseDaily}
                                            onChange={(e) => persistSettings({ ...contextSettings, chatUseDaily: e.target.checked })}
                                        /> 对话模式使用日线
                                    </label>
                                </div>

                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                                    <label style={{ fontSize: '12px' }}>
                                        History Limit
                                        <input
                                            type="number"
                                            value={contextSettings.historyLimit}
                                            onChange={(e) => persistSettings({ ...contextSettings, historyLimit: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        Recent Turns
                                        <input
                                            type="number"
                                            value={contextSettings.recentLimit}
                                            onChange={(e) => persistSettings({ ...contextSettings, recentLimit: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        Summary Min
                                        <input
                                            type="number"
                                            value={contextSettings.summaryMin}
                                            onChange={(e) => persistSettings({ ...contextSettings, summaryMin: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        Summary Step
                                        <input
                                            type="number"
                                            value={contextSettings.summaryStep}
                                            onChange={(e) => persistSettings({ ...contextSettings, summaryStep: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        Relevant TopK
                                        <input
                                            type="number"
                                            value={contextSettings.relevantTopK}
                                            onChange={(e) => persistSettings({ ...contextSettings, relevantTopK: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        Max Message Chars
                                        <input
                                            type="number"
                                            value={contextSettings.maxMessageChars}
                                            onChange={(e) => persistSettings({ ...contextSettings, maxMessageChars: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        Klines (Chat)
                                        <input
                                            type="number"
                                            value={contextSettings.klineRowsChat}
                                            onChange={(e) => persistSettings({ ...contextSettings, klineRowsChat: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                    <label style={{ fontSize: '12px' }}>
                                        Klines (Assistant)
                                        <input
                                            type="number"
                                            value={contextSettings.klineRowsAssistant}
                                            onChange={(e) => persistSettings({ ...contextSettings, klineRowsAssistant: Number(e.target.value || 0) })}
                                            style={inputStyle}
                                        />
                                    </label>
                                </div>
                            </>
                        )}

                        {settingsTab === 'prompt' && (
                            <div style={{ borderTop: '1px solid #eee', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                <div style={{ fontWeight: 600, fontSize: '12px' }}>System Prompt (per symbol)</div>
                                <label style={{ fontSize: '12px' }}>
                                    Prompt List
                                    <select
                                        value={selectedPromptId ? String(selectedPromptId) : ""}
                                        onChange={(e) => handlePromptSelect(e.target.value)}
                                        style={inputStyle}
                                    >
                                        <option value="">(No prompt selected)</option>
                                        {promptItems.map(item => (
                                            <option key={item.id} value={item.id}>
                                                {item.name}{item.is_active ? " (active)" : ""}
                                            </option>
                                        ))}
                                    </select>
                                </label>

                                <label style={{ fontSize: '12px' }}>
                                    Name
                                    <input
                                        type="text"
                                        value={promptName}
                                        onChange={(e) => setPromptName(e.target.value)}
                                        style={inputStyle}
                                    />
                                </label>

                                <label style={{ fontSize: '12px' }}>
                                    Prompt
                                    <textarea
                                        rows={6}
                                        value={promptText}
                                        onChange={(e) => setPromptText(e.target.value)}
                                        style={{ ...inputStyle, fontFamily: 'monospace' }}
                                    />
                                </label>

                                <label style={{ fontSize: '12px' }}>
                                    <input
                                        type="checkbox"
                                        checked={promptSetActive}
                                        onChange={(e) => setPromptSetActive(e.target.checked)}
                                    /> 保存时设为当前激活
                                </label>

                                {promptError && (
                                    <div style={{ fontSize: '12px', color: '#c00' }}>{promptError}</div>
                                )}

                                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                                    <Button
                                        onPress={handlePromptNew}
                                        style={{ background: '#f3f3f3', border: '1px solid #ddd', padding: '6px 10px', borderRadius: '4px' }}
                                    >
                                        New
                                    </Button>
                                    <Button
                                        onPress={handlePromptSave}
                                        isDisabled={promptLoading}
                                        style={{ background: '#007acc', color: '#fff', border: 'none', padding: '6px 10px', borderRadius: '4px' }}
                                    >
                                        Save
                                    </Button>
                                    <Button
                                        onPress={handlePromptActivate}
                                        isDisabled={!selectedPromptId || promptLoading}
                                        style={{ background: '#f3f3f3', border: '1px solid #ddd', padding: '6px 10px', borderRadius: '4px' }}
                                    >
                                        Set Active
                                    </Button>
                                    <Button
                                        onPress={handlePromptDelete}
                                        isDisabled={!selectedPromptId || promptLoading}
                                        style={{ background: '#fff3f3', border: '1px solid #e5bcbc', padding: '6px 10px', borderRadius: '4px' }}
                                    >
                                        Delete
                                    </Button>
                                </div>
                            </div>
                        )}

                        {settingsTab === 'industry' && (
                            <div style={{ borderTop: '1px solid #eee', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                <div style={{ fontWeight: 600, fontSize: '12px' }}>行业与模板（当前股票）</div>
                                <label style={{ fontSize: '12px' }}>
                                    <input
                                        type="checkbox"
                                        checked={industryEnabled}
                                        onChange={(e) => handleToggleIndustryEnabled(e.target.checked)}
                                    /> 启用行业数据
                                </label>
                                <label style={{ fontSize: '12px' }}>
                                    <input
                                        type="checkbox"
                                        checked={contextSettings.showIndustryInChat}
                                        onChange={(e) => persistSettings({ ...contextSettings, showIndustryInChat: e.target.checked })}
                                        disabled={!industryEnabled}
                                    /> 对话区显示行业信息（仅展示，不写入记忆）
                                </label>
                                <label style={{ fontSize: '12px' }}>
                                    行业
                                    <input
                                        list="industry-options"
                                        value={industryDraft}
                                        onChange={(e) => setIndustryDraft(e.target.value)}
                                        style={inputStyle}
                                        placeholder="手动输入或下拉选择"
                                        disabled={!industryEnabled}
                                    />
                                    <datalist id="industry-options">
                                        {industryOptions.map((item) => (
                                            <option key={item} value={item} />
                                        ))}
                                    </datalist>
                                </label>
                                <Button
                                    onPress={handleSaveIndustry}
                                    style={{ background: '#f3f3f3', border: '1px solid #ddd', padding: '6px 10px', borderRadius: '4px', alignSelf: 'flex-start' }}
                                    isDisabled={!industryEnabled}
                                >
                                    保存行业
                                </Button>

                                <label style={{ fontSize: '12px' }}>
                                    模板覆盖
                                    <select
                                        value={profileDraft}
                                        onChange={(e) => setProfileDraft(e.target.value)}
                                        style={inputStyle}
                                        disabled={!industryEnabled}
                                    >
                                        <option value="">(跟随自动)</option>
                                        {templates.map(t => (
                                            <option key={t.profile_id} value={t.profile_id}>
                                                {t.name || t.profile_id}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <Button
                                    onPress={handleSaveProfileOverride}
                                    style={{ background: '#f3f3f3', border: '1px solid #ddd', padding: '6px 10px', borderRadius: '4px', alignSelf: 'flex-start' }}
                                    isDisabled={!industryEnabled}
                                >
                                    保存模板
                                </Button>
                                {industryError && (
                                    <div style={{ fontSize: '12px', color: '#c00' }}>{industryError}</div>
                                )}
                                {templatesError && (
                                    <div style={{ fontSize: '12px', color: '#c00' }}>{templatesError}</div>
                                )}
                            </div>
                        )}

                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '8px' }}>
                            <Button
                                onPress={() => persistSettings(DEFAULT_SETTINGS)}
                                style={{ background: '#f3f3f3', border: '1px solid #ddd', padding: '6px 12px', borderRadius: '4px' }}
                            >
                                Reset
                            </Button>
                            <Button
                                onPress={() => setSettingsOpen(false)}
                                style={{ background: '#007acc', color: '#fff', border: 'none', padding: '6px 12px', borderRadius: '4px' }}
                            >
                                Done
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
