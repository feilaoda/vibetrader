import type { MarketType } from "./api";

export interface TraderBarSettings {
  apiBaseUrl: string;
  refreshIntervalSec: number;
  marketFilter: MarketType;
  traySymbol: string;
  showPercentInTray: boolean;
}

const STORAGE_KEY = "traderbar.settings.v1";

export const defaultSettings: TraderBarSettings = {
  apiBaseUrl: "http://127.0.0.1:8000",
  refreshIntervalSec: 5,
  marketFilter: "ashare",
  traySymbol: "AUTO",
  showPercentInTray: true,
};

export function loadSettings(): TraderBarSettings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return defaultSettings;
    }
    const parsed = JSON.parse(raw) as Partial<TraderBarSettings>;
    return {
      ...defaultSettings,
      ...parsed,
    };
  } catch {
    return defaultSettings;
  }
}

export function saveSettings(next: TraderBarSettings): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}
