import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import {
    getWatchlist,
    loadWatchlistFromServer,
    moveWatchlistItem,
    moveWatchlistItemInMarket,
    type WatchlistItem
} from "../domain/Watchlist";
import "./WatchlistPage.css";

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || "http://localhost:8000";

type MarketFilter = "all" | "ashare" | "us" | "crypto";

const marketLabel = (market: "ashare" | "us" | "crypto") => {
    if (market === "ashare") return "A股";
    if (market === "us") return "美股";
    return "Crypto";
};

const detectMarket = (symbol: string) => {
    const upper = (symbol || "").toUpperCase();
    if (upper.endsWith(".SZ") || upper.endsWith(".SH")) return "ashare";
    if (upper.endsWith(".US")) return "us";
    return "crypto";
};

export function WatchlistPage() {
    const navigate = useNavigate();
    const [list, setList] = useState<WatchlistItem[]>([]);
    const [search, setSearch] = useState("");
    const [marketFilter, setMarketFilter] = useState<MarketFilter>("all");
    const [symbolNames, setSymbolNames] = useState<Record<string, string>>({});

    const refreshList = () => {
        setList(getWatchlist());
    };

    useEffect(() => {
        refreshList();
        loadWatchlistFromServer();
        const handler = () => refreshList();
        window.addEventListener("watchlist-updated", handler);
        return () => window.removeEventListener("watchlist-updated", handler);
    }, []);

    const resolveSymbolNames = (items: WatchlistItem[]) => {
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

    useEffect(() => {
        resolveSymbolNames(list);
    }, [list]);

    const filteredList = useMemo(() => {
        const query = search.trim().toUpperCase();
        return list.filter(item => {
            if (marketFilter !== "all" && item.market !== marketFilter) return false;
            if (!query) return true;
            const name = item.name || symbolNames[item.symbol] || "";
            return item.symbol.toUpperCase().includes(query) || name.includes(query);
        });
    }, [list, search, marketFilter, symbolNames]);

    const handleMove = (item: WatchlistItem, direction: "up" | "down" | "top") => {
        const updater = marketFilter === "all" ? moveWatchlistItem : moveWatchlistItemInMarket;
        const next = updater(item.symbol, item.market, direction);
        setList(next);
    };

    return (
        <div className="watchlist-manage">
            <header className="watchlist-manage__header">
                <div className="watchlist-manage__title">
                    <button className="watchlist-manage__back" onClick={() => navigate("/")}>
                        返回首页
                    </button>
                    <div>
                        <div className="watchlist-manage__headline">Watchlist 排序</div>
                        <div className="watchlist-manage__sub">支持按市场调整顺序，修改后自动同步到服务端。</div>
                    </div>
                </div>
                <div className="watchlist-manage__actions">
                    <button className="watchlist-manage__btn" onClick={() => loadWatchlistFromServer()}>
                        刷新
                    </button>
                </div>
            </header>

            <div className="watchlist-manage__toolbar">
                <input
                    className="watchlist-manage__search"
                    placeholder="搜索 symbol 或名称"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                />
                <select
                    className="watchlist-manage__select"
                    value={marketFilter}
                    onChange={(e) => setMarketFilter(e.target.value as MarketFilter)}
                >
                    <option value="all">全部市场</option>
                    <option value="ashare">A股</option>
                    <option value="us">美股</option>
                    <option value="crypto">Crypto</option>
                </select>
            </div>

            <div className="watchlist-manage__list">
                <div className="watchlist-manage__row watchlist-manage__row--head">
                    <div>#</div>
                    <div>代码</div>
                    <div>名称</div>
                    <div>市场</div>
                    <div>操作</div>
                </div>
                {filteredList.length === 0 && (
                    <div className="watchlist-manage__empty">没有可排序的自选。</div>
                )}
                {filteredList.map((item, idx) => {
                    const name = item.name || symbolNames[item.symbol] || "";
                    return (
                        <div key={`${item.symbol}-${item.market}`} className="watchlist-manage__row">
                            <div>{idx + 1}</div>
                            <div className="watchlist-manage__symbol">{item.symbol}</div>
                            <div className="watchlist-manage__name">{name || "-"}</div>
                            <div className="watchlist-manage__market">{marketLabel(item.market)}</div>
                            <div className="watchlist-manage__ops">
                                <button
                                    className="watchlist-manage__btn watchlist-manage__btn--ghost"
                                    onClick={() => handleMove(item, "top")}
                                >
                                    置顶
                                </button>
                                <button
                                    className="watchlist-manage__btn watchlist-manage__btn--ghost"
                                    onClick={() => handleMove(item, "up")}
                                >
                                    上移
                                </button>
                                <button
                                    className="watchlist-manage__btn watchlist-manage__btn--ghost"
                                    onClick={() => handleMove(item, "down")}
                                >
                                    下移
                                </button>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
