# Industry Templates & Indicators

This project supports **industry templates** that drive optional **external indicators** (e.g., DXY, commodity prices, sector leaders).
Indicators are **cached globally in the database** and **shared across symbols** to avoid duplicate remote requests.

## Data Flow

1. Each symbol maps to an **industry** (auto via CNINFO / manual override).
2. The industry selects a **template** (auto by keywords or manual override).
3. Template `config.indicators` defines **which external data** to fetch.
4. Indicator results are cached in `industry_indicator_cache` and reused.
5. AI requests automatically include indicator context as **transient context** (not saved to history/memory).

## Cache Behavior

- Cache table: `industry_indicator_cache`
- Scope: **global** (not per symbol)
- Refresh: controlled by `refresh_minutes` (global) or per-item `refresh_minutes`
- If multiple symbols use the same indicator, **only one remote fetch** is performed within TTL.

## Template Config Schema (examples)

### Full example
```json
{
  "indicators": {
    "commodity_price": {
      "items": [
        { "source": "LME", "symbol": "CU", "ticker": "HG=F", "label": "LME铜" }
      ]
    },
    "dxy": { "ticker": "DX-Y.NYB" },
    "sector_leaders": { "symbols": ["601899.SH", "603993.SH"] }
  },
  "refresh_minutes": 30
}
```

### List style (also supported)
```json
{
  "indicators": [
    { "type": "commodity_price", "items": [
      { "source": "LME", "symbol": "CU", "ticker": "HG=F", "label": "LME铜" }
    ]},
    { "type": "dxy", "ticker": "DX-Y.NYB" },
    { "type": "sector_leaders", "symbols": ["601899.SH", "603993.SH"] }
  ],
  "refresh_minutes": 30
}
```

## Supported Indicator Types

### 1) `commodity_price`
Requires explicit items/tickers. If not configured, **no fetch** happens.

Fields:
- `items`: list of objects
  - `source`: e.g. `LME`, `COMEX` (label only)
  - `symbol`: e.g. `CU`
  - `ticker`: Yahoo ticker (e.g. `HG=F`)
  - `label`: optional display label
  - `refresh_minutes`: optional per-item TTL

### 2) `dxy`
Fields:
- `ticker`: Yahoo ticker, default `DX-Y.NYB`
- `refresh_minutes`: optional per-item TTL

### 3) `sector_leaders`
Fields:
- `symbols`: A-share symbols, e.g. `601899.SH`

Notes:
- Uses **local daily cache** only (no direct remote request).
- A background scheduler refreshes these leader symbols by calling the daily cache at the configured interval.
- If leader data is missing/incomplete, the scheduler will **force sync once** before normal refresh.
- Missing check window is configurable via `INDUSTRY_LEADER_WINDOW_DAYS` (default 30).

## Not Implemented Yet

Indicators like `inventory`, `macro`, `rates`, `nasdaq`, `policy`, etc. are placeholders and currently return `未实现`.

## API

- Get template list: `GET /api/industry/profiles`
- Update template: `PUT /api/industry/profiles/{profile_id}`
- Reset templates: `POST /api/industry/profiles/reset`
- Get indicator context: `GET /api/industry/context?symbol=600362.SH`

## AI Context

The indicator context is injected into AI calls as **transient context**:
- **Not stored** in chat history
- **Not summarized** into memory
- Sent on **every** AI request when industry data is enabled
