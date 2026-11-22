---
last_mapped_commit: 4de2c198274139dee77f7111f2325f0ff2e32095
last_mapped_at: 2026-11-22
---
# External Integrations

**Analysis Date:** 2026-11-22

## APIs & External Services

**LLM provider (Anthropic Claude):**
- Service: Anthropic Messages API, called directly (no LiteLLM or OpenRouter).
- Client: `anthropic` Python SDK, imported in `backend/app/llm/chat.py`.
- Call pattern: `client.messages.parse(...)` with a Pydantic output model from `backend/app/llm/schema.py`. Uses `output_config={"effort": EFFORT}`, `max_tokens=MAX_TOKENS`, and `model=MODEL` (module constants in `backend/app/llm/chat.py`).
- Error handling: catches `anthropic.RateLimitError`, `anthropic.APIStatusError`, and `anthropic.APIConnectionError`; checks `stop_reason` (`refusal`, `max_tokens`) before using parsed output. On failure, returns a friendly assistant message and executes no actions.
- Auth: env var `ANTHROPIC_API_KEY` (read by the SDK and by `backend/app/llm/chat.py`).
- Disabled when `LLM_MOCK=true`; the deterministic mock lives in `backend/app/llm/mock.py` and makes no network calls.
- Prompt construction: `backend/app/llm/prompts.py`.

**Market data (Massive, formerly Polygon.io) - optional:**
- Service: Massive REST API, polled (not WebSocket).
- Client: `massive` Python SDK (`RESTClient`), imported in `backend/app/market/massive_client.py`.
- Data used: snapshot endpoints via `SnapshotMarketType` and `TickerSnapshot` models, for the union of watched and held tickers.
- Poll interval: default 15 seconds (free tier, 5 requests/minute), with exponential backoff capped at 8x on failure (`backend/app/market/massive_client.py`).
- Auth: env var `MASSIVE_API_KEY`. Selection logic in `backend/app/market/factory.py`: a non-empty key selects Massive; otherwise the built-in simulator is used.
- Failure behavior: exceptions are logged and the loop retries on the next interval (`backend/app/market/massive_client.py`).

**Market data simulator - default, no external service:**
- Implementation: `backend/app/market/simulator.py` (GBM using `numpy`, seed prices in `backend/app/market/seed_prices.py`).
- Runs as an in-process asyncio task; no network access.

**Frontend to backend (same origin, no third-party calls):**
- REST: `frontend/lib/api.ts` calls `/api/portfolio`, `/api/portfolio/history`, `/api/portfolio/trade`, `/api/watchlist`, `/api/chat`, `/api/chat/history`.
- SSE: `frontend/hooks/usePriceStream.ts` opens `EventSource("/api/stream/prices")`. Server side in `backend/app/market/stream.py` (default interval 0.5 s, keepalive every 15 s).
- No CORS configuration is needed: the backend serves the static export and the API from one origin (port 8000).

**Anthropic CI integrations (GitHub Actions only):**
- `anthropics/claude-code-action@v1` in `.github/workflows/claude.yml` and `.github/workflows/claude-code-review.yml`.
- Secret referenced by name: `CLAUDE_CODE_OAUTH_TOKEN` (value not recorded).
- Review workflow runs `/code-review:code-review --comment` on pull requests.

## Data Storage

**Databases:**
- SQLite (single file), the only persistent datastore.
  - Default path: `db/finally.db` in the project root for local runs; `/app/db/finally.db` in the container (`FINALLY_DB_PATH`).
  - Connection: `FINALLY_DB_PATH` env var, resolved in `backend/app/db/database.py`.
  - Client: stdlib `sqlite3` (no ORM). Synchronous, one short-lived connection per call, `isolation_level=None`, `timeout=30.0`.
  - Schema: `backend/app/db/schema.sql`, applied lazily on first access.
  - Tables: `users_profile`, `watchlist`, `positions`, `trades`, `portfolio_snapshots`, `chat_messages`.
- Container persistence: named Docker volume `finally-data` mounted at `/app/db` (created by `scripts/start_mac.sh`; `VOLUME` declared in `Dockerfile`).
- E2E tests use an ephemeral database inside the test container (no volume in `test/docker-compose.test.yml`).

**File storage:**
- Local filesystem only. Frontend static assets are baked into the image at `/app/backend/static`. No object storage.

**Caching:**
- In-process only: the shared price cache in `backend/app/market/cache.py` (thread-locked dict of latest and previous price per ticker). No Redis or external cache.

## Authentication & Identity

**Auth provider:**
- None. Single-user application with no login (per `planning/PLAN.md`). All rows carry `user_id` defaulting to `"default"`.
- The only credentials are the server-side API keys listed under Environment Configuration.

## Monitoring & Observability

**Error tracking:**
- None detected. No Sentry, Datadog, or similar SDK in `backend/pyproject.toml` or `frontend/package.json`.

**Logs:**
- Python `logging` module (module-level loggers, e.g. `backend/app/llm/chat.py`, `backend/app/market/massive_client.py`, `backend/app/market/factory.py`), written to stdout/stderr and collected by Docker.
- Frontend: no logging library.

**Health:**
- `GET /api/health` in `backend/app/api/health.py`; used by the Docker `HEALTHCHECK` and by the E2E compose healthcheck. Reports LLM mock status via `LLM_MOCK`.

## CI/CD & Deployment

**Hosting:**
- Not deployed by this repository. Designed as a single OCI image for any container platform (AWS App Runner, Render, or similar, per `planning/PLAN.md`). No platform-specific config is present in the repo (no `deploy/` directory was found).
- Local run: `scripts/start_mac.sh` / `scripts/start_windows.ps1` build and run the image with `docker run`.

**CI Pipeline:**
- GitHub Actions only, for Claude Code automation (`.github/workflows/claude.yml`, `.github/workflows/claude-code-review.yml`). No build/test CI workflow is present in `.github/workflows/`.
- E2E is run locally or manually via `test/docker-compose.test.yml` (`docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright`).

## Environment Configuration

**Required env vars (for live operation):**
- `ANTHROPIC_API_KEY` - needed only when `LLM_MOCK` is not `true` (chat).

**Optional env vars:**
- `MASSIVE_API_KEY` - enables real market data; absent or empty means simulator.
- `LLM_MOCK` - `true` for deterministic chat responses (tests, CI, no key).
- `FINALLY_DB_PATH`, `FINALLY_STATIC_DIR` - path overrides (set by the Docker image).
- `FINALLY_PORT` - host port for the start script.

**Secrets location:**
- Project-root `.env` file (gitignored), loaded by `python-dotenv` in `backend/app/main.py` and passed to Docker with `--env-file` by the start scripts. Template with variable names and blank values: `.env.example`.
- GitHub secret `CLAUDE_CODE_OAUTH_TOKEN` for the Actions workflows.
- No secret values are recorded in this document.

## Webhooks & Callbacks

**Incoming:**
- None. The only inbound HTTP surface is the application's own `/api/*` routes and the static site (`backend/app/api/*.py`, `backend/app/main.py`). No third-party webhook endpoints.

**Outgoing:**
- Anthropic Messages API (chat turns only, unless `LLM_MOCK=true`).
- Massive REST API (only when `MASSIVE_API_KEY` is set).
- GitHub-hosted Claude Code actions (CI only, not runtime).

---

*Integration audit: 2026-11-22*
