---
last_mapped_commit: 4de2c198274139dee77f7111f2325f0ff2e32095
last_mapped_at: 2026-11-22
---
# Codebase Concerns

**Analysis Date:** 2026-11-22

## Tech Debt

**[RESOLVED 2026-11-22, commit 4de2c19] Frontend `lib/` directory was git-ignored (build-breaking on a clean clone):**
- Issue: The root `.gitignore` contains a Python-template rule `lib/` (line 17) that also matches `frontend/lib/`. The frontend imports `@/lib/types`, `@/lib/api`, `@/lib/portfolio`, `@/lib/format`, `@/lib/treemap`, which exist on disk but are not tracked by git (`git ls-files frontend/lib` returns nothing; `git check-ignore` confirms the match).
- Files: `.gitignore` (line 17), `frontend/lib/api.ts`, `frontend/lib/types.ts`, `frontend/lib/portfolio.ts`, `frontend/lib/format.ts`, `frontend/lib/treemap.ts`, `Dockerfile` (frontend stage copies `frontend/`)
- Impact: A fresh clone or the Docker build from a git checkout will fail at `npm run build` because the modules are missing. Only working copies that still have the untracked files build successfully.
- Resolution: `.gitignore` now has `!frontend/lib/` and the five files are committed. Still worth adding a clean-clone build check to CI (see Test Coverage Gaps).

**Frontend and backend contracts are duplicated by hand:**
- Issue: The TypeScript types in `frontend/lib/types.ts` mirror Pydantic/dict shapes built in `backend/app/services/portfolio.py`, `backend/app/services/watchlist.py`, and `backend/app/llm/schema.py`. There is no generated schema or shared contract test.
- Files: `frontend/lib/types.ts`, `backend/app/services/portfolio.py`, `backend/app/services/watchlist.py`, `backend/app/api/chat.py`
- Impact: Field renames on either side silently break the UI (for example `day_change_percent` or `unrealized_pnl_percent` read as `undefined`).
- Fix approach: Add a response-shape test in `frontend/tests/api.test.ts` driven by recorded backend JSON, or generate types from the FastAPI OpenAPI document.

**Duplicated market-data semantics for "daily change":**
- Issue: The simulator sets `day_open` to the seed/start price (`backend/app/market/simulator.py`, `_add_ticker_internal`). The Massive client sets `day_open` from `prev_day.close` (`backend/app/market/massive_client.py`, `_prev_close`). The cache carries `day_open` forward when not supplied (`backend/app/market/cache.py`). The two sources therefore produce different "day change %" meanings.
- Files: `backend/app/market/simulator.py`, `backend/app/market/massive_client.py`, `backend/app/market/cache.py`, `backend/app/services/watchlist.py`
- Impact: The watchlist "change today" figure shown to the user and to the LLM context means different things depending on `MASSIVE_API_KEY`.
- Fix approach: Document one definition in `planning/MARKET_DATA_SUMMARY.md` and make both sources set `day_open` accordingly.

**Hand-rolled snapshot loop and sync DB calls inside async code:**
- Issue: `PortfolioService.record_snapshot` (`backend/app/services/portfolio.py`) and every DB helper in `backend/app/db/database.py` are synchronous `sqlite3` calls invoked directly from `async` handlers and from the `async` snapshot task in `backend/app/main.py`. Each call opens a new connection (`_connect`) and may wait on `busy_timeout=30000`.
- Files: `backend/app/main.py` (`_snapshot_loop`), `backend/app/services/portfolio.py`, `backend/app/api/portfolio.py`, `backend/app/db/database.py`
- Impact: Under lock contention, the event loop (which also drives the price simulator and the SSE stream) can stall for the duration of a SQLite wait. This is latent at the current single-user scale.
- Fix approach: Run DB calls with `asyncio.to_thread` or make the DB layer async (`aiosqlite`). At minimum, keep the write transactions short.

**Stray and empty files at the repository root:**
- Issue: A zero-byte file named `curl` sits at the repo root (tracked). Several planning copies coexist (`planning/PLAN copy.md`, `planning/REVIEW.md`, `planning/MARKET_DATA_REVIEW.md`).
- Files: `curl`, `planning/PLAN copy.md`
- Impact: Noise for agents and reviewers; the `curl` file looks like a mistaken shell redirect.
- Fix approach: Delete `curl` and `planning/PLAN copy.md` if they are not referenced.

**Dual "day change" and price-cache rounding:**
- Issue: `PriceCache.update` rounds stored prices to 2 decimals (`backend/app/market/cache.py`). Simulator state is kept unrounded internally, so tiny moves are visible only after rounding. Sub-cent-priced instruments from Massive would lose precision.
- Files: `backend/app/market/cache.py`, `backend/app/market/simulator.py`
- Impact: Low for the current ticker set; a latent precision loss for non-US or sub-dollar tickers.
- Fix approach: Keep full precision in the cache and round only at the presentation boundary, or make rounding per-source configurable.

## Known Bugs and Behavioral Defects

**Missing live price silently values positions at cost:**
- Symptoms: If the cache has no price for a held ticker, `get_portfolio` uses `avg_cost` as `current_price`, so unrealized P&L shows as zero and the position looks healthy.
- Files: `backend/app/services/portfolio.py` (`get_portfolio`, the `current_price = ... else avg_cost` line)
- Trigger: A held ticker whose source has no price (Massive returns no snapshot, or the ticker was removed from the cache).
- Workaround: None in code. The UI does not flag stale positions.
- Fix approach: Return `current_price: null` plus a `stale: true` flag, and exclude these rows from total value with an explicit warning, or keep the last known price and surface its age.

**Watchlist add is not atomic across DB and market source:**
- Symptoms: `WatchlistService.add` writes the ticker to SQLite first and then calls `source.add_ticker`. If the source call raises (for example a Massive poll error during the immediate fetch), the ticker is persisted with no price and the HTTP call fails, while the ticker remains in the watchlist.
- Files: `backend/app/services/watchlist.py` (`add`), `backend/app/market/massive_client.py` (`add_ticker` -> `_poll_once`)
- Trigger: Add a ticker while the Massive API is failing or rate limited.
- Workaround: Remove and re-add the ticker.
- Fix approach: Add to the market source first and roll back the DB row on failure, or have `add_ticker` swallow poll errors (the poll path already does) and let the next poll populate the price.

**Unvalidated tickers accepted in simulator mode:**
- Symptoms: In simulator mode any string matching `TICKER_RE` (`^[A-Z][A-Z0-9.\-]{0,9}$`, `backend/app/services/watchlist.py`) is accepted and given a deterministic pseudo-random price in [50, 300) (`default_price_for`, `backend/app/market/simulator.py`). The user can buy and sell a fictitious company with a fabricated price, and the LLM can do so too.
- Files: `backend/app/services/watchlist.py`, `backend/app/market/simulator.py`, `backend/app/services/portfolio.py`
- Trigger: `POST /api/watchlist` with `{"ticker":"XYZQ"}`, then a trade.
- Workaround: None. This matches the PLAN's open question on unknown tickers, which is not resolved in the code.
- Fix approach: Maintain an allow-list of known symbols for simulator mode, or label simulated-only tickers in the UI and API responses.

**Trade path does not validate ticker format:**
- Symptoms: `PortfolioService.execute_trade` normalizes the ticker but does not apply `TICKER_RE`. Only the existence of a cached price gates a trade. Watchlist validation and trade validation therefore disagree.
- Files: `backend/app/services/portfolio.py` (`execute_trade`), `backend/app/services/watchlist.py` (`TICKER_RE`)
- Trigger: `POST /api/portfolio/trade` with an unusual ticker that happens to be in the cache.
- Fix approach: Share a single `validate_ticker` helper between watchlist and trade paths.

**Chat user message persisted before the LLM call, and history is read before it:**
- Symptoms: `handle_chat` writes the user turn to `chat_messages` before the LLM call and before the trade execution. If the process crashes between the user-message write and the assistant-message write, the history contains an unanswered user turn, which the next prompt includes.
- Files: `backend/app/llm/chat.py` (`handle_chat`)
- Trigger: Process restart or a timeout during a chat request.
- Fix approach: Acceptable as-is for a demo, but document it, or persist both turns in one transaction after the LLM call.

**Auto-executed trades can be partial and are not idempotent:**
- Symptoms: `execute_actions` runs trades sequentially and continues past failures (by design). Each executed trade commits independently, so an LLM response containing several trades can leave the portfolio partly changed. There is no retry or idempotency key; a repeated user message re-runs the trades.
- Files: `backend/app/llm/chat.py` (`execute_actions`), `backend/app/services/portfolio.py` (`execute_trade`)
- Trigger: A multi-trade reply, or a user repeating a request after a timeout on the client.
- Workaround: Look at `actions.trades` in the response.
- Fix approach: Add a per-turn cap on the number of trades, and consider an idempotency key from the client.

**Snapshot history silently truncated while storage grows unbounded:**
- Symptoms: `GET /api/portfolio/history` returns only the newest 1000 snapshots (`HISTORY_LIMIT` in `backend/app/api/portfolio.py`). At the 30-second cadence plus one per trade, this is roughly 8 hours of chart. The `portfolio_snapshots` table has no retention, so older rows accumulate forever.
- Files: `backend/app/api/portfolio.py`, `backend/app/main.py` (`SNAPSHOT_INTERVAL_SECONDS`), `backend/app/db/schema.sql`
- Trigger: Running the container for more than a few hours.
- Fix approach: Downsample or prune (for example keep 1 row per minute beyond 24 hours), and expose the window in the response.

**Fallback when Massive returns no usable price:**
- Symptoms: `parse_snapshot` falls back through last trade, minute bar, day bar, and previous close (`backend/app/market/massive_client.py`). A previous-close price can be days old and is not flagged as stale, so trades execute against it.
- Files: `backend/app/market/massive_client.py` (`parse_snapshot`)
- Fix approach: Carry the source's timestamp into `PriceUpdate` and reject trades whose price is older than a threshold.

## Security Considerations

**No authentication or authorization on any API route:**
- Risk: All `/api/*` routes (trading, watchlist, chat which spends the Anthropic budget, and the SSE stream) are unauthenticated. The container binds `0.0.0.0:8000` (`Dockerfile` CMD) and the start script maps `-p ${PORT}:8000`. Anyone who can reach the port can trade, change the watchlist, or trigger LLM calls billed to `ANTHROPIC_API_KEY`.
- Files: `backend/app/main.py`, `backend/app/api/*.py`, `Dockerfile`, `scripts/start_mac.sh`
- Current mitigation: Single-user design per `planning/PLAN.md`; the port is published on the host, which on most machines is reachable only locally. Docker publishes to all interfaces by default, so it is not strictly local.
- Recommendations: Publish the port as `127.0.0.1:8000:8000` in the start scripts (`scripts/start_mac.sh`, `scripts/start_windows.ps1`). Add a rate limit on `POST /api/chat`. Consider a shared-secret header for non-local deployments.

**No rate limiting or spend cap on the chat endpoint:**
- Risk: `POST /api/chat` makes an Anthropic call per request (`MAX_TOKENS=4000`, `max_retries=2`, `REQUEST_TIMEOUT_S=60` in `backend/app/llm/chat.py`). A script or a runaway client can exhaust the key's quota.
- Files: `backend/app/api/chat.py`, `backend/app/llm/chat.py`
- Recommendations: A per-minute limiter on the route and a configurable daily cap.

**LLM auto-execution without confirmation:**
- Risk: The model's output directly causes trades and watchlist changes (`backend/app/llm/chat.py`, `execute_actions`). The user's chat text and the portfolio context (which includes ticker strings from the watchlist) are concatenated into the prompt without escaping (`backend/app/llm/prompts.py`, `build_messages`, `<portfolio_context>` wrapper). This is a deliberate product choice (PLAN section 9) and the money is simulated, but a crafted ticker or message can steer trades.
- Files: `backend/app/llm/prompts.py`, `backend/app/llm/chat.py`
- Recommendations: Cap trades per turn and total notional per turn in `execute_actions`; keep the cap visible to the user; strip angle brackets from ticker strings before they enter the prompt.

**Server-side secret handling is correct but relies on a single environment path:**
- Risk: `ANTHROPIC_API_KEY` and `MASSIVE_API_KEY` are read from the process environment, and `backend/app/main.py` loads `<repo>/.env` with `python-dotenv`. The `.env` file is ignored by `.gitignore` and `.dockerignore`, and is passed to the container with `--env-file`. The key is never sent to the browser. This review did not open `.env`.
- Files: `backend/app/main.py`, `backend/app/llm/chat.py` (`_clean_api_key`), `.gitignore`, `.dockerignore`, `scripts/start_mac.sh`
- Current mitigation: Ignore rules, key stripped of quotes and whitespace.
- Recommendations: Confirm the `.env` file has never been committed (`git log --all -- .env`). If it has, rotate the key. Note that `planning/PLAN.md` once contained a real key (see its review section 13 item 1); confirm that the key was rotated.

**Container runs as root with a writable app tree:**
- Risk: The runtime image has no `USER` directive (`Dockerfile`), so the uvicorn process runs as root. Source and the static bundle are owned by root, but a compromised process has root in the container.
- Files: `Dockerfile`
- Recommendations: Create an unprivileged user, `chown` `/app/db`, and add `USER app`.

**Unpinned and beta dependencies on the LLM path:**
- Risk: `backend/app/llm/chat.py` uses a beta header (`REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"`) and `fallbacks: "default"`, and calls `client.beta.messages.with_raw_response.parse`. The model ID `claude-opus-5-5` is a single constant. Beta features and model IDs change, and the code depends on `stop_reason` and `stop_details` shapes. None of this was verified against live API documentation in this review.
- Files: `backend/app/llm/chat.py`, `backend/pyproject.toml` (`anthropic>=2.8.0`, unpinned upper bound)
- Recommendations: Pin the `anthropic` version through `uv.lock` (already present) and verify the model ID and beta header against current Anthropic documentation before release. Add a test that exercises `_parse_request` with a recorded response.

**Unbounded user-supplied input:**
- Risk: `ChatRequest.message` (`backend/app/api/chat.py`) has no maximum length, and the whole message is stored and sent to the model. `TradeRequest.quantity` (`backend/app/api/portfolio.py`) accepts any finite float; there is no upper bound except available cash or shares.
- Recommendations: Add `max_length` to the Pydantic models (for example 2000 characters for chat).

**CORS not configured, by design:**
- Risk: None while the UI is same-origin. If the API were ever served from another origin, no CORS policy exists to restrict it.
- Files: `backend/app/main.py`

## Performance Bottlenecks

**Full-universe SSE fan-out every 500 ms per client:**
- Problem: `_generate_events` (`backend/app/market/stream.py`) serializes all cached tickers into one JSON payload each time the cache version changes. The simulator changes every ticker every 0.5 s, so every connected client receives a full payload twice per second, with no per-client throttling or delta encoding.
- Files: `backend/app/market/stream.py`, `backend/app/market/simulator.py`
- Cause: The version counter is global and every tick bumps it.
- Improvement path: Acceptable for the single-user target. For more clients or tickers, send deltas or throttle to 1 Hz.

**Snapshot computation on every trade and every 30 s:**
- Problem: `record_snapshot` calls `get_portfolio`, which reads all positions and the watchlist, and each DB helper opens a new SQLite connection and runs `PRAGMA` statements (`backend/app/db/database.py`, `_connect`).
- Files: `backend/app/services/portfolio.py`, `backend/app/db/database.py`
- Improvement path: Reuse a connection per thread or use WAL-mode pooled connections.

**Simulator cost grows quadratically with tickers:**
- Problem: `GBMSimulator._rebuild_cholesky` rebuilds the full n x n correlation matrix and runs a Cholesky decomposition on every add or remove (`backend/app/market/simulator.py`). Each `remove_ticker` in `SimulatorDataSource` also triggers a rebuild.
- Files: `backend/app/market/simulator.py`
- Improvement path: Fine below ~50 tickers (the code comment says n < 50). Add a guard or batch the rebuild if the watchlist grows.

**Massive free-tier rate budget is easy to exceed:**
- Problem: The poller runs every 15 s (about 4 requests per minute), and `MassiveDataSource.add_ticker` triggers an extra `_poll_once` on every add (`backend/app/market/massive_client.py`). Rapid add/remove activity can exceed the 5 requests per minute free tier. Backoff exists only after failures.
- Files: `backend/app/market/massive_client.py` (`add_ticker`, `_poll_loop`)
- Improvement path: Coalesce immediate polls (for example at most one extra poll per 15 s), or defer new tickers to the next scheduled poll.

**Full-history reads on every chat turn:**
- Problem: `handle_chat` reads `db.get_chat_messages(limit=20)` and the chat endpoint reads 50 messages for the history view. Both are indexed (`idx_chat_user_time`) and bounded, so this is low risk. Recorded here for completeness.
- Files: `backend/app/llm/chat.py`, `backend/app/api/chat.py`

## Fragile Areas

**Correctness depends on a single asyncio event loop with synchronous DB calls:**
- Files: `backend/app/services/portfolio.py`, `backend/app/services/watchlist.py`, `backend/app/llm/chat.py`
- Why fragile: The check-then-act sequences in `execute_trade` (position read, cash check, trade write, then "drop unwatched ticker") and in `WatchlistService.remove` rely on no `await` occurring between them. Adding an `await` (for example moving DB calls to threads, as recommended above) could introduce races between concurrent trades and watchlist edits.
- Safe modification: Keep DB writes and their decision logic in `db/database.py` transactions. If an `await` is added between checks, move the check into the transaction.
- Test coverage: `backend/tests/db/test_atomicity.py` covers DB atomicity; no test runs concurrent trades against the service layer.

**Module-level singletons and global state:**
- Files: `backend/app/db/database.py` (`_lock`, `_db_path`, `_initialized`), `backend/app/llm/chat.py` (`_client`, `set_client`), `backend/app/main.py` (`app = create_app()` at import time)
- Why fragile: `create_app()` runs at import, which constructs `PriceCache()` and reads environment variables on import. Tests must reset `_client` and `_db_path`. `_ensure_init` re-initializes whenever the DB file is missing, which can mask a misconfigured `FINALLY_DB_PATH`.
- Safe modification: Use the test fixtures in `backend/tests/conftest.py` and `backend/tests/db/conftest.py`; do not import `app.main` in new tests unless needed.

**Frontend polling and SSE both drive state:**
- Files: `frontend/components/Terminal.tsx` (`PORTFOLIO_POLL_MS = 5000`, `HISTORY_POLL_MS = 30000`, `WATCHLIST_POLL_MS = 15000`), `frontend/hooks/usePriceStream.ts`
- Why fragile: Portfolio, history and watchlist are polled while prices arrive over SSE, so the displayed total value can lag the live prices by up to 5 s. Chat actions trigger explicit refreshes (`Terminal.tsx` lines around 112-114), so a missed refresh leaves stale data until the next poll.
- Safe modification: Keep refresh calls after every mutation; when changing intervals, check the e2e expectations in `test/e2e/`.

**Price history held entirely in client memory:**
- Files: `frontend/hooks/usePriceStream.ts` (`MAX_POINTS = 1200`, `appendPoint`, `applyBatch`)
- Why fragile: `historyRef` keeps up to 1200 points per ticker and the history map is copied on every batch (`applyBatch` builds a new object every 500 ms). This is acceptable for ten tickers but scales linearly with watchlist size.
- Safe modification: Cap tickers rendered in sparklines or move to incremental updates.

**Massive client mixes sync SDK calls with async tasks:**
- Files: `backend/app/market/massive_client.py` (`_fetch_snapshots` run via `asyncio.to_thread`)
- Why fragile: The poller's `_tickers` list is copied on the event loop before dispatch, and results for removed tickers are dropped (`if ticker not in self._tickers`). Changes to this ordering risk writing prices for removed tickers into the cache.
- Safe modification: Keep the copy-then-dispatch pattern; cover it with `backend/tests/market/test_review_fixes.py`.

**Watchlist and portfolio share the same price-universe rule in two places:**
- Files: `backend/app/main.py` (`lifespan` universe build), `backend/app/services/portfolio.py` (`execute_trade` removal), `backend/app/services/watchlist.py` (`remove`)
- Why fragile: The rule "price universe = watchlist union held tickers" is implemented separately at startup, on sell, and on watchlist removal. A change to one site without the others leaves tickers unpriced or leaked.
- Safe modification: Extract a single `price_universe()` helper in `backend/app/services` and call it from all three sites.

**Docker and build pipeline depends on untracked files and a fixed uv version:**
- Files: `Dockerfile` (`ghcr.io/astral-sh/uv:0.8.4`, `npm ci` with optional lockfile), `.gitignore` (line 17)
- Why fragile: The (now resolved) `frontend/lib/` ignore problem showed how a stray ignore rule can break a clean build. The `package-lock.json` is copied with `frontend/package-lock.json*`, so a missing lockfile silently falls back to `npm install` and an unpinned tree.
- Safe modification: Verify every change with `git clone` into an empty directory followed by `docker build`.

## Scaling Limits

**Single user, single SQLite file, single process:**
- Current capacity: One user, ten default tickers, one uvicorn worker (`Dockerfile` CMD uses one worker), SQLite file in the `finally-data` volume.
- Limit: Multiple uvicorn workers would each run their own simulator and price cache, producing inconsistent prices and duplicate snapshot tasks. SQLite serializes writes.
- Scaling path: Keep one worker. For multi-user support, the schema already carries `user_id` (PLAN section 7), but the price cache and simulator are process-global.

**Snapshot and trade tables grow without bound:**
- Current capacity: About 2,900 snapshot rows per day from the 30 s task alone, plus one per trade (`backend/app/main.py`, `backend/app/services/portfolio.py`).
- Limit: Disk usage in the volume grows indefinitely. Queries are indexed, so latency stays acceptable for years at this rate.
- Scaling path: Retention or downsampling (see Known Bugs).

## Dependencies at Risk

**Anthropic SDK and beta features:**
- Risk: The code depends on `anthropic.AsyncAnthropic`, `beta.messages.with_raw_response.parse`, a beta header, and the `stop_reason`/`stop_details` response shape. The `anthropic>=2.8.0` constraint is open-ended.
- Impact: An SDK or API change can break chat for all users of the container; the failure is caught and reported as a generic error message (`MSG_GENERIC_ERROR`), which hides the root cause.
- Migration plan: Pin the minor version in `backend/pyproject.toml`, keep `backend/uv.lock` updated, and add a contract test with recorded responses.

**Massive SDK:**
- Risk: `massive>=1.0.0` (`backend/pyproject.toml`) is a third-party client for a REST API with tiered limits. `MassiveDataSource._fetch_snapshots` calls `get_snapshot_all` synchronously in a worker thread.
- Impact: SDK changes to models such as `TickerSnapshot` or `last_trade.sip_timestamp` break `parse_snapshot`. The poll error handler swallows such failures and only logs them.
- Migration plan: Keep `backend/tests/market/test_massive.py` in sync with SDK releases.

**Frontend framework versions:**
- Risk: `frontend/package.json` pins `next` 16.4.6, `react` 19.3.1 and `vitest` 5.1.0. These versions are recent and the lockfile is the only pin. `next.config.ts` relies on `output: "export"`, which constrains which Next.js features may be used.
- Impact: Upgrading Next.js can break the static export silently.
- Migration plan: Upgrade intentionally; run `npm run build` and the e2e suite in `test/`.

## Missing Critical Features

**No authentication (see Security):**
- Problem: Acceptable for the stated single-user design, but the container is published to the host network with no access control.
- Blocks: Safe deployment beyond a single trusted machine.

**No observability beyond logs:**
- Problem: Logging uses the standard `logging` module with no structured fields or request IDs (for example `backend/app/llm/chat.py`, `backend/app/market/massive_client.py`). No metrics for LLM latency, trade counts, or Massive poll health beyond `/api/health`.
- Blocks: Diagnosing intermittent LLM or market-data failures in a container.

**No retention policy for the database:**
- Problem: See Known Bugs and Scaling Limits.
- Blocks: Long-running deployments.

## Test Coverage Gaps

**Frontend static-build path is not exercised from a clean checkout:**
- What's not tested: A fresh clone building the frontend. The missing `frontend/lib/` tracking (above) would have been caught by this check.
- Files: `Dockerfile`, `frontend/package.json`, `frontend/lib/`
- Risk: Builds pass locally and fail in CI or on a colleague's machine.
- Priority: High

**Concurrency between trades and watchlist edits:**
- What's not tested: Simultaneous `POST /api/portfolio/trade` and `DELETE /api/watchlist/{ticker}` on the same ticker, and the "drop unwatched ticker on sell" branch in `execute_trade`.
- Files: `backend/app/services/portfolio.py`, `backend/app/services/watchlist.py`
- Risk: A ticker left unpriced while on the watchlist, or priced after removal.
- Priority: Medium

**Massive failure paths for watchlist add:**
- What's not tested: A watchlist add while the Massive poll fails (the non-atomic add described above).
- Files: `backend/app/services/watchlist.py`, `backend/app/market/massive_client.py`, `backend/tests/market/test_massive.py`
- Risk: Persisted ticker without a price.
- Priority: Medium

**Stale-price handling:**
- What's not tested: A held position with no live price (the `avg_cost` fallback in `get_portfolio`), and an old previous-close price used for trades.
- Files: `backend/app/services/portfolio.py`, `backend/app/market/massive_client.py`
- Risk: Silent zero P&L and trades executed at outdated prices.
- Priority: High

**Unbounded LLM-driven trade volume:**
- What's not tested: A reply with many trades, or a trade notional larger than the available cash, executed in sequence through `execute_actions`.
- Files: `backend/app/llm/chat.py`, `backend/tests/llm/test_chat.py`
- Risk: Unexpected portfolio churn.
- Priority: Low

**Snapshot cadence and retention:**
- What's not tested: More than 1000 snapshots, and the history window returned to the client.
- Files: `backend/app/api/portfolio.py`, `backend/tests/db/test_snapshots_chat.py`
- Risk: The P&L chart silently shows a partial window.
- Priority: Low

---

*Concerns audit: 2026-11-22*
