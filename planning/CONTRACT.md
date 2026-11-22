# FinAlly — Team Contract (authoritative for the build)

This file resolves the open questions in `PLAN.md` §13. Where it differs from PLAN.md, **this file wins**.
Every team member builds against this. If you need to change it, say so in your final report — do not silently diverge.

## 0. Team & file ownership (only edit files you own)

| Role | Owns |
|---|---|
| Database Engineer | `backend/app/db/**`, `backend/tests/db/**` |
| Backend API Engineer | `backend/app/main.py`, `backend/app/api/**`, `backend/app/services/**`, `backend/tests/api/**`, `backend/tests/services/**` |
| LLM Engineer | `backend/app/llm/**`, `backend/tests/llm/**` |
| Frontend Engineer | `frontend/**` |
| DevOps Engineer | `Dockerfile`, `.dockerignore`, `.env.example`, `scripts/**`, root `README.md` "Running" section only |
| Integration Tester | `test/**` |

Already done (do not modify unless a bug blocks you, and report it): `backend/app/market/**`.
`backend/pyproject.toml` already has `anthropic` and `python-dotenv`. Do **not** edit `pyproject.toml`/`uv.lock` — ask the lead if you need a new dependency.
Do **not** commit. Never read, print or copy `.env` contents; never write a key into any file.
`uv` in the sandbox: prefix commands with `UV_CACHE_DIR=$TMPDIR/uv-cache` if you hit cache permission errors.

## 1. Runtime layout

- Python package root: `backend/app`. ASGI app: `app.main:app`. Run from `backend/`: `uv run uvicorn app.main:app --port 8000`.
- DB path: env `FINALLY_DB_PATH`, default `<repo>/db/finally.db` (i.e. `Path(__file__).resolve().parents[2] / "db" / "finally.db"` from `backend/app/...`, computed carefully). Parent dir is created if missing.
- Static frontend dir: env `FINALLY_STATIC_DIR`, default `backend/static`. If the dir doesn't exist, the API still runs (no static mount).
- `.env`: `app.main` calls `load_dotenv(<repo>/.env)` if the file exists (local dev). In Docker it comes from `--env-file`.
- Docker: WORKDIR `/app/backend`, static at `/app/backend/static`, DB at `/app/db/finally.db` (`FINALLY_DB_PATH=/app/db/finally.db`), **named volume** `finally-data:/app/db`. Container name `finally`, image `finally:latest`, port 8000.
- Frontend: Next.js static export (`output: 'export'`), build output `frontend/out/` → copied to `backend/static/`.

## 2. Database layer (`app/db`, Database Engineer)

stdlib `sqlite3`, synchronous, one short-lived connection per call (`check_same_thread=False`, `PRAGMA foreign_keys=ON`, WAL mode), guarded by a module-level `threading.Lock` for writes. Schema per PLAN §7 (keep `user_id` columns; UUID `id`s; ISO-8601 UTC timestamps like `2026-11-13T10:00:00.123456+00:00`). Lazy init: `init_db()` is idempotent (`CREATE TABLE IF NOT EXISTS`, seed only if `users_profile` has no `default` row). Schema SQL in `app/db/schema.sql`.

Public API (`from app import db` / `from app.db import ...`) — all take `user_id: str = "default"` as a keyword arg:

```python
DEFAULT_USER = "default"
DEFAULT_TICKERS = ["AAPL","GOOGL","MSFT","AMZN","TSLA","NVDA","META","JPM","V","NFLX"]
QTY_EPSILON = 1e-9

class TradeRejected(Exception):   # .message is user-facing, e.g. "Insufficient cash: need $1,950.20, have $500.00"
    message: str

def init_db(path: str | Path | None = None) -> None      # sets module path; None => env/default
def get_cash(*, user_id=...) -> float
def get_watchlist(*, user_id=...) -> list[str]            # ordered by added_at
def add_to_watchlist(ticker: str, *, user_id=...) -> bool  # False if already present
def remove_from_watchlist(ticker: str, *, user_id=...) -> bool  # False if absent
def get_positions(*, user_id=...) -> list[dict]           # [{"ticker","quantity","avg_cost","updated_at"}]
def get_position(ticker: str, *, user_id=...) -> dict | None
def apply_trade(ticker: str, side: str, quantity: float, price: float, *, user_id=...) -> dict
    # ONE transaction: validate (buy: cash >= qty*price; sell: held qty >= qty - QTY_EPSILON),
    # update cash, upsert position (buy: weighted avg_cost; sell: avg_cost unchanged;
    # delete row if remaining qty <= QTY_EPSILON), insert trade row.
    # Returns {"id","ticker","side","quantity","price","executed_at"}. Raises TradeRejected.
def get_trades(*, limit: int = 100, user_id=...) -> list[dict]   # newest first
def record_snapshot(total_value: float, *, user_id=...) -> None
def get_snapshots(*, limit: int = 1000, user_id=...) -> list[dict]  # [{"total_value","recorded_at"}] oldest→newest (the newest `limit`)
def add_chat_message(role: str, content: str, actions: dict | None = None, *, user_id=...) -> dict
def get_chat_messages(*, limit: int = 20, user_id=...) -> list[dict]
    # newest `limit`, returned oldest→newest; [{"id","role","content","actions"(dict|None),"created_at"}]
```
Tickers are normalized (`strip().upper()`) inside the DB layer. Tests use a temp DB path.

## 3. Services (`app/services`, Backend API Engineer)

```python
# app/services/portfolio.py
class TradeError(Exception): message: str          # user-facing
class PortfolioService:
    def __init__(self, cache: PriceCache, source: MarketDataSource): ...
    def get_portfolio(self) -> dict                  # shape of GET /api/portfolio
    async def execute_trade(self, ticker: str, side: str, quantity: float) -> dict
        # normalize ticker; side in {"buy","sell"}; quantity > 0 (finite) else TradeError;
        # price = cache.get_price(ticker) or TradeError("No price available for XYZ yet");
        # db.apply_trade (TradeRejected -> TradeError); record a snapshot; return the trade dict.
    def record_snapshot(self) -> None

# app/services/watchlist.py
class WatchlistError(Exception): message: str; status_code: int   # 400 / 404 / 409
class WatchlistService:
    def __init__(self, cache: PriceCache, source: MarketDataSource): ...
    def get_watchlist(self) -> list[dict]            # items of GET /api/watchlist "tickers"
    async def add(self, ticker: str) -> dict         # validates ^[A-Z][A-Z0-9.\-]{0,9}$ after normalize; source.add_ticker
    async def remove(self, ticker: str) -> None      # source.remove_ticker ONLY if not currently held
```
**Price universe = watchlist ∪ held tickers.** At startup the source starts with that union. A trade for a ticker not in the cache is rejected (user must add it to the watchlist first).
Snapshots: one at startup, one after every successful trade, and every 30 s from a background task.

The LLM layer calls these services — the **same** code path as the REST endpoints.

## 4. REST API (Backend API Engineer)

All JSON. Errors use FastAPI's `{"detail": "<human message>"}`. Body validation errors → 422 (FastAPI default). Business errors → 400 (or 404/409 as noted). All numbers are raw floats (frontend formats).

**GET `/api/health`** → `{"status":"ok","market_source":"simulator"|"massive","market_healthy":true,"llm_mock":false}`

**GET `/api/watchlist`** →
```json
{"tickers":[{"ticker":"AAPL","price":190.12,"previous_price":190.05,"change_percent":0.0368,
  "day_change_percent":0.41,"direction":"up","timestamp":1763030000.1}]}
```
`price`/`previous_price`/`timestamp` are `null` and percents `0` if no price yet.

**POST `/api/watchlist`** `{"ticker":"pypl"}` → 201 `{"ticker":"PYPL"}` (that ticker's item shape as above). 409 if already watched, 400 if invalid symbol.

**DELETE `/api/watchlist/{ticker}`** → 204. 404 if not on watchlist.

**GET `/api/portfolio`** →
```json
{"cash":8049.8,"positions_value":1950.2,"total_value":10000.0,
 "unrealized_pnl":50.2,"unrealized_pnl_percent":2.64,
 "positions":[{"ticker":"AAPL","quantity":10,"avg_cost":190.0,"current_price":195.02,
   "market_value":1950.2,"unrealized_pnl":50.2,"unrealized_pnl_percent":2.64,"weight":0.195}]}
```
If a held ticker has no live price, use `avg_cost` as `current_price`. `weight` = market_value / total_value. Positions sorted by market_value desc.

**POST `/api/portfolio/trade`** `{"ticker":"AAPL","quantity":10,"side":"buy"}` → 200
`{"trade":{"id":"…","ticker":"AAPL","side":"buy","quantity":10,"price":190.12,"executed_at":"…"},"portfolio":{…GET /api/portfolio shape…}}`
→ 400 `{"detail":"Insufficient cash: …"}` / `"Insufficient shares: …"` / `"No price available for XYZ yet"` / `"Quantity must be greater than 0"`.

**GET `/api/portfolio/history`** → `{"snapshots":[{"total_value":10000.0,"recorded_at":"…"}]}` oldest→newest, max 1000.

**POST `/api/chat`** `{"message":"buy 5 AAPL"}` → **always 200** (LLM failure ⇒ friendly message, no actions):
```json
{"id":"…","role":"assistant","content":"Done — bought 5 AAPL at $190.12.","created_at":"…",
 "actions":{"trades":[{"ticker":"AAPL","side":"buy","quantity":5,"status":"executed","price":190.12,"error":null}],
            "watchlist_changes":[{"ticker":"PYPL","action":"add","status":"executed","error":null}]}}
```
`status` is `"executed"` or `"failed"` (then `error` is the message). Empty message → 422.

**GET `/api/chat/history`** → `{"messages":[{"id","role","content","actions","created_at"}]}` (last 50, oldest→newest; `actions` null for user messages).

**GET `/api/stream/prices`** — already implemented (`app.market.create_stream_router`). One batched SSE `data:` event per change: `{"AAPL": {ticker, price, previous_price, timestamp, change, change_percent, direction, day_open, day_change_percent}, ...}`. Default event type (use `onmessage`). Tickers removed from the universe disappear from the map. Starts with `retry: 1000`.

Static: API routers are registered first; then `StaticFiles(directory=static_dir, html=True)` mounted at `/`. Unknown non-API paths fall back to `index.html` (custom 404 handler for non-`/api` GETs).

## 5. LLM (`app/llm`, LLM Engineer)

```python
# app/llm/__init__.py exports:
MODEL = "claude-opus-5-5"
async def handle_chat(message: str, portfolio: PortfolioService, watchlist: WatchlistService) -> dict
    # returns exactly the POST /api/chat response body; persists user msg + assistant msg (with actions) via app.db
```
Flow per PLAN §9: context (cash, positions with P&L, watchlist with prices, total value) + last 20 chat messages + new message → `client.messages.parse(..., output_format=<Pydantic model>)` with `output_config={"effort":"low"}`, `max_tokens≈4000`. Use the `claude-api` skill. Handle `RateLimitError`, `APIStatusError`, `APIConnectionError`, `stop_reason in {"refusal","max_tokens"}`, missing key → friendly message, no actions. Sync SDK calls must not block the event loop (use `AsyncAnthropic` or `asyncio.to_thread`).
Execution order: **watchlist changes first, then trades, in order; a failure does not stop the rest.** Results are fed into `actions`. Watchlist "add" of an already-watched ticker counts as `executed`.

**Mock mode** (`LLM_MOCK=true`, case-insensitive) — deterministic, no network:
- `(buy|sell) <qty> <TICKER>` anywhere in the message (regex, case-insensitive, optional `shares of`) → that trade; content `"Mock: executing {side} {qty} {TICKER}."`
- `(add|watch) <TICKER>` → watchlist add; `remove <TICKER>` → watchlist remove; content `"Mock: updating watchlist for {TICKER}."`
- otherwise → content `"Mock: I'm FinAlly, your AI trading assistant. Your portfolio is worth ${total_value:,.2f}."`, no actions.
Tickers uppercased. Same execution & persistence path as real mode.

## 6. Frontend (Frontend Engineer)

Next.js + TypeScript + Tailwind, static export, single page. Lightweight Charts for main price chart and P&L chart; hand-rolled SVG sparklines; hand-rolled squarified treemap (div/SVG). Vitest + React Testing Library for unit tests. Same-origin `/api/*` (no base URL). Poll `/api/portfolio` every 5 s and after any trade/chat; header total value recomputed live from SSE prices × positions + cash.

**Required `data-testid`s (E2E tests depend on these exactly):**

| testid | element |
|---|---|
| `connection-status` | status dot; attribute `data-status="connected"\|"reconnecting"\|"disconnected"` |
| `total-value`, `cash-balance` | header numbers, formatted `$10,000.00` |
| `watchlist` | watchlist container |
| `watchlist-row-{TICKER}` | one row per ticker (clicking selects it for the main chart) |
| `watchlist-price-{TICKER}` | price text, formatted `190.12` (no `$`) |
| `watchlist-remove-{TICKER}` | remove button |
| `watchlist-add-input`, `watchlist-add-button` | add form; errors shown in `watchlist-error` |
| `main-chart` | main chart container; `selected-ticker` shows the selected symbol |
| `trade-ticker`, `trade-quantity`, `trade-buy`, `trade-sell` | trade bar; errors in `trade-error`, success in `trade-success` |
| `positions-table`, `position-row-{TICKER}` | positions; row contains quantity text |
| `heatmap`, `heatmap-tile-{TICKER}` | treemap tiles |
| `pnl-chart` | P&L chart container |
| `chat-panel`, `chat-input`, `chat-send`, `chat-loading` | chat; send also on Enter |
| `chat-message-user`, `chat-message-assistant` | each message bubble |
| `chat-action` | each inline trade/watchlist confirmation (text includes ticker and side/action, e.g. `BUY 5 AAPL @ $190.12` or `FAILED`) |

## 7. E2E (Integration Tester)

`test/` is a standalone Playwright (TypeScript) project. `test/docker-compose.test.yml`: service `app` built from repo root `Dockerfile` with `LLM_MOCK=true`, no volume (fresh DB each run), healthcheck on `/api/health`; service `playwright` (`mcr.microsoft.com/playwright:<version matching @playwright/test>`) runs tests with `BASE_URL=http://finally-app:8000` (network alias; Chromium forces the bare host `app` to HTTPS because `.app` is an HSTS-preloaded TLD). Tests also runnable locally: `cd test && npx playwright test` with `BASE_URL` defaulting to `http://localhost:8000`. Tests must not depend on order of other tests' trades beyond what they set up themselves (use distinct tickers per test where possible).
