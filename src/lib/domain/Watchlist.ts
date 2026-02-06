/**
 * 自选股管理模块
 * 本地 localStorage + 服务端双重存储
 */

const WATCHLIST_KEY = 'vibetrader_watchlist';
const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || 'http://localhost:8000';

export interface WatchlistItem {
    symbol: string;
    name?: string;
    market: 'ashare' | 'crypto' | 'us';
    addedAt: number;
}

// 内存缓存
let memoryList: WatchlistItem[] = [];

// 初始化：从 localStorage 加载
try {
    const data = localStorage.getItem(WATCHLIST_KEY);
    if (data) memoryList = JSON.parse(data);
} catch {
    memoryList = [];
}

/**
 * 从服务端加载自选股（初始化时调用）
 */
export async function loadWatchlistFromServer(): Promise<void> {
    try {
        const response = await fetch(`${API_BASE_URL}/api/watchlist`);
        if (response.ok) {
            const json = await response.json();
            if (json.data && Array.isArray(json.data)) {
                // 合并或覆盖本地数据
                // 这里选择以服务端为准
                memoryList = json.data;
                saveWatchlist(memoryList);

                // 触发更新事件
                window.dispatchEvent(new Event('watchlist-updated'));
            }
        }
    } catch (e) {
        console.error('Failed to load watchlist from server:', e);
    }
}

/**
 * 获取自选股列表
 */
export function getWatchlist(): WatchlistItem[] {
    return [...memoryList];
}

/**
 * 添加到自选股
 */
export function addToWatchlist(symbol: string, market: 'ashare' | 'crypto' | 'us', name?: string): WatchlistItem[] {
    // 检查是否已存在
    if (memoryList.some(item => item.symbol === symbol && item.market === market)) {
        return [...memoryList];
    }

    const newItem: WatchlistItem = {
        symbol,
        name,
        market,
        addedAt: Date.now(),
    };

    memoryList.unshift(newItem); // 添加到开头
    saveWatchlist(memoryList);

    // 触发更新事件
    window.dispatchEvent(new Event('watchlist-updated'));

    // 异步同步到服务端
    fetch(`${API_BASE_URL}/api/watchlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newItem)
    }).then(async (res) => {
        if (!res.ok) return;
        const json = await res.json();
        if (json?.data && Array.isArray(json.data)) {
            memoryList = json.data;
            saveWatchlist(memoryList);
            window.dispatchEvent(new Event('watchlist-updated'));
        }
    }).catch(e => console.error('Failed to sync add to server:', e));

    return [...memoryList];
}

/**
 * 从自选股移除
 */
export function removeFromWatchlist(symbol: string, market: 'ashare' | 'crypto' | 'us'): WatchlistItem[] {
    memoryList = memoryList.filter(item => !(item.symbol === symbol && item.market === market));
    saveWatchlist(memoryList);

    // 触发更新事件
    window.dispatchEvent(new Event('watchlist-updated'));

    // 异步同步到服务端
    fetch(`${API_BASE_URL}/api/watchlist?symbol=${encodeURIComponent(symbol)}&market=${market}`, {
        method: 'DELETE'
    }).catch(e => console.error('Failed to sync remove to server:', e));

    return [...memoryList];
}

/**
 * 检查是否在自选股中
 */
export function isInWatchlist(symbol: string, market: 'ashare' | 'crypto' | 'us'): boolean {
    return memoryList.some(item => item.symbol === symbol && item.market === market);
}

/**
 * 切换自选状态
 */
export function toggleWatchlist(symbol: string, market: 'ashare' | 'crypto' | 'us', name?: string): boolean {
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
export function getWatchlistByMarket(market: 'ashare' | 'crypto' | 'us'): WatchlistItem[] {
    return memoryList.filter(item => item.market === market);
}

/**
 * 保存自选股列表到本地
 */
/**
 * 保存自选股列表到本地
 */
function saveWatchlist(list: WatchlistItem[]): void {
    try {
        localStorage.setItem(WATCHLIST_KEY, JSON.stringify(list));
    } catch (e) {
        console.error('Failed to save watchlist locally:', e);
    }
}

/**
 * 移动自选股顺序
 */
function applyWatchlistOrder(newList: WatchlistItem[]): WatchlistItem[] {
    if (newList.length === memoryList.length && newList.every((item, idx) => item === memoryList[idx])) {
        return [...memoryList];
    }
    memoryList = newList;
    saveWatchlist(memoryList);

    // 触发更新事件
    window.dispatchEvent(new Event('watchlist-updated'));

    // 异步同步到服务端 (覆盖更新)
    fetch(`${API_BASE_URL}/api/watchlist/sync`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newList)
    }).catch(e => console.error('Failed to sync order to server:', e));

    return [...memoryList];
}

export function moveWatchlistItem(symbol: string, market: 'ashare' | 'crypto' | 'us', direction: 'up' | 'down' | 'top'): WatchlistItem[] {
    const index = memoryList.findIndex(item => item.symbol === symbol && item.market === market);
    if (index === -1) return [...memoryList];

    const item = memoryList[index];
    const newList = [...memoryList];

    if (direction === 'top') {
        newList.splice(index, 1);
        newList.unshift(item);
    } else if (direction === 'up' && index > 0) {
        newList.splice(index, 1);
        newList.splice(index - 1, 0, item);
    } else if (direction === 'down' && index < newList.length - 1) {
        newList.splice(index, 1);
        newList.splice(index + 1, 0, item);
    } else {
        return [...memoryList];
    }

    return applyWatchlistOrder(newList);
}

/**
 * 在指定市场内移动自选股顺序（不影响其它市场的相对位置）
 */
export function moveWatchlistItemInMarket(
    symbol: string,
    market: 'ashare' | 'crypto' | 'us',
    direction: 'up' | 'down' | 'top'
): WatchlistItem[] {
    const index = memoryList.findIndex(item => item.symbol === symbol && item.market === market);
    if (index === -1) return [...memoryList];

    const marketIndices = memoryList
        .map((item, idx) => (item.market === market ? idx : -1))
        .filter(idx => idx >= 0);
    const position = marketIndices.indexOf(index);
    if (position === -1) return [...memoryList];

    let targetIndex: number | null = null;
    if (direction === 'top') {
        if (position === 0) return [...memoryList];
        targetIndex = marketIndices[0];
    } else if (direction === 'up') {
        if (position === 0) return [...memoryList];
        targetIndex = marketIndices[position - 1];
    } else if (direction === 'down') {
        if (position >= marketIndices.length - 1) return [...memoryList];
        targetIndex = marketIndices[position + 1];
    }

    if (targetIndex == null) return [...memoryList];

    const newList = [...memoryList];
    const temp = newList[targetIndex];
    newList[targetIndex] = newList[index];
    newList[index] = temp;

    return applyWatchlistOrder(newList);
}

/**
 * 获取默认 symbol（优先自选股第一个，否则返回默认）
 */
export function getDefaultSymbol(market: 'ashare' | 'crypto' | 'us'): string {
    const list = getWatchlistByMarket(market);
    if (list.length > 0) {
        return list[0].symbol;
    }
    if (market === 'ashare') return '600519.SH';
    if (market === 'us') return 'SPX.US';
    return 'BTCUSDT';
}
