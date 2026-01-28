/**
 * 自选股管理模块
 * 使用 localStorage 持久化存储
 */

const WATCHLIST_KEY = 'vibetrader_watchlist';

export interface WatchlistItem {
    symbol: string;
    name?: string;
    market: 'ashare' | 'crypto';
    addedAt: number;
}

/**
 * 获取自选股列表
 */
export function getWatchlist(): WatchlistItem[] {
    try {
        const data = localStorage.getItem(WATCHLIST_KEY);
        return data ? JSON.parse(data) : [];
    } catch {
        return [];
    }
}

/**
 * 添加到自选股
 */
export function addToWatchlist(symbol: string, market: 'ashare' | 'crypto', name?: string): WatchlistItem[] {
    const list = getWatchlist();

    // 检查是否已存在
    if (list.some(item => item.symbol === symbol && item.market === market)) {
        return list;
    }

    const newItem: WatchlistItem = {
        symbol,
        name,
        market,
        addedAt: Date.now(),
    };

    list.unshift(newItem); // 添加到开头
    saveWatchlist(list);
    return list;
}

/**
 * 从自选股移除
 */
export function removeFromWatchlist(symbol: string, market: 'ashare' | 'crypto'): WatchlistItem[] {
    const list = getWatchlist();
    const filtered = list.filter(item => !(item.symbol === symbol && item.market === market));
    saveWatchlist(filtered);
    return filtered;
}

/**
 * 检查是否在自选股中
 */
export function isInWatchlist(symbol: string, market: 'ashare' | 'crypto'): boolean {
    const list = getWatchlist();
    return list.some(item => item.symbol === symbol && item.market === market);
}

/**
 * 切换自选状态
 */
export function toggleWatchlist(symbol: string, market: 'ashare' | 'crypto', name?: string): boolean {
    if (isInWatchlist(symbol, market)) {
        removeFromWatchlist(symbol, market);
        return false;
    } else {
        addToWatchlist(symbol, market, name);
        return true;
    }
}

/**
 * 获取指定市场的自选股
 */
export function getWatchlistByMarket(market: 'ashare' | 'crypto'): WatchlistItem[] {
    return getWatchlist().filter(item => item.market === market);
}

/**
 * 保存自选股列表
 */
function saveWatchlist(list: WatchlistItem[]): void {
    try {
        localStorage.setItem(WATCHLIST_KEY, JSON.stringify(list));
    } catch (e) {
        console.error('Failed to save watchlist:', e);
    }
}

/**
 * 获取默认 symbol（优先自选股第一个，否则返回默认）
 */
export function getDefaultSymbol(market: 'ashare' | 'crypto'): string {
    const list = getWatchlistByMarket(market);
    if (list.length > 0) {
        return list[0].symbol;
    }
    return market === 'ashare' ? '600519.SH' : 'BTCUSDT';
}
