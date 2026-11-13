# Market Data Backend — Detailed Design (v1)

**Status:** Authoritative design for `backend/app/market/`. Supersedes `planning/archive/MARKET_DATA_DESIGN.md`, which predates both code reviews.
**Inputs:** `PLAN.md` §6 and §8, `MARKET_DATA_SUMMARY.md`, `MARKET_DATA_REVIEW.md`, `archive/MARKET_INTERFACE.md`, `archive/MARKET_SIMULATOR.md`, `archive/MASSIVE_API.md`, the current code in `backend/app/market/`, and the installed `massive` package models (`massive/rest/models/snapshot.py`, `trades.py`, `aggs.py`).

Every snippet below is complete enough to implement from. Most of it matches the code that is already in the repo. Where this design changes the current code, the section is marked **[CHANGE]**. All changes are listed in §13.

---

## Table of Contents

1. Goals and Non-Goals
2. Architecture
3. File Layout
4. Data Model — `PriceUpdate`
5. Price Cache — `PriceCache`
6. Unified Interface — `MarketDataSource`
7. Seed Data — `seed_prices.py`
8. Simulator — `GBMSimulator` + `SimulatorDataSource`
9. Massive API — `MassiveDataSource`
10. Factory and Configuration
11. SSE Streaming — `/api/stream/prices`
12. Application Integration (lifespan, DI, watchlist, trades, health)
13. Changes vs Current Code
14. Testing Plan
15. Edge Cases and Failure Modes

---

## 1. Goals and Non-Goals

**Goals**

- One abstract interface with two interchangeable implementations: a GBM simulator (default) and a Massive (Polygon.io) REST poller (when `MASSIVE_API_KEY` is set).
- One shared in-memory price cache that is the only place downstream code reads prices from: SSE, portfolio valuation, trade execution, the LLM context.
- Live prices to the browser over SSE at about 500 ms cadence.
- Zero external dependencies in the default (simulator) mode.
- The watchlist can change at runtime (REST or LLM). New tickers get a price immediately where possible.

**Non-Goals**

- Historical bars or candles. Charts and sparklines are built on the frontend from the SSE stream since page load (PLAN §2).
- Order books, bid/ask, volume. Market orders fill at the cached last price.
- Multi-user fan-out. The cache design allows it, but nothing here is per-user.
- WebSockets, including the Massive WebSocket feed. REST polling only (PLAN §6).

---

## 2. Architecture

```
                    ┌──────────────────────────────┐
  MASSIVE_API_KEY?──▶ create_market_data_source()  │
                    └───────┬──────────────┬───────┘
                       no   │              │  yes
                            ▼              ▼
             ┌──────────────────────┐ ┌───────────────────────┐
             │ SimulatorDataSource  │ │ MassiveDataSource     │
             │  └ GBMSimulator      │ │  └ massive.RESTClient │
             │  asyncio task, 0.5 s │ │  asyncio task, 15 s   │
             └──────────┬───────────┘ └──────────┬────────────┘
                        │ cache.update(...)      │ cache.update(...)
                        ▼                        ▼
             ┌───────────────────────────────────────────────┐
             │ PriceCache  (threading.Lock, version counter) │
             └───┬──────────────┬───────────────┬────────────┘
                 │              │               │
                 ▼              ▼               ▼
        SSE /api/stream/   /api/portfolio   /api/portfolio/trade,
        prices (0.5 s poll  valuation,       /api/chat (LLM context
        on version)         snapshots task   and auto-executed trades)
```

Key principles:

1. **Producers write to the cache and consumers read from it.** Nothing downstream calls a data source for a price. The source's only public jobs are lifecycle (`start`/`stop`) and the tracked ticker set (`add_ticker`/`remove_ticker`/`get_tickers`).
2. **One writer at a time.** Exactly one source is active per process. The cache lock protects against the Massive worker thread and the event loop touching the cache at the same moment.
3. **Tickers are normalized at every entry point** (`strip().upper()`), so `"aapl "` and `"AAPL"` are the same ticker everywhere.
4. **The source tracks watchlist ∪ held positions** (see §12.4), so a position stays valued after its ticker leaves the watchlist.

---

## 3. File Layout

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # (API layer) FastAPI app + lifespan — §12
│   └── market/
│       ├── __init__.py         # public exports
│       ├── models.py           # PriceUpdate
│       ├── cache.py            # PriceCache
│       ├── interface.py        # MarketDataSource ABC
│       ├── seed_prices.py      # seed prices, GBM params, correlation constants
│       ├── simulator.py        # GBMSimulator, SimulatorDataSource
│       ├── massive_client.py   # MassiveDataSource
│       ├── factory.py          # create_market_data_source()
│       └── stream.py           # create_stream_router() — SSE
├── tests/market/               # unit + integration tests — §14
└── market_data_demo.py         # Rich terminal demo (dev only)
```

Public API (`app/market/__init__.py`):

```python
from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "create_market_data_source",
    "create_stream_router",
]
```

Dependencies (`backend/pyproject.toml`): `fastapi`, `uvicorn[standard]`, `numpy`, `massive` (core). `rich` is dev-only, for the demo.

---

## 4. Data Model — `PriceUpdate`

`PriceUpdate` is the only type that leaves the market data layer. It is immutable, so a reader can never mutate something the cache still holds.

```python
# app/market/models.py
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    day_open: float | None = None  # Session reference: seed price (sim) / prev close (Massive)

    @property
    def change(self) -> float:
        """Absolute change since the previous update (tick-to-tick)."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def day_change_percent(self) -> float:
        """Daily change % shown in the watchlist (0.0 if no reference)."""
        if not self.day_open:
            return 0.0
        return round((self.price - self.day_open) / self.day_open * 100, 4)

    @property
    def direction(self) -> str:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "day_open": self.day_open,
            "day_change_percent": self.day_change_percent,
        }
```

Design notes:

- **Derived fields are properties.** `change`, `direction` and the percentages can never disagree with `price` and `previous_price`.
- **Two kinds of change.** `change`/`direction` are tick-to-tick and drive the green/red flash. `day_change_percent` is against the session reference and drives the "daily change %" column (PLAN §10).
- **`day_open` meaning by source.** Simulator: the seed price at process start, which acts as a synthetic session open. Massive: the previous day's close (`snap.prev_day.close`), which matches how brokers show daily change.

Example:

```python
>>> u = PriceUpdate("AAPL", 191.20, 191.05, 1731500000.0, day_open=190.0)
>>> u.direction, u.change, u.day_change_percent
('up', 0.15, 0.6316)
>>> u.to_dict()["direction"]
'up'
```

---

## 5. Price Cache — `PriceCache`

```python
# app/market/cache.py
from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Thread-safe latest-price store. One writer (the active source), many readers."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0  # bumped on every update AND every effective remove

    @staticmethod
    def normalize(ticker: str) -> str:
        return ticker.strip().upper()

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        day_open: float | None = None,
    ) -> PriceUpdate:
        ticker = self.normalize(ticker)
        with self._lock:
            ts = time.time() if timestamp is None else timestamp  # 0.0 is a valid ts
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price      # first update -> "flat"
            if day_open is None and prev is not None:
                day_open = prev.day_open                         # carry reference forward
            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
                day_open=round(day_open, 2) if day_open is not None else None,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(self.normalize(ticker))

    def get_price(self, ticker: str) -> float | None:
        u = self.get(ticker)
        return u.price if u else None

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)  # shallow copy; values are immutable

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(self.normalize(ticker), None) is not None:
                self._version += 1     # so SSE clients see the removal

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return self.normalize(ticker) in self._prices
```

**Why `threading.Lock` rather than `asyncio.Lock`?** The Massive client is synchronous and its fetch runs in a worker thread via `asyncio.to_thread`. The cache writes themselves happen back on the loop, but a thread lock keeps the cache safe whichever thread touches it. Critical sections are a dict lookup plus an assignment, so contention is negligible.

**Why a version counter?** The SSE generator compares `cache.version` with the last version it sent. It serializes and pushes only when something changed. With Massive (one write per 15 s) this cuts payloads by about 30x. With the simulator nearly every 500 ms tick changes something, so it costs nothing.

**Rounding.** Prices are stored at 2 dp, the display precision. `previous_price` comes from the rounded stored value, so `direction` matches what the user sees. A side effect is that low-volatility tickers show some `flat` ticks. That is accepted (review item L2).

Usage:

```python
cache = PriceCache()
cache.update("aapl", 190.0, day_open=190.0)   # first update: flat
u = cache.update("AAPL", 190.37)              # day_open carried forward
assert u.direction == "up" and u.day_open == 190.0
assert "AAPL" in cache and cache.get_price(" aapl ") == 190.37
cache.remove("AAPL"); assert cache.get("AAPL") is None
```

---

## 6. Unified Interface — `MarketDataSource`

```python
# app/market/interface.py
from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push updates into a PriceCache on their own schedule.
    Consumers never ask a source for a price; they read the cache.

        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL"])
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        await source.stop()
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates. Seeds the cache before returning where possible.
        Raises RuntimeError if already started."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Idempotent. No cache writes after it returns."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker (normalized). No-op if present. Implementations should
        make a best effort to have a cached price when this returns."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and remove it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers (a copy)."""
```

### Behavioural contract (both implementations must satisfy it)

| Behaviour | Simulator | Massive |
|---|---|---|
| `start()` populates the cache before returning | yes (seed prices) | yes (one synchronous poll), if the API is reachable |
| `start()` twice | `RuntimeError` | `RuntimeError` |
| `add_ticker()` before `start()` | buffered, included at start | stored, polled at start |
| `add_ticker()` yields a price on return | always (deterministic price for unknown symbols) | after an immediate poll, **only if the symbol exists** |
| `remove_ticker()` | drops from sim + cache | drops from list + cache; in-flight results discarded |
| Ticker normalization | `strip().upper()` | `strip().upper()` |
| Update cadence | 0.5 s fixed-rate | 15 s (configurable), with exponential backoff on errors (max 8x) |
| Health | always healthy | `healthy` / `last_error` properties |

Because Massive may never produce a price for a bogus symbol, **"has a cached price" is the canonical validity test** for API routes (§12.3).

**[CHANGE] Optional health on the ABC.** Add a non-abstract default so the health endpoint does not need `isinstance` checks:

```python
class MarketDataSource(ABC):
    ...
    @property
    def healthy(self) -> bool:          # simulator: always True
        return True

    @property
    def last_error(self) -> str | None:
        return None

    @property
    def name(self) -> str:              # "simulator" | "massive", for /api/health
        return type(self).__name__
```

---

## 7. Seed Data — `seed_prices.py`

```python
# app/market/seed_prices.py
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 800.00, "META": 500.00, "JPM": 195.00, "V": 280.00, "NFLX": 600.00,
}

# Annualized GBM parameters: sigma = volatility, mu = drift
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},   # high vol
    "NVDA": {"sigma": 0.40, "mu": 0.08},   # high vol, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},    # low vol (bank)
    "V": {"sigma": 0.17, "mu": 0.04},      # low vol (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}
INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR = 0.3   # cross-sector and unknown tickers
TSLA_CORR = 0.3          # TSLA does its own thing
```

The default watchlist (`AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX`) is owned by the database seed (`backend/db/`), not by this module. `SEED_PRICES` only supplies starting prices.

---

## 8. Simulator

### 8.1 The math

Geometric Brownian Motion, discretized exactly:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z ),   Z ~ N(0,1)
```

- `dt` = 0.5 s as a fraction of a trading year = `0.5 / (252 · 6.5 · 3600)` ≈ `8.48e-8`.
- Per-tick standard deviation of log-returns = `σ·√dt`. For AAPL (σ = 0.22) that is about 0.0064 %, roughly $0.012 at $190. For TSLA (σ = 0.50) it is about $0.036 at $250. Moves are small but visible at 2 dp.
- Over one simulated trading day (46,800 ticks), the standard deviation is `σ·√(1/252)`: about 1.4 % for AAPL and 3.1 % for TSLA. These are realistic intraday ranges.
- Prices can never go negative because `exp` is always positive.

**Correlation.** Build a correlation matrix `C` from the sector constants. Take its Cholesky factor `L` (so `C = L·Lᵀ`). Then `Z_corr = L · Z_indep` has the desired correlation. If tuned constants ever make `C` non-positive-definite, shrink toward the identity (`αC + (1−α)I`, α ∈ {0.5, 0.25, 0.1, 0}), which always succeeds at α = 0.

**Shock events.** On each tick, each ticker has probability `p = 0.001` of a ±2–5 % jump. With 10 tickers at 2 ticks/s that gives about one event every 50 s across the board, which adds drama without changing the long-run distribution much.

### 8.2 `GBMSimulator` (pure, synchronous, no I/O)

```python
# app/market/simulator.py (part 1)
from __future__ import annotations

import asyncio, hashlib, logging, math, random
import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (CORRELATION_GROUPS, CROSS_GROUP_CORR, DEFAULT_PARAMS,
                          INTRA_FINANCE_CORR, INTRA_TECH_CORR, SEED_PRICES,
                          TICKER_PARAMS, TSLA_CORR)

logger = logging.getLogger(__name__)


def _norm(t: str) -> str:
    return t.strip().upper()


def default_price_for(ticker: str) -> float:
    """Deterministic price in [50, 300) for symbols without a seed price.
    Stable across restarts, so a position bought at $X does not jump on reboot."""
    frac = int.from_bytes(hashlib.sha256(ticker.encode()).digest()[:4], "big") / 2**32
    return round(50.0 + frac * 250.0, 2)


class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR

    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT,
                 event_probability: float = 0.001,
                 rng: np.random.Generator | None = None,
                 py_rng: random.Random | None = None) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._rng = rng or np.random.default_rng()      # injectable for reproducible tests
        self._py_rng = py_rng or random.Random()
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}             # full precision internally
        self._day_open: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None
        for t in tickers:
            self._add_ticker_internal(t)
        self._rebuild_cholesky()                         # once, not per ticker

    def step(self) -> dict[str, float]:
        n = len(self._tickers)
        if n == 0:
            return {}
        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z
        sqrt_dt = math.sqrt(self._dt)
        out: dict[str, float] = {}
        for i, t in enumerate(self._tickers):
            mu, sigma = self._params[t]["mu"], self._params[t]["sigma"]
            self._prices[t] *= math.exp((mu - 0.5 * sigma**2) * self._dt + sigma * sqrt_dt * z[i])
            if self._py_rng.random() < self._event_prob:
                shock = self._py_rng.uniform(0.02, 0.05) * self._py_rng.choice([-1, 1])
                self._prices[t] *= 1 + shock
                logger.debug("Shock on %s: %+.1f%%", t, shock * 100)
            out[t] = round(self._prices[t], 2)
        return out

    def add_ticker(self, ticker: str) -> None:
        ticker = _norm(ticker)
        if ticker not in self._prices:
            self._add_ticker_internal(ticker)
            self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        ticker = _norm(ticker)
        if ticker in self._prices:
            self._tickers.remove(ticker)
            for d in (self._prices, self._params, self._day_open):
                d.pop(ticker, None)
            self._rebuild_cholesky()

    def get_price(self, t: str) -> float | None:     return self._prices.get(_norm(t))
    def get_day_open(self, t: str) -> float | None:  return self._day_open.get(_norm(t))
    def get_tickers(self) -> list[str]:              return list(self._tickers)

    # --- internals ---
    def _add_ticker_internal(self, ticker: str) -> None:
        ticker = _norm(ticker)
        if ticker in self._prices:
            return
        start = SEED_PRICES.get(ticker) or default_price_for(ticker)
        self._tickers.append(ticker)
        self._prices[ticker] = start
        self._day_open[ticker] = start
        self._params[ticker] = TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                corr[i, j] = corr[j, i] = self._pairwise_correlation(self._tickers[i], self._tickers[j])
        try:
            self._cholesky = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            logger.warning("Correlation matrix not PD; applying shrinkage")
            self._cholesky = None
            for alpha in (0.5, 0.25, 0.1, 0.0):
                try:
                    self._cholesky = np.linalg.cholesky(alpha * (corr - np.eye(n)) + np.eye(n))
                    break
                except np.linalg.LinAlgError:
                    continue

    @staticmethod
    def _pairwise_correlation(a: str, b: str) -> float:
        if a == "TSLA" or b == "TSLA":
            return TSLA_CORR
        tech, fin = CORRELATION_GROUPS["tech"], CORRELATION_GROUPS["finance"]
        if a in tech and b in tech:
            return INTRA_TECH_CORR
        if a in fin and b in fin:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR
```

Correlation matrix for a subset of the default watchlist (TSLA overrides its "tech" membership):

```
        AAPL  MSFT  TSLA  JPM   V
AAPL    1.0   0.6   0.3   0.3   0.3
MSFT    0.6   1.0   0.3   0.3   0.3
TSLA    0.3   0.3   1.0   0.3   0.3
JPM     0.3   0.3   0.3   1.0   0.5
V       0.3   0.3   0.3   0.5   1.0
```

Rebuilding the matrix is O(n²) plus a Cholesky O(n³). For n < 50 that takes microseconds and only happens on add or remove.

### 8.3 `SimulatorDataSource` (async wrapper)

```python
# app/market/simulator.py (part 2)
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5,
                 event_probability: float = 0.001) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None
        self._pending: list[str] = []          # add_ticker() before start()

    @property
    def name(self) -> str:
        return "simulator"

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError("SimulatorDataSource already started")
        initial = list(dict.fromkeys(_norm(t) for t in [*tickers, *self._pending]))
        self._pending = []
        self._sim = GBMSimulator(tickers=initial, event_probability=self._event_prob)
        for t in self._sim.get_tickers():
            self._seed_cache(t)                 # SSE has data on its first tick
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(initial))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        ticker = _norm(ticker)
        if self._sim is None:
            if ticker not in self._pending:
                self._pending.append(ticker)
            return
        self._sim.add_ticker(ticker)
        if ticker not in self._cache:
            self._seed_cache(ticker)            # price available immediately

    async def remove_ticker(self, ticker: str) -> None:
        ticker = _norm(ticker)
        self._pending = [t for t in self._pending if t != ticker]
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else list(self._pending)

    def _seed_cache(self, ticker: str) -> None:
        price = self._sim.get_price(ticker)
        if price is not None:
            self._cache.update(ticker, price, day_open=self._sim.get_day_open(ticker))

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            try:
                for t, p in self._sim.step().items():
                    self._cache.update(t, p)
            except Exception:
                logger.exception("Simulator step failed")   # never kill the loop
            next_tick += self._interval                       # fixed cadence, no drift
            await asyncio.sleep(max(0.0, next_tick - loop.time()))
```

Concurrency note: `add_ticker`, `remove_ticker` and `_run_loop` all run on the event loop thread. `step()` has no `await`, so the simulator's lists can never be mutated in the middle of a step. No lock is needed.

---

## 9. Massive API — `MassiveDataSource`

### 9.1 Endpoint and fields actually used

One call per poll, covering every tracked ticker:

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,...
→ client.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=[...])
→ list[TickerSnapshot]
```

The `massive` package's model (checked against the installed `massive/rest/models/snapshot.py` and `trades.py`):

```python
class TickerSnapshot:
    ticker: str | None
    day: Agg | None              # today:     open, high, low, close, volume, vwap
    prev_day: Agg | None         # yesterday: close = daily-change reference
    last_trade: LastTrade | None # price, size, sip_timestamp (ns), ...
    last_quote: LastQuote | None
    min: MinuteSnapshot | None
    todays_change: float | None
    todays_change_percent: float | None
    updated: int | None          # ns since epoch

class LastTrade:
    price: float | None
    sip_timestamp: int | None    # JSON "t" — NANOSECONDS. There is NO .timestamp attribute.
    participant_timestamp: int | None
    ...
```

> ⚠️ **[CHANGE] Bug in the current code.** `massive_client.py` reads `snap.last_trade.timestamp / 1000.0`. `LastTrade` has no `timestamp` attribute, so against the real API every snapshot raises `AttributeError` and is skipped with a warning. The cache would stay empty. The unit tests use `MagicMock` snapshots, which accept any attribute, so they don't catch this. `archive/MASSIVE_API.md` is also wrong on two points: it lists `day.previous_close` (that field does not exist; use `prev_day.close`) and millisecond trade timestamps (the v2 snapshot uses nanoseconds). The parsing below fixes all of this. Tests must build snapshots with the real `TickerSnapshot.from_dict(...)` (§14.4).

### 9.2 Parsing: a pure function with fallbacks

Keeping parsing separate from I/O makes it trivially testable against real model objects.

```python
# app/market/massive_client.py (parsing helpers)
import time

def _positive(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and x > 0 else None


def _ns_or_ms_to_seconds(ts) -> float | None:
    """Massive v2 snapshot timestamps are ns; tolerate ms/s defensively by magnitude."""
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    if ts > 1e17:   # nanoseconds (~1.7e18 today)
        return ts / 1e9
    if ts > 1e14:   # microseconds
        return ts / 1e6
    if ts > 1e11:   # milliseconds
        return ts / 1e3
    return float(ts)


def parse_snapshot(snap) -> tuple[str, float, float, float | None] | None:
    """TickerSnapshot -> (ticker, price, timestamp_s, prev_close) or None if unusable.

    Price preference: last trade → today's close (minute-aggregated) → previous close.
    The fallbacks keep the UI populated pre-market / on days the snapshot was reset.
    """
    ticker = getattr(snap, "ticker", None)
    if not ticker:
        return None
    lt, day, prev = snap.last_trade, snap.day, snap.prev_day
    price = (_positive(getattr(lt, "price", None))
             or _positive(getattr(day, "close", None))
             or _positive(getattr(prev, "close", None)))
    if price is None:
        return None
    ts = (_ns_or_ms_to_seconds(getattr(lt, "sip_timestamp", None))
          or _ns_or_ms_to_seconds(getattr(snap, "updated", None))
          or time.time())
    return ticker.strip().upper(), price, ts, _positive(getattr(prev, "close", None))
```

Example: a real-shaped payload goes through the real model.

```python
from massive.rest.models import TickerSnapshot

raw = {
    "ticker": "AAPL",
    "day":     {"o": 189.1, "h": 191.9, "l": 188.7, "c": 191.2, "v": 5.1e7},
    "prevDay": {"o": 187.0, "h": 190.2, "l": 186.5, "c": 189.5, "v": 6.0e7},
    "lastTrade": {"p": 191.23, "s": 100, "t": 1731500000123456789, "x": 4},
    "todaysChangePerc": 0.91,
    "updated": 1731500000123456789,
}
snap = TickerSnapshot.from_dict(raw)
assert parse_snapshot(snap) == ("AAPL", 191.23, 1731500000.1234567, 189.5)
```

### 9.3 The data source

```python
# app/market/massive_client.py
from __future__ import annotations

import asyncio, logging
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)
MAX_BACKOFF_MULTIPLIER = 8


class MassiveDataSource(MarketDataSource):
    """Polls the Massive snapshot endpoint for all tracked tickers in one request.

    Free tier: 5 req/min → poll_interval=15s (default). Paid: 2–5s.
    """

    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._client: RESTClient | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()             # serializes poll loop vs add_ticker polls
        self._consecutive_failures = 0
        self._last_error: str | None = None

    name = "massive"

    @property
    def healthy(self) -> bool:
        return self._consecutive_failures == 0

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError("MassiveDataSource already started")
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys([*self._tickers, *(t.strip().upper() for t in tickers)]))
        await self._poll_once()                  # cache populated before app serves traffic
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, %.1fs (first poll %s)",
                    len(self._tickers), self._interval, "ok" if self.healthy else "FAILED")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task, self._client = None, None

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        if ticker in self._tickers:
            return
        self._tickers = [*self._tickers, ticker]  # rebind, never mutate in place
        if self._client:
            await self._poll_once()               # immediate price (costs 1 API call)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals ---
    async def _poll_loop(self) -> None:
        while True:
            mult = min(2 ** self._consecutive_failures, MAX_BACKOFF_MULTIPLIER) \
                if self._consecutive_failures else 1
            await asyncio.sleep(self._interval * mult)
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        async with self._lock:
            requested = list(self._tickers)       # snapshot on the loop thread
            try:
                snaps = await asyncio.to_thread(self._fetch_snapshots, requested)
            except Exception as e:                # 401/403/429/5xx/network
                self._consecutive_failures += 1
                self._last_error = f"{type(e).__name__}: {e}"
                logger.error("Massive poll failed (%d in a row): %s",
                             self._consecutive_failures, self._last_error)
                return
            self._consecutive_failures, self._last_error = 0, None
            live = set(self._tickers)             # current set, after the await
            updated = 0
            for snap in snaps:
                parsed = parse_snapshot(snap)
                if parsed is None:
                    logger.warning("Unusable snapshot for %s", getattr(snap, "ticker", "?"))
                    continue
                ticker, price, ts, prev_close = parsed
                if ticker not in live:            # removed while request was in flight
                    continue
                self._cache.update(ticker, price, timestamp=ts, day_open=prev_close)
                updated += 1
            missing = set(requested) - {s.ticker for s in snaps if getattr(s, "ticker", None)}
            if missing:
                logger.info("Massive returned no data for: %s", ", ".join(sorted(missing)))
            logger.debug("Massive poll: %d/%d updated", updated, len(requested))

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Blocking HTTP call — runs in a worker thread."""
        return self._client.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=tickers)
```

### 9.4 Rate-limit budget

| Event | API calls |
|---|---|
| `start()` | 1 |
| Each poll interval | 1 (all tickers in one request) |
| Each `add_ticker()` | 1 |

On the free tier (5/min) at a 15 s interval the loop uses 4 calls/min, which leaves 1/min for adds. Several adds in a burst (for example the LLM adding 3 tickers in one chat turn) can trigger a 429. The poller then backs off (30 s, 60 s, 120 s, capped at 8x) and recovers by itself. To soften this, the chat handler should apply all watchlist changes first and then call `add_ticker` for each. Each call is serialized by `_lock`, and the second and later polls are cheap no-ops if the tickers already have prices.

**[CHANGE] Optional coalescing improvement:** have `add_ticker` skip the immediate poll if one completed less than 1 s ago, and instead schedule a single poll 0.5 s later. This is not required for the course scope.

### 9.5 Error semantics

| Failure | Behaviour |
|---|---|
| 401 bad key / 403 plan | logged every attempt with backoff; `healthy=False`; cache stays empty; `/api/health` reports degraded |
| 429 rate limit | backoff ×2 per consecutive failure, cap ×8 |
| 5xx / network | `massive` retries internally (3x), then backoff as above |
| Unknown symbol | silently absent from the response → no cached price → API routes reject (§12.3) |
| Market closed | `last_trade.price` is the last (possibly after-hours) trade; prices stop changing; SSE sends keepalives |
| Snapshot reset (00:00–04:00 ET) | `last_trade`/`day` may be empty → falls back to `prev_day.close` |

---

## 10. Factory and Configuration

```python
# app/market/factory.py
import logging, os
from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, default)))
    except ValueError:
        logger.warning("Invalid %s; using %s", name, default)
        return default


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY non-empty → Massive, else simulator. Returns an UNSTARTED source."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = _float_env("MASSIVE_POLL_INTERVAL", 15.0, minimum=1.0)   # [CHANGE] optional
        logger.info("Market data source: Massive (poll %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)
    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

The API key is never logged, echoed in errors, or returned by any endpoint. `last_error` comes from the exception text, which the `massive` client builds from the HTTP status and body and which does not include the key. Even so, the health endpoint only exposes a short, sanitized status (§12.5).

| Setting | Where | Default | Notes |
|---|---|---|---|
| `MASSIVE_API_KEY` | env | `""` | selects source |
| `MASSIVE_POLL_INTERVAL` | env **[CHANGE]** | `15` s | min 1 s; paid tiers can use 2–5 |
| `update_interval` | `SimulatorDataSource` | 0.5 s | |
| `event_probability` | `GBMSimulator` | 0.001 | per ticker per tick |
| `dt` | `GBMSimulator` | ≈8.48e-8 | 0.5 s / trading year |
| SSE poll interval | `_generate_events` | 0.5 s | |
| SSE keepalive | `_generate_events` | 15 s | comment line |
| SSE `retry:` | `_generate_events` | 1000 ms | browser reconnect delay |

---

## 11. SSE Streaming — `GET /api/stream/prices`

### 11.1 Implementation

```python
# app/market/stream.py
from __future__ import annotations

import asyncio, json, logging, time
from collections.abc import AsyncGenerator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from .cache import PriceCache

logger = logging.getLogger(__name__)


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])   # new router per call

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


async def _generate_events(price_cache: PriceCache, request: Request,
                           interval: float = 0.5,
                           keepalive_interval: float = 15.0) -> AsyncGenerator[str, None]:
    yield "retry: 1000\n\n"
    last_version, last_sent = -1, time.monotonic()
    try:
        while True:
            if await request.is_disconnected():
                break
            v = price_cache.version
            if v != last_version:
                last_version = v
                data = {t: u.to_dict() for t, u in price_cache.get_all().items()}
                yield f"data: {json.dumps(data)}\n\n"      # may be "{}" after last removal
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= keepalive_interval:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise                                               # never swallow cancellation
```

### 11.2 Wire contract (frontend depends on this)

Each `data:` event is **a full snapshot of every tracked ticker**, keyed by symbol. It is not a delta. A ticker missing from an event has been removed. The PLAN lists ticker, price, previous price, timestamp and direction per event; the extra fields are additive.

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.12,"previous_price":190.05,"timestamp":1731500000.51,"change":0.07,"change_percent":0.0368,"direction":"up","day_open":190.0,"day_change_percent":0.0632},"MSFT":{...}}

data: {...}

: keepalive

```

TypeScript type for the frontend:

```ts
export interface PriceTick {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;            // unix seconds (float)
  change: number;               // tick-to-tick
  change_percent: number;
  direction: "up" | "down" | "flat";
  day_open: number | null;
  day_change_percent: number;   // watchlist "daily change %"
}
export type PriceSnapshot = Record<string, PriceTick>;

const es = new EventSource("/api/stream/prices");
es.onmessage = (e) => {
  const snap: PriceSnapshot = JSON.parse(e.data);
  for (const t of Object.values(snap)) {
    // Flash only on real ticks: skip if timestamp unchanged since last seen for this ticker
  }
};
es.onerror = () => setStatus(es.readyState === EventSource.CONNECTING ? "reconnecting" : "disconnected");
es.onopen  = () => setStatus("connected");
```

**Frontend flash rule.** Because every event carries every ticker, the frontend must decide per ticker whether something new arrived. Compare `timestamp` (or `price`) with the last value it saw for that ticker, and flash only when it differs, using `direction` for the colour. Otherwise every ticker would re-flash on every event.

### 11.3 Why poll-and-push rather than an event queue?

The cache already holds the only state that matters (the latest price). Polling `version` every 500 ms keeps the generator stateless, gives a bounded rate per client no matter how fast producers write, and needs no per-client queues or back-pressure handling. In the worst case an update reaches the client 500 ms after it was produced, which is at the simulator's own cadence.

---

## 12. Application Integration

This section is for the API layer (`app/main.py`, routes). The market package itself does not depend on the database.

### 12.1 Lifespan

```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.market import PriceCache, create_market_data_source, create_stream_router
from app.db import init_db, get_watchlist_tickers, get_position_tickers   # owned by DB layer


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                                           # lazy create + seed (PLAN §7)
    cache = PriceCache()
    source = create_market_data_source(cache)
    tracked = sorted(set(get_watchlist_tickers()) | set(get_position_tickers()))
    await source.start(tracked)
    app.state.price_cache = cache
    app.state.market_source = source
    snapshot_task = asyncio.create_task(portfolio_snapshot_loop(app))   # 30 s, PLAN §7
    try:
        yield
    finally:
        snapshot_task.cancel()
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)
```

The stream router only needs the cache. To register it at import time (before lifespan has run), have a tiny dependency resolve the cache per request:

```python
# Alternative that avoids include_router inside lifespan:
from fastapi import Depends, Request

def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache

def get_market_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
```

Simplest working option: create the `PriceCache` at module level in `main.py`, call `app.include_router(create_stream_router(cache))` at import time, and have the lifespan reuse that same cache. Do not call `include_router` inside lifespan. Routes added after startup work in current Starlette, but the behaviour is not guaranteed and hides the route from OpenAPI.

```python
price_cache = PriceCache()
app = FastAPI(title="FinAlly", lifespan=lifespan)       # lifespan uses the module-level cache
app.include_router(create_stream_router(price_cache))
# ... API routers ..., then mount static files LAST so /api/* wins:
app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

### 12.2 Ticker validation helper

```python
import re
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")   # AAPL, BRK.B, RDS-A

def clean_ticker(raw: str) -> str:
    t = raw.strip().upper()
    if not TICKER_RE.fullmatch(t):
        raise HTTPException(422, f"Invalid ticker symbol: {raw!r}")
    return t
```

### 12.3 Watchlist add (REST or LLM)

```python
@router.post("/api/watchlist", status_code=201)
async def add_watchlist(body: WatchlistAdd,
                        cache: PriceCache = Depends(get_price_cache),
                        source: MarketDataSource = Depends(get_market_source)):
    ticker = clean_ticker(body.ticker)
    await source.add_ticker(ticker)          # sim: instant price; Massive: immediate poll
    if ticker not in cache:                  # Massive did not recognise the symbol
        if not db.has_position(ticker):
            await source.remove_ticker(ticker)
        raise HTTPException(404, f"No market data for {ticker}")
    db.add_watchlist(ticker)                 # idempotent (UNIQUE(user_id, ticker))
    return {"ticker": ticker, **cache.get(ticker).to_dict()}
```

If Massive is unhealthy (for example rate-limited), the add fails with 404 even for a valid symbol. Use a clearer status in that case: `if not source.healthy: raise HTTPException(503, "Market data temporarily unavailable")`.

### 12.4 Watchlist remove: keep tracking held positions

```python
@router.delete("/api/watchlist/{ticker}")
async def remove_watchlist(ticker: str, source: MarketDataSource = Depends(get_market_source)):
    ticker = clean_ticker(ticker)
    db.remove_watchlist(ticker)
    if not db.has_position(ticker):          # still held → keep pricing it
        await source.remove_ticker(ticker)
    return {"ticker": ticker, "removed": True}
```

After a full **sell** that closes a position, the trade route should do the mirror check: if the ticker is no longer on the watchlist, call `source.remove_ticker(ticker)`. A general helper keeps everything consistent:

```python
async def sync_tracked(source: MarketDataSource) -> None:
    want = set(db.get_watchlist_tickers()) | set(db.get_position_tickers())
    have = set(source.get_tickers())
    for t in want - have: await source.add_ticker(t)
    for t in have - want: await source.remove_ticker(t)
```

Note: the SSE stream contains every **tracked** ticker. A held-but-unwatched ticker therefore appears in the stream. The frontend's watchlist panel should render the tickers from `GET /api/watchlist` and look prices up in the snapshot. It should not render every key in the snapshot.

### 12.5 Trade execution reads the cache

```python
price = cache.get_price(ticker)
if price is None:
    raise HTTPException(400, f"No price available for {ticker} yet; try again shortly")
# fill at `price` (market order, instant fill, no fees) — portfolio logic is out of scope here
```

Trades by the LLM's structured output go through exactly the same function, so the same validation applies (PLAN §9).

### 12.6 Health endpoint

```python
@app.get("/api/health")
async def health(request: Request):
    src = request.app.state.market_source
    return {
        "status": "ok" if src.healthy else "degraded",
        "market_data": {
            "source": src.name,                     # "simulator" | "massive"
            "healthy": src.healthy,
            "tickers": len(src.get_tickers()),
            "cached": len(request.app.state.price_cache),
        },
    }
```

Return HTTP 200 even when the status is degraded, so Docker health checks don't restart a container whose only problem is a bad market-data key.

---

## 13. Changes vs Current Code

| # | Change | File | Priority |
|---|---|---|---|
| C1 | Parse `last_trade.sip_timestamp` (ns), not the nonexistent `.timestamp` (ms); extract `parse_snapshot()` with price fallbacks `last_trade.price → day.close → prev_day.close` | `massive_client.py` | **High: Massive mode yields no prices without this** |
| C2 | Rebuild Massive test fixtures with `TickerSnapshot.from_dict()` instead of `MagicMock`, so tests fail on wrong attribute names | `tests/market/test_massive.py` | High |
| C3 | Correct `archive/MASSIVE_API.md`: `prev_day.close` (not `day.previous_close`), timestamps in ns | docs | Medium |
| C4 | Non-abstract `healthy` / `last_error` / `name` on `MarketDataSource` | `interface.py`, `simulator.py`, `massive_client.py` | Medium |
| C5 | `MASSIVE_POLL_INTERVAL` env var | `factory.py`, `.env.example` | Low |
| C6 | Log symbols missing from a Massive response | `massive_client.py` | Low |
| C7 | Optional add-poll coalescing (§9.4) | `massive_client.py` | Low |
| C8 | Update `backend/CLAUDE.md` `PriceCache.update` signature (add `day_open`) and the `PriceUpdate` fields | docs | Low |

Everything else in §§4–11 matches the current implementation.

---

## 14. Testing Plan

Run with `cd backend && uv run --extra dev pytest -q --cov=app`. Use `asyncio_mode = "auto"`.

### 14.1 Model and cache

```python
def test_first_update_is_flat_and_day_open_carries():
    c = PriceCache()
    a = c.update("aapl", 190.0, day_open=190.0)
    b = c.update("AAPL", 191.0)
    assert a.direction == "flat" and b.direction == "up"
    assert b.day_open == 190.0 and b.day_change_percent == pytest.approx(0.5263, abs=1e-4)

def test_remove_bumps_version_only_when_present():
    c = PriceCache(); c.update("X", 1.0); v = c.version
    c.remove("Y"); assert c.version == v
    c.remove("x"); assert c.version == v + 1

def test_zero_timestamp_is_honoured():
    assert PriceCache().update("X", 1.0, timestamp=0.0).timestamp == 0.0
```

### 14.2 Simulator math (statistical; reproducible through injected RNGs)

```python
def test_log_return_std_matches_sigma_sqrt_dt():
    sim = GBMSimulator(["AAPL"], event_probability=0.0, rng=np.random.default_rng(42))
    p = [sim.get_price("AAPL")]
    for _ in range(20_000):
        sim.step(); p.append(sim.get_price("AAPL"))
    r = np.diff(np.log(p))
    expected = 0.22 * math.sqrt(GBMSimulator.DEFAULT_DT)
    assert r.std() == pytest.approx(expected, rel=0.05)

def test_tech_pair_correlation_near_0_6():
    sim = GBMSimulator(["AAPL", "MSFT"], event_probability=0.0, rng=np.random.default_rng(1))
    a, m = [], []
    for _ in range(20_000):
        pa, pm = sim.get_price("AAPL"), sim.get_price("MSFT"); sim.step()
        a.append(math.log(sim.get_price("AAPL") / pa)); m.append(math.log(sim.get_price("MSFT") / pm))
    assert np.corrcoef(a, m)[0, 1] == pytest.approx(0.6, abs=0.05)

def test_unknown_ticker_price_is_deterministic():
    assert default_price_for("ZZZZ") == default_price_for("ZZZZ")
    assert 50 <= default_price_for("ZZZZ") < 300

def test_full_default_watchlist_cholesky_ok():
    sim = GBMSimulator(list(SEED_PRICES)); assert sim._cholesky is not None
```

### 14.3 Simulator source lifecycle

```python
async def test_add_before_start_is_buffered_and_seeded():
    c = PriceCache(); s = SimulatorDataSource(c, update_interval=0.01)
    await s.add_ticker("pypl"); await s.start(["AAPL"])
    assert {"AAPL", "PYPL"} <= set(c.get_all()); await s.stop()

async def test_double_start_raises():
    s = SimulatorDataSource(PriceCache()); await s.start([])
    with pytest.raises(RuntimeError): await s.start([])
    await s.stop()
```

### 14.4 Massive, using real model objects

```python
from massive.rest.models import TickerSnapshot

def snap(ticker, price=None, t_ns=1_731_500_000_000_000_000, prev=100.0, day_close=None):
    d = {"ticker": ticker, "prevDay": {"c": prev}}
    if price is not None: d["lastTrade"] = {"p": price, "t": t_ns}
    if day_close is not None: d["day"] = {"c": day_close}
    return TickerSnapshot.from_dict(d)

def test_parse_uses_sip_timestamp_ns():
    assert parse_snapshot(snap("aapl", 101.0)) == ("AAPL", 101.0, 1_731_500_000.0, 100.0)

def test_parse_falls_back_to_day_then_prev_close():
    assert parse_snapshot(snap("X", day_close=99.0))[1] == 99.0
    assert parse_snapshot(snap("X"))[1] == 100.0

async def test_poll_discards_ticker_removed_in_flight(monkeypatch):
    c = PriceCache(); s = MassiveDataSource("k", c); s._client = object()
    s._tickers = ["AAPL", "MSFT"]
    def fetch(tickers):
        s._tickers = ["AAPL"]                       # simulate concurrent remove
        return [snap("AAPL", 1.0), snap("MSFT", 2.0)]
    monkeypatch.setattr(s, "_fetch_snapshots", fetch)
    await s._poll_once()
    assert "AAPL" in c and "MSFT" not in c

async def test_failure_sets_unhealthy_then_recovers(monkeypatch):
    c = PriceCache(); s = MassiveDataSource("k", c); s._client = object(); s._tickers = ["AAPL"]
    monkeypatch.setattr(s, "_fetch_snapshots", lambda t: (_ for _ in ()).throw(RuntimeError("429")))
    await s._poll_once(); assert not s.healthy and "429" in s.last_error
    monkeypatch.setattr(s, "_fetch_snapshots", lambda t: [snap("AAPL", 5.0)])
    await s._poll_once(); assert s.healthy and c.get_price("AAPL") == 5.0
```

Optional live smoke test, skipped unless a key is present (never commit keys):

```python
@pytest.mark.skipif(not os.getenv("MASSIVE_API_KEY"), reason="no key")
async def test_live_snapshot():
    c = PriceCache(); s = MassiveDataSource(os.environ["MASSIVE_API_KEY"], c)
    await s.start(["AAPL"]); await s.stop()
    assert c.get_price("AAPL") and c.get("AAPL").day_open
```

### 14.5 SSE

```python
class FakeRequest:
    def __init__(self, n): self.n, self.client = n, None
    async def is_disconnected(self):
        self.n -= 1; return self.n < 0

async def test_sse_emits_retry_then_snapshot_then_suppresses_unchanged():
    c = PriceCache(); c.update("AAPL", 1.0)
    out = [e async for e in _generate_events(c, FakeRequest(3), interval=0)]
    assert out[0] == "retry: 1000\n\n"
    assert out[1].startswith("data: ") and json.loads(out[1][6:])["AAPL"]["price"] == 1.0
    assert len(out) == 2                            # no change → no further data events

async def test_sse_emits_empty_after_last_removal(): ...
async def test_sse_keepalive_when_idle(): ...      # keepalive_interval=0
def test_create_stream_router_returns_independent_routers(): ...
```

### 14.6 E2E (in `test/`, `LLM_MOCK=true`, simulator mode)

- On page load the SSE delivers the 10 default tickers within 1 s, and prices change within 2 s.
- Adding `PYPL` shows it in the next SSE event. Removing it drops it from the next event.
- Kill and restart the backend: the status dot goes yellow, then green, and prices resume.

---

## 15. Edge Cases and Failure Modes

| Case | Behaviour |
|---|---|
| Empty watchlist at start | Both sources start fine with no tickers. SSE sends `{}` once, then keepalives. |
| Watchlist removal while a position is held | Ticker stays tracked (§12.4). The frontend hides it from the watchlist panel but it still values the position. |
| Trade on a ticker without a cached price | 400 with a clear message (§12.5). |
| Simulator: any symbol "exists" | Deterministic price in $50–$300 and default params. Accepted for a demo. Validation is by regex only. |
| Massive: invalid key | `healthy=False`, backoff, empty cache. `/api/health` reports `degraded`. The UI shows no prices. Log message: "Massive poll failed … 401". |
| Massive: free-tier 429 on burst adds | Backoff and auto-recover (§9.4). |
| Container restart (simulator) | Prices restart from seed prices. `day_open` resets. Positions keep their `avg_cost`, so unrealized P&L jumps to `seed − avg_cost`. Accepted. Persisting last prices is out of scope. |
| Clock skew (Massive) | Timestamps come from the exchange (SIP). The frontend should use arrival order for sparklines, not compare timestamps against the browser clock. |
| Many SSE clients | Each client is one coroutine doing an O(n) JSON dump per change. Trivial at n ≈ 10–50 tickers. |
| Shutdown | Lifespan `finally` cancels the snapshot task, then `await source.stop()`. SSE generators are cancelled by uvicorn and re-raise `CancelledError`. |
