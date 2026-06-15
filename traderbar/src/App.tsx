import { startTransition, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { LogicalSize } from "@tauri-apps/api/dpi";
import { TauriEvent } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  fetchRealtime,
  filterByMarket,
  formatChangeAmount,
  formatPercent,
  formatPrice,
  formatRatioPercent,
  formatTimestamp,
  formatVolumeCompact,
  parseMonitoredSymbols,
  type StockRow,
} from "./lib/api";
import {
  defaultSettings,
  loadSettings,
  MIN_REFRESH_INTERVAL_SEC,
  normalizeSettings,
  saveSettings,
  type TraderBarSettings,
} from "./lib/settings";

const PANEL_LABEL = "main";
const SETTINGS_LABEL = "settings";
const SETTINGS_UPDATED_EVENT = "traderbar://settings-updated";
const PANEL_SHOWN_EVENT = "traderbar://panel-shown";
const PANEL_WIDTH = 420;
const PANEL_MIN_HEIGHT = 104;
const PANEL_MAX_HEIGHT = 620;
const PANEL_HEIGHT_PADDING = 24;
const SETTINGS_SYNC_POLL_MS = 1200;
const BACKGROUND_SYMBOL_LIMIT = 1;

function currentWindowLabel(): string {
  try {
    return getCurrentWindow().label;
  } catch {
    return PANEL_LABEL;
  }
}

function compactSymbol(symbol: string): string {
  return symbol.replace(/\.(SH|SZ|BJ|US)$/i, "");
}

function shortenName(name?: string): string {
  const text = (name || "").trim();
  if (!text) {
    return "NA";
  }
  return Array.from(text).slice(0, 2).join("");
}

function displayLabel(row: StockRow | undefined, settings: TraderBarSettings): string {
  if (!row) {
    return "Idle";
  }
  if (!settings.stealthMode || settings.showRawSymbol) {
    return compactSymbol(row.symbol);
  }
  return "";
}

function formatTrayDelta(value: number | undefined, stealthMode: boolean): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  if (stealthMode) {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
  }
  return formatPercent(value);
}

function formatQuoteDelta(
  change: number | undefined,
  changePercent: number | undefined,
  stealthMode: boolean
): string {
  const amount = formatChangeAmount(change);
  const percent = formatTrayDelta(changePercent, stealthMode);
  if (amount === "--" && percent === "--") {
    return "--";
  }
  if (amount === "--") {
    return percent;
  }
  if (percent === "--") {
    return amount;
  }
  return `${amount}/${percent}`;
}

function buildTrayTitle(
  row: StockRow | undefined,
  settings: TraderBarSettings,
  offline: boolean
): string {
  if (offline) {
    return settings.stealthMode ? "Idle" : "TraderBar Offline";
  }
  if (!row?.quote) {
    return settings.stealthMode ? "Desk" : "TraderBar";
  }
  const price = formatPrice(row.quote.price);
  if (!settings.showPercentInTray) {
    return price;
  }
  return `${price} ${formatTrayDelta(row.quote.changePercent, settings.stealthMode)}`;
}

function marketLabel(market?: string): string {
  switch (market) {
    case "us":
      return "US";
    case "crypto":
      return "Crypto";
    default:
      return "A";
  }
}

function buildRowLabel(row: StockRow, settings: TraderBarSettings): string {
  const shortName = shortenName(row.name || row.quote?.name);
  if (!settings.stealthMode || settings.showRawSymbol) {
    return [displayLabel(row, settings), shortName].filter(Boolean).join(" ");
  }
  return shortName;
}

function formatVolumeProgress(quote: StockRow["quote"]): string {
  const volume = formatVolumeCompact(quote?.volume);
  const ratio = formatRatioPercent(quote?.volumeVsPreviousPct);
  if (volume === "--" && ratio === "--") {
    return "V --";
  }
  if (ratio === "--") {
    return `V ${volume}`;
  }
  return `V ${volume} ${ratio}`;
}

function formatRowMeta(quote: StockRow["quote"]): string {
  return `${formatPrice(quote?.high)}/${formatPrice(quote?.low)} ${formatVolumeProgress(quote)}`;
}

function statusLabel(loading: boolean, offline: boolean, updatedAt: number | null): string {
  if (loading) {
    return "Syncing";
  }
  if (offline) {
    return "Offline";
  }
  if (!updatedAt) {
    return "Idle";
  }
  return `Ready ${formatTimestamp(updatedAt)}`;
}

function settingsRefreshKey(settings: TraderBarSettings): string {
  return [
    settings.apiBaseUrl.trim(),
    settings.marketFilter,
    settings.monitoredSymbolsText.trim(),
    String(settings.refreshIntervalSec),
  ].join("|");
}

function buildItemsFromSettings(settings: TraderBarSettings) {
  return filterByMarket(
    parseMonitoredSymbols(settings.monitoredSymbolsText),
    settings.marketFilter
  );
}

function useTraderBarSettings() {
  const [settings, setSettings] = useState<TraderBarSettings>(() => loadSettings());
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    let disposed = false;
    void loadSettingsFromBackend()
      .then((nextSettings) => {
        if (!disposed && nextSettings) {
          setSettings(nextSettings);
        }
      })
      .finally(() => {
        if (!disposed) {
          setHydrated(true);
        }
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (!hydrated) {
      return;
    }
    saveSettings(settings);
    void saveSettingsToBackend(settings);
  }, [hydrated, settings]);

  useEffect(() => {
    const reload = () => {
      void loadSettingsFromBackend().then((nextSettings) => {
        setSettings(nextSettings || loadSettings());
      });
    };
    const onStorage = () => reload();
    const onFocus = () => reload();
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  return [settings, setSettings] as const;
}

function openSettingsWindow(): void {
  void invoke("show_settings").catch(() => {
    // ignore browser/dev preview mode
  });
}

function closeSettingsWindow(): void {
  void invoke("hide_settings").catch(() => {
    // ignore browser/dev preview mode
  });
}

function quitApp(): void {
  void invoke("quit_app").catch(() => {
    // ignore browser/dev preview mode
  });
}

async function pushSettingsToPanel(settings: TraderBarSettings): Promise<void> {
  try {
    await getCurrentWindow().emitTo(PANEL_LABEL, SETTINGS_UPDATED_EVENT, settings);
  } catch {
    // ignore browser/dev preview mode
  }
}

function parseSettingsPayload(payload: unknown): TraderBarSettings | null {
  if (!payload) {
    return null;
  }
  if (typeof payload === "string") {
    try {
      return normalizeSettings(JSON.parse(payload) as Partial<TraderBarSettings>);
    } catch {
      return null;
    }
  }
  if (typeof payload === "object") {
    return normalizeSettings(payload as Partial<TraderBarSettings>);
  }
  return null;
}

async function loadSettingsFromBackend(): Promise<TraderBarSettings | null> {
  try {
    const payload = await invoke<unknown>("load_settings_json");
    return parseSettingsPayload(payload);
  } catch {
    return null;
  }
}

async function saveSettingsToBackend(settings: TraderBarSettings): Promise<void> {
  try {
    await invoke("save_settings_json", {
      settingsJson: JSON.stringify(settings),
    });
  } catch {
    // ignore browser/dev preview mode
  }
}

function PanelView() {
  const [settings, setSettings] = useTraderBarSettings();
  const [rows, setRows] = useState<StockRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [manualRefreshNonce, setManualRefreshNonce] = useState(0);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const skipNextSettingsRefreshKeyRef = useRef<string>("");
  const refreshInFlightRef = useRef(false);
  const queuedRefreshRef = useRef<{ items: ReturnType<typeof buildItemsFromSettings>; apiBaseUrl: string } | null>(null);

  const monitoredItems = useMemo(
    () => filterByMarket(
      parseMonitoredSymbols(settings.monitoredSymbolsText),
      settings.marketFilter
    ),
    [settings.marketFilter, settings.monitoredSymbolsText]
  );
  const refreshKey = useMemo(
    () => [
      settings.apiBaseUrl.trim(),
      settings.marketFilter,
      settings.monitoredSymbolsText.trim(),
      String(settings.refreshIntervalSec),
    ].join("|"),
    [
      settings.apiBaseUrl,
      settings.marketFilter,
      settings.monitoredSymbolsText,
      settings.refreshIntervalSec,
    ]
  );
  const activeRow = rows[0];

  const resizePanel = useEffectEvent(async () => {
    try {
      const targetHeight = Math.min(
        PANEL_MAX_HEIGHT,
        Math.max(
          PANEL_MIN_HEIGHT,
          Math.ceil((panelRef.current?.getBoundingClientRect().height || 0) + PANEL_HEIGHT_PADDING)
        )
      );
      await getCurrentWindow().setSize(new LogicalSize(PANEL_WIDTH, targetHeight));
    } catch {
      // ignore browser/dev preview mode
    }
  });

  const refreshData = useEffectEvent(async (
    items: typeof monitoredItems,
    apiBaseUrl: string,
    options?: { queueIfBusy?: boolean }
  ) => {
    if (refreshInFlightRef.current) {
      if (options?.queueIfBusy) {
        queuedRefreshRef.current = { items, apiBaseUrl };
      }
      return;
    }
    refreshInFlightRef.current = true;
    setLoading(true);
    setError("");
    try {
      let effectiveItems = items;
      try {
        const visible = await getCurrentWindow().isVisible();
        if (!visible) {
          effectiveItems = items.slice(0, BACKGROUND_SYMBOL_LIMIT);
        }
      } catch {
        // ignore browser/dev preview mode
      }
      const results = await Promise.allSettled(
        effectiveItems.map(async (item) => {
          const quote = await fetchRealtime(apiBaseUrl, item.symbol);
          return {
            ...item,
            quote,
          } satisfies StockRow;
        })
      );

      const resultMap = new Map<string, StockRow>();
      effectiveItems.forEach((item, index) => {
        const result = results[index];
        if (result?.status === "fulfilled") {
          resultMap.set(item.symbol, result.value);
          return;
        }
        resultMap.set(item.symbol, {
          ...item,
          error:
            result?.status === "rejected"
              ? result.reason?.message || "Fetch failed"
              : "Fetch failed",
        } satisfies StockRow);
      });

      const nextRows = items.map((item) => {
        const existing = rows.find((row) => row.symbol === item.symbol);
        return resultMap.get(item.symbol) || existing || item;
      });

      startTransition(() => {
        setRows(nextRows);
        setUpdatedAt(Date.now());
        setOffline(false);
        setError("");
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      startTransition(() => {
        setRows([]);
        setOffline(true);
        setError(message);
        setUpdatedAt(null);
      });
    } finally {
      setLoading(false);
      refreshInFlightRef.current = false;
      const queued = queuedRefreshRef.current;
      if (queued) {
        queuedRefreshRef.current = null;
        void refreshData(queued.items, queued.apiBaseUrl, { queueIfBusy: false });
      }
    }
  });

  const applySettingsAndRefresh = useEffectEvent(async (nextSettings: TraderBarSettings) => {
    skipNextSettingsRefreshKeyRef.current = settingsRefreshKey(nextSettings);
    setSettings(nextSettings);
    const nextItems = buildItemsFromSettings(nextSettings);
    await refreshData(nextItems, nextSettings.apiBaseUrl, { queueIfBusy: true });
  });

  const handleManualRefresh = useEffectEvent(async () => {
    const latestSettings = (await loadSettingsFromBackend()) || loadSettings();
    await applySettingsAndRefresh(latestSettings);
  });

  const requestManualRefresh = () => {
    setManualRefreshNonce((value) => value + 1);
  };

  const syncSettingsFromBackend = useEffectEvent(async () => {
    const latestSettings = await loadSettingsFromBackend();
    if (!latestSettings) {
      return;
    }
    if (settingsRefreshKey(latestSettings) === settingsRefreshKey(settings)) {
      return;
    }
    await applySettingsAndRefresh(latestSettings);
  });

  useEffect(() => {
    if (skipNextSettingsRefreshKeyRef.current === refreshKey) {
      skipNextSettingsRefreshKeyRef.current = "";
    } else {
      void refreshData(monitoredItems, settings.apiBaseUrl, { queueIfBusy: false });
    }
    const timer = window.setInterval(() => {
      void refreshData(monitoredItems, settings.apiBaseUrl, { queueIfBusy: false });
    }, Math.max(MIN_REFRESH_INTERVAL_SEC, settings.refreshIntervalSec) * 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [monitoredItems, refreshKey, settings.apiBaseUrl, settings.refreshIntervalSec]);

  useEffect(() => {
    const title = buildTrayTitle(activeRow, settings, offline);
    void invoke("set_tray_title", { title }).catch(() => {
      // ignore dev/browser mode
    });
  }, [activeRow, offline, settings]);

  useEffect(() => {
    if (monitoredItems.length === 0) {
      openSettingsWindow();
    }
  }, [monitoredItems.length]);

  useEffect(() => {
    const element = panelRef.current;
    if (!element) {
      return;
    }
    const scheduleResize = () => {
      window.requestAnimationFrame(() => {
        void resizePanel();
      });
    };
    scheduleResize();
    const observer = new ResizeObserver(() => {
      scheduleResize();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      void resizePanel();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [error, offline, rows, settings.showPercentInTray, settings.showRawSymbol, settings.stealthMode, updatedAt]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        void invoke("hide_panel").catch(() => {
          // ignore dev/browser mode
        });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    let disposed = false;
    let unlistenFn: (() => Promise<void> | void) | undefined;
    void getCurrentWindow()
      .listen(TauriEvent.WINDOW_FOCUS, () => {
        if (!disposed) {
          void handleManualRefresh();
        }
      })
      .then((fn) => {
        unlistenFn = fn;
      })
      .catch(() => {
        // ignore browser/dev preview mode
      });
    return () => {
      disposed = true;
      if (unlistenFn) {
        void unlistenFn();
      }
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void syncSettingsFromBackend();
    }, SETTINGS_SYNC_POLL_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (manualRefreshNonce <= 0) {
      return;
    }
    void handleManualRefresh();
  }, [manualRefreshNonce]);

  useEffect(() => {
    let disposed = false;
    const bind = async () => {
      const unlisten = await getCurrentWindow().listen(SETTINGS_UPDATED_EVENT, (event) => {
        if (!disposed) {
          const nextSettings = parseSettingsPayload(event.payload);
          if (nextSettings) {
            void applySettingsAndRefresh(nextSettings);
          }
        }
      });
      if (disposed) {
        await unlisten();
      }
      return unlisten;
    };
    let unlistenFn: (() => Promise<void> | void) | undefined;
    void bind().then((fn) => {
      unlistenFn = fn;
    });
    return () => {
      disposed = true;
      if (unlistenFn) {
        void unlistenFn();
      }
    };
  }, [setSettings]);

  useEffect(() => {
    let disposed = false;
    let unlistenFn: (() => Promise<void> | void) | undefined;
    void getCurrentWindow()
      .listen(PANEL_SHOWN_EVENT, () => {
        if (!disposed) {
          void handleManualRefresh();
        }
      })
      .then((fn) => {
        unlistenFn = fn;
      })
      .catch(() => {
        // ignore browser/dev preview mode
      });
    return () => {
      disposed = true;
      if (unlistenFn) {
        void unlistenFn();
      }
    };
  }, []);

  return (
    <div className="app-shell">
      <div className="glass-card" ref={panelRef}>
        <header className="topbar">
          <div className={`status-pill ${offline ? "offline" : ""}`}>
            {statusLabel(loading, offline, updatedAt)}
          </div>
          <div className="topbar-actions">
            <button
              className="ghost-btn compact-btn"
              onClick={requestManualRefresh}
            >
              {settings.stealthMode ? "Sync" : "Refresh"}
            </button>
            <button className="ghost-btn compact-btn" onClick={openSettingsWindow}>
              {settings.stealthMode ? "Cfg" : "Settings"}
            </button>
            <button className="ghost-btn compact-btn" onClick={quitApp}>
              Exit
            </button>
          </div>
        </header>

        <div className="headline-card">
          {!settings.stealthMode && (
            <div className="headline-symbol">
              {activeRow ? displayLabel(activeRow, settings) : "No row"}
            </div>
          )}
          <div className="headline-price">
            {activeRow?.quote ? formatPrice(activeRow.quote.price) : "--"}
          </div>
          <div
            className={`headline-change ${
              settings.stealthMode ? "mono" : (((activeRow?.quote?.changePercent) || 0) >= 0 ? "up" : "down")
            }`}
          >
            {activeRow?.quote
              ? (
                  settings.showPercentInTray
                    ? formatQuoteDelta(
                        activeRow.quote.change,
                        activeRow.quote.changePercent,
                        settings.stealthMode
                      )
                    : formatTimestamp(activeRow.quote.timestamp)
                )
              : (error ? "No source" : "Waiting")}
          </div>
          <div className="headline-meta">
            {activeRow?.quote ? formatVolumeProgress(activeRow.quote) : "V --"}
          </div>
        </div>

        <section className="section">
          <div className="list-panel">
            {rows.length === 0 ? (
              <div className="empty-state">
                <div>{settings.stealthMode ? "No rows" : "No monitored symbols"}</div>
                <div className="muted">
                  {offline ? "Source unavailable." : "Add symbols in settings."}
                </div>
                <button className="ghost-btn empty-action" onClick={openSettingsWindow}>
                  {settings.stealthMode ? "Cfg" : "Open settings"}
                </button>
              </div>
            ) : (
              rows.map((row, index) => {
                const percent = row.quote?.changePercent;
                const showBadge = !settings.stealthMode;
                return (
                  <div
                    key={row.symbol}
                    className={`stock-row ${index === 0 ? "active" : ""}`}
                  >
                    <div className="stock-row-left">
                      {showBadge && (
                        <span className="market-badge">{marketLabel(row.market)}</span>
                      )}
                      <span className="stock-inline-label">{buildRowLabel(row, settings)}</span>
                    </div>
                    <div className="stock-inline-meta">{formatRowMeta(row.quote)}</div>
                    <div className="stock-inline-side">
                      <span className="stock-price">{formatPrice(row.quote?.price)}</span>
                      <span
                        className={`stock-change ${
                          settings.stealthMode ? "mono" : ((percent || 0) >= 0 ? "up" : "down")
                        }`}
                      >
                        {row.error
                          ? row.error
                          : settings.showPercentInTray
                            ? formatQuoteDelta(
                                row.quote?.change,
                                percent,
                                settings.stealthMode
                              )
                            : formatTimestamp(row.quote?.timestamp)}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </section>

        {(error || !settings.stealthMode) && (
          <div className="panel-footer">
            <div className="footnote">
              {error
                ? `Last error: ${error}`
                : "First symbol drives the tray text. Press Esc to hide."}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SettingsView() {
  const [settings, setSettings] = useTraderBarSettings();
  const [applying, setApplying] = useState(false);

  const handleDone = async () => {
    setApplying(true);
    try {
      await invoke("reload_panel_with_settings", {
        settingsJson: JSON.stringify(settings),
      });
    } catch {
      try {
        await invoke("show_panel_with_settings", {
          settingsJson: JSON.stringify(settings),
        });
      } catch {
        await pushSettingsToPanel(settings);
        void invoke("show_panel").catch(() => {
          // ignore browser/dev preview mode
        });
        closeSettingsWindow();
        setApplying(false);
      }
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeSettingsWindow();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="settings-shell">
      <div className="settings-card">
        <header className="settings-header">
          <div>
            <div className="settings-title">TraderBar Settings</div>
            <div className="settings-subtitle">Changes save automatically.</div>
          </div>
          <div className="settings-actions">
            <button className="ghost-btn" disabled={applying} onClick={() => setSettings({ ...defaultSettings })}>
              Reset
            </button>
            <button className="ghost-btn" disabled={applying} onClick={() => void handleDone()}>
              {applying ? "Applying..." : "Done"}
            </button>
            <button className="ghost-btn" disabled={applying} onClick={quitApp}>
              Exit
            </button>
          </div>
        </header>

        <div className="settings-body">
          <label className="field">
            <span>API Base URL</span>
            <input
              value={settings.apiBaseUrl}
              onChange={(event) =>
                setSettings((prev) => ({ ...prev, apiBaseUrl: event.target.value }))
              }
              placeholder="http://127.0.0.1:8000"
            />
          </label>

          <div className="field-grid">
            <label className="field">
              <span>Refresh</span>
              <select
                value={settings.refreshIntervalSec}
                onChange={(event) =>
                  setSettings((prev) => ({
                    ...prev,
                    refreshIntervalSec: Number(event.target.value),
                  }))
                }
              >
                <option value={60}>60 sec</option>
                <option value={120}>2 min</option>
                <option value={300}>5 min</option>
              </select>
            </label>

            <label className="field">
              <span>Market</span>
              <select
                value={settings.marketFilter}
                onChange={(event) =>
                  setSettings((prev) => ({
                    ...prev,
                    marketFilter: event.target.value as TraderBarSettings["marketFilter"],
                  }))
                }
              >
                <option value="ashare">A-share</option>
                <option value="us">US</option>
                <option value="crypto">Crypto</option>
                <option value="all">All</option>
              </select>
            </label>
          </div>

          <label className="field">
            <span>Monitored Symbols</span>
            <textarea
              className="symbols-input"
              value={settings.monitoredSymbolsText}
              onChange={(event) =>
                setSettings((prev) => ({
                  ...prev,
                  monitoredSymbolsText: event.target.value,
                }))
              }
              placeholder={"510300.SH\n159915.SZ\nQQQ.US"}
              spellCheck={false}
            />
          </label>

          <label className="switch-row">
            <span>Show percent in tray title</span>
            <input
              type="checkbox"
              checked={settings.showPercentInTray}
              onChange={(event) =>
                setSettings((prev) => ({
                  ...prev,
                  showPercentInTray: event.target.checked,
                }))
              }
            />
          </label>

          <label className="switch-row">
            <span>Stealth labels</span>
            <input
              type="checkbox"
              checked={settings.stealthMode}
              onChange={(event) =>
                setSettings((prev) => ({
                  ...prev,
                  stealthMode: event.target.checked,
                }))
              }
            />
          </label>

          <label className="switch-row">
            <span>Reveal raw codes</span>
            <input
              type="checkbox"
              checked={settings.showRawSymbol}
              onChange={(event) =>
                setSettings((prev) => ({
                  ...prev,
                  showRawSymbol: event.target.checked,
                }))
              }
            />
          </label>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return currentWindowLabel() === SETTINGS_LABEL ? <SettingsView /> : <PanelView />;
}
