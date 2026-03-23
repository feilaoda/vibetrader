import { startTransition, useEffect, useEffectEvent, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  fetchRealtime,
  fetchWatchlist,
  filterByMarket,
  formatPercent,
  formatPrice,
  formatTimestamp,
  type StockRow,
  type WatchlistItem,
} from "./lib/api";
import {
  defaultSettings,
  loadSettings,
  saveSettings,
  type TraderBarSettings,
} from "./lib/settings";

function buildTrayTitle(row: StockRow | undefined, settings: TraderBarSettings, offline: boolean): string {
  if (offline) {
    return "TraderBar Offline";
  }
  if (!row?.quote) {
    return "TraderBar";
  }
  const symbol = row.symbol.replace(".SH", "").replace(".SZ", "");
  const price = formatPrice(row.quote.price);
  if (!settings.showPercentInTray) {
    return `${symbol} ${price}`;
  }
  return `${symbol} ${price} ${formatPercent(row.quote.changePercent)}`;
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

function statusLabel(loading: boolean, offline: boolean, updatedAt: number | null): string {
  if (loading) {
    return "Syncing";
  }
  if (offline) {
    return "Disconnected";
  }
  if (!updatedAt) {
    return "Idle";
  }
  return `Updated ${formatTimestamp(updatedAt)}`;
}

export default function App() {
  const [settings, setSettings] = useState<TraderBarSettings>(() => loadSettings());
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [rows, setRows] = useState<StockRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);

  const visibleWatchlist = filterByMarket(watchlist, settings.marketFilter);

  const refreshData = useEffectEvent(async () => {
    setLoading(true);
    setError("");
    try {
      const items = await fetchWatchlist(settings.apiBaseUrl);
      const visible = filterByMarket(items, settings.marketFilter);
      const results = await Promise.allSettled(
        visible.map(async (item) => {
          const quote = await fetchRealtime(settings.apiBaseUrl, item.symbol);
          return {
            ...item,
            quote,
          } satisfies StockRow;
        })
      );

      const nextRows = visible.map((item, index) => {
        const result = results[index];
        if (result?.status === "fulfilled") {
          return result.value;
        }
        return {
          ...item,
          error: result?.status === "rejected" ? result.reason?.message || "Fetch failed" : "Fetch failed",
        } satisfies StockRow;
      });

      startTransition(() => {
        setWatchlist(items);
        setRows(nextRows);
        setUpdatedAt(Date.now());
        setOffline(false);
        setError("");
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      startTransition(() => {
        setWatchlist([]);
        setRows([]);
        setOffline(true);
        setError(message);
        setUpdatedAt(null);
      });
    } finally {
      setLoading(false);
    }
  });

  useEffect(() => {
    saveSettings(settings);
  }, [settings]);

  useEffect(() => {
    void refreshData();
    const timer = window.setInterval(() => {
      void refreshData();
    }, Math.max(3, settings.refreshIntervalSec) * 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [settings.apiBaseUrl, settings.marketFilter, settings.refreshIntervalSec]);

  const trayRow = rows.find((item) => item.symbol === settings.traySymbol)
    || rows[0]
    || visibleWatchlist[0];
  const activeRow = rows.find((item) => item.symbol === trayRow?.symbol);

  useEffect(() => {
    const title = buildTrayTitle(activeRow, settings, offline);
    void invoke("set_tray_title", { title }).catch(() => {
      // ignore dev/browser mode
    });
  }, [activeRow, offline, settings]);

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

  return (
    <div className="app-shell">
      <div className="glass-card">
        <header className="topbar">
          <div>
            <div className="eyebrow">macOS Menu Bar</div>
            <h1>TraderBar</h1>
          </div>
          <div className={`status-pill ${offline ? "offline" : ""}`}>
            {statusLabel(loading, offline, updatedAt)}
          </div>
        </header>

        <div className="headline-card">
          <div className="headline-label">Tray headline</div>
          <div className="headline-symbol">{trayRow?.symbol || "No symbol"}</div>
          <div className="headline-price">{formatPrice(activeRow?.quote?.price)}</div>
          <div
            className={`headline-change ${((activeRow?.quote?.changePercent) || 0) >= 0 ? "up" : "down"}`}
          >
            {formatPercent(activeRow?.quote?.changePercent)}
          </div>
        </div>

        <section className="section">
          <div className="section-header">
            <h2>Watchlist</h2>
            <button className="ghost-btn" onClick={() => void refreshData()}>
              Refresh
            </button>
          </div>
          <div className="list-panel">
            {rows.length === 0 ? (
              <div className="empty-state">
                <div>No watchlist rows</div>
                <div className="muted">
                  {offline ? "Cannot reach local API." : "Add symbols in VibeTrader first."}
                </div>
              </div>
            ) : (
              rows.map((row) => {
                const active = settings.traySymbol === row.symbol || (settings.traySymbol === "AUTO" && rows[0]?.symbol === row.symbol);
                const percent = row.quote?.changePercent || 0;
                return (
                  <button
                    key={row.symbol}
                    type="button"
                    className={`stock-row ${active ? "active" : ""}`}
                    onClick={() => setSettings((prev) => ({ ...prev, traySymbol: row.symbol }))}
                  >
                    <div className="stock-main">
                      <div className="stock-name-line">
                        <span className="market-badge">{marketLabel(row.market)}</span>
                        <span className="stock-symbol">{row.symbol}</span>
                      </div>
                      <div className="stock-name">{row.name || row.quote?.name || "Unnamed"}</div>
                    </div>
                    <div className="stock-side">
                      <div className="stock-price">{formatPrice(row.quote?.price)}</div>
                      <div className={`stock-change ${percent >= 0 ? "up" : "down"}`}>
                        {row.error ? row.error : formatPercent(row.quote?.changePercent)}
                      </div>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </section>

        <section className="section settings-section">
          <div className="section-header">
            <h2>Settings</h2>
            <button
              className="ghost-btn"
              onClick={() => setSettings(defaultSettings)}
            >
              Reset
            </button>
          </div>

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
                <option value={5}>5 sec</option>
                <option value={10}>10 sec</option>
                <option value={15}>15 sec</option>
                <option value={30}>30 sec</option>
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
                    traySymbol: "AUTO",
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
            <span>Tray Symbol</span>
            <select
              value={settings.traySymbol}
              onChange={(event) =>
                setSettings((prev) => ({ ...prev, traySymbol: event.target.value }))
              }
            >
              <option value="AUTO">AUTO (top row)</option>
              {rows.map((row) => (
                <option key={row.symbol} value={row.symbol}>
                  {row.symbol}
                </option>
              ))}
            </select>
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

          <div className="footnote">
            {error ? `Last error: ${error}` : "Left click tray icon to toggle the panel. Press Esc to hide."}
          </div>
        </section>
      </div>
    </div>
  );
}
