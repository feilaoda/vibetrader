import type { TFrame } from "../timeseris/TFrame";
import type { TSer } from "../timeseris/TSer";
import { Kline, KVAR_NAME } from "./Kline";
import * as Binance from "./BinanaceData";
import * as AShare from "./AShareData";

/**
 * 市场类型
 */
export type MarketType = 'crypto' | 'ashare' | 'us';

/**
 * 当前选中的市场
 */
const MARKET_STORAGE_KEY = 'vibetrader.market';
let currentMarket: MarketType = 'ashare';

try {
    if (typeof window !== 'undefined') {
        const saved = window.localStorage.getItem(MARKET_STORAGE_KEY);
        if (saved === 'ashare' || saved === 'crypto' || saved === 'us') {
            currentMarket = saved;
        }
    }
} catch {
    // ignore storage errors
}

export const setMarket = (market: MarketType) => {
    currentMarket = market;
    try {
        if (typeof window !== 'undefined') {
            window.localStorage.setItem(MARKET_STORAGE_KEY, market);
        }
    } catch {
        // ignore storage errors
    }
};

export const getMarket = (): MarketType => currentMarket;

export const getCurrentSymbol = (): string | undefined => {
    if (typeof window === 'undefined') return undefined;
    try {
        const market = getMarket();
        return (
            window.localStorage.getItem(`last_selected_symbol_${market}`) ||
            window.sessionStorage.getItem(`last_selected_symbol_${market}`) ||
            window.localStorage.getItem('last_selected_symbol') ||
            window.sessionStorage.getItem('last_selected_symbol') ||
            undefined
        );
    } catch {
        return undefined;
    }
};

export const fetchData = (baseSer: TSer, symbol: string, tframe: TFrame, tzone: string, startTime?: number, limit?: number) => {
    console.log(`[fetchData] market=${currentMarket}, symbol=${symbol}, tframe=${tframe.shortName}`);

    if (currentMarket === 'ashare' || currentMarket === 'us') {
        return fetchDataAShare(baseSer, symbol, tframe, tzone, startTime, limit);
    }
    return fetchDataBinance(baseSer, symbol, tframe, tzone, startTime, limit)
        .catch(ex => {
            console.error('[fetchData] Binance error:', ex);
            return fetchDataLocal(baseSer)
        }).then(lastKline => {
            console.log('[fetchData] Binance result:', lastKline)
            return lastKline === undefined
                ? fetchDataLocal(baseSer)
                : lastKline
        })
}

const fetchDataLocal = (baseSer: TSer) => {
    const baseUrl = import.meta.env.BASE_URL || "/";
    const assetBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
    const url = `${assetBase}klines.json`;

    return fetch(url)
        .then(r => {
            if (!r.ok) {
                throw new Error(`Failed to fetch local klines from ${url}: ${r.status}`);
            }
            return r.json();
        })
        .then(json => {
            for (const k of json) {
                const time = Date.parse(k.Date);
                const kline = new Kline(time, k.Open, k.High, k.Low, k.Close, k.Volume, time, true);
                baseSer.addToVar(KVAR_NAME, kline);
            }

            return undefined; // latestTime
        })
}

const fetchDataBinance = async (baseSer: TSer, symbol: string, tframe: TFrame, tzone: string, startTime?: number, limit?: number) => {
    const endTime = new Date().getTime();
    const backLimitTime = tframe.timeBeforeNTimeframes(endTime, limit, tzone)
    startTime = startTime
        ? startTime
        : backLimitTime //endTime - 300 * 3600 * 1000 * 24; // back 300 days

    let binanceKline: { openTime: number; open: number; high: number; low: number; close: number; volume: number; closeTime: number }[] = [];
    try {
        binanceKline = await Binance.fetchAllKlines(symbol, tframe.shortName, startTime, endTime, limit || 1000);
    } catch (e) {
        console.warn('[fetchData] REST fetch failed', e);
    }

    // Sort by openTime to ensure chronological order
    binanceKline.sort((a, b) => a.openTime - b.openTime);

    // Remove duplicates (in case of any overlap)
    const uniqueKlines = binanceKline//.filter((kline, index, self) => index === self.findIndex((k) => k.openTime === kline.openTime));

    const latestKline = uniqueKlines.length > 0 ? uniqueKlines[uniqueKlines.length - 1] : undefined;

    for (const k of uniqueKlines) {
        if (k) {
            const kline = new Kline(k.openTime, k.open, k.high, k.low, k.close, k.volume, k.closeTime, true);
            baseSer.addToVar(KVAR_NAME, kline);
        }
    }

    return latestKline ? latestKline.openTime : undefined;
}

/**
 * A股数据获取
 */
const fetchDataAShare = async (baseSer: TSer, symbol: string, tframe: TFrame, tzone: string, startTime?: number, limit?: number) => {
    const endTime = new Date().getTime();
    const backLimitTime = tframe.timeBeforeNTimeframes(endTime, limit, tzone);
    const resolvedStartTime = (startTime !== undefined && !Number.isNaN(startTime))
        ? Math.max(startTime, backLimitTime)
        : backLimitTime;

    const formatDate = (time: number, tz: string) => {
        const formatter = new Intl.DateTimeFormat('en-CA', {
            timeZone: tz,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit'
        });
        return formatter.format(new Date(time)).replace(/-/g, '');
    };
    // 转换时间戳为日期字符串 (use exchange timezone)
    let startDate = formatDate(resolvedStartTime, tzone);
    let endDate = formatDate(endTime, tzone);

    const period = AShare.timeframe_to_ashare[tframe.shortName] || '1d';

    // 检测是否是分钟级别周期
    const isMinutePeriod = ['1m', '5m', '15m', '30m', '60m', '1h'].includes(tframe.shortName);
    if (isMinutePeriod) {
        // A股分钟线仅显示当天数据
        const today = formatDate(endTime, tzone);
        startDate = today;
        endDate = today;
    }

    console.log(`[fetchDataAShare] symbol=${symbol}, period=${period}, startDate=${startDate}, endDate=${endDate}, isMinute=${isMinutePeriod}`);

    try {
        const klines = await AShare.fetchKlines(symbol, period, startDate, endDate, limit);

        console.log(`[fetchDataAShare] Got ${klines.length} klines`);

        // 如果是分钟级别但没有数据，显示提示
        if (klines.length === 0 && isMinutePeriod) {
            console.warn('[fetchDataAShare] 分钟级别数据暂不可用');
            // 可以在这里触发一个全局通知事件
            if (typeof window !== 'undefined') {
                window.dispatchEvent(new CustomEvent('ashare-no-minute-data', {
                    detail: { symbol, period: tframe.shortName }
                }));
            }
            return undefined;
        }

        // 按时间排序
        klines.sort((a, b) => a.openTime - b.openTime);

        const latestKline = klines.length > 0 ? klines[klines.length - 1] : undefined;

        for (const k of klines) {
            if (k) {
                const kline = new Kline(k.openTime, k.open, k.high, k.low, k.close, k.volume, k.closeTime, true);
                baseSer.addToVar(KVAR_NAME, kline);
            }
        }

        return latestKline ? latestKline.openTime : undefined;
    } catch (ex) {
        console.error('[fetchDataAShare] Error:', ex);
        if (isMinutePeriod) {
            console.warn('[fetchDataAShare] 分钟级别数据获取失败');
        }
        return fetchDataLocal(baseSer);
    }
}
