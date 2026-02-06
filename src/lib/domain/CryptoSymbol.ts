const QUOTE_ASSETS = [
    'USDT',
    'USD',
    'USDC',
    'BUSD',
    'BTC',
    'ETH',
    'BNB',
    'EUR',
    'JPY',
    'GBP',
    'AUD',
    'CAD',
    'CHF',
    'HKD',
    'TRY',
    'BRL',
    'RUB',
    'KRW',
    'IDR',
    'VND',
    'MXN',
    'ZAR',
];

export type CryptoSymbolParts = {
    base: string;
    quote: string;
};

export function splitCryptoSymbol(symbol?: string): CryptoSymbolParts | null {
    if (!symbol) return null;
    const upper = symbol.toUpperCase().trim();
    if (!upper) return null;

    if (upper.includes('/')) {
        const [base, quote] = upper.split('/');
        if (base && quote) {
            return { base, quote };
        }
    }

    for (const quote of QUOTE_ASSETS) {
        if (upper.endsWith(quote) && upper.length > quote.length) {
            return { base: upper.slice(0, -quote.length), quote };
        }
    }

    return null;
}
