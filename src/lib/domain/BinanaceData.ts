
const MAX_LIMIT = 1000; // Binance API max limit per request

const BINANCE_API_URL_DEFAULT = '/binance/api/v3';
const BINANCE_API_URL_US = '/binance-us/api/v3';
const BINANCE_PRIMARY = (import.meta.env.VITE_BINANCE_PRIMARY || 'us').toLowerCase() === 'com'
    ? BINANCE_API_URL_DEFAULT
    : BINANCE_API_URL_US;

interface Kline {
    openTime: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    closeTime: number;
    quoteAssetVolume: number;
    numberOfTrades: number;
    takerBuyBaseAssetVolume: number;
    takerBuyQuoteAssetVolume: number;
    ignore: number;
}

const timeframe_to_binance = {
    '1m': '1m', // 1 minute
    '3m': '3m', // 3 minutes
    '5m': '5m', // 5 minutes
    '15m': '15m', // 15 minutes
    '30m': '30m', // 30 minutes
    '1h': '1h', // 1 hour
    '2h': '2h', // 2 hours
    '4h': '4h', // 4 hours
    '1D': '1d', // 1 day
    '1W': '1w', // 1 week
    '1M': '1M', // 1 month
};


export const timeframe_to_pinetsProvider = {
    '1m': '1',
    '3m': '3',
    '5m': '5', // 5 minutes
    '15m': '15', // 15 minutes
    '30m': '30', // 30 minutes
    '1h': '60', // 1 hour
    '2h': '120', // 2 hours
    '4h': '240', // 4 hours
    '1d': '1D', // 1 day
    '1w': '1W', // 1 week
    '1M': '1M', // 1 month
};

/**
 * Fetches a batch of klines from Binance API
 */
export async function fetchKlinesBatch(
    symbol: string,
    interval: string,
    startTime: number,
    endTime: number,
    limit: number = MAX_LIMIT
): Promise<Kline[]> {
    interval = timeframe_to_binance[interval] || interval
    const query = `/klines?symbol=${symbol}&interval=${interval}&startTime=${startTime}&endTime=${endTime}&limit=${limit}`;

    const data = await fetchBinanceJson<unknown[][]>(query);
    if (!data) {
        return [];
    }

    return data.map((item: unknown[]) => ({
        openTime: item[0],
        open: parseFloat(item[1] as string),
        high: parseFloat(item[2] as string),
        low: parseFloat(item[3] as string),
        close: parseFloat(item[4] as string),
        volume: parseFloat(item[5] as string),
        closeTime: item[6],
        quoteAssetVolume: parseFloat(item[7] as string),
        numberOfTrades: parseInt(item[8] as string),
        takerBuyBaseAssetVolume: parseFloat(item[9] as string),
        takerBuyQuoteAssetVolume: parseFloat(item[10] as string),
        ignore: item[11],
    }));
}


/**
 * Fetches all klines with pagination
 */
export async function fetchAllKlines(
    symbol: string,
    interval: string,
    startTime: number,
    endTime: number,
    limit: number,
): Promise<Kline[]> {
    interval = timeframe_to_binance[interval] || interval

    const allKlines: Kline[] = [];

    let currentStartTime = startTime;
    let batchNumber = 1;
    let count = 0;

    while (currentStartTime < endTime && count < limit) {
        console.log(`\nFetching ${symbol}, ${interval}, batch ${batchNumber}...`);

        const batch = await fetchKlinesBatch(symbol, interval, currentStartTime, endTime, limit);

        if (batch.length === 0) {
            // console.log('No more data available');
            break;
        }

        allKlines.push(...batch);
        console.log(`Batch ${batchNumber}: Fetched ${batch.length} candles. Total: ${allKlines.length}`);

        // If we got less than the max limit, we've reached the end
        if (batch.length < limit) {
            // console.log('Reached end of data');
            break;
        }

        count += batch.length

        // Set next startTime to the openTime of the last candle + 1ms
        // This ensures we don't duplicate the last candle
        const lastCandle = batch[batch.length - 1];
        currentStartTime = lastCandle.openTime + 1;
        batchNumber++;

        // Add a small delay to avoid rate limiting
        await new Promise((resolve) => setTimeout(resolve, 100));
    }

    return allKlines;
}

let activeApiUrl: string | null = BINANCE_PRIMARY; // Persist the working endpoint

const resolvePrimaryUrl = () => activeApiUrl ?? BINANCE_PRIMARY;
const resolveFallbackUrl = (primary: string) =>
    primary === BINANCE_API_URL_DEFAULT ? BINANCE_API_URL_US : BINANCE_API_URL_DEFAULT;

async function fetchBinanceJson<T>(path: string, init?: RequestInit): Promise<T | null> {
    const primary = resolvePrimaryUrl();
    const fallback = resolveFallbackUrl(primary);

    const tryFetch = async (base: string): Promise<Response | null> => {
        try {
            const response = await fetch(`${base}${path}`, init);
            if (response.ok) {
                activeApiUrl = base;
            }
            return response;
        } catch (e) {
            return null;
        }
    };

    let response = await tryFetch(primary);
    if (!response || !response.ok) {
        response = await tryFetch(fallback);
    }

    if (!response || !response.ok) {
        return null;
    }

    try {
        return (await response.json()) as T;
    } catch {
        return null;
    }
}


const defaultSymbols = [
    { symbol: 'BTCUSDT' },
    { symbol: 'ETHUSDT' },
    { symbol: 'BNBUSDT' },
    { symbol: 'SOLUSDT' },
    { symbol: 'XRPUSDT' },
]

let symbolLoaded = false
let symbols: { symbol: string }[]
export async function fetchSymbolList(filterText: string, init: RequestInit): Promise<{ symbol: string }[]> {
    if (!symbolLoaded) {
        const data = await fetchBinanceJson<{ symbols: { symbol: string; status: string }[] }>(`/exchangeInfo`, init);
        if (data && Array.isArray(data.symbols)) {
            symbols = data.symbols.filter(({ status }) => status === 'TRADING');
            symbolLoaded = true;
        }
        return defaultSymbols;

    } else {
        if (filterText) {
            filterText = filterText.toUpperCase()
            let items = symbols.filter(({ symbol }) => symbol.toLocaleUpperCase().startsWith(filterText))
            if (items.length > 100) {
                items = items.slice(0, 100);
                return [...items, { symbol: '...' }]

            } else {
                return items;
            }

        } else {
            return defaultSymbols
        }
    }
}
