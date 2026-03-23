# TraderBar

`TraderBar` is a standalone Tauri menubar app for macOS. It keeps the main UI hidden by default, shows a tray icon in the top menu bar, and opens a compact stock panel when the icon is clicked.

## Product shape

- Default state: no dock presence, no visible window, tray icon only.
- Left click on tray icon: toggle a compact popover panel under the menu bar.
- Main panel:
  - `Watchlist` area: symbols, current price, change percent.
  - `Settings` area: API base URL, polling interval, market filter, tray headline symbol, tray title format.
- `Esc` or losing focus hides the panel.

## Why this architecture

- Fastest path: reuse the existing local VibeTrader API.
  - `GET /api/watchlist`
  - `GET /api/realtime/{symbol}`
- Clean split:
  - Rust handles tray, hidden window, macOS accessory mode, positioning.
  - React handles polling, rendering, settings persistence, tray title updates.
- Low coupling:
  - `traderbar/` is independent from the main web frontend.
  - It can later move from polling to websocket or an embedded local service without changing tray behavior.

## Directory layout

- [package.json](/Users/feilaoda/workspace/ai/vibetrader/traderbar/package.json)
- [src/App.tsx](/Users/feilaoda/workspace/ai/vibetrader/traderbar/src/App.tsx)
- [src/lib/api.ts](/Users/feilaoda/workspace/ai/vibetrader/traderbar/src/lib/api.ts)
- [src/lib/settings.ts](/Users/feilaoda/workspace/ai/vibetrader/traderbar/src/lib/settings.ts)
- [src-tauri/src/main.rs](/Users/feilaoda/workspace/ai/vibetrader/traderbar/src-tauri/src/main.rs)
- [src-tauri/tauri.conf.json](/Users/feilaoda/workspace/ai/vibetrader/traderbar/src-tauri/tauri.conf.json)

## Current behavior in this scaffold

- Reads watchlist from the existing API.
- Polls quotes every few seconds.
- Lets the user choose which symbol drives the tray title.
- Hides itself automatically when focus is lost.
- Uses `tauri-plugin-positioner` to place the panel under the tray.

## Next build steps

1. Install dependencies in `traderbar/`.
2. Run `npm run tauri:dev`.
3. Replace the placeholder tray chart icon with your final monochrome template icon.
4. Add optional features:
   - start at login
   - alert badges
   - watchlist reorder or pinning
   - websocket push
   - open full VibeTrader window
