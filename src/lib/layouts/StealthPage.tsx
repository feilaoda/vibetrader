import { useEffect, useMemo, useState } from 'react';
import { AIAnalysisPanel } from '../charting/pane/AIAnalysisPanel';
import { fetchKlines, fetchRealtime, type AShareRealtime } from '../domain/AShareData';
import { getDefaultSymbol, getWatchlist, loadWatchlistFromServer, type WatchlistItem } from '../domain/Watchlist';
import type { Kline } from '../domain/Kline';

type MarketType = 'crypto' | 'ashare' | 'us';

const MARKET_STORAGE_KEY = 'vibetrader.market';

const getMarket = (): MarketType => {
    try {
        const saved = localStorage.getItem(MARKET_STORAGE_KEY);
        if (saved === 'ashare' || saved === 'us' || saved === 'crypto') return saved;
    } catch {
        // ignore
    }
    return 'ashare';
};

const setMarket = (market: MarketType) => {
    try {
        localStorage.setItem(MARKET_STORAGE_KEY, market);
    } catch {
        // ignore
    }
};

type QuoteState = {
    quote: AShareRealtime | null;
    loading: boolean;
    error: string;
    updatedAt: number | null;
};

const REFRESH_MS = 10000;

const detectMarket = (symbol: string): MarketType => {
    const upper = (symbol || '').toUpperCase();
    if (upper.endsWith('.US')) return 'us';
    if (upper.endsWith('.SH') || upper.endsWith('.SZ') || upper.endsWith('.BJ')) return 'ashare';
    if (upper.includes('USDT') || upper.includes('BTC') || upper.includes('ETH')) return 'crypto';
    return getMarket();
};

const readInitialSymbol = () => {
    const params = new URLSearchParams(window.location.search);
    const urlMarket = params.get('market');
    if (urlMarket === 'ashare' || urlMarket === 'us' || urlMarket === 'crypto') {
        setMarket(urlMarket);
    }
    const market = getMarket();
    const urlSymbol = params.get('symbol')?.toUpperCase();
    return (
        urlSymbol
        || sessionStorage.getItem(`last_selected_symbol_${market}`)
        || localStorage.getItem(`last_selected_symbol_${market}`)
        || sessionStorage.getItem('last_selected_symbol')
        || localStorage.getItem('last_selected_symbol')
        || getDefaultSymbol(market)
    );
};

const persistSymbol = (symbol: string, market: MarketType) => {
    setMarket(market);
    sessionStorage.setItem(`last_selected_symbol_${market}`, symbol);
    localStorage.setItem(`last_selected_symbol_${market}`, symbol);
    sessionStorage.setItem('last_selected_symbol', symbol);
    localStorage.setItem('last_selected_symbol', symbol);
};

const formatPrice = (value?: number) => {
    const num = Number(value ?? 0);
    if (!Number.isFinite(num) || num <= 0) return '--';
    if (num >= 1000) return num.toFixed(2);
    if (num >= 10) return num.toFixed(3);
    return num.toFixed(4);
};

const formatTime = (value?: number | null) => {
    if (!value) return '--';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '--';
    return date.toLocaleTimeString('zh-CN', { hour12: false });
};

const quoteToFallbackKline = (quote: AShareRealtime | null): Kline[] => {
    if (!quote?.price) return [];
    const now = quote.timestamp || Date.now();
    return [{
        date: new Date(now).toISOString().slice(0, 10),
        time: now,
        openTime: now,
        open: Number(quote.open || quote.price),
        high: Number(quote.high || quote.price),
        low: Number(quote.low || quote.price),
        close: Number(quote.price),
        volume: Number(quote.volume || 0),
        closeTime: now,
        isClosed: false,
    } as unknown as Kline];
};

export default function StealthPage() {
    const [symbol, setSymbol] = useState(readInitialSymbol);
    const [symbolInput, setSymbolInput] = useState(symbol);
    const [watchlist, setWatchlist] = useState<WatchlistItem[]>(() => getWatchlist());
    const [klines, setKlines] = useState<Kline[]>([]);
    const [quoteState, setQuoteState] = useState<QuoteState>({
        quote: null,
        loading: false,
        error: '',
        updatedAt: null,
    });

    const activeMarket = useMemo(() => detectMarket(symbol), [symbol]);
    const visibleWatchlist = useMemo(() => {
        return watchlist.filter(item => item.market === activeMarket || activeMarket === 'crypto' && item.market === 'crypto');
    }, [activeMarket, watchlist]);

    const selectSymbol = (nextRaw: string) => {
        const next = (nextRaw || '').trim().toUpperCase();
        if (!next) return;
        const market = detectMarket(next);
        persistSymbol(next, market);
        setSymbol(next);
        setSymbolInput(next);
    };

    const loadQuote = async (target: string) => {
        setQuoteState(prev => ({ ...prev, loading: true, error: '' }));
        try {
            const quote = await fetchRealtime(target);
            setQuoteState({ quote, loading: false, error: quote ? '' : '实时数据不可用', updatedAt: Date.now() });
        } catch (err) {
            setQuoteState(prev => ({
                ...prev,
                loading: false,
                error: err instanceof Error ? err.message : '实时数据不可用',
                updatedAt: Date.now(),
            }));
        }
    };

    useEffect(() => {
        const syncWatchlist = () => setWatchlist(getWatchlist());
        window.addEventListener('watchlist-updated', syncWatchlist);
        syncWatchlist();
        void loadWatchlistFromServer().then(syncWatchlist).catch(() => undefined);
        return () => window.removeEventListener('watchlist-updated', syncWatchlist);
    }, []);

    useEffect(() => {
        persistSymbol(symbol, activeMarket);
        void loadQuote(symbol);
        void fetchKlines(symbol, '1d', undefined, undefined, 365)
            .then(rows => setKlines((rows || []) as unknown as Kline[]))
            .catch(() => setKlines([]));
        const timer = window.setInterval(() => {
            if (!document.hidden) {
                void loadQuote(symbol);
            }
        }, REFRESH_MS);
        return () => window.clearInterval(timer);
    }, [symbol, activeMarket]);

    const aiKlines = klines.length > 0 ? klines : quoteToFallbackKline(quoteState.quote);
    const activeIndex = visibleWatchlist.findIndex(item => item.symbol === symbol);
    const activeLabel = activeIndex >= 0 ? `item ${String(activeIndex + 1).padStart(2, '0')}` : 'manual';

    return (
        <div className="stealth-page">
            <aside className="stealth-sidebar">
                <div className="stealth-brand">Desk</div>
                <form
                    className="stealth-symbol-form"
                    onSubmit={(event) => {
                        event.preventDefault();
                        selectSymbol(symbolInput);
                    }}
                >
                    <input
                        value={symbolInput}
                        onChange={(event) => setSymbolInput(event.target.value)}
                        placeholder="code"
                        spellCheck={false}
                    />
                    <button type="submit">Open</button>
                </form>
                <div className="stealth-watchlist">
                    {visibleWatchlist.length === 0 ? (
                        <div className="stealth-empty">No items</div>
                    ) : visibleWatchlist.map((item, index) => (
                        <button
                            key={`${item.market}-${item.symbol}`}
                            type="button"
                            className={`stealth-watch-item ${item.symbol === symbol ? 'active' : ''}`}
                            onClick={() => selectSymbol(item.symbol)}
                            title={`${item.name || item.symbol} ${item.symbol}`}
                            aria-label={`${item.name || item.symbol} ${item.symbol}`}
                        >
                            <em>{String(index + 1).padStart(2, '0')}</em>
                            <span>{item.name || item.symbol}</span>
                            <small>{item.symbol}</small>
                        </button>
                    ))}
                </div>
            </aside>

            <main className="stealth-main">
                <section className="stealth-quote-strip">
                    <div className="stealth-quote-head">
                        <div>
                            <div className="stealth-symbol">Workspace</div>
                            <div className="stealth-subtle">
                                {activeLabel} · {quoteState.loading ? 'syncing' : `updated ${formatTime(quoteState.updatedAt)}`}
                            </div>
                        </div>
                        <button type="button" onClick={() => void loadQuote(symbol)}>Sync</button>
                    </div>
                    <div className="stealth-metrics">
                        <div className="stealth-metric primary">
                            <span>A</span>
                            <strong>{formatPrice(quoteState.quote?.price)}</strong>
                        </div>
                        <div className="stealth-metric">
                            <span>B</span>
                            <strong>{formatPrice(quoteState.quote?.high)}</strong>
                        </div>
                        <div className="stealth-metric">
                            <span>C</span>
                            <strong>{formatPrice(quoteState.quote?.low)}</strong>
                        </div>
                    </div>
                    {quoteState.error && <div className="stealth-error">{quoteState.error}</div>}
                </section>

                <section className="stealth-ai-panel">
                    <AIAnalysisPanel
                        symbol={symbol}
                        klines={aiKlines}
                        isOpen={true}
                        onClose={() => undefined}
                    />
                </section>
            </main>
        </div>
    );
}
