# Market Data Backend — Detailed Design (v1)

Implementation-ready design for the FinAlly market data subsystem: the **unified interface**, the **shared price cache**, the **GBM simulator**, the **Massive (Polygon.io) REST poller**, the **SSE stream**, and how the rest of the backend plugs into them.

This document supersedes `planning/archive/MARKET_DATA_DESIGN.md`. It keeps that design's overall shape (strategy pattern, push-into-cache, SSE reads the cache), which already exists in `backend/app/market/`. It also fixes the defects found while re-reading that code against `PLAN.md` and against the installed `massive==2.2.0` SDK. Section 15 lists every change from the current code, so an agent can treat this document as a patch plan as well as a from-scratch spec.

All code lives under `backend/app/market/` unless stated otherwise.

---

## Table of Contents

1. [Requirements (from PLAN.md)](#1-requirements)
2. [Architecture](#2-architecture)
3. [File Layout](#3-file-layout)
4. [Ticker Normalization — `tickers.py`](#4-ticker-normalization)
5. [Data Model — `models.py`](#5-data-model)
6. [Price Cache — `cache.py`](#6-price-cache)
7. [Unified Interface — `interface.py`](#7-unified-interface)
8. [Seed Data & Parameters — `seed_prices.py`](#8-seed-data--parameters)
9. [GBM Simulator — `simulator.py`](#9-gbm-simulator)
10. [Massive API Source — `massive_client.py`](#10-massive-api-source)
11. [Factory & Configuration — `factory.py`](#11-factory--configuration)
12. [SSE Stream — `stream.py`](#12-sse-stream)
13. [Integration with the Rest of the Backend](#13-integration)
14. [Testing](#14-testing)
15. [Changes vs. Current Implementation](#15-changes-vs-current-implementation)
16. [Configuration Reference](#16-configuration-reference)

---

## 1. Requirements

Distilled from `PLAN.md` §2, §5, §6, §8, §10, §12:

| # | Requirement | Where satisfied |
|---|---|---|
| R1 | Two market data implementations behind one interface; downstream code is source-agnostic | §7, §9, §10 |
| R2 | Source is selected by `MASSIVE_API_KEY` (set and non-empty → Massive, else simulator) | §11 |
| R3 | Simulator: GBM, ~500 ms ticks, per-ticker drift/vol, correlated moves, random 2–5 % events, realistic seeds | §9 |
| R4 | Massive: REST polling (not WebSocket), one call for all tickers, 15 s on free tier, 2–15 s on paid tiers | §10 |
| R5 | Shared in-memory cache: latest price, previous price, timestamp per ticker | §6 |
| R6 | `GET /api/stream/prices` SSE, ~500 ms cadence, event has ticker, price, previous price, timestamp, direction | §12 |
| R7 | Watchlist shows **daily change %** | §5 (`reference_price`), §9, §10 |
| R8 | Tickers can be added/removed at runtime (manual or via LLM) | §7, §13.3 |
| R9 | Trades fill instantly at the current cached price | §13.4 |
| R10 | Unit tests: valid prices, GBM math, Massive parsing, interface conformance | §14 |

---

## 2. Architecture

```
                    ┌───────────────────────────────┐
  MASSIVE_API_KEY → │ create_market_data_source()   │  (factory.py)
                    └──────────────┬────────────────┘
                                   │ returns one of
             ┌─────────────────────┴─────────────────────┐
             ▼                                           ▼
   SimulatorDataSource                          MassiveDataSource
   ├─ GBMSimulator (numpy, Cholesky)            ├─ massive.RESTClient (sync, in thread)
   └─ asyncio task, every 0.5 s                 └─ asyncio task, every 15 s (configurable)
             │  cache.update(...)                        │  cache.update(...)
             └─────────────────────┬─────────────────────┘
                                   ▼
                     PriceCache  (thread-safe, versioned)
                                   │
       ┌───────────────┬───────────┼────────────────┬──────────────────┐
       ▼               ▼           ▼                ▼                  ▼
  SSE /api/stream   GET /api/    POST /api/      GET /api/         LLM chat context
  /prices (0.5 s)   watchlist    portfolio/trade portfolio         (prices for prompt)
```

**Principles**

1. **Push into the cache, never pull from the source.** A source's only output is `PriceCache.update()`. No consumer ever calls the source to get a price. That separates the timing: the simulator ticks every 0.5 s, Massive every 15 s, and SSE reads every 0.5 s regardless.
2. **One writer at a time.** Exactly one `MarketDataSource` is alive per process. The cache still uses a real `threading.Lock`, because Massive parsing runs on the event loop but the HTTP call runs in `asyncio.to_thread`, and future code may write from threads.
3. **`PriceUpdate` is the only type that leaves the package.** Everything downstream works with `PriceUpdate` or its `to_dict()` form.
4. **The source tracks a set of tickers; the backend decides what that set is.** The market package does not read the database. The backend computes "watchlist ∪ open positions" and reconciles it into the source (§13.3).

### Timing

| Event | Simulator | Massive (free) | Massive (paid) |
|---|---|---|---|
| Source writes cache | every 0.5 s | every 15 s | every 2–5 s |
| SSE checks cache | every 0.5 s | every 0.5 s | every 0.5 s |
| SSE sends `data:` event | when `version` changed (≈ every tick) | ≈ every 15 s | ≈ every 2–5 s |
| SSE sends `: heartbeat` comment | rarely | every 15 s of silence | rarely |

---

## 3. File Layout

```
backend/app/market/
  __init__.py          # Public re-exports (see below)
  tickers.py           # NEW: normalize_ticker(), TickerError
  models.py            # PriceUpdate (adds reference_price / day change)
  cache.py             # PriceCache (versioned, thread-safe)
  interface.py         # MarketDataSource ABC
  seed_prices.py       # Seed prices, GBM params, sectors & correlations
  simulator.py         # GBMSimulator (vectorized) + SimulatorDataSource
  massive_client.py    # parse_snapshot() + MassiveDataSource
  factory.py           # MarketConfig.from_env() + create_market_data_source()
  stream.py            # create_stream_router() + price_event_stream()
  tracking.py          # NEW: sync_tracked_tickers() reconciliation helper
```

**`__init__.py`**

```python
"""Market data subsystem for FinAlly.

Downstream code should import only from here:

    from app.market import PriceCache, PriceUpdate, MarketDataSource, ...
"""

from .cache import PriceCache
from .factory import MarketConfig, create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router
from .tickers import TickerError, normalize_ticker
from .tracking import sync_tracked_tickers

__all__ = [
    "MarketConfig",
    "MarketDataSource",
    "PriceCache",
    "PriceUpdate",
    "TickerError",
    "create_market_data_source",
    "create_stream_router",
    "normalize_ticker",
    "sync_tracked_tickers",
]
```

---

## 4. Ticker Normalization

**File: `tickers.py`** (new)

Today the Massive source upper-cases tickers but the simulator does not. So `add_ticker("aapl")` creates a second `aapl` series in the simulator. One normalizer, used by both sources and by the API layer, removes the inconsistency.

```python
"""Ticker symbol normalization and validation."""

from __future__ import annotations

import re

# US equity symbols: 1-5 letters, optionally a class suffix like BRK.B / BF-B.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.-][A-Z]{1,2})?$")


class TickerError(ValueError):
    """Raised for a syntactically invalid ticker symbol."""


def normalize_ticker(raw: str) -> str:
    """Upper-case, strip, and validate a ticker symbol.

    >>> normalize_ticker(" aapl ")
    'AAPL'
    >>> normalize_ticker("brk.b")
    'BRK.B'
    """
    ticker = (raw or "").strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise TickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker
```

The API layer converts `TickerError` to HTTP 400. Inside the sources, `normalize_ticker` is applied defensively on every public entry point.

---

## 5. Data Model

**File: `models.py`**

`PriceUpdate` keeps its current tick-level fields and gains one optional field, `reference_price`. This is the price that "daily change %" is measured against (requirement R7):

- **Simulator:** the seed price (the price at session start).
- **Massive:** the previous trading day's close (`prev_day.close` in the snapshot).

```python
"""Data models for market data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of one ticker's price at one point in time.

    Tick-level fields (price vs previous_price) drive the green/red flash.
    Day-level fields (price vs reference_price) drive "daily change %".
    """

    ticker: str
    price: float
    previous_price: float
    timestamp: float                       # Unix seconds (float)
    reference_price: float | None = None   # Prior close / session open

    # --- tick-level -------------------------------------------------------

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> Direction:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    # --- day-level --------------------------------------------------------

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
        """The single serialization used by SSE and REST responses."""
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
>>> u = PriceUpdate("AAPL", 191.20, 191.05, 1762790400.5, reference_price=190.00)
>>> u.direction, u.change, u.day_change_percent
('up', 0.15, 0.6316)
>>> u.to_dict()["day_change"]
1.2
```

Design notes:
- `frozen=True` makes it safe to share between the event loop and threads without copying.
- `slots=True` keeps memory small; we create about 20 per second.
- Derived values are properties, so they can never disagree with `price`.
- `timestamp` has **no default**. The cache always supplies one, which avoids hidden `time.time()` calls in tests.

---

## 6. Price Cache

**File: `cache.py`**

```python
"""Thread-safe, versioned, in-memory price cache."""

from __future__ import annotations

import math
import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest PriceUpdate per ticker.

    Writers: exactly one MarketDataSource.
    Readers: SSE stream, portfolio valuation, trade execution, LLM context.

    `version` increases monotonically on every mutation (update OR remove), so a
    reader can cheaply tell whether anything changed since it last looked.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._references: dict[str, float] = {}
        self._lock = Lock()
        self._version = 0

    # --- writes -----------------------------------------------------------

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        reference_price: float | None = None,
    ) -> PriceUpdate:
        """Record a new price. previous_price is taken from the prior entry.

        reference_price is sticky: pass it once (or whenever it changes, e.g. a new
        trading day) and later updates reuse it.
        """
        if price is None or not math.isfinite(price) or price <= 0:
            raise ValueError(f"Invalid price for {ticker}: {price!r}")

        with self._lock:
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            if reference_price is not None and reference_price > 0:
                self._references[ticker] = reference_price
            ref = self._references.get(ticker)

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=time.time() if timestamp is None else timestamp,
                reference_price=None if ref is None else round(ref, 2),
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def remove(self, ticker: str) -> None:
        """Forget a ticker. Bumps version so SSE clients learn about the removal."""
        with self._lock:
            existed = self._prices.pop(ticker, None) is not None
            self._references.pop(ticker, None)
            if existed:
                self._version += 1

    # --- reads ------------------------------------------------------------

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
        """Atomically read (version, prices). Used by SSE."""
        with self._lock:
            return self._version, dict(self._prices)

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

### Behaviour by example

```python
cache = PriceCache()
cache.update("AAPL", 190.00, reference_price=188.00)   # first: flat, prev == price
cache.update("AAPL", 190.37).direction                  # 'up'
cache.get("AAPL").day_change_percent                    # 1.2606 (vs 188.00, sticky)
v = cache.version                                       # 2
cache.remove("AAPL"); cache.version                     # 3  ← removal is visible to SSE
cache.remove("AAPL"); cache.version                     # 3  ← no-op doesn't bump
```

### Why these choices

- **`threading.Lock`, not `asyncio.Lock`.** It protects against OS threads (`asyncio.to_thread`) and works from coroutines. Critical sections are a dict lookup and assignment, so they never block the loop in any measurable way.
- **`remove()` bumps the version.** In the current code it does not. A removed ticker therefore stays in the browser until some *other* price changes. With Massive that can be 15 s, and with an empty watchlist it is forever.
- **`snapshot()`.** Reading `version` and `get_all()` separately lets an update slip in between them. `snapshot()` makes the pair atomic.
- **`timestamp is None`, not `timestamp or ...`.** A timestamp of `0` is a valid (if odd) value and must not be silently replaced.
- **Input validation.** A NaN or non-positive price from a bad feed would poison portfolio math. Rejecting it at the single write point is the cheapest guard.

---

## 7. Unified Interface

**File: `interface.py`**

```python
"""Abstract interface every market data provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    A source owns one background asyncio task that pushes prices into the
    PriceCache it was constructed with. Consumers read the cache, never the source.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL"])
        await source.add_ticker("PYPL")     # any time after construction
        await source.remove_ticker("GOOGL")
        await source.stop()                 # idempotent
    """

    #: Short identifier, surfaced in /api/health and logs ("simulator" | "massive").
    name: str = "abstract"

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Start the background task, tracking `tickers` (plus any added earlier).

        Should leave the cache populated for the initial tickers where possible
        (simulator: always; Massive: if the first poll succeeds).
        Calling start() on a running source raises RuntimeError.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task and wait for it. Safe to call repeatedly."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking `ticker`. No-op if already tracked. Normalizes input."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking `ticker` and drop it from the cache. No-op if unknown."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers, in insertion order."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """True while the background task is alive."""
```

Contract details that both implementations must honour, and that the conformance tests in §14.6 check:

| Behaviour | Simulator | Massive |
|---|---|---|
| `add_ticker` before `start` | allowed; included when started | allowed; included when started |
| `add_ticker` → price in cache | **immediately** | on next poll (the poll is pulled forward if the rate budget allows, §10.4) |
| `remove_ticker` → cache | removed immediately | removed immediately |
| input `" aapl "` | stored as `AAPL` | stored as `AAPL` |
| `start` twice | `RuntimeError` | `RuntimeError` |
| `stop` twice | no-op | no-op |
| exception inside loop | logged, loop continues | logged, back-off, loop continues |

---

## 8. Seed Data & Parameters

**File: `seed_prices.py`**

Constants only. Correlation now comes from a **ticker → sector** map plus a per-sector correlation. That replaces the hard-coded `tech`/`finance`/`TSLA` branches, so adding a sector is a data change, not a code change.

```python
"""Seed prices, GBM parameters, and correlation structure for the simulator."""

from __future__ import annotations

DEFAULT_TICKERS: tuple[str, ...] = (
    "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX",
)

# Realistic starting prices (also used as the simulator's "previous close").
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00,
    "TSLA": 250.00, "NVDA": 800.00, "META": 500.00, "JPM": 195.00,
    "V": 280.00, "NFLX": 600.00,
}

# Annualized drift (mu) and volatility (sigma) per ticker.
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"mu": 0.05, "sigma": 0.22},
    "GOOGL": {"mu": 0.05, "sigma": 0.25},
    "MSFT": {"mu": 0.05, "sigma": 0.20},
    "AMZN": {"mu": 0.05, "sigma": 0.28},
    "TSLA": {"mu": 0.03, "sigma": 0.50},   # high vol
    "NVDA": {"mu": 0.08, "sigma": 0.40},   # high vol, strong drift
    "META": {"mu": 0.05, "sigma": 0.30},
    "JPM": {"mu": 0.04, "sigma": 0.18},    # low vol (bank)
    "V": {"mu": 0.04, "sigma": 0.17},      # low vol (payments)
    "NFLX": {"mu": 0.05, "sigma": 0.35},
}
DEFAULT_PARAMS: dict[str, float] = {"mu": 0.05, "sigma": 0.25}

# Unknown tickers get a deterministic pseudo-random seed price in this range.
UNKNOWN_SEED_RANGE: tuple[float, float] = (50.0, 300.0)

# Correlation structure: same-sector pairs use SECTOR_CORR[sector];
# every other pair (cross-sector, unknown, TSLA) uses CROSS_SECTOR_CORR.
SECTORS: dict[str, str] = {
    "AAPL": "tech", "GOOGL": "tech", "MSFT": "tech", "AMZN": "tech",
    "META": "tech", "NVDA": "tech", "NFLX": "tech",
    "JPM": "finance", "V": "finance",
    # TSLA intentionally unassigned: it "does its own thing".
}
SECTOR_CORR: dict[str, float] = {"tech": 0.6, "finance": 0.5}
CROSS_SECTOR_CORR: float = 0.3

# Random "event" shocks.
EVENT_PROBABILITY: float = 0.001          # per ticker per tick
EVENT_MAGNITUDE: tuple[float, float] = (0.02, 0.05)
```

**Why the matrix is always positive-definite.** It is a block matrix: 1 on the diagonal, `CROSS_SECTOR_CORR` everywhere off-diagonal, raised to `SECTOR_CORR[s]` inside each sector block. Whenever every `SECTOR_CORR[s] ≥ CROSS_SECTOR_CORR ≥ 0` and all values are `< 1`, the eigenvalues are strictly positive. So Cholesky succeeds for any ticker set. The simulator still keeps a defensive fallback (§9.2).

---

## 9. GBM Simulator

**File: `simulator.py`** contains:
- `GBMSimulator`: a pure, synchronous, seedable math engine (no asyncio, no cache).
- `SimulatorDataSource`: the async `MarketDataSource` adapter.

### 9.1 The math

For each tick of length `dt` (in trading-years):

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt · Z )
Z = L · ε,  ε ~ N(0, I),  L = cholesky(C)
```

- `dt = update_interval / (252 · 6.5 · 3600) ≈ 8.48e-8` for 0.5 s ticks.
- Per-tick σ for AAPL ≈ `0.22 · √8.48e-8 ≈ 6.4e-5`, i.e. about **1.2 ¢ on a $190 stock**. That is visible in a 2-decimal display without being cartoonish.
- An event adds `log(1 ± U(0.02, 0.05))` to the log-return of that ticker for that tick.
- With 10 tickers at 2 ticks/s and p = 0.001, we expect one event about every **50 s** somewhere on the board.
- `time_scale` (default 1.0) multiplies `dt`, for demos that want livelier markets (e.g. 50× ≈ a trading day in 8 min).

### 9.2 `GBMSimulator`

Vectorized with numpy: one `standard_normal(n)` draw, one mat-vec, and one `exp` per tick. Prices, μ and σ live in parallel arrays aligned with `self._tickers`.

```python
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
    CROSS_SECTOR_CORR,
    DEFAULT_PARAMS,
    EVENT_MAGNITUDE,
    EVENT_PROBABILITY,
    SECTOR_CORR,
    SECTORS,
    SEED_PRICES,
    TICKER_PARAMS,
    UNKNOWN_SEED_RANGE,
)
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)

TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800


def seed_price_for(ticker: str) -> float:
    """Seed price: realistic for known tickers, stable pseudo-random otherwise.

    crc32 (unlike hash()) is stable across processes, so an unknown ticker such
    as PYPL starts at the same price after every restart.
    """
    if ticker in SEED_PRICES:
        return SEED_PRICES[ticker]
    lo, hi = UNKNOWN_SEED_RANGE
    frac = (zlib.crc32(ticker.encode()) % 10_000) / 10_000
    return round(lo + frac * (hi - lo), 2)


def pairwise_correlation(a: str, b: str) -> float:
    sa, sb = SECTORS.get(a), SECTORS.get(b)
    if sa is not None and sa == sb:
        return SECTOR_CORR[sa]
    return CROSS_SECTOR_CORR


class GBMSimulator:
    """Correlated Geometric Brownian Motion over a dynamic set of tickers."""

    def __init__(
        self,
        tickers: list[str] | None = None,
        *,
        dt: float = 0.5 / TRADING_SECONDS_PER_YEAR,
        event_probability: float = EVENT_PROBABILITY,
        seed: int | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._rng = np.random.default_rng(seed)

        self._tickers: list[str] = []
        self._prices = np.empty(0, dtype=float)
        self._mu = np.empty(0, dtype=float)
        self._sigma = np.empty(0, dtype=float)
        self._chol: np.ndarray | None = None
        self.last_events: dict[str, float] = {}   # ticker -> shock applied last step

        for t in tickers or []:
            self._append(t)
        self._rebuild_cholesky()

    # --- public -----------------------------------------------------------

    def step(self) -> dict[str, float]:
        """Advance one tick. Returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        self.last_events = {}
        if n == 0:
            return {}

        eps = self._rng.standard_normal(n)
        z = self._chol @ eps if self._chol is not None else eps

        log_ret = (self._mu - 0.5 * self._sigma**2) * self._dt \
            + self._sigma * math.sqrt(self._dt) * z

        hits = self._rng.random(n) < self._event_prob
        if hits.any():
            lo, hi = EVENT_MAGNITUDE
            mags = self._rng.uniform(lo, hi, n) * self._rng.choice([-1.0, 1.0], n)
            log_ret = np.where(hits, log_ret + np.log1p(mags), log_ret)
            for i in np.flatnonzero(hits):
                self.last_events[self._tickers[i]] = float(mags[i])
                logger.debug("Event on %s: %+.1f%%", self._tickers[i], mags[i] * 100)

        self._prices = self._prices * np.exp(log_ret)
        return {t: round(float(p), 2) for t, p in zip(self._tickers, self._prices)}

    def add_ticker(self, ticker: str) -> bool:
        """Returns True if the ticker was newly added."""
        if ticker in self._tickers:
            return False
        self._append(ticker)
        self._rebuild_cholesky()
        return True

    def remove_ticker(self, ticker: str) -> bool:
        """Returns True if the ticker was present."""
        if ticker not in self._tickers:
            return False
        i = self._tickers.index(ticker)
        self._tickers.pop(i)
        self._prices = np.delete(self._prices, i)
        self._mu = np.delete(self._mu, i)
        self._sigma = np.delete(self._sigma, i)
        self._rebuild_cholesky()
        return True

    def get_price(self, ticker: str) -> float | None:
        if ticker not in self._tickers:
            return None
        return round(float(self._prices[self._tickers.index(ticker)]), 2)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals --------------------------------------------------------

    def _append(self, ticker: str) -> None:
        if ticker in self._tickers:
            return
        params = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
        self._tickers.append(ticker)
        self._prices = np.append(self._prices, seed_price_for(ticker))
        self._mu = np.append(self._mu, params["mu"])
        self._sigma = np.append(self._sigma, params["sigma"])

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._chol = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                corr[i, j] = corr[j, i] = pairwise_correlation(self._tickers[i], self._tickers[j])
        try:
            self._chol = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            # Only reachable if seed_prices constants are edited into an invalid
            # matrix. Degrade to independent moves rather than crash the feed.
            logger.error("Correlation matrix not positive-definite; using independent draws")
            self._chol = None
```

Usage (pure, deterministic):

```python
>>> sim = GBMSimulator(["AAPL", "MSFT", "JPM"], seed=42)
>>> sim.step()
{'AAPL': 190.0, 'MSFT': 419.98, 'JPM': 195.01}     # reproducible for seed=42
>>> sim.add_ticker("PYPL"); sim.get_price("PYPL")   # crc32-derived, stable across runs
True
124.75
```

### 9.3 `SimulatorDataSource`

```python
class SimulatorDataSource(MarketDataSource):
    """MarketDataSource that drives a GBMSimulator from an asyncio task."""

    name = "simulator"

    def __init__(
        self,
        price_cache: PriceCache,
        *,
        update_interval: float = 0.5,
        event_probability: float = EVENT_PROBABILITY,
        time_scale: float = 1.0,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        # Created eagerly so add_ticker() works before start().
        self._sim = GBMSimulator(
            dt=update_interval * time_scale / TRADING_SECONDS_PER_YEAR,
            event_probability=event_probability,
            seed=seed,
        )
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, tickers: list[str]) -> None:
        if self.is_running:
            raise RuntimeError("SimulatorDataSource already started")
        for t in tickers:
            await self.add_ticker(t)          # seeds the cache for each ticker
        self._task = asyncio.create_task(self._run_loop(), name="market-simulator")
        logger.info("Simulator started: %d tickers", len(self._sim.get_tickers()))

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
        if self._sim.add_ticker(ticker) or ticker not in self._cache:
            price = self._sim.get_price(ticker)
            # Seed price doubles as the "previous close" for day change %.
            self._cache.update(ticker, price, reference_price=price)
            logger.info("Simulator: tracking %s @ %.2f", ticker, price)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers()

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            try:
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker, price)
            except Exception:
                logger.exception("Simulator step failed")
            # Fixed-rate scheduling: no drift from step() time; skip ticks if late.
            next_tick += self._interval
            delay = next_tick - loop.time()
            if delay < 0:
                next_tick = loop.time()
                delay = 0
            await asyncio.sleep(delay)
```

Key behaviours:
- **The cache is seeded before the first tick.** The first SSE event contains every ticker immediately, so there is no blank watchlist.
- **`add_ticker` seeds the cache synchronously.** `POST /api/watchlist` can return the new ticker's price in its response, and a trade on it can execute immediately.
- **Fixed-rate loop.** The sparkline sample spacing stays even even if a step takes a few ms.
- **Single-threaded.** `step()` for 50 tickers takes about 20 µs, so there is no need for `to_thread`.

---

## 10. Massive API Source

**File: `massive_client.py`**

### 10.1 What we call

One endpoint, one HTTP request per poll, covering every tracked ticker:

```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,...
```

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=key, retries=1, connect_timeout=5, read_timeout=10)
snaps = client.get_snapshot_all(SnapshotMarketType.STOCKS, tickers=["AAPL", "MSFT"])
# -> list[massive.rest.models.TickerSnapshot]
```

### 10.2 Snapshot fields: verified against `massive==2.2.0`

These were checked by reading `massive/rest/models/snapshot.py` and `trades.py` in the installed package. **They differ from `planning/archive/MASSIVE_API.md`, which is wrong on two points.**

| Need | Attribute on `TickerSnapshot` | Units / notes |
|---|---|---|
| Last price | `snap.last_trade.price` | `last_trade` may be `None` |
| Last trade time | `snap.last_trade.sip_timestamp` | **nanoseconds**. `LastTrade` has **no `.timestamp`** attribute |
| Fallback price | `snap.min.close` → `snap.day.close` → `snap.prev_day.close` | `min.timestamp` is ms |
| Previous close (day-change reference) | `snap.prev_day.close` | there is **no** `day.previous_close` |
| Day change % (informational) | `snap.todays_change_percent` | we recompute from `reference_price` instead |
| Snapshot update time | `snap.updated` | nanoseconds |

> ⚠️ **Bug in the current code:** `massive_client.py` reads `snap.last_trade.timestamp`. On a real `TickerSnapshot` that attribute does not exist, so every snapshot raises `AttributeError` and is skipped. **The Massive path never produces a price.** The unit tests pass only because `MagicMock` invents the attribute. §14.4 adds a regression test built with the real `TickerSnapshot.from_dict`.

Verified in this repo's venv:

```python
>>> from massive.rest.models import TickerSnapshot
>>> s = TickerSnapshot.from_dict({"ticker": "AAPL",
...     "lastTrade": {"p": 190.5, "t": 1707580800123456789},
...     "prevDay": {"c": 188.0}, "todaysChangePerc": 1.3})
>>> s.last_trade.price, s.last_trade.sip_timestamp, s.prev_day.close
(190.5, 1707580800123456789, 188.0)
>>> hasattr(s.last_trade, "timestamp")
False
```

### 10.3 Parsing: a pure function

Parsing is a pure function, separate from I/O, so it can be tested exhaustively with real SDK model objects.

```python
"""Massive (formerly Polygon.io) REST poller."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from massive import RESTClient
from massive.exceptions import AuthError, BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedQuote:
    ticker: str
    price: float
    timestamp: float               # Unix seconds
    reference_price: float | None  # previous close


def to_unix_seconds(value: int | float | None) -> float | None:
    """Massive mixes ns (trades, `updated`), ms (aggs) and s. Infer by magnitude."""
    if not value:
        return None
    v = float(value)
    if v > 1e17:
        return v / 1e9   # nanoseconds
    if v > 1e14:
        return v / 1e6   # microseconds
    if v > 1e11:
        return v / 1e3   # milliseconds
    return v             # seconds


def _positive(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def parse_snapshot(snap: Any) -> ParsedQuote | None:
    """Extract a ParsedQuote from a massive TickerSnapshot, or None if unusable.

    Price preference: last trade → current minute bar close → today's close →
    previous day's close (e.g. before 4am ET, when snapshots are cleared).
    """
    raw_ticker = getattr(snap, "ticker", None)
    if not raw_ticker:
        return None
    ticker = raw_ticker.upper()

    last_trade = getattr(snap, "last_trade", None)
    minute = getattr(snap, "min", None)
    day = getattr(snap, "day", None)
    prev_day = getattr(snap, "prev_day", None)

    candidates = [
        (_positive(getattr(last_trade, "price", None)), getattr(last_trade, "sip_timestamp", None)),
        (_positive(getattr(minute, "close", None)), getattr(minute, "timestamp", None)),
        (_positive(getattr(day, "close", None)), None),
        (_positive(getattr(prev_day, "close", None)), None),
    ]
    for price, ts in candidates:
        if price is not None:
            timestamp = (
                to_unix_seconds(ts)
                or to_unix_seconds(getattr(snap, "updated", None))
                or time.time()
            )
            return ParsedQuote(
                ticker=ticker,
                price=price,
                timestamp=timestamp,
                reference_price=_positive(getattr(prev_day, "close", None)),
            )
    return None
```

### 10.4 `MassiveDataSource`

Additions over the current implementation:
- **Rate-budget aware.** It never issues requests closer together than `min_request_gap` (default 12 s = 60/5 for the free tier).
- **`add_ticker` wakes the poller early** when the budget allows, so a newly added ticker gets a price quickly instead of waiting up to a full interval.
- **Exponential back-off** on failures, capped at `max_backoff`.
- **Classified errors**: an auth/plan error (401/403) is logged once at ERROR, with a hint.
- **Unchanged trades are skipped.** If a ticker's trade timestamp has not advanced, the cache is not touched. That keeps `previous_price`/`direction` meaningful (otherwise every quiet poll would flatten the flash to "flat") and avoids pointless SSE pushes after hours.
- **The ticker list is copied before going to the worker thread**, so `add_ticker`/`remove_ticker` on the loop cannot mutate a list mid-request.

```python
class MassiveDataSource(MarketDataSource):
    """Polls the Massive snapshot endpoint and writes results to the PriceCache.

    Free tier: 5 req/min → poll_interval=15, min_request_gap=12.
    Paid tiers: poll_interval 2–5 s, min_request_gap ≈ 1.
    """

    name = "massive"

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        *,
        poll_interval: float = 15.0,
        min_request_gap: float = 12.0,
        max_backoff: float = 120.0,
        client: Any | None = None,          # injectable for tests
    ) -> None:
        self._cache = price_cache
        self._interval = poll_interval
        self._min_gap = min(min_request_gap, poll_interval)
        self._max_backoff = max_backoff
        self._client = client or RESTClient(
            api_key=api_key, retries=1, connect_timeout=5.0, read_timeout=10.0
        )
        self._tickers: list[str] = []
        self._last_trade_ts: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_request_at = 0.0            # loop.time() of last request
        self._consecutive_failures = 0
        self._auth_error_logged = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, tickers: list[str]) -> None:
        if self.is_running:
            raise RuntimeError("MassiveDataSource already started")
        for t in tickers:
            t = normalize_ticker(t)
            if t not in self._tickers:
                self._tickers.append(t)
        await self._poll_once()                # populate cache before first SSE tick
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, every %.1fs",
                    len(self._tickers), self._interval)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        self._wake.set()                       # ask for an early poll (budget permitting)
        logger.info("Massive: tracking %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        self._last_trade_ts.pop(ticker, None)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals --------------------------------------------------------

    def _next_delay(self) -> float:
        if self._consecutive_failures == 0:
            return self._interval
        return min(self._interval * 2 ** self._consecutive_failures, self._max_backoff)

    async def _poll_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            # Sleep for the normal interval, or until add_ticker() wakes us.
            # (The event is cleared in _poll_once, when the ticker list is captured,
            # so an add_ticker() that lands mid-request still triggers a follow-up.)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._next_delay())
            except TimeoutError:
                pass
            # Enforce the rate budget even when woken early.
            gap = self._last_request_at + self._min_gap - loop.time()
            if gap > 0:
                await asyncio.sleep(gap)
            await self._poll_once()

    async def _poll_once(self) -> bool:
        """One request/parse/update cycle. Returns True on success. Never raises."""
        self._wake.clear()                     # this request covers all adds so far
        tickers = tuple(self._tickers)
        if not tickers:
            return True
        self._last_request_at = asyncio.get_running_loop().time()
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        except (AuthError, BadResponse) as exc:
            self._consecutive_failures += 1
            msg = str(exc)
            if any(k in msg for k in ("NOT_AUTHORIZED", "401", "403", "Unknown API Key")):
                if not self._auth_error_logged:
                    logger.error(
                        "Massive rejected the request (%s). Check MASSIVE_API_KEY and "
                        "that your plan includes the stocks snapshot endpoint. "
                        "Unset MASSIVE_API_KEY to use the simulator.", msg[:200])
                    self._auth_error_logged = True
            else:
                logger.warning("Massive poll failed (%d in a row): %s",
                               self._consecutive_failures, msg[:200])
            return False
        except Exception:
            self._consecutive_failures += 1
            logger.exception("Massive poll failed (%d in a row)", self._consecutive_failures)
            return False

        self._consecutive_failures = 0
        wanted = set(self._tickers)            # re-read: list may have changed mid-request
        updated = 0
        for snap in snapshots or []:
            quote = parse_snapshot(snap)
            if quote is None:
                logger.debug("Unusable snapshot: %r", getattr(snap, "ticker", None))
                continue
            if quote.ticker not in wanted:
                continue                       # removed while the request was in flight
            if self._last_trade_ts.get(quote.ticker) == quote.timestamp \
                    and quote.ticker in self._cache:
                continue                       # nothing new since last poll
            self._last_trade_ts[quote.ticker] = quote.timestamp
            self._cache.update(quote.ticker, quote.price,
                               timestamp=quote.timestamp,
                               reference_price=quote.reference_price)
            updated += 1

        missing = wanted - {s.ticker.upper() for s in snapshots or [] if getattr(s, "ticker", None)}
        if missing:
            logger.debug("Massive returned no data for: %s", ", ".join(sorted(missing)))
        logger.debug("Massive poll: %d/%d tickers updated", updated, len(tickers))
        return True

    def _fetch_snapshots(self, tickers: tuple[str, ...]) -> list:
        """Blocking HTTP call. Runs in a worker thread via asyncio.to_thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=list(tickers),
        )
```

### 10.5 Rate-budget walkthrough (free tier: 15 s interval, 12 s gap)

```
t=0     start(): poll #1 (10 tickers)
t=15    poll #2
t=20    user adds PYPL → wake → gap check: 20 < 15+12 → sleep 7s
t=27    poll #3 (now includes PYPL)        ← PYPL priced 7s after add, not 10s
t=42    poll #4
...
worst case: 60/12 = 5 requests/min  ✓ within free tier
```

### 10.6 Error matrix

| Situation | What happens | User sees |
|---|---|---|
| Bad key / plan lacks snapshot (401/403) | ERROR logged once with fix hint; back-off up to 120 s | Watchlist shows "—" prices; trades return 400 "price unavailable" |
| 429 rate limited | urllib3 retries once, then WARNING + back-off | Prices update less often |
| Network/timeout | WARNING + back-off | Stale prices (last known) |
| Unknown symbol (e.g. `FOOBAR`) | Not in response; DEBUG log | Ticker row with no price |
| Market closed / pre-market | `last_trade` from last session; timestamps don't advance → no cache writes | Prices static, no flashes (correct) |
| Snapshot missing `last_trade` | falls back to `min.close` / `day.close` / `prev_day.close` | Price still shown |

---

## 11. Factory & Configuration

**File: `factory.py`**

The factory reads configuration from the environment in one place. The rest of the package takes explicit constructor arguments, which keeps it easy to test.

```python
"""Select and build the market data source from configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default


@dataclass(frozen=True)
class MarketConfig:
    massive_api_key: str = ""
    massive_poll_interval: float = 15.0
    massive_min_request_gap: float = 12.0
    simulator_interval: float = 0.5
    simulator_time_scale: float = 1.0
    simulator_seed: int | None = None

    @classmethod
    def from_env(cls) -> MarketConfig:
        seed = os.environ.get("SIMULATOR_SEED", "").strip()
        return cls(
            massive_api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
            massive_poll_interval=_float_env("MASSIVE_POLL_INTERVAL", 15.0),
            massive_min_request_gap=_float_env("MASSIVE_MIN_REQUEST_GAP", 12.0),
            simulator_interval=_float_env("SIMULATOR_INTERVAL", 0.5),
            simulator_time_scale=_float_env("SIMULATOR_TIME_SCALE", 1.0),
            simulator_seed=int(seed) if seed.isdigit() else None,
        )


def create_market_data_source(
    price_cache: PriceCache, config: MarketConfig | None = None
) -> MarketDataSource:
    """MASSIVE_API_KEY non-empty → MassiveDataSource, else SimulatorDataSource.

    Returns an *unstarted* source; the caller awaits source.start(tickers).
    """
    cfg = config or MarketConfig.from_env()
    if cfg.massive_api_key:
        logger.info("Market data: Massive API (poll every %.1fs)", cfg.massive_poll_interval)
        return MassiveDataSource(
            cfg.massive_api_key,
            price_cache,
            poll_interval=cfg.massive_poll_interval,
            min_request_gap=cfg.massive_min_request_gap,
        )
    logger.info("Market data: GBM simulator")
    return SimulatorDataSource(
        price_cache,
        update_interval=cfg.simulator_interval,
        time_scale=cfg.simulator_time_scale,
        seed=cfg.simulator_seed,
    )
```

Only `MASSIVE_API_KEY` is part of the public contract in `PLAN.md` §5. The other variables are optional tuning knobs with safe defaults. They do not need to go in `.env.example`, although adding them as comments helps.

---

## 12. SSE Stream

**File: `stream.py`**

### 12.1 Wire contract

- `GET /api/stream/prices` → `Content-Type: text/event-stream`
- The first frame is `retry: 1000`, which makes EventSource reconnect after 1 s.
- Each data frame is a **full snapshot** of all tracked tickers, keyed by ticker. Because it is a full snapshot, removals propagate: a ticker absent from the frame has been removed.
- `id:` is the cache version. It is useful for debugging; the server does not replay.
- A frame is sent only when the cache version has changed since the last frame (checked every 500 ms).
- After 15 s without a data frame, a `: heartbeat` comment is sent so proxies and load balancers don't drop an idle connection. This matters for Massive outside market hours.

```
retry: 1000

id: 1532
data: {"AAPL":{"ticker":"AAPL","price":190.37,"previous_price":190.35,"timestamp":1762790400.51,"change":0.02,"change_percent":0.0105,"direction":"up","reference_price":190.0,"day_change":0.37,"day_change_percent":0.1947},"MSFT":{...}}

: heartbeat

```

### 12.2 Implementation

Two fixes over the current code. The router is created **inside** the factory, so calling `create_stream_router` twice (for example in tests) no longer double-registers a route on a module-level router. And the generator takes an `is_disconnected` callable, so it can be unit-tested without an ASGI server.

```python
"""SSE endpoint streaming live prices from the PriceCache."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",   # disable proxy buffering (nginx etc.)
}


def create_stream_router(
    price_cache: PriceCache, *, interval: float = 0.5, heartbeat: float = 15.0
) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            price_event_stream(price_cache, request.is_disconnected,
                               interval=interval, heartbeat=heartbeat),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return router


async def price_event_stream(
    cache: PriceCache,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    interval: float = 0.5,
    heartbeat: float = 15.0,
) -> AsyncIterator[str]:
    yield "retry: 1000\n\n"
    last_version = -1
    last_sent = time.monotonic()
    try:
        while not await is_disconnected():
            version, prices = cache.snapshot()
            now = time.monotonic()
            if version != last_version:
                last_version = version
                payload = json.dumps(
                    {t: u.to_dict() for t, u in prices.items()}, separators=(",", ":")
                )
                yield f"id: {version}\ndata: {payload}\n\n"
                last_sent = now
            elif now - last_sent >= heartbeat:
                yield ": heartbeat\n\n"
                last_sent = now
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass   # client went away / server shutting down
    logger.debug("SSE client disconnected")
```

### 12.3 Frontend consumption (reference for the Frontend agent)

```ts
type PriceUpdate = {
  ticker: string; price: number; previous_price: number; timestamp: number;
  change: number; change_percent: number; direction: "up" | "down" | "flat";
  reference_price: number | null; day_change: number | null; day_change_percent: number | null;
};

const es = new EventSource("/api/stream/prices");
es.onopen = () => setStatus("connected");
es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
es.onmessage = (e) => {
  const snapshot: Record<string, PriceUpdate> = JSON.parse(e.data);
  // Full snapshot: replace the map (removals vanish), append to per-ticker sparkline
  // only when the timestamp advanced, and flash when direction !== "flat".
  applySnapshot(snapshot);
};
```

---

## 13. Integration

### 13.1 Lifespan wiring (`backend/app/main.py`)

The cache is created at import time, so the SSE router can be registered at import time too. Only the *source*, which needs a running event loop and the database, is created in `lifespan`. (The archived design called `include_router` inside `lifespan`. Avoid that: routes added after startup are not reliably picked up.)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.market import (
    MarketDataSource, PriceCache, create_market_data_source, create_stream_router,
)
from app.market.seed_prices import DEFAULT_TICKERS

price_cache = PriceCache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    source = create_market_data_source(price_cache)
    app.state.price_cache = price_cache
    app.state.market_source = source

    tickers = await db.tracked_tickers()     # watchlist ∪ open positions (§13.3)
    await source.start(tickers or list(DEFAULT_TICKERS))
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)
app.include_router(create_stream_router(price_cache))   # GET /api/stream/prices
# ... include portfolio / watchlist / chat routers, then mount static files LAST
# so "/" doesn't shadow "/api/*":
# app.mount("/", StaticFiles(directory="static", html=True), name="static")


# FastAPI dependencies for other routers
def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
```

### 13.2 Health endpoint

```python
@app.get("/api/health")
async def health(request: Request):
    src: MarketDataSource = request.app.state.market_source
    cache: PriceCache = request.app.state.price_cache
    return {"status": "ok", "market_data": {
        "source": src.name, "running": src.is_running,
        "tickers": len(src.get_tickers()), "priced": len(cache),
    }}
```

### 13.3 Keeping the tracked set right: `tracking.py`

The source must track **watchlist ∪ tickers with open positions**. Otherwise, removing a held ticker from the watchlist stops its price updating, and the portfolio value freezes. Instead of scattering add/remove calls with special cases, every mutating route calls one reconciler after its DB write:

```python
"""Reconcile the data source's tracked set with what the app needs."""

from __future__ import annotations

from collections.abc import Iterable

from .interface import MarketDataSource
from .tickers import normalize_ticker


async def sync_tracked_tickers(source: MarketDataSource, desired: Iterable[str]) -> None:
    want = {normalize_ticker(t) for t in desired}
    have = set(source.get_tickers())
    for t in sorted(want - have):
        await source.add_ticker(t)
    for t in sorted(have - want):
        await source.remove_ticker(t)
```

Backend usage (the DB helper names are illustrative and belong to the backend agent):

```python
@router.post("/api/watchlist", status_code=201)
async def add_watchlist(body: WatchlistAdd, request: Request):
    try:
        ticker = normalize_ticker(body.ticker)
    except TickerError as e:
        raise HTTPException(400, str(e))
    await db.add_watchlist(ticker)
    await sync_tracked_tickers(request.app.state.market_source, await db.tracked_tickers())
    update = request.app.state.price_cache.get(ticker)   # simulator: present; Massive: maybe None
    return {"ticker": ticker, "price": update.to_dict() if update else None}


@router.delete("/api/watchlist/{ticker}")
async def remove_watchlist(ticker: str, request: Request):
    ticker = normalize_ticker(ticker)
    await db.remove_watchlist(ticker)
    await sync_tracked_tickers(request.app.state.market_source, await db.tracked_tickers())
    return {"ticker": ticker, "removed": True}
```

```sql
-- db.tracked_tickers()
SELECT ticker FROM watchlist WHERE user_id = 'default'
UNION
SELECT ticker FROM positions WHERE user_id = 'default' AND quantity > 0;
```

The LLM chat flow's `watchlist_changes` goes through the same functions as the REST routes, so it gets the same behaviour for free.

### 13.4 Trade execution and valuation

```python
price = cache.get_price(ticker)
if price is None:
    # Trading a ticker we don't track yet: start tracking, then re-check.
    await source.add_ticker(ticker)          # simulator: priced immediately
    price = cache.get_price(ticker)
if price is None:                            # Massive: wait for next poll
    raise HTTPException(400, f"No price available for {ticker} yet; try again in a few seconds.")
# ...validate cash/shares, fill at `price`, then:
await sync_tracked_tickers(source, await db.tracked_tickers())  # e.g. a full sell drops it
```

Portfolio valuation and the LLM context read `cache.get_all()` once per request. They value each position at `cache.get_price(t)`, falling back to `avg_cost` if a price is momentarily missing, so totals never show NaN.

---

## 14. Testing

Use `pytest` and `pytest-asyncio` (already configured with `asyncio_mode = "auto"`). Everything below runs offline in under 2 s.

### 14.1 Models & cache

```python
from app.market.cache import PriceCache
from app.market.models import PriceUpdate


def test_day_change_uses_reference():
    u = PriceUpdate("AAPL", 191.2, 191.05, 0.0, reference_price=190.0)
    assert u.direction == "up"
    assert u.day_change == 1.2
    assert u.day_change_percent == 0.6316


def test_reference_is_sticky_and_removal_bumps_version():
    c = PriceCache()
    c.update("AAPL", 190.0, reference_price=188.0)
    c.update("AAPL", 190.5)
    assert c.get("AAPL").reference_price == 188.0
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")
    assert c.version == v + 1


def test_rejects_bad_prices():
    import pytest
    c = PriceCache()
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            c.update("AAPL", bad)


def test_zero_timestamp_preserved():
    assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0
```

### 14.2 GBM simulator

```python
import numpy as np
from app.market.seed_prices import DEFAULT_TICKERS, SEED_PRICES
from app.market.simulator import GBMSimulator, seed_price_for


def test_deterministic_with_seed():
    a, b = GBMSimulator(["AAPL", "MSFT"], seed=7), GBMSimulator(["AAPL", "MSFT"], seed=7)
    assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]


def test_full_default_set_and_extras_are_positive_definite():
    sim = GBMSimulator(list(DEFAULT_TICKERS) + ["PYPL", "XOM", "BRK.B"], seed=1)
    assert sim._chol is not None


def test_prices_stay_positive_under_stress():
    sim = GBMSimulator(["TSLA"], dt=1 / 252, event_probability=0.5, seed=3)  # 1 day/tick
    assert all(sim.step()["TSLA"] > 0 for _ in range(5_000))


def test_gbm_log_return_statistics():
    """Mean and std of log-returns match (mu - sigma^2/2)dt and sigma*sqrt(dt)."""
    dt, n = 1 / 252, 20_000
    sim = GBMSimulator(["AAPL"], dt=dt, event_probability=0.0, seed=11)
    p = [float(sim._prices[0])]
    for _ in range(n):
        sim.step()
        p.append(float(sim._prices[0]))       # unrounded internal price
    r = np.diff(np.log(p))
    mu, sigma = 0.05, 0.22
    sd = sigma * np.sqrt(dt)
    assert abs(r.std() - sd) < 0.03 * sd                          # within 3%
    assert abs(r.mean() - (mu - sigma**2 / 2) * dt) < 4 * sd / np.sqrt(n)  # 4 std errors


def test_correlation_is_realised():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], dt=1 / 252, event_probability=0, seed=5)
    rets = []
    prev = sim._prices.copy()
    for _ in range(20_000):
        sim.step()
        rets.append(np.log(sim._prices / prev))
        prev = sim._prices.copy()
    c = np.corrcoef(np.array(rets).T)
    assert abs(c[0, 1] - 0.6) < 0.05     # tech/tech
    assert abs(c[0, 2] - 0.3) < 0.05     # tech/finance


def test_seed_prices():
    assert GBMSimulator(["AAPL"]).get_price("AAPL") == SEED_PRICES["AAPL"]
    assert seed_price_for("PYPL") == seed_price_for("PYPL")      # stable
    assert 50 <= seed_price_for("ZZZZ") <= 300


def test_add_remove_keep_arrays_aligned():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], seed=0)
    assert sim.remove_ticker("MSFT") and not sim.remove_ticker("MSFT")
    assert sim.add_ticker("PYPL") and not sim.add_ticker("PYPL")
    out = sim.step()
    assert list(out) == ["AAPL", "JPM", "PYPL"] == sim.get_tickers()
```

### 14.3 Simulator source

```python
import asyncio
import pytest
from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


async def test_lifecycle_and_normalization():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.02, seed=1)
    await src.add_ticker(" aapl ")                 # before start
    await src.start(["MSFT"])
    assert src.get_tickers() == ["AAPL", "MSFT"]
    assert cache.get("AAPL").reference_price == 190.0
    v = cache.version
    await asyncio.sleep(0.1)
    assert cache.version > v                       # ticking
    with pytest.raises(RuntimeError):
        await src.start([])
    await src.remove_ticker("aapl")
    assert "AAPL" not in cache
    await src.stop(); await src.stop()
    assert not src.is_running
```

### 14.4 Massive parsing: with **real** SDK models

```python
from massive.rest.models import TickerSnapshot
from app.market.massive_client import parse_snapshot, to_unix_seconds


def snap(**d):
    return TickerSnapshot.from_dict(d)


def test_parses_last_trade_ns_timestamp():
    # Regression: LastTrade has sip_timestamp (ns), not .timestamp
    q = parse_snapshot(snap(ticker="AAPL", lastTrade={"p": 190.5, "t": 1707580800123456789},
                            prevDay={"c": 188.0}))
    assert q.price == 190.5
    assert q.reference_price == 188.0
    assert abs(q.timestamp - 1707580800.123) < 1e-3


def test_falls_back_when_no_trade():
    q = parse_snapshot(snap(ticker="AAPL", min={"c": 189.9, "t": 1707580800000},
                            prevDay={"c": 188.0}))
    assert q.price == 189.9 and abs(q.timestamp - 1707580800.0) < 1e-6
    q = parse_snapshot(snap(ticker="AAPL", prevDay={"c": 188.0}))
    assert q.price == 188.0


def test_unusable_snapshots():
    assert parse_snapshot(snap(ticker="AAPL")) is None
    assert parse_snapshot(snap(lastTrade={"p": 1.0})) is None
    assert parse_snapshot(snap(ticker="X", lastTrade={"p": 0})) is None


def test_unit_inference():
    assert to_unix_seconds(1_707_580_800) == 1_707_580_800
    assert to_unix_seconds(1_707_580_800_000) == 1_707_580_800
    assert to_unix_seconds(1_707_580_800_000_000_000) == 1_707_580_800
    assert to_unix_seconds(None) is None
```

### 14.5 Massive source: a fake client, no network

```python
import asyncio
from unittest.mock import MagicMock
from massive.exceptions import BadResponse
from massive.rest.models import TickerSnapshot
from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource


def fake_client(*responses):
    client = MagicMock()
    client.get_snapshot_all.side_effect = list(responses)
    return client


def snaps(**prices):
    return [TickerSnapshot.from_dict({"ticker": t, "lastTrade": {"p": p, "t": 1_700_000_000_000_000_000 + i}})
            for i, (t, p) in enumerate(prices.items())]


async def test_start_polls_and_populates():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60, client=fake_client(snaps(AAPL=190.5)))
    await src.start(["aapl"])
    assert cache.get_price("AAPL") == 190.5
    await src.stop()


async def test_unchanged_trade_is_not_rewritten():
    cache = PriceCache()
    same = snaps(AAPL=190.5)
    src = MassiveDataSource("k", cache, poll_interval=60, client=fake_client(same, same))
    src._tickers = ["AAPL"]
    await src._poll_once(); v = cache.version
    await src._poll_once()
    assert cache.version == v


async def test_errors_back_off_and_never_raise():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=10, max_backoff=40,
                            client=fake_client(BadResponse("429"), BadResponse("429"),
                                               BadResponse("429")))
    src._tickers = ["AAPL"]
    for expected in (20, 40, 40):
        assert await src._poll_once() is False
        assert src._next_delay() == expected


async def test_add_ticker_wakes_poller_within_budget():
    cache = PriceCache()
    client = fake_client(snaps(AAPL=1.0), snaps(AAPL=1.0, PYPL=60.0))
    src = MassiveDataSource("k", cache, poll_interval=60, min_request_gap=0.05, client=client)
    await src.start(["AAPL"])
    await src.add_ticker("PYPL")
    await asyncio.sleep(0.2)
    assert cache.get_price("PYPL") == 60.0
    await src.stop()


async def test_removed_mid_flight_is_ignored():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60, client=MagicMock())

    def fetch_while_user_removes(**kwargs):
        src._tickers.remove("AAPL")    # user removes AAPL while the HTTP call is in flight
        return snaps(AAPL=1.0)

    src._client.get_snapshot_all.side_effect = fetch_while_user_removes
    src._tickers = ["AAPL"]
    assert await src._poll_once() is True
    assert "AAPL" not in cache
```

### 14.6 Interface conformance: one suite, both sources

```python
from unittest.mock import MagicMock

import pytest
from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.fixture(params=["simulator", "massive"])
def make_source(request):
    def _make(cache):
        if request.param == "simulator":
            return SimulatorDataSource(cache, update_interval=0.05, seed=0)
        client = MagicMock()
        client.get_snapshot_all.return_value = []
        return MassiveDataSource("k", cache, poll_interval=60, client=client)
    return _make


async def test_conformance(make_source):
    cache = PriceCache()
    src = make_source(cache)
    await src.start(["AAPL"])
    assert src.is_running and src.get_tickers() == ["AAPL"]
    await src.add_ticker("msft"); await src.add_ticker("MSFT")
    assert src.get_tickers() == ["AAPL", "MSFT"]
    await src.remove_ticker("AAPL"); await src.remove_ticker("AAPL")
    assert src.get_tickers() == ["MSFT"] and "AAPL" not in cache
    with pytest.raises(RuntimeError):
        await src.start([])
    await src.stop(); await src.stop()
    assert not src.is_running
```

### 14.7 SSE generator

```python
import json
from app.market.cache import PriceCache
from app.market.stream import price_event_stream


async def test_stream_frames_and_removal():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    ticks = iter([False, False, False, True])

    async def disconnected():
        return next(ticks)

    frames = []
    gen = price_event_stream(cache, disconnected, interval=0, heartbeat=999)
    frames.append(await anext(gen))                 # retry
    frames.append(await anext(gen))                 # AAPL snapshot
    cache.remove("AAPL")
    frames.append(await anext(gen))                 # empty snapshot after removal
    assert frames[0] == "retry: 1000\n\n"
    assert json.loads(frames[1].split("data: ")[1])["AAPL"]["price"] == 190.0
    assert json.loads(frames[2].split("data: ")[1]) == {}


async def test_heartbeat_when_idle():
    cache = PriceCache()
    calls = iter([False, False, True])

    async def disconnected():
        return next(calls)

    gen = price_event_stream(cache, disconnected, interval=0, heartbeat=0)
    assert await anext(gen) == "retry: 1000\n\n"
    assert (await anext(gen)).startswith("id: 0")   # initial (empty) snapshot
    assert await anext(gen) == ": heartbeat\n\n"
```

### 14.8 Factory

```python
from app.market.factory import MarketConfig, create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


def test_selection(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "5")
    src = create_market_data_source(PriceCache())
    assert isinstance(src, MassiveDataSource) and src._interval == 5.0


def test_bad_numbers_fall_back(monkeypatch):
    monkeypatch.setenv("SIMULATOR_INTERVAL", "fast")
    assert MarketConfig.from_env().simulator_interval == 0.5
```

### 14.9 Optional live smoke test

Mark it `@pytest.mark.live` and skip it unless `MASSIVE_API_KEY` is set. It calls the real endpoint once for `["AAPL"]` and asserts that `parse_snapshot` returns a positive price. It is the only test that would have caught the `sip_timestamp` bug end-to-end.

---

## 15. Changes vs. Current Implementation

Ordered by severity. Each row is an independent, small patch to `backend/app/market/`.

| # | Severity | File | Change |
|---|---|---|---|
| 1 | **High** | `massive_client.py` | Read `last_trade.sip_timestamp` (ns), not `last_trade.timestamp` (does not exist, so every snapshot is skipped today). Move parsing into `parse_snapshot()` with fallbacks (§10.3) and test it with real `TickerSnapshot.from_dict` (§14.4). |
| 2 | **High** | `cache.py` | `remove()` bumps `version`, so SSE clients see removals. |
| 3 | **Medium** | `models.py`, `cache.py`, both sources | Add `reference_price` + `day_change[_percent]` for the watchlist's daily change % (PLAN §10). Simulator: the seed price. Massive: `prev_day.close`. |
| 4 | **Medium** | `tickers.py`, both sources | A single `normalize_ticker()` everywhere (the simulator currently accepts `"aapl"` as a distinct ticker). |
| 5 | **Medium** | `massive_client.py` | Skip unchanged trades. Back-off on errors. Rate-budgeted early poll on `add_ticker`. Copy the ticker list before `to_thread`. Ignore tickers removed mid-request. Injectable `client`. |
| 6 | **Medium** | `stream.py` | Router created inside the factory (no duplicate routes). Use `cache.snapshot()`. Send `{}` when empty. Heartbeat comments. `id:` lines. Injectable `is_disconnected` for tests. |
| 7 | **Medium** | new `tracking.py` | `sync_tracked_tickers()`, so held positions keep pricing after a watchlist removal. |
| 8 | Low | `cache.py` | `version` read under the lock. `snapshot()`. `timestamp is None` check. Reject NaN/≤0 prices. |
| 9 | Low | `simulator.py` | Vectorized numpy step. Seedable `default_rng`. Stable CRC-based seed price for unknown tickers. Fixed-rate loop. `dt` derived from `update_interval × time_scale`. Cholesky fallback. Sector-map correlations. |
| 10 | Low | `interface.py` | `name`, `is_running`, `start()`-twice raises, `add_ticker` before `start` allowed. |
| 11 | Low | `factory.py` | `MarketConfig.from_env()` with optional tuning variables. |
| 12 | Docs | `planning/archive/MASSIVE_API.md` | Field names `day.previous_close` / `last_trade.timestamp` are wrong for `massive` 2.x. Use `prev_day.close` / `last_trade.sip_timestamp`. |

The public API that downstream code already depends on is unchanged in name and behaviour: `PriceCache.get/get_price/get_all/update/remove/version`, `MarketDataSource.start/stop/add_ticker/remove_ticker/get_tickers`, `create_market_data_source(cache)`, and `create_stream_router(cache)`. The `to_dict()` payload only *gains* keys, so it stays compatible with any frontend code written against the current shape.

---

## 16. Configuration Reference

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | *(empty)* | Non-empty selects the Massive source; otherwise the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between polls (use `2`–`5` on paid tiers) |
| `MASSIVE_MIN_REQUEST_GAP` | `12` | Hard floor between requests, including early polls after `add_ticker` |
| `SIMULATOR_INTERVAL` | `0.5` | Seconds per simulator tick |
| `SIMULATOR_TIME_SCALE` | `1.0` | Multiplies GBM `dt` for livelier demos |
| `SIMULATOR_SEED` | *(unset)* | Integer gives deterministic prices (E2E tests, screenshots) |

| Constant | Location | Default |
|---|---|---|
| SSE check interval | `create_stream_router(interval=)` | 0.5 s |
| SSE heartbeat | `create_stream_router(heartbeat=)` | 15 s |
| SSE client retry | `price_event_stream` | 1000 ms |
| Event probability / magnitude | `seed_prices.py` | 0.001 / 2–5 % |
| Sector correlations | `seed_prices.py` | tech 0.6, finance 0.5, cross 0.3 |
| Massive back-off cap | `MassiveDataSource(max_backoff=)` | 120 s |
