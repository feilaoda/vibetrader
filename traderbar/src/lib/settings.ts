import type { MarketType } from "./api";

export interface TraderBarSettings {
  apiBaseUrl: string;
  refreshIntervalSec: number;
  marketFilter: MarketType;
  showPercentInTray: boolean;
  stealthMode: boolean;
  showRawSymbol: boolean;
  monitoredSymbolsText: string;
}

const STORAGE_KEY = "traderbar.settings.v1";
export const MIN_REFRESH_INTERVAL_SEC = 60;

export const defaultSettings: TraderBarSettings = {
  apiBaseUrl: "http://127.0.0.1:8000",
  refreshIntervalSec: MIN_REFRESH_INTERVAL_SEC,
  marketFilter: "ashare",
  showPercentInTray: true,
  stealthMode: true,
  showRawSymbol: false,
  monitoredSymbolsText: "510300.SH\n159915.SZ\nQQQ.US",
};

function normalizeRefreshInterval(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return MIN_REFRESH_INTERVAL_SEC;
  }
  return Math.max(MIN_REFRESH_INTERVAL_SEC, parsed);
}

export function normalizeSettings(settings: Partial<TraderBarSettings>): TraderBarSettings {
  return {
    ...defaultSettings,
    ...settings,
    refreshIntervalSec: normalizeRefreshInterval(settings.refreshIntervalSec),
  };
}

export function loadSettings(): TraderBarSettings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return defaultSettings;
    }
    const parsed = JSON.parse(raw) as Partial<TraderBarSettings>;
    return normalizeSettings(parsed);
  } catch {
    return defaultSettings;
  }
}

export function saveSettings(next: TraderBarSettings): void {
  const normalized = normalizeSettings(next);
  const serialized = JSON.stringify(normalized);
  if (window.localStorage.getItem(STORAGE_KEY) === serialized) {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, serialized);
}
