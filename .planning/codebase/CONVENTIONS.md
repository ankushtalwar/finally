---
last_mapped_commit: 4de2c198274139dee77f7111f2325f0ff2e32095
last_mapped_at: 2026-11-22
---
# Coding Conventions

**Analysis Date:** 2026-11-22

## Naming Patterns

**Files:**
- Python modules: `snake_case.py` — `backend/app/services/portfolio.py`, `backend/app/market/massive_client.py`, `backend/app/llm/mock.py`
- Python tests: `test_<subject>.py` under `backend/tests/<area>/` — `backend/tests/market/test_cache.py`, `backend/tests/services/test_portfolio_service.py`
- React components: `PascalCase.tsx` in `frontend/components/` — `frontend/components/Watchlist.tsx`, `frontend/components/TradeBar.tsx`
- React hooks: `camelCase.ts` with a `use` prefix in `frontend/hooks/` — `frontend/hooks/usePriceStream.ts`, `frontend/hooks/usePolling.ts`
- Pure TS utilities: `camelCase.ts` in `frontend/lib/` — `frontend/lib/format.ts`, `frontend/lib/treemap.ts`, `frontend/lib/api.ts`
- Shared TS types: `frontend/lib/types.ts`
- Frontend unit tests: `frontend/tests/<Name>.test.tsx` or `<name>.test.ts`. Both casings exist (`Chat.test.tsx`, `api.test.ts`, `treemap.test.ts`). Prefer the component name for component tests and the module name for lib tests.
- E2E specs: `test/e2e/<feature>.spec.ts` (kebab/lowercase) plus `test/e2e/helpers.ts`
- SQL schema: `backend/app/db/schema.sql`

**Functions:**
- Python: `snake_case`. Private helpers are prefixed with `_` — `_now()`, `_norm()`, `_position_dict()`, `_snapshot_loop()` in `backend/app/main.py`.
- TypeScript: `camelCase`. Components are named exports or default exports with PascalCase names. Hooks use `use` prefix. Pure helpers are named functions, not arrow constants (`appendPoint`, `applyBatch` in `frontend/hooks/usePriceStream.ts`).
- Async Python functions are used wherever the call touches the market data source: `async def execute_trade(...)` in `backend/app/services/portfolio.py`, `async def add(...)` in `backend/app/services/watchlist.py`. Synchronous DB access stays `def`.

**Variables:**
- Python: `snake_case`. Module-level constants are `UPPER_SNAKE_CASE` — `MODEL`, `MAX_TOKENS`, `HISTORY_LIMIT` in `backend/app/llm/chat.py`; `SNAPSHOT_INTERVAL_SECONDS` in `backend/app/main.py`; `DEFAULT_TICKERS`, `QTY_EPSILON` in `backend/app/db/database.py`.
- TypeScript: `camelCase` for locals. Module-level constants use `UPPER_SNAKE_CASE` when they are configuration (`MAX_POINTS`, `RECONNECT_BASE_MS` in `frontend/hooks/usePriceStream.ts`).
- Privacy in Python is by leading underscore on module-level state: `_lock`, `_db_path`, `_initialized` in `backend/app/db/database.py`, and `self._cache`, `self._source` on service classes.

**Types and classes:**
- Python classes: `PascalCase`. Exceptions end in `Error` (`TradeError`, `WatchlistError`) or are named by the domain (`TradeRejected`). `TradeRejected` carries a `# noqa: N818` because its name is fixed by the contract (`backend/app/db/database.py`).
- Python Pydantic models: `PascalCase` with a suffix that names the role — `TradeRequest`, `ChatRequest` (API bodies), `ChatResponse`, `TradeInstruction`, `WatchlistChange` (LLM schema in `backend/app/llm/schema.py`).
- TypeScript types and interfaces: `PascalCase`, no `I` prefix (`Portfolio`, `ChatMessage`, `WatchlistRowData`, `RowProps`). Props interfaces are named `<Component>Props` or `RowProps` for internal row components.
- Frontend classes are rare; the one exception is the error type `ApiError` in `frontend/lib/api.ts`.

**Identifiers over the wire:**
- JSON and API field names are `snake_case` on both sides: `previous_price`, `day_change_percent`, `avg_cost`, `watchlist_changes`. The frontend keeps them as-is in `frontend/lib/types.ts`.
- React-facing camelCase exists only in derived view models, for example `dayChangePercent` in `WatchlistRowData` (`frontend/components/Watchlist.tsx`). Do not rename wire fields to camelCase.
- Ticker symbols are normalized to uppercase and trimmed at every entry point. Use `normalize_ticker()` in `backend/app/services/watchlist.py` and `_norm()` in `backend/app/db/database.py`. Do not add a second normalizer.

**Test identifiers:**
- Frontend DOM hooks use `data-testid` with a kebab-case pattern `<component>-<element>[-<TICKER>]`: `watchlist-row-AAPL`, `watchlist-price-AAPL`, `watchlist-add-input`, `position-row-JPM`, `cash-balance`, `trade-success`. The E2E specs select by these IDs, so keep them stable.

## Code Style

**Python formatting and linting:**
- Ruff is the only Python linter and formatter configured. Settings are in `backend/pyproject.toml` under `[tool.ruff]`:
  - `line-length = 100`, `target-version = "py312"`
  - `[tool.ruff.lint] select = ["E", "F", "I", "N", "W"]`, with `E501` ignored (the formatter handles length).
- Run with `uv run --extra dev ruff check app/ tests/` from `backend/` (see `backend/CLAUDE.md`).
- Every module in `backend/app/` that defines types starts with a module docstring, then `from __future__ import annotations`. 20 of 27 modules under `backend/app/` use the future import. Add it to any new module.
- Imports are grouped: stdlib, third-party, then local `app.*` imports. Relative imports are used only inside a package for siblings (`from .mock import mock_response` in `backend/app/llm/chat.py`, `from .deps import ...` in `backend/app/api/chat.py`). Use absolute `from app import db` for cross-package imports.
- Where an import must come after a side effect, mark it `# noqa: E402`. Example: `backend/app/main.py` loads `.env` before importing `app.db`.

**Frontend formatting and linting:**
- No Prettier config is checked in. Formatting follows the existing style: double quotes, semicolons, trailing commas, 2-space indent. Match the surrounding file.
- ESLint 9 flat config in `frontend/eslint.config.mjs` extends `eslint-config-next/core-web-vitals` and `eslint-config-next/typescript`. Ignores `.next/**`, `out/**`, `node_modules/**`, `next-env.d.ts`.
- The only lint suppression in source is `// eslint-disable-next-line react-hooks/exhaustive-deps` at `frontend/components/TimeSeriesChart.tsx:78`. Do not add more. Fix the dependency list or move the value into a ref.
- TypeScript is `strict: true` in `frontend/tsconfig.json`. There are no `any` casts and no `@ts-ignore` in `frontend/components`, `frontend/hooks`, `frontend/lib`, or `frontend/app`.
- Path alias `@/*` maps to the `frontend/` root. Use it for every cross-folder import: `import { api } from "@/lib/api";`, `import type { Point } from "@/hooks/usePriceStream";`.
- Tailwind v4 utility classes are written inline. Dark theme tokens are custom names (`border-line-soft`, `bg-raised`, `border-l-accent`), defined in `frontend/app/globals.css`. Use these tokens instead of raw hex colors in components.

**Type annotations:**
- Python: annotate every public function and method. Modern union syntax (`float | None`, `list[str]`, `dict[str, Any]`) is used, not `Optional`/`List`.
- Python dict-shaped returns from services are typed as `dict` (not TypedDict) in `backend/app/services/portfolio.py` and `backend/app/services/watchlist.py`. Keep that for JSON-bound data; use Pydantic models for request/response validation.
- Frozen dataclasses with `slots=True` model immutable data: `PriceUpdate` in `backend/app/market/models.py`.

## Import Organization

**Python order:**
1. `from __future__ import annotations`
2. Standard library (`import logging`, `import asyncio`, `from collections.abc import AsyncIterator`)
3. Third-party (`from fastapi import APIRouter`, `from pydantic import BaseModel`, `import anthropic`)
4. Local `app.*` (`from app import db`, `from app.market import PriceCache`)
5. Relative (`from .schema import ChatResponse`) — only inside a package

**TypeScript order:**
1. Framework and library imports (`import { useState } from "react";`, `import { render } from "@testing-library/react";`)
2. Path-aliased local imports (`import { api } from "@/lib/api";`)
3. Relative sibling imports (`import Panel from "./Panel";`)
4. Type-only imports use `import type { ... }` (e.g., `import type { ChatMessage, Portfolio } from "./types";` in `frontend/lib/api.ts`). Inline `type` modifiers are used for mixed imports (`import Watchlist, { type WatchlistRowData } from "@/components/Watchlist";`).

**Path aliases:**
- Frontend: `@/` → `frontend/` (`frontend/tsconfig.json` `paths`, mirrored in `frontend/vitest.config.mts` `resolve.alias`).
- Backend: no aliases. Import from the `app` package root.

**Barrel files:**
- `backend/app/market/__init__.py`, `backend/app/services/__init__.py`, `backend/app/llm/__init__.py`, `backend/app/api/__init__.py`, and `backend/app/db/__init__.py` re-export their public API. Import from the package, not from submodules, in consumer code: `from app.market import PriceCache, create_stream_router`, `from app.services import PortfolioService, TradeError`.
- The frontend has no barrel files. Import from the concrete module.

## Error Handling

**Backend, domain errors:**
- Domain validation raises a dedicated exception that carries a user-safe `message` attribute. Examples:
  - `TradeError(message)` in `backend/app/services/portfolio.py`
  - `WatchlistError(message, status_code=400)` in `backend/app/services/watchlist.py`
  - `TradeRejected(message)` in `backend/app/db/database.py` (DB layer), translated to `TradeError` by the service layer
- Services never raise `HTTPException`. Routes catch the domain error and convert it:
  ```python
  # backend/app/api/portfolio.py
  try:
      executed = await svc.execute_trade(body.ticker, body.side, body.quantity)
  except TradeError as exc:
      raise HTTPException(status_code=400, detail=exc.message) from exc
  ```
- Error responses use FastAPI's default `{"detail": "..."}` body. The frontend reads `detail` as a string or as the first `msg` in a 422 array (`errorMessage()` in `frontend/lib/api.ts`). Keep `detail` a string for domain errors.
- Validation errors on request bodies are Pydantic-driven. Use `field_validator` for normalization and blank checks (`ChatRequest.not_blank` in `backend/app/api/chat.py`).

**Backend, broad catches:**
- Broad `except Exception` appears 8 times in `backend/app/`. Use it only at the outer boundary of a background task or external call, and always log with `logger.exception(...)` before returning a fallback. Example: `PortfolioService.record_snapshot()` in `backend/app/services/portfolio.py` logs and swallows so a snapshot failure never fails a trade.
- The chat path maps SDK exceptions to user-facing messages (`MSG_RATE_LIMIT`, `MSG_GENERIC_ERROR` in `backend/app/llm/chat.py`). Failures execute no actions and return a friendly assistant message, not a 500.
- Cancellation is always re-raised. Background loops catch `asyncio.CancelledError` only to clean up (`backend/app/main.py` `lifespan`, `backend/app/market/stream.py`).

**Backend, database:**
- DB functions run inside context managers `_read()` and `_write()` defined in `backend/app/db/database.py`. Writes are serialized by a module-level `threading.RLock` and run in `BEGIN IMMEDIATE`. Do not open raw `sqlite3` connections elsewhere.
- Use the `QTY_EPSILON` (1e-9) constant for quantity comparisons. Do not compare floats with `==`.

**Frontend:**
- API calls go through the single `request<T>()` helper in `frontend/lib/api.ts`. It throws `ApiError` (with `status`) on non-2xx responses. Callers catch `ApiError` and show `err.message` inline (see the trade and watchlist flows in `frontend/components/TradeBar.tsx` and `frontend/components/Watchlist.tsx`).
- Components do not swallow errors silently. Failed actions surface a `data-testid="trade-error"` or equivalent inline message.
- SSE/polling failures go through the reconnect logic in `frontend/hooks/usePriceStream.ts` (`RECONNECT_BASE_MS`, `RECONNECT_MAX_MS`), not ad hoc retries in components.

**No `console.*` in frontend source.** Zero occurrences in `frontend/components`, `frontend/hooks`, `frontend/lib`, `frontend/app`. Surface state in the UI instead.

## Logging

**Backend framework:** Python standard `logging`. Each module creates `logger = logging.getLogger(__name__)`. There are 36 `logger.` calls under `backend/app/`. No `print()` calls are used in `backend/app/`.

**Patterns:**
- `logger.info` for lifecycle events (static dir not found, source started). See `backend/app/main.py`.
- `logger.exception` for caught errors where the traceback matters. Always inside an `except` block.
- `logger.warning` for degraded but recoverable states (for example Massive poll failures in `backend/app/market/massive_client.py`).
- Do not log API keys, prompt bodies, or full portfolio dumps. The chat path logs status and error class only.

`market_data_demo.py` at `backend/market_data_demo.py` is a standalone script and may print; it is not part of the app package.

## Comments

**When to comment:**
- Explain *why* a non-obvious choice was made, especially for contract references and float handling. Examples in the codebase:
  - `# No gt=0 here: a bad quantity in one trade must fail only that trade` (`backend/app/llm/schema.py`)
  - `# Price universe = watchlist ∪ held tickers: drop a fully-sold, unwatched ticker.` (`backend/app/services/portfolio.py`)
  - `# Fixed-width microseconds keep the format stable and lexically sortable.` (`backend/app/db/database.py`)
- Cite the spec when a name or behavior is fixed by it: `"see planning/CONTRACT.md section 2"` in the module docstring of `backend/app/db/database.py`, `# noqa: N818 - name fixed by CONTRACT.md`.
- Do not restate the code. Skip comments like `# increment counter`.

**Docstrings:**
- Python: triple-double-quoted, one-line summary, optional body. Public classes and functions have docstrings. Private helpers often do not. Example: `"""Portfolio valuation and trade execution."""` at the top of `backend/app/services/portfolio.py`.
- TypeScript: JSDoc-style `/** ... */` one-liners on exported helpers. Examples: `/** Append a tick to a per-ticker series, one point per second (latest wins). */` in `frontend/hooks/usePriceStream.ts`, `/** \`$10,000.00\` */` in `frontend/lib/format.ts`. Use inline examples in backticks for formatters.

## Function Design

**Size:** Keep service methods focused on one operation. The longest service method is `PortfolioService.get_portfolio()` in `backend/app/services/portfolio.py`, which builds the full valuation. Split helpers out (`_position_dict`) rather than inlining DB reads.

**Parameters:**
- Python: keyword-only for optional DB scoping arguments, with the default user: `def get_cash(*, user_id: str = DEFAULT_USER)` in `backend/app/db/database.py`. Keep `user_id` keyword-only so every call site names it.
- Services receive their collaborators in `__init__` (`PriceCache`, `MarketDataSource`). Do not import singletons inside service methods.
- TypeScript: component props are a single typed `Props` object, destructured in the signature.

**Return values:**
- Python services return plain `dict` or `list[dict]` ready for JSON. Routes return them directly and FastAPI serializes.
- DB helpers return `dict` rows built by `_position_dict` / `_chat_dict`, never raw `sqlite3.Row`.
- Frontend hooks return an object of named values (`{ prices, history, status }` style) rather than tuples.

## Module Design

**Exports:**
- Python: explicit `__all__` is not used. Public names are those without a leading underscore, re-exported from the package `__init__.py`.
- TypeScript: named exports for helpers and types; default export for the main component in each file (`export default function Watchlist`). Tests import both forms.

**Abstractions:**
- Strategy pattern for market data: `MarketDataSource` ABC in `backend/app/market/interface.py` with `SimulatorDataSource` and `MassiveDataSource` implementations. Add a new source by subclassing and wiring it in `backend/app/market/factory.py`. Do not branch on source type in downstream code.
- Factory functions: `create_market_data_source(cache)` (`backend/app/market/factory.py`), `create_stream_router(price_cache)` (`backend/app/market/stream.py`). Prefer a factory with an injected cache over module-level globals.
- Dependency injection in routes uses FastAPI `Depends` with getters that read `request.app.state` (`backend/app/api/deps.py`). New services are attached to `app.state` in the lifespan in `backend/app/main.py`.

**Configuration:**
- Environment variables are read at call time with `os.environ.get(...)`, not cached at import, so tests can monkeypatch them. Names in use: `ANTHROPIC_API_KEY`, `MASSIVE_API_KEY`, `LLM_MOCK`, `FINALLY_DB_PATH`, `FINALLY_STATIC_DIR`. Document new variables in `.env.example` (names only).
- Module-level constants hold tunables: `MODEL`, `MAX_TOKENS`, `EFFORT`, `HISTORY_LIMIT`, `REQUEST_TIMEOUT_S` in `backend/app/llm/chat.py`.

**Barrel Files:** Used in every Python package under `backend/app/`. Not used in the frontend.

## Frontend Component Conventions

- Client components start with `"use client";` (10 of 17 files in `frontend/components` and `frontend/hooks` do so). Any component that uses state, effects, or browser APIs needs it. Keep server-only pieces in `frontend/app/`.
- Derive values during render. Do not add an effect to sync derived state. `usePriceFlash` in `frontend/components/Watchlist.tsx` derives its flash class from `price` changes using the "adjust state during render" pattern.
- Ticker rows and list items are `role="button"` `div`s with `tabIndex={0}` and keyboard handlers for Enter and Space, not bare `div`s with only `onClick`. Mirror `WatchlistRow` in `frontend/components/Watchlist.tsx`.
- Flash animations alternate `flash-a` / `flash-b` class names so consecutive ticks retrigger the CSS animation.
- Chart library is isolated: `lightweight-charts` is imported only in `frontend/components/TimeSeriesChart.tsx`. Other chart components wrap that file rather than importing the library.

## Git and Commits

- The repo uses short, imperative commit subjects with no enforced prefix (recent history: `Build out FinAlly platform: API, DB, LLM chat, frontend, Docker, E2E`, `Strip whitespace and quotes from ANTHROPIC_API_KEY`).
- Do not commit `.env`, `backend/.coverage`, `*.tsbuildinfo`, `frontend/.next`, `frontend/out`, `backend/static`, or `test-results` / `playwright-report`. These are covered by `.gitignore` files and must stay out of commits.

---

*Convention analysis: 2026-11-22*
