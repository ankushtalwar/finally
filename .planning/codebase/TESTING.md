---
last_mapped_commit: 4de2c198274139dee77f7111f2325f0ff2e32095
last_mapped_at: 2026-11-22
---
# Testing Patterns

**Analysis Date:** 2026-11-22

## Test Framework

**Backend runner:**
- pytest 8.3+ with `pytest-asyncio` (`asyncio_mode = "auto"`) and `pytest-cov`
- Config: `backend/pyproject.toml` under `[tool.pytest.ini_options]` (`testpaths = ["tests"]`, `python_files = ["test_*.py"]`, `python_classes = ["Test*"]`, `python_functions = ["test_*"]`, `asyncio_default_fixture_loop_scope = "function"`)
- Coverage config: `[tool.coverage.run]` (`source = ["app"]`, `omit = ["tests/*"]`) and `[tool.coverage.report]` in the same file
- HTTP client for API tests: `fastapi.testclient.TestClient` (httpx is a dev dependency)
- Lint runs in the same toolchain: ruff 0.7+ (`backend/pyproject.toml` `[tool.ruff]`)

**Frontend runner:**
- Vitest 5.1 with jsdom and globals enabled
- Config: `frontend/vitest.config.mts` (`environment: "jsdom"`, `globals: true`, `setupFiles: ["./tests/setup.ts"]`, `include: ["tests/**/*.test.{ts,tsx}"]`, `css: false`)
- Testing Library: `@testing-library/react` 16, `@testing-library/user-event` 14, `@testing-library/jest-dom` 7 (matchers loaded from `frontend/tests/setup.ts`)
- Type checking: `frontend/tsconfig.json` includes test files, so `npm run typecheck` covers them

**E2E runner:**
- Playwright Test 1.64 in a separate package at `test/` (`test/package.json`, `test/playwright.config.ts`)
- Projects run serially (`workers: 1`) because all tests share one app container and one SQLite database. Order: `fresh-start` first, then `api`, then `chromium`.

**Assertion libraries:**
- Python: pytest `assert` plus `pytest.raises(..., match=...)`
- TypeScript unit tests: Vitest `expect` plus jest-dom matchers (`toBeInTheDocument`, `toHaveTextContent`, `toHaveAttribute`)
- E2E: Playwright `expect`, including `expect.poll` for values that update over time

**Run commands:**
```bash
# Backend (run from backend/)
uv sync --extra dev                         # Install dev tools
uv run --extra dev pytest -v                # All tests
uv run --extra dev pytest tests/market -v   # One area
uv run --extra dev pytest --cov=app         # Coverage
uv run --extra dev ruff check app/ tests/   # Lint

# Frontend (run from frontend/)
npm test                 # vitest run (single pass)
npm run test:watch       # vitest (watch)
npm run typecheck        # tsc --noEmit
npm run lint             # eslint .

# E2E (run from test/, against a running app on BASE_URL, default http://localhost:8000)
npm test                 # playwright test
npm run test:list        # list discovered tests
npm run report           # open the HTML report
```

Root-level `planning/CONTRACT.md` and `backend/CLAUDE.md` are the source for the backend commands above.

## Test File Organization

**Backend location:** Separate `backend/tests/` tree that mirrors `backend/app/` by area. Each area folder has `__init__.py`.
```
backend/tests/
├── conftest.py              # Top-level pytest config (docstring only)
├── api/                     # REST routes against the full app
│   ├── conftest.py          # make_client fixture, full lifespan with fake market source
│   └── test_api.py
├── db/                      # SQLite layer (app/db/database.py)
│   ├── conftest.py          # db_path, db fixtures with isolated temp file
│   ├── test_init.py
│   ├── test_trades.py
│   ├── test_watchlist.py
│   ├── test_snapshots_chat.py
│   ├── test_atomicity.py
│   └── test_edge_cases.py
├── llm/                     # Chat orchestration, schema, mock, wire format
│   ├── conftest.py
│   ├── test_api_key.py
│   ├── test_chat.py
│   ├── test_mock.py
│   ├── test_schema.py
│   └── test_wire.py
├── market/                  # Price cache, simulator, Massive client, SSE stream
│   ├── test_cache.py
│   ├── test_factory.py
│   ├── test_massive.py
│   ├── test_models.py
│   ├── test_review_fixes.py
│   ├── test_simulator.py
│   ├── test_simulator_source.py
│   └── test_stream.py
└── services/                # Portfolio and watchlist services
    ├── conftest.py          # temp_db, cache, source fixtures
    ├── fakes.py             # FakeSource test double
    ├── test_portfolio_service.py
    └── test_watchlist_service.py
```

**Backend naming:** `test_<subject>.py`. Test classes are optional. `backend/tests/market/test_cache.py` groups tests in `class TestPriceCache:`. Newer modules use bare functions (`backend/tests/api/test_api.py`). Both are accepted. Prefer bare functions for new tests.

**Frontend location:** Flat `frontend/tests/` folder, not co-located with source. The vitest include glob is `tests/**/*.test.{ts,tsx}`, so a test placed beside a component will not run.
```
frontend/tests/
├── setup.ts                 # jest-dom import, Lightweight Charts module mock, cleanup
├── mocks.ts                 # portfolioFixture, FakeEventSource, jsonResponse helper
├── api.test.ts              # lib/api.ts
├── format.test.ts           # lib/format.ts
├── portfolio.test.ts        # lib/portfolio.ts
├── treemap.test.ts          # lib/treemap.ts
├── priceStream.test.tsx     # hooks/usePriceStream.ts
├── Watchlist.test.tsx       # components/Watchlist.tsx
├── Chat.test.tsx            # components/ChatPanel.tsx
├── Terminal.test.tsx        # components/Terminal.tsx
├── components.test.tsx      # Header, PositionsTable, Heatmap, TradeBar, Sparkline rendering
└── behaviour.test.tsx       # Price flash lifecycle, portfolio calculations, heatmap sizing
```

**E2E location:** `test/e2e/` with one spec per feature area plus a shared `helpers.ts`:
- `fresh-start.spec.ts` (strict default-state check; runs first)
- `api.spec.ts`, `sse.spec.ts`, `chat.spec.ts`, `portfolio.spec.ts`, `trading.spec.ts`, `watchlist.spec.ts`
- `test/docker-compose.test.yml` brings up the app container plus a Playwright container. See `test/README.md`.

**Naming:**
- Python: `test_<behavior>` with a readable phrase, e.g. `test_health_reports_massive`, `test_health_real_simulator`.
- TypeScript: `describe("<Unit>")` with `it("<behavior sentence>")`. Example: `it("flashes green on uptick and red on downtick")`.
- E2E: `test("buy shares: cash decreases, position row and heatmap tile appear")`. The describe block names the feature.

## Test Structure

**Python suite organization:**
```python
# backend/tests/api/test_api.py (abridged)
from .conftest import SEED_PRICE

WATCH_ITEM_KEYS = {"ticker", "price", "previous_price", ...}

# --- health / lifespan ------------------------------------------------------ #

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "market_source": "simulator", ...}
```
- Section dividers use `# --- name ---` comments to group related tests.
- Module-level key sets (`PORTFOLIO_KEYS`, `POSITION_KEYS`) assert response shape with `set(body) == PORTFOLIO_KEYS` or `PORTFOLIO_KEYS <= set(body)`.

**Python class-based pattern:**
```python
# backend/tests/market/test_cache.py
class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
```

**Frontend suite organization:**
```tsx
// frontend/tests/Watchlist.test.tsx (abridged)
describe("Watchlist", () => {
  it("renders a row per ticker with formatted price and day change", () => {
    setup();
    expect(screen.getByTestId("watchlist")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-price-AAPL")).toHaveTextContent("190.12");
  });
});
```
- A `setup(overrides)` helper returns `render(...)` output plus the `props` object, with `vi.fn()` defaults that the test can override (`frontend/tests/Watchlist.test.tsx`).
- Query by `data-testid` for elements and by role for interactive controls. Use `userEvent` for interaction, not `fireEvent`.
- Use `rerender(...)` to test changes over time (the flash-class tests).

**Patterns:**
- Setup: `beforeEach` / fixtures for DB and cache. Frontend `setup()` helper per file. `afterEach(cleanup)` is global in `frontend/tests/setup.ts`.
- Teardown: `afterEach(() => vi.unstubAllGlobals())` in `frontend/tests/api.test.ts`. Pytest `tmp_path` and `monkeypatch` clean up automatically.
- Assertions: check shape, then values. Use `pytest.approx` for float money math when relevant.

## Mocking

**Backend, Python:**
- Framework: `unittest.mock` is not used. Mocking is done with pytest `monkeypatch` plus hand-written fakes.
- Test double for the market source: `FakeSource(MarketDataSource)` in `backend/tests/services/fakes.py`. It records tickers and removals, sets seed prices into the cache on add, and never moves prices on its own.
- Environment variables are set with `monkeypatch.setenv` / `delenv` (e.g. `FINALLY_DB_PATH`, `LLM_MOCK`, `MASSIVE_API_KEY`) in `backend/tests/api/conftest.py`.
- The app factory is patched to inject the fake: `monkeypatch.setattr(main, "create_market_data_source", lambda cache: FakeSource(cache, SEED_PRICE))`.

```python
# backend/tests/api/conftest.py (abridged)
def _make_client(monkeypatch, tmp_path, *, fake_source: bool = True, static_dir=None):
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "finally.db"))
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    if fake_source:
        monkeypatch.setattr(
            main, "create_market_data_source", lambda cache: FakeSource(cache, SEED_PRICE)
        )
    return TestClient(main.create_app())
```

- DB state reset: `backend/tests/db/conftest.py` monkeypatches `database._db_path` and `database._initialized` to `None` / `False` so module globals do not leak between tests.

**Backend, LLM:** Never call the real Anthropic API in tests. Use `LLM_MOCK=true` (deterministic `app/llm/mock.py`) for API tests. Tests for the real client path patch the SDK client (see `backend/tests/llm/test_api_key.py`, `test_wire.py`).

**Frontend, TypeScript:**
- Framework: `vi` from Vitest (`vi.fn`, `vi.mock`, `vi.stubGlobal`, `vi.unstubAllGlobals`).
- Global chart mock lives in `frontend/tests/setup.ts`. `lightweight-charts` is replaced with a recording fake whose `createChart`, `addSeries`, and series methods are `vi.fn()`:
```ts
// frontend/tests/setup.ts (abridged)
vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), update: vi.fn() };
  const chart = { addSeries: vi.fn(() => series), remove: vi.fn(), timeScale: () => ({ fitContent: vi.fn() }) };
  return { createChart: vi.fn(() => chart), AreaSeries: "Area", ... , __chart: chart, __series: series };
});
```
- `fetch` is stubbed per test: `vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res))` (see `mockFetch` in `frontend/tests/api.test.ts`).
- `EventSource` is replaced with `FakeEventSource` from `frontend/tests/mocks.ts`. Drive it with `.open()`, `.emit(data)`, and `.fail()` to test SSE behavior without a server.
- Props callbacks are `vi.fn().mockResolvedValue(undefined)` when the component awaits them.

**E2E, Playwright:**
- No mocking in the browser. E2E tests run against the real app container with `LLM_MOCK=true`.
- Direct API calls go through Playwright's `request` fixture to set up or reset state. Helpers live in `test/e2e/helpers.ts`:
  - `ensureWatched(request, ticker)`, `flattenPosition(request, ticker)` restore known state
  - `openApp(page, ticker)`, `tradeViaUi(page, side, ticker, qty)` drive the UI
  - `readNumber(locator)` and `parseMoney(text)` read formatted values, using `expect.poll` to wait for updates

**What to mock:**
- The market data source in backend API and service tests (`FakeSource`).
- The LLM. Use `LLM_MOCK=true` or patch the SDK client. Never make network calls.
- `fetch`, `EventSource`, and `lightweight-charts` in frontend tests.
- Time-dependent or random data: the simulator takes injectable RNGs (`GBMSimulator` in `backend/app/market/simulator.py`). Pass a seeded RNG in tests.

**What NOT to mock:**
- The SQLite layer. DB tests and service tests use a real temp database file through `db_path` / `temp_db`. Do not replace `app.db` functions with fakes.
- `PriceCache`. Tests use the real cache (`cache` fixture in `backend/tests/services/conftest.py`).
- Pydantic models and schema validation. Test with real models so validation rules are exercised.
- Pure formatting and calculation helpers in `frontend/lib/`. Test them directly.

## Fixtures and Factories

**Python fixtures:**
- `temp_db` (services): creates and initializes a temp SQLite file and sets `FINALLY_DB_PATH`.
- `db_path` / `db` (db tests): isolated file under `tmp_path / "nested" / "finally.db"`, module state reset.
- `cache` and `source` (services): `PriceCache()` and `FakeSource(cache)`.
- `client` and `make_client` (API tests): full app with lifespan. `make_client(fake_source=False)` runs the real simulator.
- `SEED_PRICE = 100.0` is exported from `backend/tests/api/conftest.py` for deterministic expected values.

```python
# backend/tests/services/conftest.py
@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    db.init_db(path)
    return path
```

**Frontend fixtures:**
- `portfolioFixture` in `frontend/tests/mocks.ts`: a typed `Portfolio` object with one AAPL position. Reuse it for any test that needs a portfolio payload.
- `jsonResponse(body, status)` builds a `Response` with the JSON content type. Use it with `fetch` stubs.
- Inline arrays for watchlist rows (see `rows` in `frontend/tests/Watchlist.test.tsx`). Keep test data inline unless shared by 3+ files.

**E2E fixtures:**
- `DEFAULT_TICKERS` (the 10 seeded tickers) and the `Portfolio` / `Position` types are defined in `test/e2e/helpers.ts`. Keep them in sync with `backend/app/db/database.py` `DEFAULT_TICKERS`.
- Use distinct tickers per spec to avoid cross-test interference. Comment at the top of `test/e2e/trading.spec.ts` states this: JPM for buy, V for sell, AMZN for errors.

**Location:**
- Python shared fixtures: `backend/tests/conftest.py` (top-level, currently just a docstring), `backend/tests/<area>/conftest.py`, and shared fakes in `backend/tests/services/fakes.py`.
- TypeScript shared fixtures and helpers: `frontend/tests/mocks.ts`. Global setup: `frontend/tests/setup.ts`.
- E2E: `test/e2e/helpers.ts`.

## Coverage

**Requirements:** No threshold is enforced in config. The `backend/pyproject.toml` coverage config omits tests and reports `app/` only. Market subsystem coverage was recorded at about 99% in `planning/MARKET_DATA_SUMMARY.md`. Treat that as a reference for the market package, not a repo-wide gate.

**View coverage:**
```bash
cd backend && uv run --extra dev pytest --cov=app --cov-report=term-missing
cd frontend && npx vitest run --coverage   # requires @vitest/coverage-v8 if enabled; not in devDependencies
```

- `backend/.coverage` exists as a local artifact and is gitignored. Do not commit it.
- The frontend does not have a coverage provider in `frontend/package.json`. Add one before relying on frontend coverage numbers.

## Test Types

**Unit tests (backend):**
- Scope: single module with real collaborators where cheap. Examples: `backend/tests/market/test_cache.py`, `backend/tests/market/test_models.py`, `backend/tests/llm/test_schema.py`, `backend/tests/llm/test_mock.py`.
- Services are tested with a temp DB and `FakeSource`: `backend/tests/services/test_portfolio_service.py` covers trade validation messages (`"Quantity must be greater than 0"`, `"Insufficient cash"`, `"Insufficient shares"`, `"No price available for ZZZZ yet"`) with `pytest.raises(TradeError, match=...)`.

**Integration tests (backend):**
- `backend/tests/api/test_api.py` runs the full FastAPI app with the real lifespan, real SQLite file, and `FakeSource`. Use `client` / `make_client` fixtures.
- `backend/tests/db/test_atomicity.py` checks multi-statement transactions.
- `backend/tests/market/test_simulator_source.py` and `test_stream.py` run the real simulator and SSE generator (with `asyncio`).

**Component and behavior tests (frontend):**
- `frontend/tests/*.test.tsx` render components with mock props and assert DOM output and user interaction.
- `frontend/tests/priceStream.test.tsx` covers `usePriceStream` with `FakeEventSource`: reconnect backoff (`RECONNECT_BASE_MS`, `RECONNECT_MAX_MS`), batch merging (`applyBatch`), and history capping (`MAX_POINTS`).

**E2E tests:**
- Framework: Playwright in `test/`. Browser: Desktop Chrome, viewport 1600x1000.
- Scenarios (from `planning/PLAN.md` section 12): fresh start (default watchlist, $10k cash, prices streaming), watchlist add/remove, buy/sell with cash and position updates, portfolio visualization (tiles exist per position), mocked AI chat with inline trade confirmation, and SSE reconnect.
- Config: `test/playwright.config.ts`. `retries: 1`, `timeout: 60_000`, `expect.timeout: 10_000`, traces on first retry, screenshots on failure. `forbidOnly` when `CI` is set.
- Output: `test-results/` and `playwright-report/` (both gitignored).

## Common Patterns

**Async testing (Python):**
```python
# asyncio_mode = "auto" in pyproject, so async tests need no marker.
async def test_add_ticker_polls_immediately(...):
    source = MassiveDataSource(api_key="test", price_cache=cache)
    await source.add_ticker("AAPL")
    assert cache.get_price("AAPL") is not None
```
- Background loops are started and stopped inside the test. Always call `await source.stop()` in a `finally` or fixture teardown. Example: `FakeSource.stop()` only records state; the real `SimulatorDataSource` needs explicit stop.
- For time-based loops, prefer patching the interval to a small value over `asyncio.sleep` in tests.

**Async testing (frontend):**
```ts
// Wait for a DOM change driven by an async handler
await userEvent.type(input, "pypl");
await userEvent.click(screen.getByTestId("watchlist-add-button"));
await waitFor(() => expect(props.onAdd).toHaveBeenCalledWith("PYPL"));
```
- Use `waitFor` from `@testing-library/react` for async DOM updates. Use `findBy*` for elements that appear later.
- Drive SSE with `FakeEventSource.emit(...)` and then assert, instead of waiting for timers.

**Async testing (E2E):**
```ts
// test/e2e/helpers.ts
await expect.poll(async () => { /* read text, parse */ }).not.toBeNaN();
```
- Use `expect.poll` for values that change over time (prices, cash after a trade). Use `toBeVisible` and `toHaveCount` for DOM state.

**Error testing (Python):**
```python
# backend/tests/services/test_portfolio_service.py
with pytest.raises(TradeError, match="Quantity must be greater than 0"):
    await svc.execute_trade("AAPL", "buy", 0)
```
- Assert on `exc.message` through `match=` for user-facing text. Assert HTTP status for route tests: `assert r.status_code == 400` with `r.json()["detail"]`.
- LLM failures: `test_sdk_exceptions` in `backend/tests/llm/test_chat.py` maps SDK errors to friendly messages with no actions executed. `test_unexpected_service_exception_is_contained` checks that a service failure does not escape the chat route. Schema validation is in `backend/tests/llm/test_schema.py`.

**Error testing (frontend):**
```ts
// frontend/tests/api.test.ts
const err = await api.trade("AAPL", 1, "buy").catch((e) => e);
expect(err).toBeInstanceOf(ApiError);
expect(err.status).toBe(400);
expect(err.message).toMatch(/Insufficient cash/);
```
- Test both FastAPI error shapes: string `detail` and 422 array `detail`. Test non-JSON error bodies fall back to `Request failed (<status>)`.

**Snapshot tests:** Not used. Assert explicit values.

## Writing New Tests

- Backend: add to the matching folder under `backend/tests/`. Reuse `temp_db`, `cache`, `source`, or `client` fixtures. Do not introduce `unittest.mock` if a fake or `monkeypatch` does the job.
- Frontend: add to `frontend/tests/` with `.test.ts` or `.test.tsx`. Import `portfolioFixture`, `jsonResponse`, and `FakeEventSource` from `@/tests/mocks` or `./mocks`.
- E2E: add to `test/e2e/` and reuse `helpers.ts`. Pick a ticker that no other spec uses, or add a cleanup step with `flattenPosition` / `ensureWatched`.
- Before committing, run `ruff check`, `npm run typecheck`, `npm run lint`, and the matching test command. Keep new tests deterministic: no real network calls, no real clock dependence without patching, and `LLM_MOCK=true` for anything touching chat.

---

*Testing analysis: 2026-11-22*
