# FinAlly — AI Trading Workstation

**FinAlly** (Finance Ally) is an AI-powered trading terminal. It streams live market data, lets you trade a simulated $10,000 portfolio, and pairs you with a Claude chat assistant that can analyze your positions and place trades for you in plain English. Think of a Bloomberg terminal with an AI copilot.

It is the capstone project of an agentic AI coding course. Coding agents build the whole app, and they coordinate through shared documents in [`planning/`](planning/).

> **Project status:** the market data subsystem is finished and tested. The portfolio and chat APIs, the frontend, Docker packaging and E2E tests are still being built. See [Status & Roadmap](#status--roadmap).

---

## Table of Contents

- [Features](#features)
- [Status & Roadmap](#status--roadmap)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Market Data](#market-data)
- [API Reference](#api-reference)
- [Database](#database)
- [AI Assistant](#ai-assistant)
- [Project Structure](#project-structure)
- [Development](#development)
- [Testing](#testing)
- [How This Project Is Built (Agent Workflow)](#how-this-project-is-built-agent-workflow)
- [Security Notes](#security-notes)
- [License](#license)

---

## Features

When the app is finished, you'll open `http://localhost:8000` and land in the terminal right away. There is no login or signup. You'll be able to:

- **Watch live prices.** A watchlist of 10 default tickers updates about twice a second. Prices flash green on an uptick and red on a downtick.
- **See sparklines.** Each ticker gets a mini-chart that builds up from the live stream after the page loads.
- **Open a detailed chart.** Click a ticker to show a larger price chart in the main panel.
- **Trade instantly.** Market orders fill at the current price with no fees and no confirmation step. Fractional shares are supported.
- **Track your portfolio.** A treemap heatmap sizes positions by weight and colors them by P&L. A P&L chart shows total value over time, and a positions table shows the details.
- **Chat with the AI.** Ask about risk, concentration or performance. The assistant can execute trades and edit your watchlist, and it confirms each action inline in the chat.
- **Manage the watchlist.** Add or remove tickers yourself, or ask the AI to do it.
- **Check the connection.** A colored dot in the header shows whether the price stream is live (green), reconnecting (yellow) or disconnected (red).

### Design language

| Element | Value |
|---|---|
| Background | Dark, around `#0d1117` / `#1a1a2e` (never pure black) |
| Accent yellow | `#ecad0a` |
| Primary blue | `#209dd7` |
| Secondary purple (submit buttons) | `#753991` |
| Layout | Dense and desktop-first in a terminal style; still works on tablets |

---

## Status & Roadmap

| Component | State | Notes |
|---|---|---|
| Market data: GBM simulator, Massive client, price cache, SSE router | ✅ Done | 91 tests, 98% coverage. See [`planning/MARKET_DATA_SUMMARY.md`](planning/MARKET_DATA_SUMMARY.md) |
| FastAPI app entrypoint (startup, static serving, health check) | ✅ Done | `backend/app/main.py` |
| SQLite schema, lazy init and seed data | ✅ Done | `backend/app/db/` |
| Portfolio and trade APIs | ✅ Done | `backend/app/api/`, `backend/app/services/` |
| Watchlist APIs | ✅ Done | |
| LLM chat with structured outputs and auto-execution | ✅ Done | `backend/app/llm/`, mock mode via `LLM_MOCK=true` |
| Next.js frontend (static export) | ✅ Done | `frontend/` |
| Multi-stage Dockerfile and start/stop scripts | ✅ Done | `Dockerfile`, `scripts/` |
| Playwright E2E tests | 🧪 In progress | `test/` |
| Cloud deployment (Terraform for App Runner) | 💡 Stretch goal | |

The full specification lives in [`planning/PLAN.md`](planning/PLAN.md). Open questions and suggested simplifications are listed in section 13 of that file.

---

## Architecture

The app runs as one container on one port:

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python / uv)                          │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static files               │
│                      (Next.js static export)    │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market simulator / poller     │
└─────────────────────────────────────────────────┘
```

Data flows like this:

```
 MarketDataSource (simulator or Massive)
            │  writes every ~500ms (sim) / 2–15s (Massive)
            ▼
       PriceCache  (thread-safe, in-memory, versioned)
            │
   ┌────────┼──────────────────┬───────────────────┐
   ▼        ▼                  ▼                   ▼
 SSE     Portfolio          Trade              LLM chat
 stream  valuation          execution          context
   │
   ▼
 Browser (EventSource) → watchlist, sparklines, charts, header totals
```

### Why these choices

| Decision | Rationale |
|---|---|
| SSE instead of WebSockets | Prices only flow from server to client, so one-way push is enough. It's simpler, and browsers reconnect automatically. |
| Static Next.js export | Everything is served from one origin, so there's no CORS setup, and it needs only one port and one container. |
| SQLite instead of Postgres | With one user and no auth, there's no need for a database server. There's nothing to configure. |
| Single Docker container | Students start the whole app with one command. |
| `uv` for Python | It's fast, uses a reproducible lockfile, and is modern tooling worth learning. |
| Market orders only | There's no order book, no limit-order logic and no partial fills, which keeps the portfolio math simple. |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, TypeScript, Tailwind CSS, a canvas-based charting library |
| Backend | FastAPI, Uvicorn, Python 3.12+, managed with `uv` |
| Market data | NumPy (GBM + Cholesky correlation), [`massive`](https://pypi.org/project/massive/) client for Polygon.io |
| Database | SQLite |
| AI | Anthropic Claude via the official `anthropic` Python SDK, with structured outputs (`messages.parse` + Pydantic) |
| Testing | pytest, pytest-asyncio, pytest-cov, Playwright (E2E) |
| Tooling | Ruff (lint + format), Docker |

---

## Getting Started

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package and project manager)
- Python 3.12 or newer (uv can install it for you)
- Docker (to run the full app with one command)
- Node.js 20 or newer (for frontend development)

### Run the backend locally (without Docker)

```bash
git clone <this-repo-url> finally
cd finally/backend

uv sync --extra dev          # install runtime and dev dependencies
uv run pytest                # run the backend test suite
LLM_MOCK=true uv run uvicorn app.main:app --port 8000   # API only (serves the UI too if backend/static exists)
uv run market_data_demo.py   # live terminal dashboard of simulated prices
```

The demo uses Rich to draw a live dashboard of all 10 default tickers, with sparklines, colored direction arrows and an event log of notable moves. It runs for 60 seconds, or until you press `Ctrl+C`.

### Run the full app (Docker)

```bash
cp .env.example .env               # then fill in your keys (optional with LLM_MOCK=true)
./scripts/start_mac.sh             # macOS / Linux
# or
.\scripts\start_windows.ps1        # Windows PowerShell
```

The start script builds the `finally:latest` image if it doesn't exist, replaces any old `finally` container, runs it with the `finally-data` volume on port 8000, waits for `/api/health`, prints the URL and opens your browser.

| Option (sh / ps1) | Effect |
|---|---|
| `--build` / `-Build` | Rebuild the image even if it already exists (do this after pulling new code) |
| `--no-open` / `-NoOpen` | Don't open the browser |

If `.env` is missing, the script warns and starts anyway. The market simulator works without keys, and chat needs `ANTHROPIC_API_KEY` or `LLM_MOCK=true`. Set `FINALLY_PORT` (for example `FINALLY_PORT=8080 ./scripts/start_mac.sh`) to publish on a different host port. If Docker isn't running, the scripts stop with a clear error.

Or run it with Docker directly:

```bash
docker build -t finally:latest .
docker run -d --name finally -v finally-data:/app/db -p 8000:8000 --env-file .env finally:latest
```

Leave out `--env-file .env` if you have no `.env` file (Docker refuses to start when the file is missing). For example, `-e LLM_MOCK=true` runs chat without a key.

Then open **http://localhost:8000**.

To stop the app:

```bash
./scripts/stop_mac.sh              # or .\scripts\stop_windows.ps1
```

The stop scripts remove the container but keep the `finally-data` volume, so your portfolio survives restarts. All scripts are idempotent, so running them twice is safe. To reset all data, run `docker volume rm finally-data` while the app is stopped.

---

## Configuration

Put a `.env` file in the project root. It is gitignored, so **never commit real keys.**

```bash
# Required for AI chat (not needed when LLM_MOCK=true)
ANTHROPIC_API_KEY=

# Optional: Massive (Polygon.io) key for real market data.
# Leave empty to use the built-in simulator (recommended).
MASSIVE_API_KEY=

# Optional: "true" returns deterministic mock LLM responses (tests, CI, no key)
LLM_MOCK=false
```

| Variable | Required | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | Only for chat, unless `LLM_MOCK=true` | Lets the backend call Claude. The SDK reads it automatically. |
| `MASSIVE_API_KEY` | No | If set and non-empty, the backend polls the Massive REST API. If empty or missing, it uses the GBM simulator. |
| `LLM_MOCK` | No | `true` swaps Claude for deterministic canned responses, so you can develop and run E2E tests without a key. |

---

## Market Data

The market data layer lives in [`backend/app/market/`](backend/app/market/). It uses a strategy pattern: two data sources implement one abstract interface, and everything downstream reads from a shared `PriceCache` without knowing which source is active.

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no key needed)
└── MassiveDataSource    →  Polygon.io REST poller (when MASSIVE_API_KEY is set)
        │
        ▼
   PriceCache  →  SSE stream, portfolio valuation, trade execution
```

### Simulator (default)

- **Geometric Brownian Motion:** `S(t+dt) = S(t) · exp((μ − σ²/2)·dt + σ·√dt·Z)`, ticking every 500 ms. `dt` is scaled to a fraction of a trading year.
- **Correlated moves:** the simulator applies a Cholesky decomposition of a sector correlation matrix. Tech stocks correlate at 0.6, finance stocks at 0.5, and pairs across sectors (or involving unknown tickers) at 0.3. TSLA moves mostly on its own.
- **Random events:** each ticker has about a 0.1% chance per tick of a sudden 2–5% jump. With 10 tickers, that's roughly one event every 50 seconds.
- **Realistic seeds:** each default ticker starts from a realistic price and has its own volatility. For example, TSLA and NVDA are high-volatility, while JPM and V are low-volatility.

| Ticker | Seed | σ (vol) | μ (drift) |
|---|---|---|---|
| AAPL | 190 | 0.22 | 0.05 |
| GOOGL | 175 | 0.25 | 0.05 |
| MSFT | 420 | 0.20 | 0.05 |
| AMZN | 185 | 0.28 | 0.05 |
| TSLA | 250 | 0.50 | 0.03 |
| NVDA | 800 | 0.40 | 0.08 |
| META | 500 | 0.30 | 0.05 |
| JPM | 195 | 0.18 | 0.04 |
| V | 280 | 0.17 | 0.04 |
| NFLX | 600 | 0.35 | 0.05 |

Tickers added at runtime get default parameters (σ = 0.25, μ = 0.05).

### Massive API (optional)

- Polls the REST API for snapshots of every tracked ticker. It doesn't use WebSockets, so it works on every tier.
- The default poll interval is 15 seconds, which fits the free tier's 5 calls/min. Paid tiers can poll every 2–15 seconds.
- It polls once immediately on startup so the cache has data right away. If a poll fails, it logs the error and tries again on the next interval.

### Using it from backend code

```python
from app.market import PriceCache, create_market_data_source, create_stream_router

cache = PriceCache()
source = create_market_data_source(cache)        # picks simulator or Massive
await source.start(["AAPL", "GOOGL", "MSFT"])

cache.get("AAPL")         # PriceUpdate | None
cache.get_price("AAPL")   # float | None
cache.get_all()           # dict[str, PriceUpdate]

await source.add_ticker("PYPL")
await source.remove_ticker("GOOGL")

app.include_router(create_stream_router(cache))  # GET /api/stream/prices
await source.stop()
```

---

## API Reference

All endpoints are served from the same origin under `/api`.

### Market data

| Method | Path | Description | Status |
|---|---|---|---|
| `GET` | `/api/stream/prices` | SSE stream of live prices | ✅ Implemented (router) |

**SSE event format:** each tick sends one batched `data:` event containing every tracked ticker. The server only sends an event when the cache has changed. The first message sets `retry: 1000`, so the browser reconnects after 1 second if the connection drops.

```
retry: 1000

data: {"AAPL": {"ticker": "AAPL", "price": 190.52, "previous_price": 190.47,
                "timestamp": 1731230000.12, "change": 0.05,
                "change_percent": 0.0263, "direction": "up",
                "day_open": 189.70, "day_change_percent": 0.43}, "GOOGL": {...}}
```

Use it from the browser like this:

```js
const es = new EventSource("/api/stream/prices");
es.onmessage = (e) => {
  const prices = JSON.parse(e.data); // { [ticker]: PriceUpdate }
};
```

Full request/response shapes and error codes for every endpoint are in [`planning/CONTRACT.md`](planning/CONTRACT.md) §4.

### Portfolio

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/portfolio` | Positions, cash balance, total value, unrealized P&L |
| `POST` | `/api/portfolio/trade` | Execute a market order: `{"ticker": "AAPL", "quantity": 10, "side": "buy"}` |
| `GET` | `/api/portfolio/history` | Portfolio value snapshots, for the P&L chart |

### Watchlist

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/watchlist` | Watched tickers with their latest prices |
| `POST` | `/api/watchlist` | Add a ticker: `{"ticker": "PYPL"}` |
| `DELETE` | `/api/watchlist/{ticker}` | Remove a ticker |

### Chat

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Send a message and get back the full JSON response, including any actions executed (always 200) |
| `GET` | `/api/chat/history` | The last 50 chat messages, oldest first |

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check for Docker and deployment |

### Trade rules

- Market orders only, filled at the current cached price.
- Buys need enough cash, and sells need enough shares.
- Fractional quantities are allowed.
- Selling doesn't change a position's `avg_cost`. A position is removed when its quantity reaches (effectively) zero.

---

## Database

The database is a single SQLite file at `db/finally.db`. It is **initialized lazily**: on first startup or first request, the backend creates any missing tables and seeds default data. There's no separate migration step.

Every table has a `user_id` column that defaults to `"default"`. The app is single-user today, but this leaves room for multiple users later without a schema migration.

| Table | Purpose |
|---|---|
| `users_profile` | Cash balance (starts at `10000.0`) |
| `watchlist` | Watched tickers, unique per user |
| `positions` | Current holdings: quantity and average cost, one row per ticker |
| `trades` | Append-only trade log: side, quantity, price, timestamp |
| `portfolio_snapshots` | Total portfolio value, recorded every 30 seconds and after each trade |
| `chat_messages` | Conversation history, plus a JSON record of the actions each assistant reply executed |

**Seed data:** one profile with $10,000 cash, and a watchlist of `AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX`.

In Docker, the database lives on the named volume `finally-data` mounted at `/app/db`, so it persists across container restarts.

---

## AI Assistant

The chat assistant calls **Anthropic Claude** directly through the official `anthropic` Python SDK. The model is set by one constant in the backend.

### How a chat turn works

1. Load the portfolio context: cash, positions with P&L, the watchlist with live prices, and total value.
2. Load recent conversation history from `chat_messages`.
3. Call `client.messages.parse(...)` with a Pydantic response schema, `effort: "low"` for speed, and roughly 4k max tokens.
4. Check `stop_reason` (for example `refusal` or `max_tokens`) before using the parsed output.
5. Auto-execute any trades and watchlist changes, using the same validation as manual trades.
6. Save the message and the executed actions, then return the full response. Responses aren't streamed; the UI shows a loading indicator instead.

### Response schema

```json
{
  "message": "You're 62% in tech. I trimmed NVDA and added JPM to diversify.",
  "trades": [{ "ticker": "NVDA", "side": "sell", "quantity": 2 }],
  "watchlist_changes": [{ "ticker": "JPM", "action": "add" }]
}
```

Trades run **without a confirmation step**. That's a deliberate choice: the money is simulated, so nothing real is at stake, and showing an agent act on your behalf is the point of the course. If a trade fails validation (for example, insufficient cash), the error goes back into the chat so the assistant can explain what happened.

If the API call fails (rate limit, connection error, API error), the user gets a friendly message and **no actions run**.

### Mock mode

With `LLM_MOCK=true`, the backend returns deterministic canned responses. Use it for fast, free, reproducible E2E tests, for development without an API key, and in CI.

---

## Project Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export) + Vitest tests
├── backend/                  # FastAPI uv project (Python)
│   ├── app/
│   │   ├── main.py           # App factory: lifespan, routers, static serving
│   │   ├── api/              # REST routes (portfolio, watchlist, chat, health)
│   │   ├── services/         # PortfolioService, WatchlistService (shared by REST + LLM)
│   │   ├── db/               # SQLite schema, lazy init, seed data, queries
│   │   ├── llm/              # Claude chat, structured outputs, mock mode
│   │   └── market/           # Market data subsystem
│   │       ├── models.py         # PriceUpdate (frozen dataclass)
│   │       ├── interface.py      # MarketDataSource ABC
│   │       ├── cache.py          # Thread-safe, versioned PriceCache
│   │       ├── seed_prices.py    # Seed prices, GBM params, correlation groups
│   │       ├── simulator.py      # GBMSimulator + SimulatorDataSource
│   │       ├── massive_client.py # Polygon.io REST poller
│   │       ├── factory.py        # Picks the source from the environment
│   │       └── stream.py         # SSE router factory
│   ├── tests/                # pytest: market, db, services, api, llm
│   ├── market_data_demo.py   # Rich terminal demo
│   ├── pyproject.toml
│   └── uv.lock
├── test/                     # Playwright E2E + docker-compose.test.yml
├── scripts/                  # start/stop scripts (mac + Windows)
├── db/                       # Local runtime DB location (finally.db, gitignored)
├── Dockerfile                # Multi-stage: Node 20 → Python 3.12
├── .env.example
├── planning/                 # Shared contract between agents
│   ├── PLAN.md               # Full product and technical spec
│   ├── CONTRACT.md           # Authoritative API/ownership contract for the build
│   ├── MARKET_DATA_SUMMARY.md
│   ├── REVIEW.md             # Spec review feedback
│   └── archive/              # Earlier market data design docs
├── .claude/                  # Agent definitions, commands, skills, settings
├── .github/workflows/        # Claude Code review and assistant workflows
├── CLAUDE.md                 # Entry point for coding agents
├── LICENSE
└── README.md
```

---

## Development

### Backend

```bash
cd backend
uv sync --extra dev                # install dependencies

uv run ruff check app/ tests/      # lint
uv run ruff format .               # format
```

Ruff is set to a 100-character line length and targets Python 3.12. It enforces the `E`, `F`, `I`, `N` and `W` rule sets.

### Frontend

The frontend is a self-contained Next.js project in `frontend/`, built with `output: 'export'`. The build output (`frontend/out/`) is copied into the backend's static directory. The frontend talks to the backend only through `/api/*` and `/api/stream/*`.

```bash
cd frontend
npm ci
npx vitest run     # unit tests
npm run lint
npm run build      # static export → out/
```

### Docker build

```
Stage 1 — node:20-slim
  npm ci && npm run build           → static export

Stage 2 — python:3.12-slim
  install uv, uv sync from lockfile
  copy frontend build into static/
  EXPOSE 8000
  CMD uvicorn (FastAPI app)
```

---

## Testing

### Backend unit tests

```bash
cd backend
uv run pytest                                  # all tests
uv run pytest -v                               # verbose
uv run pytest tests/market/test_simulator.py   # a single module
uv run pytest --cov=app --cov-report=html      # coverage report → htmlcov/
```

Current market data coverage:

| Module | Tests | Coverage |
|---|---|---|
| `test_models.py` | 11 | 100% |
| `test_cache.py` | 15 | 100% |
| `test_simulator.py` | 19 | 98% |
| `test_simulator_source.py` | 10 | integration |
| `test_factory.py` | 7 | 100% |
| `test_massive.py` | 19 | 95% (real `TickerSnapshot` models, API calls mocked) |
| `test_stream.py` | 10 | 97% |

### Other test suites

- **Backend (beyond market data):** `tests/db`, `tests/services`, `tests/api` and `tests/llm` cover trade execution and P&L math, edge cases (overselling, insufficient cash, selling at a loss), LLM structured output and error handling (no real API calls), and API status codes and response shapes.
- **Frontend:** `cd frontend && npx vitest run` covers component rendering, price-flash triggers, watchlist CRUD, portfolio calculations, and the chat loading state.
- **E2E (`test/`):** Playwright in its own `docker-compose.test.yml`, run with `LLM_MOCK=true`. Scenarios include a fresh start, watchlist add and remove, buy and sell, portfolio visuals, mocked AI trades, and SSE reconnection. See [`test/README.md`](test/README.md).

---

## How This Project Is Built (Agent Workflow)

FinAlly is built by coordinated coding agents rather than written by hand.

- **[`planning/PLAN.md`](planning/PLAN.md)** is the source of truth. Every agent reads it as the shared contract.
- **[`CLAUDE.md`](CLAUDE.md)** points agents at the plan and at summaries of completed components.
- **`backend/CLAUDE.md`** is a developer guide for agents working on the backend.
- **`.claude/agents/`** defines specialist agents, such as a spec `reviewer` that writes `planning/REVIEW.md` and a `code-improver`.
- **`.claude/commands/`** holds reusable commands, such as `/doc-review` for annotating planning docs.
- **`.github/workflows/`** runs Claude Code for automated PR review and for `@claude` mentions.

When a component is done, its agent writes a summary into `planning/` (for example `MARKET_DATA_SUMMARY.md`), so later agents can build on it without re-reading every design document.

---

## Security Notes

- Keep `ANTHROPIC_API_KEY` and `MASSIVE_API_KEY` **only** in `.env`, which is gitignored. Never put them in code, docs or commits.
- If a key is ever committed, **rotate it immediately**. Deleting it from the repo isn't enough, because it stays in git history.
- The app has no authentication and is meant to run locally. Don't expose it on a public network without adding auth.
- All trading is simulated, and no real money or brokerage is involved.

---

## License

See [LICENSE](LICENSE).
