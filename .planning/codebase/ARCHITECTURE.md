---
last_mapped_commit: 4de2c198274139dee77f7111f2325f0ff2e32095
last_mapped_at: 2026-11-22
---
<!-- refreshed: 2026-11-22 -->
# Architecture

**Analysis Date:** 2026-11-22

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Browser (Next.js static export, served from /)                      │
│  frontend/app/page.tsx -> components/Terminal.tsx                    │
│  EventSource  ·  fetch('/api/...')  ·  polling hooks                 │
└───────────┬──────────────────────────┬───────────────────────────────┘
            │ GET /api/stream/prices   │ REST /api/*
            ▼ (SSE, 0.5s tick)         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI app  `backend/app/main.py`  (uvicorn :8000, one container)  │
│                                                                      │
│  API routers `backend/app/api/`                                      │
│   health · watchlist · portfolio · chat                              │
│        │ Depends(get_*_service)  (deps.py reads app.state)           │
│        ▼                                                             │
│  Services `backend/app/services/`                                    │
│   PortfolioService · WatchlistService                                │
│        │                         │                                   │
│        ▼                         ▼                                   │
│  LLM orchestration               Market data  `backend/app/market/`  │
│  `backend/app/llm/`              PriceCache ◄── MarketDataSource     │
│   chat.py · prompts.py           (Simulator | Massive poller)        │
│   schema.py · mock.py                                                │
│        │                                                             │
│        ▼                                                             │
│  Persistence `backend/app/db/database.py` (sqlite3, sync)            │
│   file: FINALLY_DB_PATH (default db/finally.db)                      │
│                                                                      │
│  Background tasks (started in lifespan):                             │
│   market source loop · portfolio snapshot loop (30s)                 │
│                                                                      │
│  Static mount "/" -> SPAStaticFiles (backend/static, from frontend)  │
└──────────────────────────────────────────────────────────────────────┘
```

## Pattern Overview

**Overall:** Layered modular monolith. One FastAPI process owns HTTP routing, business services, persistence, a shared in-memory price cache, and a background market-data producer. The frontend is a separate static SPA that talks only to same-origin `/api/*`.

**Key Characteristics:**
- Single process, single port (8000). The frontend is built with `output: 'export'` (`frontend/next.config.ts`) and copied into `backend/static/` by the Dockerfile.
- Producer/consumer split for prices: a single `MarketDataSource` writes into `PriceCache` (`backend/app/market/cache.py`). REST and SSE read from the cache, never from the source directly.
- Services are the single entry point for business rules. Both the REST routers and the LLM action executor call the same `PortfolioService` and `WatchlistService` methods, so trades and watchlist changes are validated identically whether they come from a user or from Claude.
- Synchronous SQLite behind async handlers. `backend/app/db/database.py` uses one short-lived `sqlite3` connection per call, a module-level `RLock`, and `BEGIN IMMEDIATE` for writes. Route handlers call it directly on the event loop.
- Dependency injection through `app.state`. Services are created in the lifespan and stored on `app.state`; `backend/app/api/deps.py` exposes them as FastAPI `Depends` providers.
- Configuration by environment variable, read at call time or at startup: `MASSIVE_API_KEY` selects the data source, `LLM_MOCK` selects mock chat, `ANTHROPIC_API_KEY` configures the LLM client, `FINALLY_DB_PATH` and `FINALLY_STATIC_DIR` set paths. Root `.env` is loaded by `backend/app/main.py` if present.

## Layers

**HTTP / API layer:**
- Purpose: Translate HTTP to service calls and map domain errors to status codes.
- Location: `backend/app/api/`
- Contains: `APIRouter` modules (`health.py`, `watchlist.py`, `portfolio.py`, `chat.py`), Pydantic request models, `deps.py` providers. `backend/app/api/__init__.py` exports the `ROUTERS` list that `main.py` includes.
- Depends on: `app.services`, `app.db` (only for history reads in `portfolio.py` and `chat.py`), `app.llm` (chat only), `app.market.massive_client` (health only).
- Used by: The frontend via `frontend/lib/api.ts`.

**Service layer (domain):**
- Purpose: Trade validation and execution, portfolio valuation, watchlist rules, and price-universe management.
- Location: `backend/app/services/portfolio.py`, `backend/app/services/watchlist.py`
- Contains: `PortfolioService` (`get_portfolio`, `execute_trade`, `record_snapshot`), `WatchlistService` (`item`, `get_watchlist`, `add`, `remove`), `TradeError`, `WatchlistError`.
- Depends on: `app.db`, `app.market` (`PriceCache`, `MarketDataSource`).
- Used by: API routers, `app/llm/chat.py` (`execute_actions`), and the lifespan snapshot loop.

**LLM layer:**
- Purpose: Build prompts from portfolio state, call Claude (or a deterministic mock), validate structured output, execute the resulting actions through the services, and persist the turn.
- Location: `backend/app/llm/`
- Contains: `chat.py` (`handle_chat`, `call_llm`, `execute_actions`, `get_client`, error-message constants), `prompts.py` (`SYSTEM_PROMPT`, `build_context`, `build_messages`), `schema.py` (`ChatResponse`, `TradeInstruction`, `WatchlistChange`), `mock.py` (`mock_response`, regex-triggered).
- Depends on: `anthropic` SDK, `app.db`, `app.services` (passed in as arguments, imported under `TYPE_CHECKING`).
- Used by: `backend/app/api/chat.py`.

**Market data layer:**
- Purpose: Produce price ticks for the universe of tickers and expose them through one thread-safe cache and one SSE stream.
- Location: `backend/app/market/`
- Contains: `interface.py` (`MarketDataSource` ABC), `simulator.py` (`GBMSimulator`, `SimulatorDataSource`), `massive_client.py` (`MassiveDataSource`, REST polling), `factory.py` (`create_market_data_source`), `cache.py` (`PriceCache`), `models.py` (`PriceUpdate`), `seed_prices.py` (seed prices, drift, volatility, correlation groups), `stream.py` (`create_stream_router`, SSE generator).
- Depends on: `numpy` (simulator correlation), `massive` SDK (Massive source).
- Used by: `main.py` lifespan, services (through the abstract interface), and the SSE router.

**Persistence layer:**
- Purpose: Schema creation, seeding, and all SQL. The only module that touches SQLite.
- Location: `backend/app/db/` (`database.py`, `schema.sql`, `__init__.py` re-exports the public functions)
- Contains: `init_db()` (idempotent, runs `schema.sql`, seeds the default user, cash, and watchlist), read helpers, `apply_trade()` (atomic cash, position, and trade-log update), snapshot and chat helpers.
- Depends on: stdlib `sqlite3` only.
- Used by: Services, LLM layer, API routers, and `main.py`.

**Frontend layer:**
- Purpose: Terminal-style single-page UI.
- Location: `frontend/app/` (Next.js App Router, `layout.tsx` and `page.tsx` only), `frontend/components/`, `frontend/hooks/`, `frontend/lib/`
- Contains: `Terminal.tsx` (root composition and polling), `Watchlist`, `MainChart`, `Heatmap`, `PnlChart`, `PositionsTable`, `TradeBar`, `ChatPanel`, `Header`, `Panel`, `Sparkline`, `TimeSeriesChart`. Hooks: `usePriceStream.ts` (SSE with reconnect and per-ticker history), `usePolling.ts`. Lib: `api.ts` (typed fetch client), `portfolio.ts` (client-side revaluation), `treemap.ts` (layout), `format.ts`, `types.ts`.
- Depends on: Same-origin `/api/*` only. No CORS.
- Used by: Browser.

## Data Flow

### Primary Request Path: Market Trade

1. `TradeBar.tsx` calls `api.trade` in `frontend/lib/api.ts`, which POSTs to `/api/portfolio/trade`.
2. `backend/app/api/portfolio.py::trade` validates the body (`TradeRequest`) and calls `PortfolioService.execute_trade`.
3. `backend/app/services/portfolio.py::execute_trade` normalizes the ticker and side, reads the live price from `PriceCache`, and calls `db.apply_trade`.
4. `backend/app/db/database.py::apply_trade` runs inside `_write()` (`BEGIN IMMEDIATE`). It checks cash or shares, updates `users_profile`, `positions`, and `trades`, and deletes the position row when quantity reaches zero (within `QTY_EPSILON`).
5. If a sell fully closes an unwatched ticker, `execute_trade` calls `source.remove_ticker` to drop it from the price universe.
6. `record_snapshot()` writes a `portfolio_snapshots` row. The router returns `{trade, portfolio}`. Rejections raise `TradeError`, which becomes HTTP 400 with `detail`.

### Secondary Flow: Chat Turn with Auto-Execution

1. `ChatPanel.tsx` calls `api.sendChat`, which POSTs to `/api/chat`.
2. `backend/app/api/chat.py::chat` wraps `llm.handle_chat` in a catch-all. It always returns 200 and falls back to a fixed assistant message on unexpected errors.
3. `backend/app/llm/chat.py::handle_chat` loads the last 20 messages (`HISTORY_LIMIT`), persists the user message, and computes `portfolio.get_portfolio()`.
4. If `LLM_MOCK=true`, `mock.py::mock_response` returns a deterministic `ChatResponse`. Otherwise `build_context` and `build_messages` build the prompt and `call_llm` calls `client.beta.messages.with_raw_response.parse` with `ChatResponse` as the schema. `LLMError` carries a user-facing message.
5. `execute_actions` runs the trades in order through `PortfolioService.execute_trade` and watchlist changes through `WatchlistService.add/remove`. Failures are recorded per action, not raised.
6. The assistant message and the executed-actions JSON are stored with `db.add_chat_message`, and the response body is returned.

### Price Stream Path

1. The lifespan builds the source with `create_market_data_source(cache)` and calls `source.start(universe)`, where `universe = watchlist ∪ held positions`.
2. The simulator or the Massive poller writes each tick through `PriceCache.update()`. The cache bumps `version` on every write or removal.
3. `backend/app/market/stream.py::_generate_events` polls `cache.version` every 0.5s. When it changes, it emits the full `{ticker: PriceUpdate.to_dict()}` map as one `data:` event. It sends a `: keepalive` comment every 15s when idle.
4. `frontend/hooks/usePriceStream.ts` owns the `EventSource`, merges each batch with `applyBatch`, and appends per-ticker history points (one per second, capped at `MAX_POINTS`). Sparklines and the main chart are built from this client-side history, not from a server endpoint.

### Watchlist Path

1. `WatchlistService.add` validates the ticker against `TICKER_RE`, inserts with `db.add_to_watchlist` (409 on duplicate), and calls `source.add_ticker`.
2. `WatchlistService.remove` deletes the row (404 if missing). It stops pricing the ticker only if it is not also held, which keeps P&L live for held positions.

### Snapshot Loop

1. `main.py::_snapshot_loop` calls `portfolio.record_snapshot()` every `SNAPSHOT_INTERVAL_SECONDS = 30.0`. One snapshot is also written at startup and after each trade.
2. `GET /api/portfolio/history` returns the most recent 1000 rows (`HISTORY_LIMIT` in `portfolio.py`) for the P&L chart.

### Client Polling

`Terminal.tsx` polls portfolio every 5s, snapshot history every 30s, and watchlist every 15s (`usePolling`). Prices come from SSE, not polling.

**State Management:**
- Server state is durable in SQLite (`db/finally.db`). Live prices are in-memory only (`PriceCache`) and are lost on restart; the simulator reseeds from `seed_prices.py`.
- Client state is React state in `Terminal.tsx`, plus `usePriceStream` history. No global store library.

## Key Abstractions

**`MarketDataSource`:**
- Purpose: Swappable price producer.
- Examples: `backend/app/market/interface.py`, `backend/app/market/simulator.py::SimulatorDataSource`, `backend/app/market/massive_client.py::MassiveDataSource`
- Pattern: Abstract base class with lifecycle `start(tickers)` → `add_ticker` / `remove_ticker` → `stop()`. The factory chooses the implementation from `MASSIVE_API_KEY`.

**`PriceCache`:**
- Purpose: Single shared, thread-safe store of latest and previous price per ticker, plus a monotonic `version` used for SSE change detection.
- Examples: `backend/app/market/cache.py`, `backend/app/market/models.py` (`PriceUpdate` immutable dataclass with `direction`, `change_percent`, `day_change_percent`, `to_dict`)
- Pattern: Producer writes, many readers read. The version counter avoids re-sending unchanged state.

**Services as the rule boundary:**
- Purpose: One place for trade and watchlist rules.
- Examples: `backend/app/services/portfolio.py::PortfolioService.execute_trade`, `backend/app/services/watchlist.py::WatchlistService.add`
- Pattern: Service methods raise `TradeError` or `WatchlistError` with a user-safe `message` (`WatchlistError` also carries `status_code`). Routers translate these to HTTP. The LLM executor calls the same methods.

**`TradeRejected` → `TradeError`:**
- Purpose: Keep SQL and validation details inside the DB layer while letting the service raise its own error type.
- Examples: `backend/app/db/database.py` raises `TradeRejected`; `backend/app/services/portfolio.py` re-raises as `TradeError`.
- Pattern: Two-level exception translation.

**Structured LLM output:**
- Purpose: Force the model to return a validated object, with no free-text parsing.
- Examples: `backend/app/llm/schema.py` (`ChatResponse` with `message`, `trades`, `watchlist_changes`), `backend/app/llm/chat.py::_parse_request`
- Pattern: Pydantic schema passed to the Anthropic SDK parse call. Refusal and truncation are checked before reading the parsed output.

**Factories and injected dependencies:**
- Purpose: Avoid module globals for the cache and router.
- Examples: `backend/app/market/stream.py::create_stream_router(price_cache)`, `backend/app/market/factory.py::create_market_data_source`, `backend/app/api/deps.py`
- Pattern: Closures and `app.state`. `create_app()` in `main.py` builds the app, so tests can construct it separately.

## Entry Points

**ASGI application:**
- Location: `backend/app/main.py` (`app = create_app()`)
- Triggers: `uvicorn app.main:app`, as in the Dockerfile `CMD`. Run from `backend/`.
- Responsibilities: Load root `.env`, build `PriceCache`, include routers and the SSE router, mount static files, run the lifespan (DB init, source start, snapshot loop, source stop).

**Frontend page:**
- Location: `frontend/app/page.tsx` (renders `Terminal`), with `frontend/app/layout.tsx` for metadata.
- Triggers: Browser navigation to `/` (exported to `frontend/out/index.html`).
- Responsibilities: Mount the single-page terminal.

**Market demo script:**
- Location: `backend/market_data_demo.py`
- Triggers: `uv run market_data_demo.py` from `backend/`.
- Responsibilities: Live terminal rendering of simulator output, independent of the web app.

**Start and stop scripts:**
- Location: `scripts/start_mac.sh`, `scripts/stop_mac.sh`, `scripts/start_windows.ps1`, `scripts/stop_windows.ps1`
- Triggers: Operator. These wrap `docker build` and `docker run -v finally-data:/app/db -p 8000:8000 --env-file .env`.
- Responsibilities: Build the single image and run it with the named volume.

## Architectural Constraints

- **Threading:** The web app runs on the asyncio event loop (uvicorn, single worker per the Dockerfile). Price generation runs as asyncio tasks. `PriceCache` and the DB module use `threading.RLock`, so they are safe if called from worker threads, such as the Massive client's blocking SDK call run in a thread. Do not run multiple uvicorn workers: the cache, the simulator, and the snapshot loop are per-process.
- **Global state:** Module-level singletons in `backend/app/db/database.py` (`_lock`, `_db_path`, `_initialized`), `backend/app/llm/chat.py` (`_client`, the cached Anthropic client, plus `set_client` for tests), and `backend/app/main.py` (`app`, the module-level instance created at import). The price cache and services are per-app and live on `app.state`.
- **Circular imports:** None observed. Layering is `api → services → (db, market)` and `api → llm → services`. `llm/chat.py` imports services only under `TYPE_CHECKING`.
- **Single user:** Every table has `user_id` defaulting to `"default"`. The default user is hard-coded in `database.py` (`DEFAULT_USER`). Multi-user support is a schema-compatible future change, not an implemented one.
- **Process-local prices:** `PriceCache` is not persisted. Restarting the process reseeds prices. The price universe is rebuilt from the watchlist and positions at startup.
- **Database location:** `FINALLY_DB_PATH` overrides the default `db/finally.db` under the repo root (`database.py::_DEFAULT_DB_PATH`). The Dockerfile sets it to `/app/db/finally.db`.

## Anti-Patterns

### Calling SQLite directly from route handlers

**What happens:** `backend/app/api/portfolio.py::history` calls `db.get_snapshots` directly, and `backend/app/api/chat.py::chat_history` calls `db.get_chat_messages`. Both are read-only and simple.
**Why it's wrong:** Reads skip the service layer, so any future read rule (e.g., per-user filters) has to be added in two places. Writes from routers would be worse.
**Do this instead:** Keep read-only pass-throughs acceptable, but put any new write or business rule in a service in `backend/app/services/`, then call it from the router (as `execute_trade` and `WatchlistService` do).

### Blocking SQLite on the event loop

**What happens:** Service methods such as `PortfolioService.get_portfolio` and `record_snapshot` are synchronous and call `db.*`, which blocks the event loop for the duration of each query. `execute_trade` is `async` but its DB call is synchronous.
**Why it's wrong:** Under load, a slow query stalls the SSE generator and every other request.
**Do this instead:** For a new heavy query, call it through `asyncio.to_thread(...)`, as the Massive client already does for its REST call. Keep queries short, since the current design targets a single user.

## Error Handling

**Strategy:** Domain errors are typed exceptions with user-safe messages. Routers convert them to HTTP. Unexpected errors are logged and turned into a safe response.

**Patterns:**
- `TradeError` → HTTP 400 with `{"detail": message}` (`backend/app/api/portfolio.py`).
- `WatchlistError` → HTTP `status_code` (400, 404, or 409) with `detail` (`backend/app/api/watchlist.py`).
- Pydantic validation → HTTP 422 with the default FastAPI body, which `frontend/lib/api.ts::errorMessage` reads.
- `/api/chat` never returns an error status. LLM failures become assistant messages through `LLMError` and constants in `backend/app/llm/chat.py`. Unexpected errors go to `FALLBACK_MESSAGE` in `backend/app/api/chat.py`.
- Snapshot failures are logged and swallowed in `PortfolioService.record_snapshot` so the loop keeps running.
- Stream disconnects are detected with `request.is_disconnected()` in `backend/app/market/stream.py`.

## Cross-Cutting Concerns

**Logging:** Standard `logging` with a module-level `logger = logging.getLogger(__name__)` in each module. No structured logging framework.
**Validation:** Pydantic models at the HTTP boundary (`TradeRequest`, `AddTickerRequest`, `ChatRequest`, `ChatResponse`), plus service-level checks (side, finite positive quantity, ticker regex `TICKER_RE` in `backend/app/services/watchlist.py`).
**Authentication:** None. The app is single-user and unauthenticated by design (`PLAN.md`). The only secrets are `ANTHROPIC_API_KEY` and `MASSIVE_API_KEY`, read from the environment.

---

*Architecture analysis: 2026-11-22*
