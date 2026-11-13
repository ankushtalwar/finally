# Market Data Backend — Code Review

**Scope:** `backend/app/market/` (9 modules, ~770 lines) and `backend/tests/market/` (6 test files), reviewed against `planning/PLAN.md` and `planning/MARKET_DATA_SUMMARY.md`.
**Note:** This supersedes `planning/archive/MARKET_DATA_REVIEW.md`, which is stale (it reported 5 failing tests caused by a missing `massive` package; that no longer applies).

---

## 1. Test Results

Command: `cd backend && uv sync --extra dev && uv run --extra dev pytest -q --cov=app`

- **73 passed, 0 failed** (Python 3.14.4 locally; project targets >=3.12 / Docker 3.12).
- **ruff check:** clean.
- **Warnings:** 73 `DeprecationWarning`s from `tests/conftest.py:11` (`asyncio.DefaultEventLoopPolicy` is slated for removal in Python 3.16). The custom `event_loop_policy` fixture should be deleted; `asyncio_mode = "auto"` already handles it.

| Module | Coverage |
|---|---|
| cache, factory, interface, models, seed_prices, `__init__` | 100% |
| simulator.py | 98% |
| massive_client.py | 94% |
| **stream.py** | **33%** |
| Total | 91% |

The SSE generator and route are essentially untested (see M3).

---

## 2. Overall Assessment

The design is sound and matches PLAN.md section 6: one abstract interface, two implementations, a shared cache, and an environment-driven factory. The code is small, readable and well documented. GBM math is correct, correlation via Cholesky is a good touch, and Massive blocking I/O is correctly pushed to a thread. I found no crash-level bugs on the happy path. The issues below are mostly edge cases and gaps that will matter when the rest of the platform (trades, watchlist API, frontend) is built on top.

---

## 3. Findings

### Medium

**M1. No "daily change %" data (spec gap).** PLAN.md section 10 requires the watchlist to show *daily change %*. `PriceUpdate.change`/`change_percent` (`models.py:33-42`) are tick-to-tick deltas against the previous cache value, and on the first update `previous_price == price`. The Massive snapshot response contains the previous day's close and today's change, but it is discarded (`massive_client.py:287-294`). The simulator has no session-open reference price either. The frontend cannot show a meaningful daily change today. Suggest adding an optional `day_open`/`prev_close` field to `PriceUpdate` (seeded from `SEED_PRICES` in the simulator, from the snapshot in Massive) and a `day_change_percent` property.

**M2. Ticker symbols are not normalized or validated consistently.** `MassiveDataSource.add_ticker/remove_ticker` upper-case and strip; `SimulatorDataSource`, `GBMSimulator`, `PriceCache` and `start()` do not. I verified `GBMSimulator(['aapl','AAPL'])` creates two independent tickers. Callers must normalize, or the layer should. Also:
- The simulator accepts *any* string and invents a random $50–$300 price (`simulator.py:561`), so `add_ticker("ZZZZ")` "works". The Massive source silently never produces a price for an unknown ticker. There is no way for the watchlist/trade code to ask "is this ticker valid / does it have a price yet?" other than checking the cache. The trade and watchlist endpoints must reject tickers with no cached price.
- Random seed price for unknown tickers makes behaviour non-deterministic across restarts (e.g. a position bought at a random price). Consider a deterministic hash-based price.

**M3. SSE endpoint is untested and has structural problems (`stream.py`).**
- `router` is a **module-level singleton** (`stream.py:17`) and `create_stream_router` adds a route to it each call. I verified calling it twice returns the same object with 2 routes; the first call's cache wins and later caches are silently ignored (this will bite test suites that build multiple apps). Create the `APIRouter` inside the factory.
- `except asyncio.CancelledError` (end of `_generate_events`) swallows cancellation; it should log and re-raise.
- Change detection uses `price_cache.version`, but `PriceCache.remove()` does **not** bump `_version` (`cache.py:82-85`). With the simulator this is masked by constant updates, but with Massive (15 s poll) a removed ticker stays on the client until the next poll, and removing the last ticker never emits anything. Bump the version in `remove()`.
- `version` is read without the lock (`cache.py:90`); fine under CPython's GIL but inconsistent with the rest of the class.
- No heartbeat/comment line when nothing changes. With Massive at 15 s intervals, proxies may time out idle connections. Send `: keepalive` every ~15 s.
- `Connection: keep-alive` is a hop-by-hop header and is unnecessary/ignored under HTTP/2; harmless but can be dropped.
- The PLAN says "each SSE event contains ticker, price, previous price, timestamp, direction". The implementation sends one event containing a dict of *all* tickers. That is reasonable but should be documented as the contract for the frontend (it is already in the docstring).

**M4. Massive source: a newly added ticker has no price for up to one poll interval (15 s on free tier).** `add_ticker` only appends to the list (`massive_client.py:252-256`). The simulator seeds the cache immediately, so behaviour differs between the two implementations. A user who adds a ticker and immediately trades will hit "no price". Trigger an immediate out-of-cycle poll (or a single-ticker fetch) on add.

**M5. Massive source concurrency races.**
- `_fetch_snapshots` runs in a worker thread and reads `self._tickers` (`massive_client.py:313`) while `add_ticker` mutates the same list with `append` on the event loop. `remove_ticker` rebinds the list so is safe, but `add_ticker` is not. Pass a copy: `tickers=list(self._tickers)` captured on the loop thread before `to_thread`.
- A poll in flight when `remove_ticker` runs can write the removed ticker back into the cache after it was removed (the snapshot was already fetched). Filter results by `snap.ticker in self._tickers` before updating.
- Because the whole batch is one call, one invalid ticker in the list can cause the whole request to fail or return partial data depending on API behaviour, starving valid tickers. Worth testing against the real API.

### Low

**L1. `PriceCache.update` uses `timestamp or time.time()`** (`cache.py:53`): a timestamp of `0.0` is treated as missing (I verified). Use `if timestamp is None`.

**L2. Prices are rounded to 2 dp inside the cache, and `previous_price` comes from the already-rounded value.** For low-volatility tickers the per-tick std is ~$0.01, so many ticks show `flat`, and the flash animation will be sparse. Not a bug, but you may want a slightly higher tick volatility for demo purposes, or keep more precision internally.

**L3. Simulator uses the global `np.random` and `random` state** (`simulator.py:494, 515, 561`). Tests/demos cannot be made reproducible without seeding globals; accept an optional `numpy.random.Generator`/`random.Random` in the constructor.

**L4. `SimulatorDataSource.add_ticker/remove_ticker` silently do nothing if `start()` has not been called** (`if self._sim:`), and `add_ticker` before start loses the ticker. Either raise or buffer. Likewise `start()` called twice leaks the first task (the interface says "undefined", so a guard would be cheap).

**L5. Loop timing drift.** `_run_loop` sleeps `interval` *after* the step, so the cadence is interval + step time. Negligible here.

**L6. Cholesky robustness.** The correlation matrix is built from constants that are positive-definite today (I checked a 46-ticker mix). If someone tunes the constants, `np.linalg.cholesky` will raise `LinAlgError` from `add_ticker`, which is not caught and would propagate to the API caller. Wrap with a fallback (e.g. identity/shrinkage).

**L7. Massive error handling is log-only.** 401/429 are logged at error level every poll with no backoff or status exposure. On a bad key the app will log forever with an empty cache and the UI shows the connection dot green. Consider surfacing a health flag via `/api/health` and backing off on 429.

**L8. `MassiveDataSource.start()` creates the `RESTClient` and does the first poll before creating the task; if the first poll fails, it is swallowed**, which is fine, but the log message "poller started" then misleads. Minor.

**L9. Packaging/housekeeping.**
- `rich` is a runtime dependency used only by `market_data_demo.py`; move it to the dev extra.
- `conftest.py` deprecated fixture (see section 1).
- Docstring in `cache.py:12-13` says "SimulatorDataSource or MassiveDataSource" are writers; fine, but `PriceCache` also exposes `__len__`/`__contains__`, which have no tests of lock behaviour — trivial.
- `planning/MARKET_DATA_SUMMARY.md` claims "reviewed, all issues resolved"; the items above show that is optimistic, and the archived review is out of date. Update both.

---

## 4. Test Suite Assessment

Good: tests cover GBM positivity, correlation structure, add/remove behaviour, cache semantics, factory selection, and Massive parsing with mocks (including malformed snapshots and ms→s timestamp conversion).

Gaps to close:
1. **SSE** (`stream.py`, 33%): no test for the event format, the `retry:` preamble, version-based change suppression, or disconnect handling. Use `httpx.AsyncClient` with `ASGITransport` and a bounded read, or test `_generate_events` directly with a fake `Request`.
2. Cache `remove()` not bumping version (M3) — add a test once fixed.
3. Case-handling/normalization tests across sources (M2).
4. Massive: in-flight poll + removal race (M5), list-mutation during fetch.
5. Statistical tests of the simulator (e.g. drift/vol over many steps) would catch scaling errors in `dt`; current tests check validity but not magnitude.
6. No test covers `start()`/`add_ticker()` ordering errors (L4).

---

## 5. Recommended Priorities

1. Fix `stream.py` router singleton, `CancelledError`, and `remove()` version bump (small, high value before the API layer is built on top).
2. Decide how to supply daily change % (M1) — it affects the `PriceUpdate` contract the frontend will depend on, so settle it before frontend work starts.
3. Normalize/validate tickers at one choke point and make "no price yet" an explicit state for the trade/watchlist endpoints (M2, M4).
4. Harden Massive concurrency (M5) and add backoff/health surfacing (L7).
5. Add SSE tests and remove the deprecated conftest fixture.
6. Housekeeping items (L1–L9).

No source code was changed as part of this review.
