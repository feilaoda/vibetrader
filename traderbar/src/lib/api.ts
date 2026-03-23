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
  timestamp?: number;
  source?: string;
  stale?: boolean;
  note?: string;
}

export interface StockRow extends WatchlistItem {
  quote?: RealtimeQuote;
  error?: string;
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

export async function fetchWatchlist(apiBaseUrl: string): Promise<WatchlistItem[]> {
  const payload = await readJson<{ data?: WatchlistItem[] }>(
    `${apiBaseUrl.replace(/\/$/, "")}/api/watchlist`
  );
  return Array.isArray(payload.data) ? payload.data : [];
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

export function formatPercent(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
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
