import React, { useEffect, useMemo, useRef, useState } from "react";

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || "http://localhost:8000";
const NOTES_WIDGET_POS_KEY = "vibetrader_symbol_notes_widget_pos";

type SymbolNote = {
    id: number;
    symbol: string;
    note_date: string;
    content: string;
    is_global?: boolean;
    created_at?: string | null;
    updated_at?: string | null;
};

type Props = {
    symbol: string;
    rightOffset?: number;
};

type NoteGroup = {
    date: string;
    items: SymbolNote[];
};

const toDateInputValue = (raw?: string | null) => {
    if (!raw) return new Date().toISOString().slice(0, 10);
    const text = String(raw).trim();
    if (/^\d{8}$/.test(text)) {
        return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
    }
    if (/^\d{4}-\d{2}-\d{2}/.test(text)) {
        return text.slice(0, 10);
    }
    return new Date().toISOString().slice(0, 10);
};

export const SymbolNotesWidget: React.FC<Props> = ({ symbol, rightOffset = 12 }) => {
    const widgetRef = useRef<HTMLDivElement | null>(null);
    const dragOffsetRef = useRef<{ dx: number; dy: number } | null>(null);
    const customPosRef = useRef<{ left: number; top: number } | null>(null);
    const [notes, setNotes] = useState<SymbolNote[]>([]);
    const [loading, setLoading] = useState(false);
    const [errorText, setErrorText] = useState("");
    const [collapsed, setCollapsed] = useState(false);
    const [customPos, setCustomPos] = useState<{ left: number; top: number } | null>(null);

    const [modalOpen, setModalOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [editing, setEditing] = useState<SymbolNote | null>(null);
    const [noteDate, setNoteDate] = useState(() => toDateInputValue());
    const [content, setContent] = useState("");
    const [isGlobal, setIsGlobal] = useState(false);

    const grouped = useMemo<NoteGroup[]>(() => {
        const groups: Record<string, SymbolNote[]> = {};
        for (const n of notes) {
            const d = n.note_date || "未设置日期";
            if (!groups[d]) groups[d] = [];
            groups[d].push(n);
        }
        return Object.keys(groups)
            .sort((a, b) => (a < b ? 1 : -1))
            .map((d) => ({ date: d, items: groups[d] }));
    }, [notes]);

    const updateCustomPos = (pos: { left: number; top: number } | null) => {
        customPosRef.current = pos;
        setCustomPos(pos);
    };

    useEffect(() => {
        try {
            const raw = localStorage.getItem(NOTES_WIDGET_POS_KEY);
            if (!raw) return;
            const obj = JSON.parse(raw || "{}");
            const left = Number(obj?.left);
            const top = Number(obj?.top);
            if (Number.isFinite(left) && Number.isFinite(top)) {
                updateCustomPos({ left, top });
            }
        } catch {
            // ignore
        }
    }, []);

    const loadNotes = async () => {
        const sym = (symbol || "").trim();
        if (!sym) {
            setNotes([]);
            return;
        }
        setLoading(true);
        setErrorText("");
        try {
            const res = await fetch(`${API_BASE_URL}/api/notes?symbol=${encodeURIComponent(sym)}&limit=300`);
            if (!res.ok) {
                throw new Error(`HTTP ${res.status}`);
            }
            const json = await res.json();
            setNotes(Array.isArray(json?.data) ? json.data : []);
        } catch (err: any) {
            setErrorText(err?.message || "加载笔记失败");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadNotes();
    }, [symbol]);

    useEffect(() => {
        const onMouseMove = (e: MouseEvent) => {
            if (!dragOffsetRef.current) return;
            const panel = widgetRef.current;
            if (!panel) return;
            const { dx, dy } = dragOffsetRef.current;
            const panelWidth = panel.offsetWidth || 340;
            const panelHeight = panel.offsetHeight || 240;
            const minLeft = 8;
            const minTop = 8;
            const maxLeft = Math.max(minLeft, window.innerWidth - panelWidth - 8);
            const maxTop = Math.max(minTop, window.innerHeight - panelHeight - 8);
            const left = Math.max(minLeft, Math.min(maxLeft, e.clientX - dx));
            const top = Math.max(minTop, Math.min(maxTop, e.clientY - dy));
            updateCustomPos({ left, top });
        };
        const onMouseUp = () => {
            if (!dragOffsetRef.current) return;
            dragOffsetRef.current = null;
            if (customPosRef.current) {
                try {
                    localStorage.setItem(NOTES_WIDGET_POS_KEY, JSON.stringify(customPosRef.current));
                } catch {
                    // ignore
                }
            }
        };
        window.addEventListener("mousemove", onMouseMove, true);
        window.addEventListener("mouseup", onMouseUp, true);
        return () => {
            window.removeEventListener("mousemove", onMouseMove, true);
            window.removeEventListener("mouseup", onMouseUp, true);
        };
    }, []);

    const handleDragStart = (e: React.MouseEvent<HTMLDivElement>) => {
        e.stopPropagation();
        const target = e.target as HTMLElement;
        if (target.closest('[data-no-drag="1"]')) return;
        e.preventDefault();
        const panel = widgetRef.current;
        if (!panel) return;
        const rect = panel.getBoundingClientRect();
        dragOffsetRef.current = {
            dx: e.clientX - rect.left,
            dy: e.clientY - rect.top,
        };
        updateCustomPos({ left: rect.left, top: rect.top });
    };

    const openCreateModal = () => {
        setEditing(null);
        setNoteDate(toDateInputValue());
        setContent("");
        setIsGlobal(false);
        setModalOpen(true);
    };

    const openEditModal = (item: SymbolNote) => {
        setEditing(item);
        setNoteDate(toDateInputValue(item.note_date));
        setContent(item.content || "");
        setIsGlobal(Boolean(item.is_global));
        setModalOpen(true);
    };

    const handleSave = async () => {
        const text = content.trim();
        if (!text) {
            setErrorText("笔记内容不能为空");
            return;
        }
        const sym = (symbol || "").trim();
        if (!sym) {
            setErrorText("无效股票代码");
            return;
        }
        setSaving(true);
        setErrorText("");
        try {
            const payload = {
                symbol: sym,
                note_date: toDateInputValue(noteDate),
                content: text,
                is_global: isGlobal,
            };
            const url = editing ? `${API_BASE_URL}/api/notes/${editing.id}` : `${API_BASE_URL}/api/notes`;
            const method = editing ? "PUT" : "POST";
            const res = await fetch(url, {
                method,
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const detail = await res.text();
                throw new Error(detail || `HTTP ${res.status}`);
            }
            setModalOpen(false);
            await loadNotes();
        } catch (err: any) {
            setErrorText(err?.message || "保存失败");
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (item: SymbolNote) => {
        if (!window.confirm(`删除 ${item.note_date} 的这条笔记？`)) return;
        try {
            const res = await fetch(`${API_BASE_URL}/api/notes/${item.id}`, { method: "DELETE" });
            if (!res.ok) {
                const detail = await res.text();
                throw new Error(detail || `HTTP ${res.status}`);
            }
            await loadNotes();
        } catch (err: any) {
            setErrorText(err?.message || "删除失败");
        }
    };

    return (
        <>
            <div
                ref={widgetRef}
                onMouseDown={(e) => e.stopPropagation()}
                onWheel={(e) => e.stopPropagation()}
                style={{
                    position: "absolute",
                    ...(customPos
                        ? { left: `${customPos.left}px`, top: `${customPos.top}px` }
                        : { right: `${Math.max(8, rightOffset)}px`, top: "78px" }),
                    width: collapsed ? "160px" : "340px",
                    maxHeight: collapsed ? "46px" : "70vh",
                    zIndex: 120,
                    border: "1px solid #e5c66b",
                    background: "#fff8dc",
                    borderRadius: "8px",
                    boxShadow: "0 3px 12px rgba(0, 0, 0, 0.15)",
                    overflow: "hidden",
                    display: "flex",
                    flexDirection: "column",
                }}
            >
                <div
                    onMouseDown={handleDragStart}
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "6px 10px",
                        borderBottom: collapsed ? "none" : "1px solid #ecd79c",
                        background: "#fbeab1",
                        fontSize: "12px",
                        fontWeight: 600,
                        cursor: "move",
                        userSelect: "none",
                    }}
                >
                    <span>便签 {notes.length ? `(${notes.length})` : ""}</span>
                    <div style={{ display: "flex", gap: "6px" }}>
                        {!collapsed && (
                            <button
                                data-no-drag="1"
                                onClick={openCreateModal}
                                style={{ border: "none", background: "#2f7cf6", color: "#fff", borderRadius: "4px", padding: "2px 8px", cursor: "pointer", fontSize: "12px" }}
                            >
                                新增
                            </button>
                        )}
                        <button
                            data-no-drag="1"
                            onClick={() => setCollapsed((v) => !v)}
                            style={{ border: "none", background: "#d7b861", color: "#222", borderRadius: "4px", padding: "2px 8px", cursor: "pointer", fontSize: "12px" }}
                        >
                            {collapsed ? "展开" : "收起"}
                        </button>
                    </div>
                </div>

                {!collapsed && (
                    <div style={{ padding: "8px", overflowY: "auto", minHeight: "96px" }}>
                        {loading && <div style={{ fontSize: "12px", color: "#666" }}>加载中...</div>}
                        {!loading && errorText && (
                            <div style={{ fontSize: "12px", color: "#b42318", marginBottom: "8px" }}>{errorText}</div>
                        )}
                        {!loading && !errorText && grouped.length === 0 && (
                            <div style={{ fontSize: "12px", color: "#666" }}>暂无笔记，点击“新增”记录。</div>
                        )}
                        {!loading && grouped.map((group) => (
                            <div key={group.date} style={{ marginBottom: "10px" }}>
                                <div style={{ fontSize: "11px", color: "#7a5a00", fontWeight: 700, marginBottom: "6px" }}>{group.date}</div>
                                {group.items.map((item) => (
                                    <div key={item.id} style={{ background: "#fffdf2", border: "1px solid #ead8a6", borderRadius: "6px", padding: "8px", marginBottom: "6px" }}>
                                        <div style={{ whiteSpace: "pre-wrap", fontSize: "12px", color: "#232323", lineHeight: 1.4 }}>{item.content}</div>
                                        <div style={{ display: "flex", justifyContent: "space-between", marginTop: "6px" }}>
                                            <span style={{ fontSize: "10px", color: "#8a8a8a", display: "flex", gap: "6px", alignItems: "center" }}>
                                                {item.is_global && <span style={{ color: "#8b5e00", fontWeight: 700 }}>全局</span>}
                                                {item.is_global && item.symbol && item.symbol !== symbol && (
                                                    <span style={{ color: "#6b7280" }}>来自 {item.symbol}</span>
                                                )}
                                                <span>{item.updated_at ? String(item.updated_at).slice(0, 16) : ""}</span>
                                            </span>
                                            <span style={{ display: "flex", gap: "6px" }}>
                                                <button data-no-drag="1" onClick={() => openEditModal(item)} style={{ border: "none", background: "transparent", color: "#1d4ed8", cursor: "pointer", fontSize: "11px" }}>编辑</button>
                                                <button data-no-drag="1" onClick={() => handleDelete(item)} style={{ border: "none", background: "transparent", color: "#b42318", cursor: "pointer", fontSize: "11px" }}>删除</button>
                                            </span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {modalOpen && (
                <div
                    style={{
                        position: "fixed",
                        inset: 0,
                        background: "rgba(0,0,0,0.35)",
                        zIndex: 2000,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                    onClick={() => (!saving ? setModalOpen(false) : undefined)}
                >
                    <div
                        style={{
                            width: "520px",
                            maxWidth: "92vw",
                            background: "#fff",
                            borderRadius: "10px",
                            boxShadow: "0 12px 30px rgba(0,0,0,0.25)",
                            padding: "14px",
                            display: "flex",
                            flexDirection: "column",
                            gap: "10px",
                        }}
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div style={{ fontSize: "15px", fontWeight: 700 }}>{editing ? "编辑笔记" : "新增笔记"}</div>
                        <div style={{ fontSize: "12px", color: "#555" }}>股票：{symbol}</div>
                        <label style={{ fontSize: "12px", fontWeight: 600 }}>
                            日期
                            <input
                                type="date"
                                value={noteDate}
                                onChange={(e) => setNoteDate(e.target.value)}
                                style={{ width: "100%", marginTop: "4px", padding: "8px", borderRadius: "6px", border: "1px solid #d0d7de" }}
                            />
                        </label>
                        <label style={{ fontSize: "12px", fontWeight: 600 }}>
                            内容
                            <textarea
                                value={content}
                                onChange={(e) => setContent(e.target.value)}
                                placeholder="记录你的观察、交易计划、复盘心得..."
                                rows={8}
                                style={{ width: "100%", marginTop: "4px", padding: "8px", borderRadius: "6px", border: "1px solid #d0d7de", resize: "vertical" }}
                            />
                        </label>
                        <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", fontWeight: 600 }}>
                            <input
                                type="checkbox"
                                checked={isGlobal}
                                onChange={(e) => setIsGlobal(e.target.checked)}
                            />
                            全局可见（所有股票显示）
                        </label>
                        <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                            <button
                                disabled={saving}
                                onClick={() => setModalOpen(false)}
                                style={{ border: "1px solid #d0d7de", background: "#fff", borderRadius: "6px", padding: "6px 12px", cursor: saving ? "not-allowed" : "pointer" }}
                            >
                                取消
                            </button>
                            <button
                                disabled={saving}
                                onClick={handleSave}
                                style={{ border: "none", background: "#2f7cf6", color: "#fff", borderRadius: "6px", padding: "6px 12px", cursor: saving ? "not-allowed" : "pointer" }}
                            >
                                {saving ? "保存中..." : "保存"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};
