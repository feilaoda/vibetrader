export type MarketType = "all" | "ashare" | "us" | "crypto";

export interface WatchlistItem {
  symbol: string;
  name?: string;
  market?: string;
}

export interface RealtimeQuote {
  symbol: string;
  name?: string;
  price?: number;
  change?: number;
  changePercent?: number;
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
  previousVolume?: number;
  volumeVsPreviousPct?: number;
  timestamp?: number;
  source?: string;
  stale?: boolean;
  note?: string;
}

export interface StockRow extends WatchlistItem {
  quote?: RealtimeQuote;
  error?: string;
}

function inferMarket(symbol: string): WatchlistItem["market"] {
  const upper = symbol.toUpperCase();
  if (upper.endsWith(".US")) {
    return "us";
  }
  if (upper.endsWith(".SH") || upper.endsWith(".SZ") || upper.endsWith(".BJ")) {
    return "ashare";
  }
  if (upper.includes("USDT") || upper.includes("BTC") || upper.includes("ETH")) {
    return "crypto";
  }
  return "ashare";
}

export function parseMonitoredSymbols(text: string): WatchlistItem[] {
  const normalized = (text || "")
    .split(/\n|,|;|\t/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
  const seen = new Set<string>();
  const items: WatchlistItem[] = [];
  for (const symbol of normalized) {
    if (seen.has(symbol)) {
      continue;
    }
    seen.add(symbol);
    items.push({
      symbol,
      market: inferMarket(symbol),
    });
  }
  return items;
}

async function readJson<T>(url: string): Promise<T> {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchRealtime(
  apiBaseUrl: string,
  symbol: string
): Promise<RealtimeQuote> {
  return readJson<RealtimeQuote>(
    `${apiBaseUrl.replace(/\/$/, "")}/api/realtime/${encodeURIComponent(symbol)}`
  );
}

export function filterByMarket(items: WatchlistItem[], market: MarketType): WatchlistItem[] {
  if (market === "all") {
    return items;
  }
  return items.filter((item) => (item.market || "ashare") === market);
}

export function formatPrice(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  if (value >= 1000) {
    return value.toFixed(2);
  }
  if (value >= 10) {
    return value.toFixed(3);
  }
  return value.toFixed(4);
}

export function formatChangeAmount(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  const sign = value >= 0 ? "+" : "";
  return `${sign}${formatPrice(value)}`;
}

export function formatPercent(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function formatRatioPercent(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  if (value >= 100) {
    return `${value.toFixed(0)}%`;
  }
  if (value >= 10) {
    return `${value.toFixed(1)}%`;
  }
  return `${value.toFixed(2)}%`;
}

export function formatVolumeCompact(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value) || value < 0) {
    return "--";
  }
  if (value >= 1e8) {
    return `${(value / 1e8).toFixed(value >= 1e9 ? 1 : 2)}亿`;
  }
  if (value >= 1e4) {
    return `${(value / 1e4).toFixed(value >= 1e7 ? 1 : 2)}万`;
  }
  if (value >= 1e3) {
    return `${(value / 1e3).toFixed(1)}k`;
  }
  return `${Math.round(value)}`;
}

export function formatTimestamp(timestamp?: number): string {
  if (!timestamp) {
    return "--";
  }
  const date = new Date(timestamp);
  const hh = `${date.getHours()}`.padStart(2, "0");
  const mm = `${date.getMinutes()}`.padStart(2, "0");
  const ss = `${date.getSeconds()}`.padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}
