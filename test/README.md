# FinAlly E2E tests

Playwright (TypeScript) end-to-end tests. This is a standalone npm project, separate from `frontend/` and `backend/`.

## Run with Docker Compose (recommended)

This builds the app from the repo-root `Dockerfile` with `LLM_MOCK=true` and a fresh database (no volume), waits for `/api/health`, then runs the suite in the official Playwright image.

```bash
# from the repo root
docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
docker compose -f test/docker-compose.test.yml down -v
```

The HTML report is written to `test/playwright-report/` (open with `cd test && npx playwright show-report`). Traces for retried failures are in `test/test-results/`.

## Run locally against a running app

Start the app with mock LLM mode on, for example from `backend/`:

```bash
LLM_MOCK=true uv run uvicorn app.main:app --port 8000
```

Then:

```bash
cd test
npm ci
npx playwright install chromium
npx playwright test                       # BASE_URL defaults to http://localhost:8000
BASE_URL=http://localhost:8001 npx playwright test
FRESH_DB=false npx playwright test        # skip exact $10,000 checks on a used database
npx playwright test e2e/chat.spec.ts      # one file
```

## Layout

| File | Covers |
|---|---|
| `e2e/fresh-start.spec.ts` | 10 default tickers, $10,000.00 cash, prices streaming, connected status, chart selection. Runs first (own project). |
| `e2e/watchlist.spec.ts` | Add and remove a ticker via the UI, duplicate error |
| `e2e/trading.spec.ts` | Buy, sell (partial and full), insufficient cash and shares errors |
| `e2e/portfolio.spec.ts` | P&L chart and snapshot history, heatmap tile after a trade |
| `e2e/chat.spec.ts` | Mock LLM: trade via chat, fallback reply, watchlist add via chat, Enter to send |
| `e2e/sse.spec.ts` | Stream blocked at load then restored, stream dropped mid-session then reconnected, offline then online |
| `e2e/api.spec.ts` | REST contract: health, shapes, 400/404/409/422 errors, chat history, SSE format, SPA fallback |

## Conventions

- Selectors use only the `data-testid`s listed in `planning/CONTRACT.md` §6.
- All specs share one app and one database, so they run serially (`workers: 1`). Each spec uses its own tickers, and specs that move cash compare against a value read before the action, never an absolute amount.
- Keep the `mcr.microsoft.com/playwright` image tag in `docker-compose.test.yml` equal to the `@playwright/test` version in `package.json`.
