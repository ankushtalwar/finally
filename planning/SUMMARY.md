# FinAlly — Project Summary

Single summary document for the project. The full specification remains in [`PLAN.md`](PLAN.md) (the contract for all agents); detailed market data design notes live in [`archive/`](archive/).

## 1. Overview

FinAlly (Finance Ally) is an AI-powered trading workstation: live streaming prices, a simulated $10k portfolio, and an LLM chat assistant that can analyze positions and execute trades and watchlist changes. Built entirely by coding agents as a capstone for an agentic AI coding course.

**Features:** SSE price streaming with green/red flashes, sparklines, market-order trading (instant fill, no fees), portfolio heatmap + P&L chart + positions table, AI chat with auto-executed trades, watchlist management, dark Bloomberg-style UI.

## 2. Architecture

Single Docker container, single port (8000):

- **Frontend:** Next.js (TypeScript, Tailwind), static export served by FastAPI
- **Backend:** FastAPI (Python, `uv`), REST under `/api/*`, SSE at `/api/stream/prices`
- **Database:** SQLite at `db/finally.db`, lazily initialized and seeded (volume-mounted)
- **AI:** LiteLLM → OpenRouter (Cerebras, `openai/gpt-oss-120b`) with structured outputs; `LLM_MOCK=true` for deterministic tests
- **Market data:** built-in GBM simulator by default, Massive (Polygon.io) REST poller if `MASSIVE_API_KEY` is set

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter key for AI chat |
| `MASSIVE_API_KEY` | No | Real market data; omit to use the simulator |
| `LLM_MOCK` | No | `true` for deterministic mock LLM responses |

### Quick start

```bash
cp .env.example .env   # add OPENROUTER_API_KEY
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
# open http://localhost:8000
```

### Project structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # PLAN.md, SUMMARY.md, archive/
├── test/        # Playwright E2E tests
├── db/          # SQLite volume mount (runtime)
└── scripts/     # Start/stop helpers
```

## 3. Status

| Component | Status |
|---|---|
| Market data (`backend/app/market/`) | Complete, tested, reviewed twice |
| Database, portfolio, watchlist, chat/LLM APIs | Not yet built |
| Frontend | Not yet built |
| Dockerfile, scripts, E2E tests | Not yet built |

## 4. Market Data Subsystem

### Design

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default)
└── MassiveDataSource    →  Polygon.io REST poller (MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory)
        ├──→ SSE stream (/api/stream/prices)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

Key decisions: strategy pattern (downstream code is source-agnostic); `PriceCache` as the single source of truth; GBM with Cholesky-correlated sector moves (tech 0.6, finance 0.5, cross-sector 0.3); ~0.1% per-tick chance of a 2–5% shock event; SSE over WebSockets.

### Modules (`backend/app/market/`)

| File | Purpose |
|---|---|
| `models.py` | `PriceUpdate` frozen dataclass (ticker, price, previous_price, timestamp, `day_open`, change, `day_change_percent`, direction) |
| `interface.py` | `MarketDataSource` ABC: `start/stop/add_ticker/remove_ticker/get_tickers` |
| `cache.py` | `PriceCache` with version counter for SSE change detection |
| `seed_prices.py` | Seed prices, per-ticker drift/volatility, correlation groups |
| `simulator.py` | `GBMSimulator` + `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` REST poller (via the `massive` package) |
| `factory.py` | `create_market_data_source()` — picks source from `MASSIVE_API_KEY` |
| `stream.py` | `create_stream_router()` — SSE endpoint (one event with all tickers, `: keepalive` every 15 s) |

### Usage

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source, create_stream_router

cache = PriceCache()
source = create_market_data_source(cache)
await source.start(["AAPL", "GOOGL", "MSFT"])

cache.get("AAPL")        # PriceUpdate | None
cache.get_price("AAPL")  # float | None
cache.get_all()          # dict[str, PriceUpdate]

await source.add_ticker("TSLA")
await source.remove_ticker("GOOGL")
await source.stop()

router = create_stream_router(cache)  # GET /api/stream/prices (text/event-stream)
```

`PriceCache` methods: `update`, `get`, `get_price`, `get_all`, `remove`, and a `version` property. Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX.

**Caller responsibilities:** the simulator accepts any ticker (deterministic hash-based price for unknown ones), and Massive never prices an unknown ticker, so trade/watchlist endpoints must reject tickers with no cached price. `MassiveDataSource.healthy` / `last_error` exist but are not yet wired into `/api/health`.

### Tests and review history

97 tests, all passing, 99% coverage, ruff clean (7 modules in `backend/tests/market/`).

Two code reviews were done and all findings resolved:

- **First review (7 items):** hatch wheel build config, top-level `massive` import, SSE return typing, public `get_tickers()`, correlation constant cleanup, unused test imports, Massive test mocks.
- **Second review:**
  - **M1** daily change: `day_open` / `day_change_percent` (simulator: seed price; Massive: snapshot `prev_day.close`), included in SSE payload.
  - **M2** tickers normalized (strip + upper) everywhere; deterministic unknown-ticker price.
  - **M3** router created per call; `CancelledError` re-raised; `remove()` bumps version; keepalive comments.
  - **M4** Massive `add_ticker` polls immediately.
  - **M5** poll uses a copy of the ticker list, serialized by a lock, discards results for removed tickers.
  - **L1** `timestamp=0.0` honoured; **L3** injectable RNGs; **L4** pre-start `add_ticker` buffered, double `start()` raises; **L5** fixed-cadence loop; **L6** Cholesky shrinkage fallback; **L7** health/`last_error` + exponential backoff (max 8x); **L9** `rich` moved to dev extra, deprecated `event_loop_policy` fixture removed.
  - **L2** (2-dp rounding) intentionally unchanged; **L8** start log reports first-poll result.

## 5. Backend Developer Guide

```bash
cd backend
uv sync --extra dev                        # install deps incl. test/lint tools
uv run --extra dev pytest -v               # all tests
uv run --extra dev pytest --cov=app        # with coverage
uv run --extra dev ruff check app/ tests/  # lint
uv run market_data_demo.py                 # live Rich terminal demo (60 s)
```

Layout: `backend/app/market/` (market data), `backend/tests/market/` (tests), `backend/market_data_demo.py` (demo).

## 6. Documentation Map

| Document | Purpose |
|---|---|
| `CLAUDE.md` | Agent instructions (imports `PLAN.md`) |
| `backend/CLAUDE.md` | Backend agent guide (market data API, test commands) |
| `README.md` | Public project intro |
| `planning/PLAN.md` | Full specification (source of truth) |
| `planning/SUMMARY.md` | This document |
| `planning/archive/` | Detailed market data design, interface, simulator and Massive API notes, plus the first (stale) review |
