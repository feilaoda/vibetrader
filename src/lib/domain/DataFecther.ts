import { Provider } from "pinets";
import type { TFrame } from "../timeseris/TFrame";
import type { TSer } from "../timeseris/TSer";
import { Kline, KVAR_NAME } from "./Kline";
import * as Binance from "./BinanaceData";
import * as AShare from "./AShareData";

/**
 * 市场类型
 */
export type MarketType = 'crypto' | 'ashare';

/**
 * 当前选中的市场
 */
let currentMarket: MarketType = 'ashare';

export const setMarket = (market: MarketType) => {
    currentMarket = market;
};

export const getMarket = (): MarketType => currentMarket;

export const fetchData = (baseSer: TSer, symbol: string, tframe: TFrame, tzone: string, startTime?: number, limit?: number) => {
    console.log(`[fetchData] market=${currentMarket}, symbol=${symbol}, tframe=${tframe.shortName}`);

    if (currentMarket === 'ashare') {
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

const fetchDataLocal = (baseSer: TSer) => fetch("./klines.json")
    .then(r => r.json())
    .then(json => {
        for (const k of json) {
            const time = Date.parse(k.Date);
            const kline = new Kline(time, k.Open, k.High, k.Low, k.Close, k.Volume, time, true);
            baseSer.addToVar(KVAR_NAME, kline);
        }

        return undefined; // latestTime
    })

const fetchDataBinance = async (baseSer: TSer, symbol: string, tframe: TFrame, tzone: string, startTime?: number, limit?: number) => {
    const endTime = new Date().getTime();
    const backLimitTime = tframe.timeBeforeNTimeframes(endTime, limit, tzone)
    startTime = startTime
        ? startTime
        : backLimitTime //endTime - 300 * 3600 * 1000 * 24; // back 300 days

    const provider = Provider.Binance
    const pinets_tframe = Binance.timeframe_to_pinetsProvider[tframe.shortName] || tframe.shortName

    return provider.getMarketData(symbol, pinets_tframe, limit, startTime, endTime)
        //return Binance.fetchAllKlines(symbol, timeframe, startTime, endTime, limit)
        .then(binanceKline => {
            // console.log(`\nSuccessfully fetched ${binanceKline.length} klines`);

            // Sort by openTime to ensure chronological order
            binanceKline.sort((a, b) => a.openTime - b.openTime);

            // Remove duplicates (in case of any overlap)
            const uniqueKlines = binanceKline//.filter((kline, index, self) => index === self.findIndex((k) => k.openTime === kline.openTime));

            // console.log(`After deduplication: ${uniqueKlines.length} klines`);

            const latestKline = uniqueKlines.length > 0 ? uniqueKlines[uniqueKlines.length - 1] : undefined;
            // console.log(`latestKline: ${new Date(latestKline.openTime)}, ${latestKline.close}`)

            for (const k of uniqueKlines) {
                if (k) {
                    const kline = new Kline(k.openTime, k.open, k.high, k.low, k.close, k.volume, k.closeTime, true);
                    baseSer.addToVar(KVAR_NAME, kline);
                }
            }

            return latestKline ? latestKline.openTime : undefined;
        })
}

/**
 * A股数据获取
 */
const fetchDataAShare = async (baseSer: TSer, symbol: string, tframe: TFrame, tzone: string, startTime?: number, limit?: number) => {
    const endTime = new Date().getTime();
    const backLimitTime = tframe.timeBeforeNTimeframes(endTime, limit, tzone);

    // 转换时间戳为日期字符串
    const startDate = startTime
        ? new Date(startTime).toISOString().slice(0, 10).replace(/-/g, '')
        : new Date(backLimitTime).toISOString().slice(0, 10).replace(/-/g, '');
    const endDate = new Date(endTime).toISOString().slice(0, 10).replace(/-/g, '');

    const period = AShare.timeframe_to_ashare[tframe.shortName] || '1d';

    // 检测是否是分钟级别周期
    const isMinutePeriod = ['1m', '5m', '15m', '30m', '60m', '1h'].includes(tframe.shortName);

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

