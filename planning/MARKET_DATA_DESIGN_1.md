# Market Data Backend — Detailed Design (Revision 1)

Implementation-ready design for the FinAlly market data subsystem: the unified
source interface, the shared price cache, the GBM simulator, the Massive
(Polygon.io) REST poller, the SSE stream, and how the rest of the backend
(watchlist, trades, chat, health) plugs into it.

All code lives under `backend/app/market/`. Every full-module snippet below
starts with a `# file:` comment naming its target path, so an agent can copy it
straight into place.

---

## 0. Status and How This Document Relates to the Existing Code

A first version of the subsystem already exists in `backend/app/market/`
(73 passing tests; see `planning/MARKET_DATA_SUMMARY.md`). This revision keeps
its architecture — strategy-pattern sources writing into one `PriceCache`,
read by SSE and by REST routes — and fixes the gaps found while re-reading the
code against `PLAN.md` and the installed `massive` SDK:

| # | Gap in current code | Impact | Fixed in this design by |
|---|---|---|---|
| 1 | `massive_client.py` reads `snap.last_trade.timestamp`. The SDK's `LastTrade` has **no** `timestamp` field (it has `sip_timestamp`, `participant_timestamp`, `trf_timestamp`, all in **nanoseconds**). | Every real snapshot raises `AttributeError`, is logged and skipped — with a real API key **no prices ever reach the cache**. Unit tests pass only because they use `MagicMock`. | `parse_snapshot()` (§8.2) with explicit field fallbacks and unit-agnostic timestamp conversion. |
| 2 | `archive/MASSIVE_API.md` documents `day.previous_close` / `day.change_percent`. For stocks, the SDK's `TickerSnapshot.day` is an `Agg` (o/h/l/c/v only). Previous close is `prev_day.close`; day change is `todays_change_percent`. | Anyone following the archived doc writes code that silently returns `None`. | §7.1 field reference, verified against `massive/rest/models/snapshot.py`. |
| 3 | `PriceUpdate` only carries tick-to-tick change. `PLAN.md` §10 asks the watchlist to show **daily change %**. | Frontend has nothing to compute daily change from. | `reference_price` field (§3): previous close for Massive, session-open seed for the simulator. |
| 4 | `PriceCache.remove()` does not bump `version`. | A removed ticker keeps showing in SSE until the next price write (up to 15 s on Massive). | `remove()` bumps the version (§4). |
| 5 | SSE skips the event when the cache is empty. | Removing the last ticker leaves it on screen forever. | Always send the snapshot, including `{}` (§12). |
| 6 | `stream.py` registers the route on a **module-level** `APIRouter`. | Calling `create_stream_router()` twice (tests) registers `/prices` twice. | Router is created inside the factory (§12). |
| 7 | `PriceCache.version` reads without the lock; `update()` uses `timestamp or time.time()` (a `0` timestamp is replaced). | Minor races on free-threaded Python; wrong timestamps in tests. | §4. |
| 8 | Simulator uses global `random` / `np.random`. | Non-reproducible tests and E2E runs. | Per-instance `numpy.random.Generator` with optional `MARKET_SIM_SEED` (§7). |
| 9 | `SimulatorDataSource.add_ticker()` is a silent no-op before `start()`. Massive does not normalize at start. | Order-of-operations bugs during startup. | Simulator is constructed eagerly; all inputs go through `normalize_ticker()` (§5). |
| 10 | Massive poller retries at a fixed 15 s on every error, including 401 and 429. | Rate-limit storms; log spam on a bad key. | Exponential backoff, capped at 5 min (§8.3). |
| 11 | No rule for tickers that are held but no longer watched. | Removing a held ticker from the watchlist stops its price → portfolio valuation breaks. | `TickerTracker` reference-counts watchlist + positions (§10). |

Nothing in the public API used by downstream code is removed. `PriceUpdate`
gains fields and `PriceCache.update()` gains an optional keyword argument, so
both changes are backward compatible.

---

## 1. Requirements (traced to PLAN.md)

| Requirement | Source | Where satisfied |
|---|---|---|
| One interface, two implementations, selected by `MASSIVE_API_KEY` | §5, §6 | §6, §9 |
| Simulator: GBM, ~500 ms ticks, correlated moves, 2–5 % random events, realistic seeds | §6 | §7 |
| Massive: REST polling of the union of watched tickers; 15 s default (free tier) | §6 | §8 |
| Cache holds latest price, previous price, timestamp | §6 | §3, §4 |
| `GET /api/stream/prices` SSE, ~500 ms cadence; ticker, price, previous price, timestamp, direction | §6, §8 | §12 |
| Watchlist shows daily change % | §10 | §3 (`reference_price`) |
| Watchlist add/remove (REST + LLM) changes the tracked set | §2, §9 | §10 |
| Trades fill at the current cached price | §2 | §11.2 |
| Deterministic runs for E2E tests | §12 | §7 (`MARKET_SIM_SEED`) |
| Never expose API keys | org policy | §9 (key never logged), §14 |

---

## 2. Architecture

```
                   ┌───────────────────────────── FastAPI process ─────────────────────────────┐
                   │                                                                            │
 MASSIVE_API_KEY?  │   create_market_data_source()                                              │
 ──────────────────┼──►  ├── SimulatorDataSource ── asyncio task, every 0.5 s ──┐                 │
                   │     └── MassiveDataSource  ── asyncio task, every 15 s  ──┤ cache.update()  │
                   │            (sync SDK call runs in a worker thread)        ▼                 │
                   │                                                    ┌──────────────┐         │
                   │   TickerTracker ── add_ticker/remove_ticker ──►    │  PriceCache  │ version │
                   │   (watchlist ∪ positions)                          └──────┬───────┘ counter │
                   │                                                           │ read            │
                   │         ┌──────────────────────┬─────────────────────────┼──────────────┐  │
                   │         ▼                      ▼                         ▼              ▼  │
                   │  /api/stream/prices    POST /portfolio/trade     GET /portfolio   /api/chat│
                   │  (SSE, per client)     (fill price)              (valuation)      (context)│
                   └────────────────────────────────────────────────────────────────────────────┘
```

**Rules that keep this simple:**

1. Sources **push** into the cache; nobody asks a source for a price.
2. The cache is the **only** shared state. It is guarded by a `threading.Lock`
   because the Massive SDK call runs in a worker thread (`asyncio.to_thread`).
   The critical section is a dict lookup plus an assignment.
3. SSE handlers **poll** the cache's version counter every 500 ms and send a
   full snapshot when it has changed. Full snapshots make the client trivially
   correct: removed tickers disappear, reconnects need no replay.
4. The tracked ticker set is owned by the source; the `TickerTracker` decides
   when to add or remove (watchlist ∪ open positions).

### 2.1 Module layout

```
backend/app/market/
  __init__.py        # public re-exports
  models.py          # PriceUpdate (frozen dataclass)
  cache.py           # PriceCache (thread-safe, versioned)
  tickers.py         # normalize_ticker(), InvalidTickerError          (new)
  interface.py       # MarketDataSource ABC (+ sync_tickers, status)
  seed_prices.py     # seeds, per-ticker sigma/mu, correlation groups   (unchanged)
  simulator.py       # GBMSimulator + SimulatorDataSource
  massive_client.py  # parse_snapshot() + MassiveDataSource
  factory.py         # create_market_data_source() from env
  tracker.py         # TickerTracker (watchlist ∪ positions refcount)   (new)
  stream.py          # create_stream_router(), generate_price_events()
```

---

## 3. Data Model — `models.py`

`PriceUpdate` is the only type that leaves the market layer.

```python
# file: backend/app/market/models.py
"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of one ticker's price at a point in time.

    previous_price  -- the price at the previous cache write (tick-to-tick).
                       Drives the green/red flash in the UI.
    reference_price -- the "start of day" price used for daily change:
                       Massive: previous session close (snapshot.prev_day.close)
                       Simulator: the seed price the session started from.
                       None if unknown.
    """

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    reference_price: float | None = None

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def day_change(self) -> float | None:
        if self.reference_price is None:
            return None
        return round(self.price - self.reference_price, 4)

    @property
    def day_change_percent(self) -> float | None:
        if not self.reference_price:
            return None
        return round((self.price - self.reference_price) / self.reference_price * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "reference_price": self.reference_price,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }
```

Example:

```python
>>> PriceUpdate("AAPL", 191.20, 191.05, 1763049600.0, reference_price=190.00).to_dict()
{'ticker': 'AAPL', 'price': 191.2, 'previous_price': 191.05, 'timestamp': 1763049600.0,
 'change': 0.15, 'change_percent': 0.0785, 'direction': 'up',
 'reference_price': 190.0, 'day_change': 1.2, 'day_change_percent': 0.6316}
```

---

## 4. Price Cache — `cache.py`

```python
# file: backend/app/market/cache.py
"""Thread-safe, versioned in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest price per ticker.

    Writers: exactly one MarketDataSource (simulator task or Massive poller thread).
    Readers: SSE streams, portfolio valuation, trade execution, chat context.

    `version` increases on every change (update or removal). SSE handlers compare
    it with the last version they sent to decide whether to push.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        reference_price: float | None = None,
    ) -> PriceUpdate:
        """Record a price. Returns the stored PriceUpdate.

        - previous_price is the last stored price (equal to price on first write).
        - reference_price, if omitted, is carried over from the previous update
          (or set to this price on first write).
        - A write identical to the stored one (same price and timestamp) is a
          no-op and does not bump the version. This matters for Massive outside
          market hours, where every poll returns the same last trade.
        """
        ts = time.time() if timestamp is None else timestamp
        price = round(price, 2)
        with self._lock:
            prev = self._prices.get(ticker)
            if prev is not None and prev.price == price and prev.timestamp == ts:
                return prev
            if reference_price is not None:
                ref = round(reference_price, 2)
            elif prev is not None:
                ref = prev.reference_price
            else:
                ref = price
            update = PriceUpdate(
                ticker=ticker,
                price=price,
                previous_price=prev.price if prev else price,
                timestamp=ts,
                reference_price=ref,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def snapshot(self) -> tuple[int, dict[str, PriceUpdate]]:
        """Version and prices read atomically (what SSE needs)."""
        with self._lock:
            return self._version, dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: object) -> bool:
        with self._lock:
            return ticker in self._prices
```

Worked example:

```python
cache = PriceCache()
cache.update("AAPL", 190.00)                  # v1: prev=190.00 ref=190.00 flat
cache.update("AAPL", 190.12)                  # v2: prev=190.00 ref=190.00 up
cache.update("AAPL", 190.05, reference_price=189.50)  # v3: ref replaced
cache.update("AAPL", 190.05, timestamp=cache.get("AAPL").timestamp)  # no-op, still v3
cache.remove("AAPL")                          # v4
```

---

## 5. Ticker Normalization — `tickers.py`

Every entry point (REST body, LLM output, DB rows, source methods) passes
through one function, so `"aapl "`, `"AAPL"` and `"Aapl"` are the same key.

```python
# file: backend/app/market/tickers.py
"""Ticker symbol normalization and validation."""

from __future__ import annotations

import re

# US equity symbols: 1-10 chars, letters first, allow digits, '.' and '-'
# (BRK.B, BF-B). Deliberately permissive; Massive is the real authority.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class InvalidTickerError(ValueError):
    """Raised when a string cannot be a ticker symbol."""


def normalize_ticker(raw: str) -> str:
    if not isinstance(raw, str):
        raise InvalidTickerError(f"Ticker must be a string, got {type(raw).__name__}")
    ticker = raw.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker
```

```python
normalize_ticker(" pypl ")   # "PYPL"
normalize_ticker("brk.b")    # "BRK.B"
normalize_ticker("DROP TABLE")  # InvalidTickerError -> route returns HTTP 400
```

---

## 6. Unified Interface — `interface.py`

```python
# file: backend/app/market/interface.py
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from .tickers import normalize_ticker


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations write into a shared PriceCache on their own schedule.
    Downstream code reads prices from the cache, never from the source.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL"])   # exactly once
        await source.add_ticker("TSLA")          # idempotent
        await source.remove_ticker("GOOGL")      # idempotent, also clears cache entry
        await source.stop()                      # idempotent
    """

    name: str = "abstract"

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Start the background task. Prices for `tickers` should be in the
        cache as soon as possible (the simulator seeds them synchronously;
        Massive does one blocking poll before returning)."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Safe to call more than once."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and remove it from the cache."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers, in insertion order."""

    async def sync_tickers(self, tickers: Iterable[str]) -> None:
        """Make the tracked set equal to `tickers` (adds first, then removes)."""
        desired = {normalize_ticker(t) for t in tickers}
        current = set(self.get_tickers())
        for t in sorted(desired - current):
            await self.add_ticker(t)
        for t in sorted(current - desired):
            await self.remove_ticker(t)

    def status(self) -> dict:
        """Small, secret-free status dict for /api/health."""
        return {"source": self.name, "tickers": len(self.get_tickers())}
```

---

## 7. Simulator — `simulator.py`

### 7.1 The math

Each tick, each price follows geometric Brownian motion:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

* `dt` = 0.5 s as a fraction of a trading year = `0.5 / (252 × 6.5 × 3600)` ≈ **8.48 × 10⁻⁸**
* `σ·√dt` per tick: √dt ≈ 2.912 × 10⁻⁴
* `Z` = correlated standard normals: `Z = L · ε`, where `L = cholesky(C)` and `ε ~ N(0, I)`
* Prices are always positive (multiplicative `exp`).

**What the numbers mean on screen (no events):**

| Ticker | σ | 1 tick (0.5 s) | 1 minute (120 ticks) | 6.5 h "day" |
|---|---|---|---|---|
| V | 0.17 | ±0.005 % (≈ $0.014 on $280) | ±0.054 % | ±1.07 % |
| AAPL | 0.22 | ±0.0064 % (≈ $0.012 on $190) | ±0.070 % (≈ $0.13) | ±1.39 % |
| NVDA | 0.40 | ±0.0117 % (≈ $0.09 on $800) | ±0.128 % | ±2.52 % |
| TSLA | 0.50 | ±0.0146 % (≈ $0.04 on $250) | ±0.160 % | ±3.15 % |

(±1 standard deviation. Prices are rounded to cents, so on cheap low-vol
tickers some ticks show as "flat" — that is fine and looks realistic.)

**Random events.** Each tick, each ticker has probability `p` of a shock of
±U(2 %, 5 %). With the default `p = 0.001`, 10 tickers and 2 ticks/s, there is
an event somewhere about every **50 s** — the "drama" the plan asks for.

Tuning note: events dominate long-run volatility. Per ticker there are
`p × 7200 ≈ 7.2` events per wall-clock hour, each with E[shock²] ≈ 0.0013, so
an hour of events alone adds ≈ ±9.7 % (vs ±0.55 % from GBM for AAPL). That is
deliberate for a short demo; for long sessions set `MARKET_SIM_EVENT_PROB=0.0002`
(≈ ±4.3 %/hour, one event every ~4 min across 10 tickers).

### 7.2 Correlation

Sector-block correlation (unchanged from `seed_prices.py`):

| Pair | ρ |
|---|---|
| tech × tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | 0.6 |
| finance × finance (JPM, V) | 0.5 |
| anything × TSLA | 0.3 |
| cross-sector / unknown tickers | 0.3 |

The matrix is rebuilt whenever a ticker is added or removed (O(n³) Cholesky,
n < 50, microseconds). These block matrices are positive definite, but a
defensive fallback projects to the nearest PD matrix if a future edit to the
groups ever breaks that.

### 7.3 Seed prices for unknown tickers

Known tickers start from `SEED_PRICES`. Unknown tickers (e.g. `PYPL` added via
chat) get a **deterministic** price in $50–$300 derived from a CRC32 of the
symbol, so the same ticker starts at the same price across restarts and test
runs instead of a fresh random number each time.

### 7.4 Code

```python
# file: backend/app/market/simulator.py
"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import zlib

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)


def fallback_seed_price(ticker: str) -> float:
    """Deterministic starting price in [$50.00, $300.00) for unknown tickers."""
    return 50.0 + (zlib.crc32(ticker.encode()) % 25_000) / 100.0


class GBMSimulator:
    """Correlated geometric Brownian motion for a set of tickers.

    Pure computation, no asyncio. step() is the hot path (every 500 ms).
    """

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

    def __init__(
        self,
        tickers: list[str] | None = None,
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._dt = dt
        self._sqrt_dt = math.sqrt(dt)
        self._event_prob = event_probability
        self._rng = np.random.default_rng(seed)

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._seed_prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        self.add_tickers(tickers or [])

    # --- public API ---

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick. Returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        shocks = self._rng.random(n) < self._event_prob

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]
            drift = (mu - 0.5 * sigma * sigma) * self._dt
            diffusion = sigma * self._sqrt_dt * z[i]
            price = self._prices[ticker] * math.exp(drift + diffusion)

            if shocks[i]:
                magnitude = self._rng.uniform(0.02, 0.05)
                sign = 1.0 if self._rng.random() < 0.5 else -1.0
                price *= 1.0 + sign * magnitude
                logger.debug("Event on %s: %+.1f%%", ticker, sign * magnitude * 100)

            self._prices[ticker] = price
            result[ticker] = round(price, 2)
        return result

    def add_ticker(self, ticker: str) -> None:
        self.add_tickers([ticker])

    def add_tickers(self, tickers: list[str]) -> None:
        """Add several tickers with a single Cholesky rebuild."""
        added = False
        for raw in tickers:
            ticker = normalize_ticker(raw)
            if ticker in self._prices:
                continue
            seed = SEED_PRICES.get(ticker) or fallback_seed_price(ticker)
            self._tickers.append(ticker)
            self._prices[ticker] = seed
            self._seed_prices[ticker] = seed
            self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))
            added = True
        if added:
            self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._seed_prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        price = self._prices.get(ticker)
        return round(price, 2) if price is not None else None

    def get_seed_price(self, ticker: str) -> float | None:
        return self._seed_prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals ---

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        try:
            self._cholesky = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            # Project to nearest positive-definite correlation matrix.
            w, v = np.linalg.eigh(corr)
            corr = v @ np.diag(np.clip(w, 1e-6, None)) @ v.T
            d = np.sqrt(np.diag(corr))
            self._cholesky = np.linalg.cholesky(corr / np.outer(d, d))
            logger.warning("Correlation matrix was not PD; used nearest PD approximation")

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource that runs GBMSimulator in an asyncio task."""

    name = "simulator"

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        # Built eagerly so add_ticker() before start() is not lost.
        self._sim = GBMSimulator(event_probability=event_probability, seed=seed)
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None:
            raise RuntimeError("SimulatorDataSource already started")
        self._sim.add_tickers(tickers)
        for ticker in self._sim.get_tickers():
            self._seed_cache(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(self._sim.get_tickers()))

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)  # price is available immediately
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers()

    def status(self) -> dict:
        return {**super().status(), "interval_s": self._interval,
                "running": self._task is not None and not self._task.done()}

    def _seed_cache(self, ticker: str) -> None:
        price = self._sim.get_price(ticker)
        if price is not None:
            self._cache.update(ticker, price, reference_price=self._sim.get_seed_price(ticker))

    async def _run_loop(self) -> None:
        while True:
            try:
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker, price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

Usage example (no FastAPI required):

```python
import asyncio
from app.market import PriceCache
from app.market.simulator import SimulatorDataSource

async def main():
    cache = PriceCache()
    sim = SimulatorDataSource(cache, seed=42)
    await sim.start(["AAPL", "TSLA"])
    print(cache.get("AAPL").to_dict())        # seeded: 190.00, flat
    await asyncio.sleep(2)
    print(cache.get("AAPL").to_dict())        # ~4 ticks later
    await sim.add_ticker("pypl")              # normalized, priced immediately
    print(cache.get_price("PYPL"))            # deterministic, e.g. 187.34
    await sim.stop()

asyncio.run(main())
```

---

## 8. Massive API Client — `massive_client.py`

### 8.1 Endpoint and fields (verified against the installed `massive` SDK)

One call returns every requested ticker:

```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT
SDK: RESTClient(api_key=...).get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=[...])
```

Raw JSON for one ticker (abbreviated) and the SDK attribute it maps to:

```json
{
  "ticker": "AAPL",                          // snap.ticker
  "todaysChange": -1.28,                     // snap.todays_change
  "todaysChangePerc": -0.67,                 // snap.todays_change_percent
  "updated": 1763049600123456789,            // snap.updated            (ns)
  "day":     {"o":190.1,"h":191.0,"l":188.7,"c":189.12,"v":41000000},   // snap.day      (Agg)
  "prevDay": {"o":189.0,"h":190.9,"l":188.2,"c":190.40,"v":52000000},   // snap.prev_day (Agg)
  "min":     {"c":189.10,"t":1763049540000},                            // snap.min      (ms)
  "lastTrade": {"p":189.12,"s":100,"t":1763049600123456789,"x":4}       // snap.last_trade
}
```

| Need | Attribute | Notes |
|---|---|---|
| current price | `last_trade.price` | may be `None` on plans without trade data, and pre-market |
| fallback price | `min.close` → `day.close` → `prev_day.close` | first non-empty wins |
| timestamp | `last_trade.sip_timestamp` (**ns**) → `min.timestamp` (**ms**) → `updated` (**ns**) | units differ: normalize |
| reference (day change) | `prev_day.close` | **not** `day.previous_close` (that field does not exist on stock snapshots) |

Errors raised by the SDK: `massive.exceptions.AuthError` (empty/invalid key),
`massive.exceptions.BadResponse` (any non-200, incl. 403 plan-not-entitled and
429 rate-limit; the body text is in the message), plus `urllib3` network
errors. The SDK already retries 5xx internally.

### 8.2 Parsing as a pure function

Keeping parsing separate from I/O means it can be tested with real SDK model
objects built from JSON, which is exactly what caught gap #1.

### 8.3 Polling, backoff and rate limits

* Default interval 15 s → 4 requests/min, inside the free tier's 5/min.
  Paid plans: set `MASSIVE_POLL_INTERVAL=2` (or anything ≥ 1).
* On failure the next delay is `interval × 2^failures`, capped at 300 s; a
  success resets it. A bad key therefore logs at 15 s, 30 s, 60 s, … not every 15 s.
* Tickers are requested in chunks of 100 to keep the URL short.
* `add_ticker` does not trigger an extra request (it would break the free-tier
  budget when the LLM adds several tickers at once). The new ticker is priced on
  the next poll; until then trades on it return HTTP 400 "price not yet
  available" (§11.2) and SSE simply omits it.
* A ticker removed while a request is in flight is ignored when the response
  arrives, so it cannot reappear in the cache.

### 8.4 Code

```python
# file: backend/app/market/massive_client.py
"""Massive (Polygon.io) REST poller."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from massive import RESTClient
from massive.exceptions import AuthError
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Quote:
    ticker: str
    price: float
    timestamp: float  # Unix seconds
    reference_price: float | None


def to_unix_seconds(value: float | int | None) -> float | None:
    """Massive mixes ns, us, ms and s timestamps. Detect by magnitude."""
    if not value:
        return None
    v = float(value)
    if v > 1e17:
        return v / 1e9  # nanoseconds
    if v > 1e14:
        return v / 1e6  # microseconds
    if v > 1e11:
        return v / 1e3  # milliseconds
    return v


def _num(obj: Any, attr: str) -> float | None:
    """Positive numeric attribute or None. Rejects None, bools, strings, mocks."""
    value = getattr(obj, attr, None) if obj is not None else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def parse_snapshot(snap: Any) -> Quote | None:
    """Extract a Quote from a massive TickerSnapshot. Returns None if unusable."""
    ticker = getattr(snap, "ticker", None)
    if not isinstance(ticker, str) or not ticker:
        return None

    last_trade = getattr(snap, "last_trade", None)
    minute = getattr(snap, "min", None)
    day = getattr(snap, "day", None)
    prev_day = getattr(snap, "prev_day", None)

    price: float | None = None
    ts: float | None = None
    if (price := _num(last_trade, "price")) is not None:
        ts = to_unix_seconds(_num(last_trade, "sip_timestamp")
                             or _num(last_trade, "participant_timestamp"))
    elif (price := _num(minute, "close")) is not None:
        ts = to_unix_seconds(_num(minute, "timestamp"))
    else:
        price = _num(day, "close") or _num(prev_day, "close")

    if price is None:
        return None
    if ts is None:
        ts = to_unix_seconds(_num(snap, "updated")) or time.time()

    return Quote(ticker=ticker.upper(), price=price, timestamp=ts,
                 reference_price=_num(prev_day, "close"))


class MassiveDataSource(MarketDataSource):
    """Polls the Massive snapshot endpoint and writes results to the PriceCache."""

    name = "massive"
    MAX_TICKERS_PER_REQUEST = 100
    MAX_BACKOFF_S = 300.0

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
        client_factory: Callable[..., Any] = RESTClient,
    ) -> None:
        self._api_key = api_key  # never logged
        self._cache = price_cache
        self._interval = poll_interval
        self._client_factory = client_factory
        self._client: Any = None
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._failures = 0
        self._last_success: float | None = None
        self._last_error: str | None = None

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None:
            raise RuntimeError("MassiveDataSource already started")
        self._client = self._client_factory(api_key=self._api_key)
        for t in tickers:
            t = normalize_ticker(t)
            if t not in self._tickers:
                self._tickers.append(t)
        await self._poll_once()  # cache has data before the app serves requests
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, %.1fs interval",
                    len(self._tickers), self._interval)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added %s (priced on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def status(self) -> dict:
        return {
            **super().status(),
            "interval_s": self._interval,
            "consecutive_failures": self._failures,
            "last_success": self._last_success,
            "last_error": self._last_error,  # exception class name only, no payloads
        }

    # --- internals ---

    def next_delay(self) -> float:
        if self._failures == 0:
            return self._interval
        return min(self._interval * (2 ** self._failures), self.MAX_BACKOFF_S)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.next_delay())
            await self._poll_once()

    async def _poll_once(self) -> int:
        """One poll. Returns number of tickers written. Never raises."""
        requested = list(self._tickers)
        if not requested or self._client is None:
            return 0
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, requested)
        except AuthError:
            self._record_failure("AuthError")
            logger.error("Massive rejected the API key; check MASSIVE_API_KEY "
                         "(retrying in %.0fs)", self.next_delay())
            return 0
        except Exception as exc:  # BadResponse (403/429/...), network errors
            self._record_failure(type(exc).__name__)
            logger.warning("Massive poll failed (%s); retrying in %.0fs",
                           type(exc).__name__, self.next_delay())
            return 0

        self._failures = 0
        self._last_error = None
        self._last_success = time.time()

        tracked = set(self._tickers)  # may have changed while we were waiting
        written = 0
        for snap in snapshots:
            quote = parse_snapshot(snap)
            if quote is None or quote.ticker not in tracked:
                continue
            self._cache.update(quote.ticker, quote.price, timestamp=quote.timestamp,
                               reference_price=quote.reference_price)
            written += 1
        if written < len(requested):
            logger.debug("Massive: %d/%d tickers priced", written, len(requested))
        return written

    def _record_failure(self, error_name: str) -> None:
        self._failures += 1
        self._last_error = error_name

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Blocking SDK calls; runs in a worker thread."""
        results: list = []
        for i in range(0, len(tickers), self.MAX_TICKERS_PER_REQUEST):
            chunk = tickers[i : i + self.MAX_TICKERS_PER_REQUEST]
            results.extend(self._client.get_snapshot_all(
                market_type=SnapshotMarketType.STOCKS, tickers=chunk))
        return results
```

Market-hours behaviour: outside trading hours `last_trade` stays the same each
poll, so the cache's identical-write check (§4) leaves the version unchanged and
SSE sends nothing new. The UI shows a static, correct last price with the day
change from the previous close.

---

## 9. Factory — `factory.py`

```python
# file: backend/app/market/factory.py
"""Select and configure the market data source from environment variables."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _float_env(env: Mapping[str, str], key: str, default: float, minimum: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return max(float(raw), minimum)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", key, raw, default)
        return default


def create_market_data_source(
    price_cache: PriceCache, env: Mapping[str, str] | None = None
) -> MarketDataSource:
    """MASSIVE_API_KEY non-empty -> Massive, otherwise the simulator.

    Returns an unstarted source; the caller awaits source.start(tickers).
    """
    env = os.environ if env is None else env
    api_key = env.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        interval = _float_env(env, "MASSIVE_POLL_INTERVAL", 15.0, minimum=1.0)
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache,
                                 poll_interval=interval)

    seed_raw = env.get("MARKET_SIM_SEED", "").strip()
    seed = int(seed_raw) if seed_raw.lstrip("-").isdigit() else None
    logger.info("Market data source: GBM simulator%s",
                f" (seed={seed})" if seed is not None else "")
    return SimulatorDataSource(
        price_cache=price_cache,
        update_interval=_float_env(env, "MARKET_SIM_INTERVAL", 0.5, minimum=0.05),
        event_probability=_float_env(env, "MARKET_SIM_EVENT_PROB", 0.001, minimum=0.0),
        seed=seed,
    )
```

The API key is passed to the client and never logged, returned by `status()`,
or included in error messages.

---

## 10. Which Tickers Are Tracked — `tracker.py`

`PLAN.md` says the stream covers "all tickers known to the system". That must
include **held** tickers, not only watched ones; otherwise removing a held
ticker from the watchlist breaks portfolio valuation. `TickerTracker` keeps a
set of reasons per ticker and only calls the source on the first acquire and
last release.

```python
# file: backend/app/market/tracker.py
"""Reference-counted ticker tracking: watchlist ∪ open positions."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from .interface import MarketDataSource
from .tickers import normalize_ticker

WATCHLIST = "watchlist"
POSITION = "position"


class TickerTracker:
    def __init__(self, source: MarketDataSource) -> None:
        self._source = source
        self._reasons: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    def seed(self, *, watchlist: Iterable[str], positions: Iterable[str]) -> list[str]:
        """Record startup state without touching the source; returns tickers for start()."""
        for t in watchlist:
            self._reasons.setdefault(normalize_ticker(t), set()).add(WATCHLIST)
        for t in positions:
            self._reasons.setdefault(normalize_ticker(t), set()).add(POSITION)
        return sorted(self._reasons)

    async def acquire(self, ticker: str, reason: str) -> str:
        ticker = normalize_ticker(ticker)
        async with self._lock:
            first = ticker not in self._reasons
            self._reasons.setdefault(ticker, set()).add(reason)
            if first:
                await self._source.add_ticker(ticker)
        return ticker

    async def release(self, ticker: str, reason: str) -> None:
        ticker = normalize_ticker(ticker)
        async with self._lock:
            reasons = self._reasons.get(ticker)
            if not reasons:
                return
            reasons.discard(reason)
            if not reasons:
                del self._reasons[ticker]
                await self._source.remove_ticker(ticker)

    def is_tracked(self, ticker: str) -> bool:
        return normalize_ticker(ticker) in self._reasons
```

| Event | Call |
|---|---|
| `POST /api/watchlist {ticker}` or LLM `{"action":"add"}` | `acquire(t, WATCHLIST)` |
| `DELETE /api/watchlist/{t}` or LLM `{"action":"remove"}` | `release(t, WATCHLIST)` |
| buy creates a position | `acquire(t, POSITION)` |
| sell brings quantity to 0 (position row deleted) | `release(t, POSITION)` |

Frontend consequence: SSE may include held tickers that are not on the
watchlist. The watchlist panel filters by `GET /api/watchlist`; the positions
table and heatmap use whatever SSE provides.

---

## 11. Integration with the Rest of the Backend

### 11.1 Lifespan wiring (`backend/app/main.py`)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.market import PriceCache, create_market_data_source, create_stream_router
from app.market.tracker import TickerTracker
from app.db import load_watchlist_tickers, load_position_tickers  # built by the backend agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache)
    tracker = TickerTracker(source)
    tickers = tracker.seed(
        watchlist=await load_watchlist_tickers(),   # ["AAPL", "GOOGL", ...]
        positions=await load_position_tickers(),     # tickers with quantity > 0
    )
    await source.start(tickers)

    app.state.price_cache = cache
    app.state.market_source = source
    app.state.ticker_tracker = tracker
    app.include_router(create_stream_router(cache))
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)


# Dependencies for route modules
def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache

def get_ticker_tracker(request: Request) -> TickerTracker:
    return request.app.state.ticker_tracker
```

Mount the static frontend **after** the API routers so `/api/*` is matched first.

### 11.2 Trade execution reads the fill price

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.market import PriceCache
from app.market.tickers import InvalidTickerError, normalize_ticker
from app.market.tracker import POSITION, TickerTracker

router = APIRouter(prefix="/api/portfolio")


class TradeRequest(BaseModel):
    ticker: str
    quantity: float = Field(gt=0)
    side: str = Field(pattern="^(buy|sell)$")


@router.post("/trade")
async def trade(req: TradeRequest,
                cache: PriceCache = Depends(get_price_cache),
                tracker: TickerTracker = Depends(get_ticker_tracker)):
    try:
        ticker = normalize_ticker(req.ticker)
    except InvalidTickerError as e:
        raise HTTPException(400, str(e))

    price = cache.get_price(ticker)
    if price is None and req.side == "buy":
        # Not tracked yet (trade bar accepts any ticker). Simulator prices it
        # immediately; Massive prices it on the next poll.
        await tracker.acquire(ticker, POSITION)
        price = cache.get_price(ticker)
        if price is None:
            await tracker.release(ticker, POSITION)
            raise HTTPException(400, f"Price for {ticker} not available yet; try again shortly")
    if price is None:
        raise HTTPException(400, f"No price for {ticker}")

    result = await execute_trade(ticker, req.side, req.quantity, price)  # DB logic, portfolio agent
    if req.side == "buy":
        await tracker.acquire(ticker, POSITION)          # idempotent
    elif result.remaining_quantity == 0:
        await tracker.release(ticker, POSITION)
    return result
```

The same helper is used by the chat flow for LLM-requested trades, so manual
and AI trades share one price source and one validation path.

### 11.3 Watchlist routes

```python
@router.post("/api/watchlist")
async def add(body: WatchlistAdd, tracker: TickerTracker = Depends(get_ticker_tracker),
              cache: PriceCache = Depends(get_price_cache)):
    ticker = normalize_ticker(body.ticker)          # 400 on InvalidTickerError
    await db_add_watchlist(ticker)                   # 409 if already present
    await tracker.acquire(ticker, WATCHLIST)
    update = cache.get(ticker)
    return {"ticker": ticker, "price": update.to_dict() if update else None}


@router.delete("/api/watchlist/{ticker}")
async def remove(ticker: str, tracker: TickerTracker = Depends(get_ticker_tracker)):
    ticker = normalize_ticker(ticker)
    await db_remove_watchlist(ticker)                # 404 if absent
    await tracker.release(ticker, WATCHLIST)         # keeps tracking if still held
    return {"ticker": ticker, "removed": True}
```

### 11.4 Portfolio valuation and chat context

```python
prices = cache.get_all()
for pos in positions:
    current = prices.get(pos.ticker)
    mark = current.price if current else pos.avg_cost   # graceful fallback
    pos.market_value = pos.quantity * mark
    pos.unrealized_pnl = (mark - pos.avg_cost) * pos.quantity
```

The chat endpoint builds its "watchlist with live prices" context from the same
`cache.get_all()` call.

### 11.5 Health

```python
@app.get("/api/health")
async def health(request: Request):
    cache = request.app.state.price_cache
    return {"status": "ok", "market": request.app.state.market_source.status(),
            "cached_tickers": len(cache)}
```

Example: `{"status":"ok","market":{"source":"simulator","tickers":10,"interval_s":0.5,"running":true},"cached_tickers":10}`

---

## 12. SSE Stream — `stream.py`

### 12.1 Wire format

```
retry: 1000

id: 1532
data: {"AAPL":{"ticker":"AAPL","price":190.12,"previous_price":190.05,"timestamp":1763049600.51,"change":0.07,"change_percent":0.0368,"direction":"up","reference_price":190.0,"day_change":0.12,"day_change_percent":0.0632},"MSFT":{...}}

: keep-alive

```

* Unnamed events, so the browser's `onmessage` fires.
* Every `data:` is a **complete snapshot** keyed by ticker (≈ 230 bytes/ticker,
  ≈ 2.3 KB per event for 10 tickers, ≈ 4.6 KB/s).
* `id:` is the cache version (useful for debugging; no replay is needed because
  every event is complete).
* A `: keep-alive` comment is sent after 15 s without data (Massive mode,
  market closed) so proxies and load balancers don't drop idle connections.
* An empty watchlist sends `data: {}`.

### 12.2 Code

```python
# file: backend/app/market/stream.py
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class _Disconnectable(Protocol):
    async def is_disconnected(self) -> bool: ...


def create_stream_router(
    price_cache: PriceCache, *, interval: float = 0.5, heartbeat: float = 15.0
) -> APIRouter:
    """Router factory. A fresh router per call, so tests can build many apps."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            generate_price_events(price_cache, request, interval=interval, heartbeat=heartbeat),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return router


async def generate_price_events(
    price_cache: PriceCache,
    request: _Disconnectable,
    *,
    interval: float = 0.5,
    heartbeat: float = 15.0,
) -> AsyncGenerator[str, None]:
    yield "retry: 1000\n\n"
    last_version = -1
    last_sent = time.monotonic()
    try:
        while not await request.is_disconnected():
            version, prices = price_cache.snapshot()
            now = time.monotonic()
            if version != last_version:
                last_version = version
                payload = json.dumps(
                    {t: u.to_dict() for t, u in prices.items()}, separators=(",", ":")
                )
                yield f"id: {version}\ndata: {payload}\n\n"
                last_sent = now
            elif now - last_sent >= heartbeat:
                yield ": keep-alive\n\n"
                last_sent = now
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.debug("SSE stream cancelled")
        raise
```

### 12.3 Frontend consumption (reference for the frontend agent)

```ts
type PriceUpdate = {
  ticker: string; price: number; previous_price: number; timestamp: number;
  change: number; change_percent: number; direction: "up" | "down" | "flat";
  reference_price: number | null; day_change: number | null; day_change_percent: number | null;
};
type Status = "connected" | "reconnecting" | "disconnected";

export function subscribePrices(
  onPrices: (p: Record<string, PriceUpdate>) => void,
  onStatus: (s: Status) => void,
): () => void {
  const es = new EventSource("/api/stream/prices");
  es.onopen = () => onStatus("connected");
  es.onmessage = (e) => onPrices(JSON.parse(e.data));     // full snapshot: replace state
  es.onerror = () =>
    onStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
  return () => es.close();
}
```

Flash: when `direction !== "flat"` and the price differs from what the row last
rendered, add `flash-up`/`flash-down` and remove it after ~500 ms. Sparklines:
append `{time: timestamp, value: price}` per ticker on each event where the
timestamp changed.

---

## 13. Package exports — `__init__.py`

```python
# file: backend/app/market/__init__.py
"""Market data subsystem for FinAlly."""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router
from .tickers import InvalidTickerError, normalize_ticker
from .tracker import TickerTracker

__all__ = [
    "InvalidTickerError",
    "MarketDataSource",
    "PriceCache",
    "PriceUpdate",
    "TickerTracker",
    "create_market_data_source",
    "create_stream_router",
    "normalize_ticker",
]
```

---

## 14. Error Handling and Edge Cases

| Situation | Behaviour |
|---|---|
| Empty watchlist and no positions at startup | Sources start with no tickers; SSE sends `data: {}`; first `acquire` starts pricing. |
| Invalid ticker string (REST or LLM) | `InvalidTickerError` → HTTP 400 / error line in chat response. Source never sees it. |
| Valid-looking ticker Massive doesn't know (`ZZZZ`) | Accepted into watchlist; never priced; omitted from SSE; trade returns 400 "not available". |
| Ticker added on Massive | Priced on next poll (≤ 15 s). |
| Ticker removed while a Massive request is in flight | Response for it is discarded (`tracked` check). |
| Bad/missing entitlement API key | `AuthError`/`BadResponse` logged by class name only; backoff to 5 min; `/api/health` shows `consecutive_failures` and `last_error`. Fix `.env`, restart. |
| 429 rate limit | Same backoff path; resets on next success. |
| Market closed | Last trade repeats; cache no-op; SSE heartbeats only. |
| Simulator exception in a step | Logged with traceback; loop continues next tick. |
| Client disconnects | `is_disconnected()` ends the generator within one interval; `CancelledError` re-raised. |
| Simulator restart | Prices reset to seeds (no persistence of simulated prices). Positions keep their real `avg_cost`, so P&L can jump at restart. Accepted for a demo. |
| API key exposure | Key is held only in `MassiveDataSource._api_key`; never logged, never in `status()`, never in error messages. |

---

## 15. Testing

All tests live in `backend/tests/market/`. Run with `cd backend && uv run pytest`.
`asyncio_mode = "auto"` is already set in `pyproject.toml`.

### 15.1 Cache

```python
# file: backend/tests/market/test_cache_v1.py
from app.market.cache import PriceCache


def test_reference_price_carried_over_and_day_change():
    c = PriceCache()
    c.update("AAPL", 100.0, reference_price=95.0)
    u = c.update("AAPL", 101.0)
    assert u.reference_price == 95.0
    assert u.day_change == 6.0
    assert u.direction == "up"


def test_identical_write_is_noop():
    c = PriceCache()
    c.update("AAPL", 100.0, timestamp=1.0)
    v = c.version
    c.update("AAPL", 100.0, timestamp=1.0)
    assert c.version == v


def test_zero_timestamp_is_respected():
    c = PriceCache()
    assert c.update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0


def test_remove_bumps_version_only_when_present():
    c = PriceCache()
    c.update("AAPL", 100.0)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")
    assert c.version == v + 1
```

### 15.2 Simulator

```python
# file: backend/tests/market/test_simulator_v1.py
import numpy as np

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator, SimulatorDataSource, fallback_seed_price


def test_seeded_runs_are_reproducible():
    a = GBMSimulator(["AAPL", "TSLA"], seed=7)
    b = GBMSimulator(["AAPL", "TSLA"], seed=7)
    assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]


def test_full_default_set_has_valid_cholesky():
    sim = GBMSimulator(list(SEED_PRICES), seed=1)
    chol = sim._cholesky
    assert chol is not None and np.allclose(np.diag(chol @ chol.T), 1.0)


def test_unknown_ticker_seed_is_deterministic_and_in_range():
    p = fallback_seed_price("PYPL")
    assert p == fallback_seed_price("PYPL")
    assert 50.0 <= p < 300.0


def test_correlated_tech_moves():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], event_probability=0.0, seed=3)
    rets = {t: [] for t in sim.get_tickers()}
    prev = {t: sim.get_price(t) for t in rets}
    for _ in range(4000):
        for t, p in sim.step().items():
            rets[t].append(np.log(sim._prices[t] / prev[t]))
            prev[t] = sim._prices[t]
    rho_tech = np.corrcoef(rets["AAPL"], rets["MSFT"])[0, 1]
    rho_cross = np.corrcoef(rets["AAPL"], rets["JPM"])[0, 1]
    assert 0.5 < rho_tech < 0.7
    assert 0.2 < rho_cross < 0.4


def test_events_happen_at_expected_rate():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=0)
    before = sim.get_price("AAPL")
    after = sim.step()["AAPL"]
    assert 0.019 < abs(after / before - 1) < 0.051


async def test_source_seeds_cache_and_add_before_start():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.add_ticker("pypl")  # before start: not lost
    await src.start(["AAPL"])
    try:
        assert cache.get_price("AAPL") == 190.0
        assert cache.get("AAPL").reference_price == 190.0
        assert "PYPL" in src.get_tickers()
    finally:
        await src.stop()
```

### 15.3 Massive (real SDK models, no network)

```python
# file: backend/tests/market/test_massive_v1.py
from unittest.mock import MagicMock

from massive.exceptions import AuthError
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource, parse_snapshot, to_unix_seconds


def snap(**over):
    d = {
        "ticker": "AAPL",
        "updated": 1763049600123456789,
        "prevDay": {"c": 190.40},
        "day": {"c": 189.50},
        "min": {"c": 189.10, "t": 1763049540000},
        "lastTrade": {"p": 189.12, "s": 100, "t": 1763049600123456789},
    }
    d.update(over)
    # The SDK's from_dict cannot take explicit nulls: drop keys set to None.
    return TickerSnapshot.from_dict({k: v for k, v in d.items() if v is not None})


def test_parse_uses_last_trade_and_ns_timestamp():
    q = parse_snapshot(snap())
    assert q.price == 189.12
    assert abs(q.timestamp - 1763049600.123) < 1e-3
    assert q.reference_price == 190.40


def test_parse_falls_back_to_minute_bar_then_day():
    assert parse_snapshot(snap(lastTrade=None)).price == 189.10
    assert parse_snapshot(snap(lastTrade=None, min=None)).price == 189.50


def test_parse_rejects_empty():
    assert parse_snapshot(TickerSnapshot.from_dict({"ticker": "ZZZZ"})) is None


def test_timestamp_units():
    assert to_unix_seconds(1_763_049_600) == 1_763_049_600
    assert to_unix_seconds(1_763_049_600_000) == 1_763_049_600
    assert to_unix_seconds(1_763_049_600_000_000_000) == 1_763_049_600


async def test_poll_writes_cache_and_ignores_removed():
    cache = PriceCache()
    client = MagicMock()
    client.get_snapshot_all.return_value = [snap(), snap(ticker="MSFT")]
    src = MassiveDataSource("k", cache, client_factory=lambda **_: client)
    await src.start(["AAPL"])          # MSFT in the response but not tracked
    await src.stop()
    assert cache.get_price("AAPL") == 189.12
    assert cache.get_price("MSFT") is None


async def test_backoff_on_auth_error_and_reset():
    cache = PriceCache()
    client = MagicMock()
    client.get_snapshot_all.side_effect = AuthError("bad key")
    src = MassiveDataSource("k", cache, poll_interval=15, client_factory=lambda **_: client)
    await src.start(["AAPL"])
    await src._poll_once()
    assert src.next_delay() == 60          # 15 * 2^2
    assert src.status()["last_error"] == "AuthError"
    assert "k" not in str(src.status().values())
    client.get_snapshot_all.side_effect = None
    client.get_snapshot_all.return_value = [snap()]
    await src._poll_once()
    assert src.next_delay() == 15
    await src.stop()
```

### 15.4 Factory, tracker, SSE

```python
# file: backend/tests/market/test_wiring_v1.py
import json

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource
from app.market.stream import create_stream_router, generate_price_events
from app.market.tracker import POSITION, WATCHLIST, TickerTracker


def test_factory_selects_by_env():
    c = PriceCache()
    assert isinstance(create_market_data_source(c, {}), SimulatorDataSource)
    assert isinstance(create_market_data_source(c, {"MASSIVE_API_KEY": "  "}), SimulatorDataSource)
    src = create_market_data_source(c, {"MASSIVE_API_KEY": "x", "MASSIVE_POLL_INTERVAL": "0.1"})
    assert isinstance(src, MassiveDataSource) and src._interval == 1.0  # clamped


async def test_tracker_keeps_held_ticker_after_watchlist_removal():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    tracker = TickerTracker(src)
    await src.start(tracker.seed(watchlist=["AAPL"], positions=[]))
    await tracker.acquire("AAPL", POSITION)
    await tracker.release("AAPL", WATCHLIST)
    assert "AAPL" in src.get_tickers()
    await tracker.release("AAPL", POSITION)
    assert "AAPL" not in src.get_tickers() and cache.get("AAPL") is None
    await src.stop()


def test_router_factory_is_reentrant():
    c = PriceCache()
    assert len(create_stream_router(c).routes) == 1
    assert len(create_stream_router(c).routes) == 1


class FakeRequest:
    def __init__(self, polls: int):
        self.polls = polls

    async def is_disconnected(self) -> bool:
        self.polls -= 1
        return self.polls < 0


async def test_sse_sends_snapshot_then_empty_after_removal():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    gen = generate_price_events(cache, FakeRequest(polls=2), interval=0)
    assert await gen.__anext__() == "retry: 1000\n\n"
    first = await gen.__anext__()
    assert json.loads(first.split("data: ", 1)[1])["AAPL"]["price"] == 190.0
    cache.remove("AAPL")
    second = await gen.__anext__()
    assert json.loads(second.split("data: ", 1)[1]) == {}
```

### 15.5 E2E determinism

The E2E compose file sets `MARKET_SIM_SEED=1234` and `LLM_MOCK=true`, so seed
prices and the first ticks are identical on every run. Assertions should still
compare against values read from the UI/API, never against hard-coded prices.

---

## 16. Configuration Summary

| Variable / parameter | Default | Meaning |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty → Massive; empty/absent → simulator |
| `MASSIVE_POLL_INTERVAL` | `15` s (min 1) | Free tier: 15. Paid: 2–5 |
| `MARKET_SIM_INTERVAL` | `0.5` s (min 0.05) | Simulator tick |
| `MARKET_SIM_EVENT_PROB` | `0.001` | Shock probability per ticker per tick |
| `MARKET_SIM_SEED` | unset | Integer → reproducible simulator |
| SSE push interval | `0.5` s | `create_stream_router(interval=...)` |
| SSE heartbeat | `15` s | Comment line when nothing changed |
| SSE `retry:` | `1000` ms | Browser reconnect delay |
| Massive chunk size | 100 tickers/request | `MAX_TICKERS_PER_REQUEST` |
| Massive max backoff | 300 s | `MAX_BACKOFF_S` |

Add the four new optional variables (commented out) to `.env.example`.

---

## 17. Implementation Checklist (from current code to this design)

1. Add `tickers.py` and `tracker.py`.
2. `models.py`: add `reference_price`, `day_change`, `day_change_percent`, extend `to_dict()`.
3. `cache.py`: lock `version`, `snapshot()`, `remove()` bumps version, no-op identical writes, `timestamp is None` check, `reference_price` kwarg.
4. `interface.py`: add `name`, `sync_tickers()`, `status()`.
5. `simulator.py`: per-instance RNG + seed, `add_tickers()` batch, deterministic fallback seeds, eager simulator construction, PD fallback, cache seeding with `reference_price`.
6. `massive_client.py`: `parse_snapshot()` / `to_unix_seconds()` (**fixes the `last_trade.timestamp` bug**), backoff, chunking, in-flight removal guard, `client_factory` for tests, `status()`.
7. `factory.py`: env-driven intervals and seed, `env` parameter for tests.
8. `stream.py`: router inside factory, `snapshot()`, empty snapshot, heartbeat, `id:`, re-raise `CancelledError`.
9. `__init__.py`: export the new names.
10. Update `tests/market/test_massive.py::test_timestamp_conversion`: it asserts on the non-existent `last_trade.timestamp` field (the bug in gap #1). Switch it, and preferably all Massive tests, to real `TickerSnapshot.from_dict(...)` objects as in §15.3. `MagicMock` snapshots hide field-name bugs because every attribute exists.
11. Correct `planning/archive/MASSIVE_API.md` (`day.previous_close` → `prev_day.close`, `last_trade.timestamp` → `last_trade.sip_timestamp` in ns).
12. `.env.example`: document the optional variables.
