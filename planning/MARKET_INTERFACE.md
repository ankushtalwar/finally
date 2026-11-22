# Unified Market Data Interface

_Updated 2026-11-10. Builds on [`MASSIVE_API.md`](MASSIVE_API.md) (API research) and [`MARKET_SIMULATOR.md`](MARKET_SIMULATOR.md) (simulator design). Supersedes `planning/archive/MARKET_INTERFACE.md`._

## 1. Goal

FinAlly has exactly one way to get stock prices:

- `MASSIVE_API_KEY` set and non-empty → **real prices from Massive** (formerly Polygon.io).
- Otherwise → the **built-in GBM simulator**.

Nothing downstream knows which source is active — not the SSE stream, portfolio valuation, trade execution, LLM context or snapshots. Both sources write into one in-memory `PriceCache`; every consumer reads only from the cache.

```
                 ┌─────────────────────────────────────┐
  env vars ────► │ create_market_data_source(cache)    │  factory.py
                 └──────────────┬──────────────────────┘
                                │ returns one of
            ┌───────────────────┴────────────────────┐
            ▼                                        ▼
  SimulatorDataSource                       MassiveDataSource
  (GBM, 0.5 s ticks)                        (REST poller; mode = snapshot | eod)
            │ cache.update(...)                      │ cache.update(...)
            └───────────────────┬────────────────────┘
                                ▼
                  PriceCache (thread-safe, versioned)
                                │ get / get_price / get_all / version
     ┌───────────────┬──────────┴───────┬────────────────┬──────────────┐
     ▼               ▼                  ▼                ▼              ▼
 SSE /api/stream  Portfolio value   Trade execution   LLM context   Snapshots
```

Most of this exists in `backend/app/market/` (73 passing tests). This document is the **contract**, and marks the **changes still needed** with 🔧. The items in §10 are what an implementing agent should work through.

---

## 2. Selection rules

| `MASSIVE_API_KEY` | `MASSIVE_MODE` 🔧 | Source | Behavior |
|---|---|---|---|
| empty / unset | ignored | `SimulatorDataSource` | GBM ticks every 0.5 s |
| set | `auto` (default) | `MassiveDataSource` | Probe the snapshot endpoint once at start; on 403 / `NOT_AUTHORIZED` use `eod`, else `snapshot` |
| set | `snapshot` | `MassiveDataSource` | Full Market Snapshot. Needs Starter+ |
| set | `eod` | `MassiveDataSource` | Grouped Daily. Works on every plan; prices change once per day |

Optional tuning (🔧 new, all optional — add to `.env.example` with comments):

| Variable | Default | Meaning |
|---|---|---|
| `MASSIVE_MODE` | `auto` | `auto`, `snapshot`, `eod` |
| `MASSIVE_POLL_INTERVAL` | `15` (snapshot) / `3600` (eod) | Seconds between polls. 2–5 is fine on Starter+ |

Startup logs the source **name and mode only**, never the key.

---

## 3. Core types

### 3.1 `PriceUpdate` (`models.py`) — also the SSE payload contract

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    timestamp: float                       # Unix seconds
    reference_price: float | None = None   # 🔧 "daily change" baseline

    # properties
    change: float                # price - previous_price          (tick over tick)
    change_percent: float        # tick-over-tick %, 0.0 if previous_price == 0
    direction: str               # "up" | "down" | "flat"
    day_change_percent: float | None   # 🔧 (price / reference_price - 1) * 100, None if no reference

    def to_dict(self) -> dict: ...
```

🔧 `reference_price` gives the watchlist's "daily change %" (PLAN.md §10) a defined meaning, closing PLAN.md §13 item 7:

| Source | `reference_price` |
|---|---|
| Massive snapshot | `prevDay.c` (previous session close) |
| Massive EOD | close of the session before the latest one |
| Simulator | the ticker's price when it entered the simulation (session start) |

### 3.2 SSE event (`GET /api/stream/prices`)

One unnamed `data:` event per changed cache version, keyed by ticker, sent at most every 0.5 s. The first line of the stream is `retry: 1000`.

```json
{"AAPL": {"ticker": "AAPL", "price": 190.42, "previous_price": 190.38,
          "timestamp": 1731230000.12, "change": 0.04, "change_percent": 0.021,
          "direction": "up", "reference_price": 188.90, "day_change_percent": 0.805}}
```

Frontend agents should use `day_change_percent` for the watchlist's change column and `direction` (or `price` vs `previous_price`) for flash animations.

### 3.3 `PriceCache` (`cache.py`)

```python
class PriceCache:
    def update(self, ticker: str, price: float, timestamp: float | None = None,
               reference_price: float | None = None) -> PriceUpdate      # 🔧 new kwarg
    def get(self, ticker: str) -> PriceUpdate | None
    def get_price(self, ticker: str) -> float | None
    def get_all(self) -> dict[str, PriceUpdate]     # shallow copy
    def remove(self, ticker: str) -> None
    @property
    def version(self) -> int                         # bumped on every update
```

Rules:
- Prices are rounded to 2 dp on write. On the first update `previous_price == price`.
- 🔧 `reference_price=None` **keeps** the ticker's existing reference, so the simulator sets it once and the snapshot poller refreshes it every cycle.
- Every method takes a `threading.Lock`, so writers in `asyncio.to_thread` workers are safe.

### 3.4 `MarketDataSource` (`interface.py`)

```python
class MarketDataSource(ABC):
    async def start(self, tickers: list[str]) -> None    # seed cache, then start background task
    async def stop(self) -> None                         # idempotent; no cache writes after it returns
    async def add_ticker(self, ticker: str) -> None      # idempotent
    async def remove_ticker(self, ticker: str) -> None   # idempotent; evicts ticker from cache
    def get_tickers(self) -> list[str]
    @property
    def description(self) -> str                        # 🔧 e.g. "simulator", "massive:snapshot", "massive:eod"
```

Guarantees every implementation must meet:

1. **Push, never pull.** Sources write to the cache on their own schedule. Consumers never ask a source for a price.
2. **A failed cycle never kills the loop.** Exceptions inside a cycle are logged (type + message, no key) and the next cycle runs.
3. **`start()` seeds the cache before returning** where possible (simulator: seed prices; Massive: awaits the first poll). A fresh page load then has data immediately.
4. **Inputs are already normalized.** The app layer passes upper-case, validated symbols (§7). Sources call `ticker.strip().upper()` defensively but don't validate.
5. 🔧 **`add_ticker()` before `start()`** appends to the pending list, which `start()` then uses (today the simulator silently drops it). **`start()` twice** raises `RuntimeError`.

`description` is surfaced by `GET /api/health` (`{"status": "ok", "market_data": "massive:eod"}`) so the UI can show a small "EOD" / "SIM" badge. In EOD mode prices don't move, so the frontend shouldn't expect flashes.

---

## 4. Factory (`factory.py`)

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not api_key:
        logger.info("Market data source: simulator")
        return SimulatorDataSource(
            price_cache=price_cache,
            event_probability=float(os.getenv("SIM_EVENT_PROBABILITY", "0.0002")),
        )

    mode = os.environ.get("MASSIVE_MODE", "auto").strip().lower()             # 🔧
    if mode not in {"auto", "snapshot", "eod"}:
        raise ValueError(f"MASSIVE_MODE must be auto|snapshot|eod, got {mode!r}")
    interval = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()            # 🔧
    logger.info("Market data source: Massive (mode=%s)", mode)
    return MassiveDataSource(
        api_key=api_key,
        price_cache=price_cache,
        mode=mode,
        poll_interval=float(interval) if interval else None,   # None → mode default
    )
```

---

## 5. App wiring (FastAPI lifespan)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.market import PriceCache, create_market_data_source, stream_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(tracked_tickers_from_db())   # watchlist ∪ open positions (§7)
    app.state.price_cache = cache
    app.state.market = source
    try:
        yield
    finally:
        await source.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(stream_router)    # reads request.app.state.price_cache (§8)
# ...other /api routers...
# static mount at "/" goes LAST
```

If Massive's first call fails with a bad key (401) the app still starts; the poller logs errors each cycle and `/api/health` reports `massive:error`. A dead key must not stop the UI from loading.

---

## 6. `MassiveDataSource` — required changes 🔧

### 6.1 Snapshot parsing (fixed 2026-11-12 — `parse_snapshot()` in `massive_client.py`; reference price not yet returned)

`LastTrade` has `sip_timestamp` (ns) and **no `timestamp`**, so the current `snap.last_trade.timestamp / 1000.0` raises for every ticker and nothing is cached (see MASSIVE_API.md §3.1). Also, on Starter `lastTrade` can be absent. Replace with a parser that uses the fallback chain:

```python
Row = tuple[str, float, float, float | None]   # (ticker, price, ts_seconds, reference_price)

def parse_snapshot(s: TickerSnapshot) -> Row | None:
    prev_close = s.prev_day.close if s.prev_day and s.prev_day.close else None
    if s.last_trade and s.last_trade.price:
        ns = s.last_trade.sip_timestamp or s.updated
        return s.ticker, s.last_trade.price, ns / 1e9 if ns else time.time(), prev_close
    if s.min and s.min.close:
        ts = s.min.timestamp / 1e3 if s.min.timestamp else time.time()
        return s.ticker, s.min.close, ts, prev_close
    if s.day and s.day.close:
        return s.ticker, s.day.close, s.updated / 1e9 if s.updated else time.time(), prev_close
    if prev_close:                                   # pre-market, data just cleared
        return s.ticker, prev_close, time.time(), prev_close
    return None
```

Tests must build inputs with the real `TickerSnapshot.from_dict({...})`. `MagicMock` accepts any attribute name, which is how this bug passed.

### 6.2 Two modes

```python
class MassiveDataSource(MarketDataSource):
    DEFAULT_INTERVAL = {"snapshot": 15.0, "eod": 3600.0}

    def __init__(self, api_key: str, price_cache: PriceCache,
                 mode: str = "auto", poll_interval: float | None = None) -> None: ...

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = [t.strip().upper() for t in tickers]
        if self._mode == "auto":
            self._mode = await self._detect_mode()
        self._interval = self._interval or self.DEFAULT_INTERVAL[self._mode]
        await self._poll_once()
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def _detect_mode(self) -> str:
        probe = self._tickers[:1] or ["AAPL"]
        try:
            await asyncio.to_thread(self._client.get_snapshot_all, "stocks", probe)
            return "snapshot"
        except BadResponse as e:
            if "NOT_AUTHORIZED" in str(e) or "403" in str(e):
                logger.warning("Massive plan has no snapshot access; using end-of-day prices")
                return "eod"
            logger.error("Massive probe failed (%s); defaulting to snapshot mode", type(e).__name__)
            return "snapshot"

    async def _poll_once(self) -> None:
        if not self._tickers:
            return
        try:
            fetch = self._fetch_snapshot_rows if self._mode == "snapshot" else self._fetch_eod_rows
            for ticker, price, ts, ref in await asyncio.to_thread(fetch):
                self._cache.update(ticker, price, timestamp=ts, reference_price=ref)
        except Exception as e:
            logger.error("Massive poll failed (%s): %s", self._mode, type(e).__name__)
```

**Snapshot mode** (Starter+): one call per cycle.

```python
def _fetch_snapshot_rows(self) -> list[Row]:
    snaps = self._client.get_snapshot_all("stocks", tickers=self._tickers)
    return [r for s in snaps if (r := parse_snapshot(s))]
```

**EOD mode** (all plans): two Grouped Daily sessions, starting from *yesterday* in New York time (today returns 403 on Basic). Fetch only when the New York date has changed since the last success, so the hourly poll costs nothing on most cycles.

```python
def _fetch_eod_rows(self) -> list[Row]:
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    if self._eod_loaded_for == today_ny and not self._eod_missing():
        return []                                   # nothing new until after midnight ET
    sessions = last_sessions(self._client, set(self._tickers), sessions=2)   # MASSIVE_API.md §4.1
    if not sessions:
        return []
    (day, latest), prior = sessions[0], (sessions[1][1] if len(sessions) > 1 else {})
    ts = datetime.combine(day, dt_time(16, 0), ZoneInfo("America/New_York")).timestamp()
    self._eod_loaded_for = today_ny
    return [(t, b.close, ts, prior[t].close if t in prior else None)
            for t, b in latest.items() if b.close]
```

`_eod_missing()` returns True when a tracked ticker has no cache entry (it was just added), which forces one refetch. Bars are cached by the source between fetches, so a newly added ticker can often be served from the last grouped response with no request at all.

### 6.3 Smaller items

- **Newly added ticker, snapshot mode:** call `get_snapshot_ticker` once inside `add_ticker()` (via `to_thread`) so the UI shows a price at once, not up to 15 s later.
- **Market closed:** in snapshot mode, check `get_market_status()` every 20th poll; while `market == "closed"`, poll every 60 s.
- **Unknown symbols:** Massive omits them silently. They simply never appear in the cache; trades are rejected (§7) and `GET /api/watchlist` returns `price: null` for them.

---

## 7. The price universe (app layer owns it)

> **tracked tickers = watchlist ∪ tickers with an open position**

`remove_ticker()` evicts the price from the cache, so un-watching a held ticker would break valuation and sells. A small service owns the rule:

```python
# app/services/market.py  🔧 new
def normalize_ticker(raw: str) -> str:
    t = raw.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
        raise ValueError(f"Invalid ticker: {raw!r}")          # → 400 at the API
    return t

async def ensure_tracked(ticker: str) -> None:                 # watchlist add, buy
    await market.add_ticker(ticker)                            # idempotent

async def release_if_unused(ticker: str) -> None:              # watchlist remove, position closed
    if not watchlist_contains(ticker) and not position_exists(ticker):
        await market.remove_ticker(ticker)
```

Trade rule: reject with 400 `{"detail": "No price available for XYZ yet"}` when `cache.get_price(ticker) is None`. A buy of an untracked but valid ticker first calls `ensure_tracked` (the simulator prices it immediately; Massive may need one cycle, in which case the 400 tells the user to retry).

---

## 8. SSE router (`stream.py`)

The contract is unchanged (§3.2). 🔧 One fix: `create_stream_router()` registers `/prices` on a **module-level** `APIRouter`, so calling it twice (common in tests) registers the route twice. Make the router a plain module object that reads the cache from app state:

```python
stream_router = APIRouter(prefix="/api/stream", tags=["streaming"])

@stream_router.get("/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    cache: PriceCache = request.app.state.price_cache
    return StreamingResponse(
        _generate_events(cache, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

Keep `create_stream_router` as a thin deprecated alias until callers move over.

---

## 9. Testing requirements

| Area | Test |
|---|---|
| Factory | No key → simulator. Key → Massive. Bad `MASSIVE_MODE` raises. Key value never appears in captured logs |
| Snapshot parsing | Real `TickerSnapshot.from_dict`: ns `lastTrade.t`; no `lastTrade` (falls to `min`); empty `min`/`day` (falls to `prevDay`); nothing usable → skipped |
| EOD | Real `GroupedDailyAgg.from_dict` rows; weekend returns `[]` and steps back; starts at yesterday NY time; unwanted tickers filtered; no refetch on same NY date |
| Auto-detect | `BadResponse('{"status":"NOT_AUTHORIZED"...}')` → `eod`; other errors → `snapshot` and app still starts |
| Cache | `reference_price` kept when later updates pass `None`; `day_change_percent` correct; `None` without reference |
| Contract (both sources, parametrized) | `start` seeds cache; add/remove idempotent; `stop` idempotent; `add_ticker` before `start` is honored; second `start` raises |
| SSE | Two app instances in one test session don't double-register routes |
| Live (opt-in) | `@pytest.mark.massive_live`, skipped unless `MASSIVE_API_KEY` set; one real snapshot or grouped call |

---

## 10. Work items, in order

| # | Change | Files | Priority |
|---|---|---|---|
| 1 | `parse_snapshot` with ns timestamps and fallback chain | `massive_client.py` | **Critical** — Massive mode caches nothing today |
| 2 | Replace `MagicMock` snapshots with real models in tests | `tests/market/test_massive.py` | **Critical** — prevents regression |
| 3 | `reference_price` + `day_change_percent` | `models.py`, `cache.py` | High — defines "daily change %"; prerequisite for #4 and simulator change S1 |
| 4 | `MASSIVE_MODE` auto/snapshot/eod; Grouped Daily EOD path | `massive_client.py`, `factory.py` | High — free keys can't work without it |
| 5 | `description` property; `/api/health` reports it | `interface.py`, both sources | Medium |
| 6 | Lifecycle hardening (pending tickers, double start) | both sources | Medium |
| 7 | Price universe service + `normalize_ticker` | new `app/services/market.py` | Medium — before portfolio/watchlist APIs |
| 8 | Module-level `stream_router` reading `app.state` | `stream.py` | Low |
| 9 | Add-ticker prefetch; market-closed backoff | `massive_client.py` | Low |

Simulator-specific changes are listed in [`MARKET_SIMULATOR.md`](MARKET_SIMULATOR.md) §6.
