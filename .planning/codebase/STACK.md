---
last_mapped_commit: 4de2c198274139dee77f7111f2325f0ff2e32095
last_mapped_at: 2026-11-22
---
# Technology Stack

**Analysis Date:** 2026-11-22

## Languages

**Primary:**
- Python 3.12+ - All server code in `backend/app/` (FastAPI app, SQLite access, market data, LLM chat). `requires-python = ">=3.12"` in `backend/pyproject.toml`.
- TypeScript 5.9.3 - All frontend code in `frontend/app/`, `frontend/components/`, `frontend/hooks/`, `frontend/lib/`, and the Playwright E2E suite in `test/e2e/`.

**Secondary:**
- SQL (SQLite dialect) - Schema in `backend/app/db/schema.sql`, executed from `backend/app/db/database.py`.
- CSS (Tailwind v4 via `@import "tailwindcss"` and `@theme`) - `frontend/app/globals.css`.
- Shell / PowerShell - Start/stop wrappers in `scripts/start_mac.sh`, `scripts/stop_mac.sh`, `scripts/start_windows.ps1`, `scripts/stop_windows.ps1`.
- YAML - GitHub Actions in `.github/workflows/claude.yml` and `.github/workflows/claude-code-review.yml`; Docker Compose in `test/docker-compose.test.yml`.

## Runtime

**Backend environment:**
- CPython 3.12 (Docker base `python:3.12-slim`, see `Dockerfile` stage `runtime`).
- Local dev venv lives at `backend/.venv` (not committed; excluded from analysis).

**Frontend environment:**
- Node.js 20 for the Docker build stage (`node:20-slim` in `Dockerfile`). Frontend tooling (Next 16, Vitest 5, Vite 8) is run locally with a modern Node; no `.nvmrc` or `engines` field is present.

**Package managers:**
- Python: `uv` 0.8.4 (pinned in `Dockerfile` via `COPY --from=ghcr.io/astral-sh/uv:0.8.4`).
  - Lockfile: `backend/uv.lock` (present). Docker uses `uv sync --frozen`.
- Node (frontend): npm.
  - Lockfile: `frontend/package-lock.json` (present). Docker uses `npm ci` when the lockfile exists.
- Node (E2E): npm.
  - Lockfile: `test/package-lock.json` (present).

## Frameworks

**Core (backend):**
- FastAPI `>=0.115.0` (locked 0.128.7) - REST routes under `/api/*` and static file serving. Entry point `backend/app/main.py` (`app.main:app`).
- Uvicorn `>=0.32.0` with `[standard]` extras (locked 0.40.0) - ASGI server, started by the Docker `CMD`.
- Starlette 0.52.1 (transitive via FastAPI) - `StaticFiles` mount and HTTP exception types used in `backend/app/main.py`.
- Pydantic 2.12.5 (transitive) - Request models in `backend/app/api/*.py` and the LLM structured-output schema in `backend/app/llm/schema.py`.

**Core (frontend):**
- Next.js 16.4.6 - Built as a static export (`output: "export"`, `images.unoptimized`, `reactStrictMode`) in `frontend/next.config.ts`. Output goes to `frontend/out/`, copied into the image at `/app/backend/static`.
- React 19.3.1 and react-dom 19.3.1 - UI runtime (`frontend/package.json`).
- Tailwind CSS 4.3.5 with `@tailwindcss/postcss` 4.3.5 - styling; configured via `frontend/postcss.config.mjs` and `frontend/app/globals.css`.
- lightweight-charts 5.3.0 - Canvas price/P&L charts (`frontend/components/MainChart.tsx`, `frontend/components/TimeSeriesChart.tsx`, `frontend/components/PnlChart.tsx`, `frontend/components/Sparkline.tsx`).
- Hand-rolled treemap - `frontend/lib/treemap.ts`, rendered by `frontend/components/Heatmap.tsx` (no charting dependency for the heatmap).

**Testing:**
- pytest >=8.3.0 with pytest-asyncio >=0.24.0 (`asyncio_mode = "auto"`) and pytest-cov >=5.0.0 - backend tests in `backend/tests/`, configured in `backend/pyproject.toml`.
- httpx >=0.28.1 - FastAPI test client transport (dev extra).
- Vitest 5.1.0 with jsdom 27.4.0, `@testing-library/react` 16.3.3, `@testing-library/jest-dom` 7.0.1, `@testing-library/user-event` 14.6.10 - frontend unit tests in `frontend/tests/`, configured in `frontend/vitest.config.mts`.
- Playwright `@playwright/test` 1.64.1 - E2E tests in `test/e2e/`, configured in `test/playwright.config.ts`. Browser image pinned as `mcr.microsoft.com/playwright:v1.64.1-noble` in `test/docker-compose.test.yml`.

**Build/Dev:**
- uv with hatchling build backend (`[build-system]` in `backend/pyproject.toml`) - the backend is installed as a wheel of the `app` package.
- Ruff >=0.7.0 (dev extra) - lint (`select = ["E","F","I","N","W"]`, line-length 100) configured in `backend/pyproject.toml`.
- ESLint 9.39.5 with `eslint-config-next` 16.4.6 - `frontend/eslint.config.mjs`, script `npm run lint`.
- TypeScript 5.9.3 - `frontend/tsconfig.json` (strict, `paths: { "@/*": ["./*"] }`), script `npm run typecheck`.
- Vite 8.3.8 with `@vitejs/plugin-react` 6.3.0 - pulled in for Vitest.
- Coverage source: `app` (`[tool.coverage.run]`).

## Key Dependencies

**Backend (runtime, from `backend/pyproject.toml`):**
- `anthropic` >=2.8.0 (locked 2.8.0) - Official Anthropic Python SDK. Used in `backend/app/llm/chat.py` with `client.messages.parse(...)` and typed exceptions (`RateLimitError`, `APIStatusError`, `APIConnectionError`). Model ID is a constant `MODEL` in `backend/app/llm/chat.py`.
- `massive` >=1.0.0 (locked 2.2.0) - Massive (Polygon.io) REST client. Used in `backend/app/market/massive_client.py` (`RESTClient`, `SnapshotMarketType`, `TickerSnapshot`).
- `numpy` >=2.0.0 (locked 2.4.2) - Used by the GBM market simulator in `backend/app/market/simulator.py`.
- `python-dotenv` >=1.2.1 (locked 1.2.1) - Loads the project-root `.env` in `backend/app/main.py` (`load_dotenv`).
- `fastapi`, `uvicorn[standard]` - see Frameworks.

**Stdlib modules of note (no install):**
- `sqlite3` - synchronous database access in `backend/app/db/database.py` (one short-lived connection per call, `check_same_thread=False`, `timeout=30.0`).
- `asyncio`, `threading` - background tasks (market data loop, portfolio snapshot loop) and the price cache lock in `backend/app/market/cache.py`.

**Frontend (runtime, from `frontend/package.json`):**
- `next` 16.4.6, `react` 19.3.1, `react-dom` 19.3.1, `lightweight-charts` 5.3.0.
- Browser `EventSource` is used directly (no SSE library) in `frontend/hooks/usePriceStream.ts`.
- `fetch` is wrapped in `frontend/lib/api.ts` (same-origin `/api/*` calls).

**E2E (`test/package.json`):**
- `@playwright/test` 1.64.1, `typescript` 5.9.3, `@types/node` 22.20.5.

**Infrastructure / tooling:**
- Docker (multi-stage `Dockerfile`: `node:20-slim` frontend stage, `python:3.12-slim` runtime stage).
- GitHub Actions: `actions/checkout@v4`, `astral-sh/setup-uv@v6`, `anthropics/claude-code-action@v1` (see `.github/workflows/`).

## Configuration

**Environment variables (names only; values are never recorded here):**
- `ANTHROPIC_API_KEY` - Required for live LLM chat. Read in `backend/app/llm/chat.py`; surrounding whitespace, CR, and wrapping quotes are stripped before use.
- `MASSIVE_API_KEY` - Optional. If set and non-empty, `backend/app/market/factory.py` selects the Massive REST poller; otherwise the built-in simulator is used.
- `LLM_MOCK` - `"true"` enables deterministic mock responses (`backend/app/llm/mock.py`). Also read in `backend/app/api/health.py`. Default in the image is `false` (`Dockerfile` `ENV`).
- `FINALLY_DB_PATH` - SQLite file path. Default in the image is `/app/db/finally.db`; read in `backend/app/db/database.py`.
- `FINALLY_STATIC_DIR` - Directory served at `/`. Default in the image is `/app/backend/static`; read in `backend/app/main.py`.
- `FINALLY_PORT` - Host port used by `scripts/start_mac.sh` (default 8000).
- `BASE_URL` - Target URL for Playwright (`test/playwright.config.ts`, default `http://localhost:8000`).
- `CI` - Playwright behavior toggle (`forbidOnly`), set in `test/docker-compose.test.yml`.

**Env file:**
- A project-root `.env` is read by `load_dotenv` at backend startup and passed to Docker via `--env-file` by the start scripts. Template: `.env.example` (keys: `ANTHROPIC_API_KEY`, `MASSIVE_API_KEY`, `LLM_MOCK`). The real `.env` is gitignored and is not read by this analysis.

**Build configuration files:**
- `backend/pyproject.toml` - project metadata, dependencies, ruff, pytest, coverage.
- `backend/uv.lock` - locked Python dependency graph.
- `frontend/package.json`, `frontend/package-lock.json` - Node dependencies and scripts.
- `frontend/next.config.ts` - static export configuration.
- `frontend/tsconfig.json` - TypeScript configuration (`@/*` alias to frontend root).
- `frontend/vitest.config.mts` - Vitest (jsdom, `tests/**/*.test.{ts,tsx}`).
- `frontend/eslint.config.mjs` - ESLint flat config.
- `frontend/postcss.config.mjs` - PostCSS for Tailwind v4.
- `test/playwright.config.ts`, `test/tsconfig.json`, `test/docker-compose.test.yml` - E2E.
- `Dockerfile`, `.dockerignore` - container build.
- `.gitignore` - ignores `.env`, `db/finally.db`, build output, and caches.

**Database schema:**
- `backend/app/db/schema.sql` defines `users_profile`, `watchlist`, `positions`, `trades`, `portfolio_snapshots`, `chat_messages`. Created lazily on first use by `backend/app/db/database.py`.

## Platform Requirements

**Development:**
- Python 3.12+ and `uv`.
- Node.js (20 is the reference used in Docker) and npm.
- Docker (for the production image and the E2E stack in `test/`).
- macOS, Linux, or Windows. Start/stop scripts exist for macOS/Linux (bash) and Windows (PowerShell).

**Production:**
- Single container built from the root `Dockerfile`, serving the API and the static frontend on port 8000 (`EXPOSE 8000`, `CMD uvicorn app.main:app --host 0.0.0.0 --port 8000`).
- Persistent storage: named volume `finally-data` mounted at `/app/db` (`VOLUME ["/app/db"]`).
- Health check: `GET /api/health` via a Python `urllib` probe in the `Dockerfile` `HEALTHCHECK`.
- Designed for any container platform (App Runner, Render, or similar). No cloud-specific SDK is used.

---

*Stack analysis: 2026-11-22*
