/**
 * A股数据获取模块
 * 通过 FastAPI 后端代理 AKShare 数据
 */

const API_BASE_URL = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_ASHARE_API_URL || 'http://localhost:8000';

export interface AShareKline {
    openTime: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    closeTime: number;
}

export interface AShareSymbol {
    symbol: string;
    name: string;
}

export interface AShareRealtime {
    symbol: string;
    name: string;
    price: number;
    change: number;
    changePercent: number;
    open: number;
    high: number;
    low: number;
    volume: number;
    amount: number;
    timestamp: number;
}

/**
 * 时间周期映射
 */
export const timeframe_to_ashare: Record<string, string> = {
    '1m': '1m',
    '5m': '5m',
    '15m': '15m',
    '30m': '30m',
    '1h': '60m',
    '1d': '1d',
    '1D': '1d',
    '1w': '1w',
    '1W': '1w',
    '1M': '1M',
};

/**
 * 获取 K 线数据
 */
export async function fetchKlines(
    symbol: string,
    period: string = '1d',
    startDate?: string,
    endDate?: string,
    limit: number = 1000
): Promise<AShareKline[]> {
    const params = new URLSearchParams({
        period: timeframe_to_ashare[period] || period,
        limit: limit.toString(),
    });

    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);

    const url = `${API_BASE_URL}/api/klines/${encodeURIComponent(symbol)}?${params}`;

    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Failed to fetch klines: ${response.statusText}`);
    }

    const data = await response.json();
    return data.data || [];
}

/**
 * 搜索股票代码
 */
export async function fetchSymbolList(
    filterText: string,
    init?: RequestInit
): Promise<AShareSymbol[]> {
    const params = new URLSearchParams({
        q: filterText,
        limit: '50',
    });

    const url = `${API_BASE_URL}/api/symbols?${params}`;

    try {
        const response = await fetch(url, init);
        if (!response.ok) {
            return defaultSymbols;
        }

        const data = await response.json();
        return data.data || defaultSymbols;
    } catch (e) {
        console.error('Error fetching A-share symbols:', e);
        return defaultSymbols;
    }
}

/**
 * 获取实时行情
 */
export async function fetchRealtime(symbol: string): Promise<AShareRealtime | null> {
    const url = `${API_BASE_URL}/api/realtime/${encodeURIComponent(symbol)}`;

    try {
        const response = await fetch(url);
        if (!response.ok) {
            return null;
        }
        return await response.json();
    } catch (e) {
        console.error('Error fetching realtime data:', e);
        return null;
    }
}

/**
 * 健康检查
 */
export async function checkHealth(): Promise<boolean> {
    try {
        const response = await fetch(`${API_BASE_URL}/api/health`, {
            signal: AbortSignal.timeout(3000),
        });
        return response.ok;
    } catch {
        return false;
    }
}

/**
 * 默认股票列表 (常用 A 股)
 */
const defaultSymbols: AShareSymbol[] = [
    { symbol: '600519.SH', name: '贵州茅台' },
    { symbol: '000858.SZ', name: '五粮液' },
    { symbol: '601318.SH', name: '中国平安' },
    { symbol: '600036.SH', name: '招商银行' },
    { symbol: '000001.SZ', name: '平安银行' },
    { symbol: '600900.SH', name: '长江电力' },
    { symbol: '601012.SH', name: '隆基绿能' },
    { symbol: '300750.SZ', name: '宁德时代' },
];

export { defaultSymbols };
