---
last_mapped_commit: 4de2c198274139dee77f7111f2325f0ff2e32095
last_mapped_at: 2026-11-22
---
# Codebase Structure

**Analysis Date:** 2026-11-22

## Directory Layout

```
finally/                         # repo root
├── backend/                     # FastAPI uv project (Python 3.12)
│   ├── app/                     # Application package (imported as `app`)
│   │   ├── main.py              # ASGI app factory, lifespan, static mount
│   │   ├── api/                 # HTTP routers + deps
│   │   ├── services/            # Portfolio and watchlist business rules
│   │   ├── llm/                 # Claude chat orchestration, prompts, schema, mock
│   │   ├── market/              # Price sources, cache, SSE stream
│   │   └── db/                  # SQLite layer + schema.sql
│   ├── tests/                   # pytest suites (mirrors app/ areas)
│   ├── pyproject.toml           # Project + tool config (ruff, pytest, coverage)
│   ├── uv.lock                  # Locked Python dependencies
│   ├── market_data_demo.py      # Terminal demo of the simulator
│   ├── README.md / CLAUDE.md    # Backend docs and developer guide
│   └── static/                  # (build output, gitignored) copied frontend export
├── frontend/                    # Next.js 16 TypeScript project (static export)
│   ├── app/                     # Next.js App Router: layout, page, globals.css
│   ├── components/              # Terminal UI components (flat)
│   ├── hooks/                   # usePriceStream, usePolling
│   ├── lib/                     # api client, types, formatting, treemap, portfolio math
│   ├── tests/                   # Vitest + Testing Library suites
│   ├── public/                  # Static assets copied as-is
│   ├── out/                     # (build output, gitignored) static export
│   ├── next.config.ts           # output: 'export', unoptimized images
│   └── package.json             # Scripts: dev, build, lint, typecheck, test
├── test/                        # Playwright E2E suite (separate npm project)
│   ├── e2e/                     # *.spec.ts specs + helpers.ts
│   ├── playwright.config.ts     # Projects: fresh-start → api, chromium, ...
│   ├── docker-compose.test.yml  # App container (LLM_MOCK=true) + Playwright runner
│   └── package.json
├── db/                          # Runtime volume mount point (finally.db gitignored)
│   └── .gitkeep
├── planning/                    # Agent-facing project docs (PLAN.md is the spec)
│   ├── PLAN.md                  # Full project specification
│   ├── CONTRACT.md              # API and DB contract referenced by code comments
│   ├── MARKET_*.md              # Market-data design, interface, review, summary
│   ├── MASSIVE_API.md           # Massive (Polygon.io) integration notes
│   ├── REVIEW.md                # Review notes
│   └── archive/                 # Superseded market-data design docs
├── scripts/                     # Docker start/stop wrappers (mac + windows)
├── independent-reviewer/        # Claude plugin (.claude-plugin/plugin.json, hooks/)
├── .github/workflows/           # claude.yml and claude-code-review.yml
├── .claude/                     # Claude Code / GSD tooling (agents, commands, hooks)
├── .planning/                   # GSD planning state (config.json, codebase/ docs)
├── Dockerfile                   # Stage 1 Node 20 build → Stage 2 Python 3.12 runtime
├── README.md                    # Project readme
├── .env.example                 # Documented env var names (committed)
├── .env                         # Local secrets (gitignored; never read by tools)
└── .gitignore
```

## Directory Purposes

**`backend/app/`:**
- Purpose: The whole server. Only `main.py` is process-level; everything else is imported.
- Contains: Python modules grouped by layer (see below). `__init__.py` files re-export public names (`app/market/__init__.py`, `app/services/__init__.py`, `app/db/__init__.py`, `app/api/__init__.py` exports `ROUTERS`, `app/llm/__init__.py`).
- Key files: `backend/app/main.py`, `backend/app/api/__init__.py`, `backend/app/services/portfolio.py`, `backend/app/llm/chat.py`, `backend/app/market/factory.py`, `backend/app/db/database.py`.

**`backend/app/api/`:**
- Purpose: One router per URL area. Each router is an `APIRouter` with a `prefix`.
- Contains: `health.py` (`/api/health`), `watchlist.py` (`/api/watchlist`), `portfolio.py` (`/api/portfolio`, `/trade`, `/history`), `chat.py` (`/api/chat`, `/history`), `deps.py` (service providers).
- Key files: `backend/app/api/portfolio.py`, `backend/app/api/deps.py`.

**`backend/app/services/`:**
- Purpose: Business rules used by both HTTP and LLM paths.
- Contains: `portfolio.py` (`PortfolioService`, `TradeError`, `VALID_SIDES`), `watchlist.py` (`WatchlistService`, `WatchlistError`, `TICKER_RE`, `normalize_ticker`).

**`backend/app/llm/`:**
- Purpose: Everything about the AI assistant.
- Contains: `chat.py` (model constants, client creation, `handle_chat`, `call_llm`, `execute_actions`, user-facing message constants), `prompts.py` (`SYSTEM_PROMPT`, `build_context`, `build_messages`), `schema.py` (Pydantic response models), `mock.py` (deterministic responses for `LLM_MOCK=true`).

**`backend/app/market/`:**
- Purpose: Market data producers, shared cache, SSE.
- Contains: `interface.py`, `simulator.py`, `massive_client.py`, `factory.py`, `cache.py`, `models.py`, `seed_prices.py`, `stream.py`.

**`backend/app/db/`:**
- Purpose: Persistence. `schema.sql` is executed on every startup by `init_db()` (idempotent `CREATE ... IF NOT EXISTS`).
- Contains: `database.py` (all SQL), `schema.sql`, `__init__.py` (re-exports).

**`backend/tests/`:**
- Purpose: pytest suites. Directory names mirror the app packages.
- Contains: `api/` (`test_api.py`, `conftest.py`), `db/` (atomicity, edge cases, init, trades, watchlist, snapshots and chat), `llm/` (chat, mock, schema, wire format, API key), `market/` (cache, factory, Massive, models, simulator, stream, review fixes), `services/` (portfolio and watchlist services, with `fakes.py`). Shared fixtures live in `backend/tests/conftest.py`.

**`frontend/app/`:**
- Purpose: Next.js App Router shell. Only the root route exists.
- Contains: `layout.tsx` (metadata and `<html>`), `page.tsx` (renders `Terminal`), `globals.css` (global styles).

**`frontend/components/`:**
- Purpose: All UI. Flat directory, one component per file, PascalCase names.
- Contains: `Terminal.tsx` (root layout and data orchestration), `Header.tsx`, `Watchlist.tsx`, `MainChart.tsx`, `TimeSeriesChart.tsx` (shared chart primitive), `PnlChart.tsx`, `Heatmap.tsx`, `PositionsTable.tsx`, `TradeBar.tsx`, `ChatPanel.tsx`, `Sparkline.tsx`, `Panel.tsx` (panel wrapper, per its name).

**`frontend/hooks/`:**
- Purpose: Reusable stateful logic.
- Contains: `usePriceStream.ts` (EventSource, reconnect backoff, history), `usePolling.ts`.

**`frontend/lib/`:**
- Purpose: Framework-free helpers and typed data.
- Contains: `api.ts` (fetch wrapper and `api` object), `types.ts` (shared interfaces such as `Portfolio`, `WatchlistItem`, `PriceTick`), `portfolio.ts` (`revaluePortfolio`), `treemap.ts` (layout), `format.ts` (number and time formatting).

**`frontend/tests/`:**
- Purpose: Vitest unit and component tests, with `setup.ts` (jest-dom) and `mocks.ts` (fixtures).
- Contains: `*.test.ts(x)` files, one per lib module or component area (`api.test.ts`, `portfolio.test.ts`, `treemap.test.ts`, `format.test.ts`, `priceStream.test.tsx`, `Chat.test.tsx`, `Watchlist.test.tsx`, `Terminal.test.tsx`, `components.test.tsx`, `behaviour.test.tsx`).

**`test/e2e/`:**
- Purpose: Browser and API end-to-end tests against the running container.
- Contains: `fresh-start.spec.ts`, `api.spec.ts`, `trading.spec.ts`, `portfolio.spec.ts`, `watchlist.spec.ts`, `chat.spec.ts`, `sse.spec.ts`, `helpers.ts`.

**`db/`:**
- Purpose: Runtime location of `finally.db` when run outside Docker. The Docker start scripts use a named volume (`finally-data`) at `/app/db` instead of this folder.

**`scripts/`:**
- Purpose: Operator wrappers around `docker build` and `docker run`. `start_mac.sh` and `stop_mac.sh` are the POSIX pair; `start_windows.ps1` and `stop_windows.ps1` are the PowerShell pair.

**`planning/`:**
- Purpose: Shared contract for agents. Code comments reference `planning/CONTRACT.md` section 2 for the DB contract. Read `planning/PLAN.md` first. `MARKET_DATA_SUMMARY.md` summarizes the completed market-data work; details are in `planning/archive/`.

**`.claude/`, `.planning/`, `independent-reviewer/`:**
- Purpose: Tooling and agent workflow, not product code. Do not edit `.claude/gsd-core/` or the generated `.claude/gsd-file-manifest.json` when working on the app.

## Key File Locations

**Entry Points:**
- `backend/app/main.py`: ASGI `app`, lifespan, static mount.
- `frontend/app/page.tsx`: Browser root route.
- `Dockerfile`: Build pipeline and `CMD ["uvicorn", "app.main:app", ...]`.

**Configuration:**
- `backend/pyproject.toml`: Python dependencies, ruff (line length 100), pytest (`asyncio_mode = "auto"`), coverage.
- `frontend/next.config.ts`: `output: 'export'`, `images.unoptimized`.
- `frontend/package.json`: npm scripts and pinned versions.
- `frontend/eslint.config.mjs`, `frontend/postcss.config.mjs`, `frontend/vitest.config.mts`, `frontend/tsconfig.json`: Frontend tooling.
- `test/playwright.config.ts`, `test/docker-compose.test.yml`: E2E setup.
- `.env.example`: Documents `ANTHROPIC_API_KEY`, `MASSIVE_API_KEY`, `LLM_MOCK`. Do not read `.env`.

**Core Logic:**
- `backend/app/services/portfolio.py`: Trade execution and valuation.
- `backend/app/db/database.py`: All SQL, including `apply_trade`.
- `backend/app/llm/chat.py`: Chat turn and action executor.
- `backend/app/market/simulator.py`, `backend/app/market/massive_client.py`: Price producers.
- `backend/app/market/stream.py`: SSE generator.
- `frontend/components/Terminal.tsx`: UI composition and polling intervals.
- `frontend/hooks/usePriceStream.ts`: Live price state.

**Testing:**
- `backend/tests/`: pytest.
- `frontend/tests/`: Vitest.
- `test/e2e/`: Playwright.

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (`massive_client.py`, `seed_prices.py`).
- Python test files: `test_*.py`, with `conftest.py` per package.
- React components: `PascalCase.tsx` (`TradeBar.tsx`, `PnlChart.tsx`).
- Hooks: `useCamelCase.ts` (`usePriceStream.ts`).
- Lib and utility modules: `camelCase.ts` or lowercase (`api.ts`, `treemap.ts`, `portfolio.ts`).
- Frontend tests: `*.test.ts` or `*.test.tsx` in `frontend/tests/`. E2E specs: `*.spec.ts` in `test/e2e/` (kebab-case, e.g. `fresh-start.spec.ts`).
- Markdown in `planning/`: `UPPER_SNAKE.md`.

**Directories:**
- Python packages: lowercase single words (`api`, `llm`, `market`, `db`, `services`).
- Frontend: lowercase single words (`components`, `hooks`, `lib`).

**Identifiers:**
- Python: `snake_case` functions, `PascalCase` classes, `UPPER_SNAKE` module constants (`HISTORY_LIMIT`, `MODEL`, `SNAPSHOT_INTERVAL_SECONDS`). Routers are named `router` in each API module.
- TypeScript: `PascalCase` components, `camelCase` functions, `UPPER_SNAKE` constants (`PORTFOLIO_POLL_MS`, `MAX_POINTS`).

## Where to Add New Code

**New REST endpoint for trading or portfolio:**
- Route: `backend/app/api/portfolio.py` (or a new module in `backend/app/api/`, registered in `ROUTERS` in `backend/app/api/__init__.py`).
- Business logic: `backend/app/services/portfolio.py`. Add the new method there so the LLM path can reuse it.
- Persistence: a new function in `backend/app/db/database.py`, re-exported from `backend/app/db/__init__.py`.
- Tests: `backend/tests/api/test_api.py` (HTTP), `backend/tests/services/` (rules), `backend/tests/db/` (SQL).

**New service:**
- Implementation: `backend/app/services/<name>.py`, exported from `backend/app/services/__init__.py`.
- Provider: add `get_<name>_service` to `backend/app/api/deps.py`, and create the instance in `backend/app/main.py::lifespan` and store it on `app.state`.

**New database table or column:**
- Schema: `backend/app/db/schema.sql` (keep `CREATE ... IF NOT EXISTS`; `init_db()` runs it on every start).
- Access: `backend/app/db/database.py`. Include `user_id` with default `"default"` to match the existing tables.
- Seed data: `init_db()` in `backend/app/db/database.py`.

**New LLM action type:**
- Schema: `backend/app/llm/schema.py`.
- Execution: `execute_actions` in `backend/app/llm/chat.py`.
- Prompt guidance: `SYSTEM_PROMPT` in `backend/app/llm/prompts.py`.
- Mock trigger: `backend/app/llm/mock.py`.

**New market data source:**
- Implementation: `backend/app/market/<name>.py` subclassing `MarketDataSource` (`backend/app/market/interface.py`).
- Selection: `backend/app/market/factory.py`.
- Export: `backend/app/market/__init__.py` if public.

**New frontend panel or widget:**
- Component: `frontend/components/<Name>.tsx`. Wrap in `Panel` (`frontend/components/Panel.tsx`) for consistent chrome.
- Wire into: `frontend/components/Terminal.tsx`, which owns polling and state.
- Data access: add a method to `api` in `frontend/lib/api.ts` and a type in `frontend/lib/types.ts`.
- Pure logic: `frontend/lib/` (keeps it testable without React).
- Tests: `frontend/tests/<Name>.test.tsx`.

**New hook:**
- Implementation: `frontend/hooks/use<Name>.ts`.

**New shared utility:**
- Backend: `backend/app/` in the matching layer. There is no general `utils` package.
- Frontend: `frontend/lib/<name>.ts`.

**New E2E scenario:**
- Spec: `test/e2e/<feature>.spec.ts`. Keep the existing `fresh-start` project order in mind (`test/playwright.config.ts`).

## Special Directories

**`backend/static/`:**
- Purpose: Copy of the Next.js static export. Served by `SPAStaticFiles` in `backend/app/main.py`.
- Generated: Yes (Dockerfile `COPY --from=frontend`, or manual copy of `frontend/out`).
- Committed: No (`.gitignore`).

**`frontend/out/`:**
- Purpose: `next build` output for `output: 'export'`.
- Generated: Yes.
- Committed: No.

**`db/`:**
- Purpose: Holds `finally.db` at runtime when run outside Docker. `FINALLY_DB_PATH` overrides the location.
- Generated: Yes, created by `init_db()`.
- Committed: Only `.gitkeep`. `db/*.db` is gitignored.

**`backend/.venv/`, `frontend/node_modules/`, `frontend/.next/`:**
- Purpose: Dependency and cache directories.
- Generated: Yes.
- Committed: No.

**`backend/.pytest_cache/`, `backend/.ruff_cache/`, `backend/.coverage`, `__pycache__/`:**
- Purpose: Tool caches.
- Generated: Yes.
- Committed: No.

**`backend/.playwright-mcp/`:**
- Purpose: Browser-automation console logs captured during development.
- Generated: Yes.
- Committed: Not intended. Not part of the app.

---

*Structure analysis: 2026-11-22*
