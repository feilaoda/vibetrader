# Industry Templates & Indicators

This project supports **industry templates** that drive optional **external indicators** (e.g., DXY, commodity prices, sector leaders).
Indicators are **cached globally in the database** and **shared across symbols** to avoid duplicate remote requests.

## Data Flow

1. Each symbol maps to an **industry** (auto via CNINFO / manual override).
2. The industry selects a **template** (auto by keywords or manual override).
3. Template `config.indicators` defines **which external data** to fetch.
4. Indicator results are cached in `industry_indicator_cache` and reused.
5. AI requests automatically include indicator context as **transient context** (not saved to history/memory).

## Default Behavior (A-share)

- On first access, each A-share symbol auto-initializes:
  - `enabled = true`
  - `market_broad_index = 000985.SH`
  - `market_style_index` auto-assigned by symbol/industry heuristics
- If a symbol has **no industry mapping**, system still sends market index context (`market_thermometer`) and skips industry-specific extra context.

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
    "market_thermometer": {
      "broad_index": "000985.SH",
      "style_index": "000852.SH",
      "refresh_minutes": 5
    },
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
    { "type": "market_thermometer", "broad_index": "000985.SH", "style_index": "000852.SH", "refresh_minutes": 5 },
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

### 1) `market_thermometer`
Builds A-share market context using **1+1 index mode**:
- 全市场指数（default `000985.SH`, 中证全指）
- 风格指数（e.g. `000300.SH` / `000852.SH` / `399006.SZ`)

Output includes:
- 位置（20日线 + 近半年箱体高/中/低）
- 趋势（日线 + 周线）
- 量能（放量/缩量）
- 赚钱效应（涨跌比，若可用）
- 市场温度计（冰点 / 启动 / 沸腾 / 过渡）
- 指数数据日期（全市场指数/风格指数均带日期）

Fields:
- `broad_index`: broad market index symbol, default `000985.SH`
- `style_index`: style index symbol, default `000300.SH`
- `include_breadth`: default `true` (uses A-share spot snapshot)
- `turnover_baseline_trillion`: turnover baseline in 万亿, default `1.0`
- `refresh_minutes`: optional TTL

Notes:
- During A-share trading sessions, scheduler prewarms market thermometer cache at **5-minute cadence** by default.
- Refresh window includes: trading session + **pre-open 10 minutes** + **post-close 10 minutes** for each session.
- You can override via `INDUSTRY_MARKET_REFRESH_MINUTES` (default `5`).

### 2) `commodity_price`
Requires explicit items/tickers. If not configured, **no fetch** happens.

Fields:
- `items`: list of objects
  - `source`: e.g. `LME`, `COMEX` (label only)
  - `symbol`: e.g. `CU`
  - `ticker`: Yahoo ticker (e.g. `HG=F`)
  - `label`: optional display label
  - `refresh_minutes`: optional per-item TTL

### 3) `dxy`
Fields:
- `ticker`: Yahoo ticker, default `DX-Y.NYB`
- `refresh_minutes`: optional per-item TTL

### 4) `sector_leaders`
Fields:
- `symbols`: A-share symbols, e.g. `601899.SH`

Notes:
- Uses **local daily cache** only (no direct remote request).
- A background scheduler refreshes these leader symbols by calling the daily cache at the configured interval.
- If leader data is missing/incomplete, the scheduler will **force sync once** before normal refresh.
- Missing check window is configurable via `INDUSTRY_LEADER_WINDOW_DAYS` (default 30).

### 5) `industry_anomaly`
Detects **industry-level abnormal moves** from cached daily data.

Fields:
- `scope`: `watchlist` (default) or `all`  
- `min_count`: minimum symbol count per industry (default `5`)
- `top`: top N industries to show (default `6`)
- `rs_threshold`: relative strength threshold vs broad index (default `1.5`)
- `strong_ratio_threshold`: strong-move ratio threshold (default `0.35`)
- `weak_ratio_threshold`: weak-move ratio threshold (default `0.35`)
- `broad_index`: broad index symbol for relative strength (default `000985.SH`)
- `refresh_minutes`: cache TTL

Notes:
- Uses **local daily cache only** (no remote fetch per symbol).
- Returns a concise line like:  
  `行业异动: 半导体 强势 +2.8%(RS +1.6，38/12)；化工 弱势 -2.1%(RS -1.4，8/25)`

## Not Implemented Yet

Indicators like `inventory`, `macro`, `rates`, `nasdaq`, `policy`, etc. are placeholders and currently return `未实现`.

## API

- Get template list: `GET /api/industry/profiles`
- Update template: `PUT /api/industry/profiles/{profile_id}`
- Reset templates: `POST /api/industry/profiles/reset`
- Get indicator context: `GET /api/industry/context?symbol=600362.SH`
- Get per-symbol industry settings: `GET /api/industry/settings/{symbol}`
- Update per-symbol industry settings: `POST /api/industry/settings/{symbol}`
  - body supports:
    - `enabled`
    - `market_broad_index` (e.g. `000985.SH`)
    - `market_style_index` (e.g. `000300.SH` / `000852.SH` / `399006.SZ` / `000688.SH`)

## AI Context

The indicator context is injected into AI calls as **transient context**:
- **Not stored** in chat history
- **Not summarized** into memory
- Sent on **every** AI request when industry data is enabled
