# Market Data Backend — Detailed Design (v2)

Implementation-ready design for the FinAlly market data subsystem: the unified interface, the shared price cache, the GBM simulator, the Massive (Polygon.io) REST client, the SSE stream, and how the rest of the backend plugs into them.

**Scope:** everything under `backend/app/market/`, plus the integration points in `backend/app/main.py` and the watchlist / portfolio routes.

**Relationship to existing work.** A first version of this subsystem is already implemented and tested (see `MARKET_DATA_SUMMARY.md`; the v1 design is in `archive/MARKET_DATA_DESIGN.md`). This document is the authoritative design going forward. It keeps the v1 architecture and public API, and adds the changes needed to close the gaps found while reviewing v1 against `PLAN.md` and the real `massive` package. Every change from the current code is marked **Δ** and listed in [§14](#14-change-list-vs-current-code).

---

## Table of Contents

1. [Requirements](#1-requirements)
2. [Architecture](#2-architecture)
3. [Data Model — `models.py`](#3-data-model--modelspy)
4. [Ticker Normalisation — `tickers.py`](#4-ticker-normalisation--tickerspy)
5. [Price Cache — `cache.py`](#5-price-cache--cachepy)
6. [Unified Interface — `interface.py`](#6-unified-interface--interfacepy)
7. [Simulator — `seed_prices.py`, `simulator.py`](#7-simulator)
8. [Massive API Client — `massive_client.py`](#8-massive-api-client--massive_clientpy)
9. [Factory & Configuration — `factory.py`](#9-factory--configuration--factorypy)
10. [SSE Streaming — `stream.py`](#10-sse-streaming--streampy)
11. [FastAPI Integration](#11-fastapi-integration)
12. [Error Handling & Edge Cases](#12-error-handling--edge-cases)
13. [Testing](#13-testing)
14. [Change List vs Current Code](#14-change-list-vs-current-code)

---

## 1. Requirements

From `PLAN.md` §6, §8 and §10:

| # | Requirement | Where it is met |
|---|---|---|
| R1 | One abstract interface, two implementations (simulator, Massive); downstream code is source-agnostic | `MarketDataSource`, §6 |
| R2 | Source picked by `MASSIVE_API_KEY` (set and non-empty → Massive, else simulator) | `create_market_data_source`, §9 |
| R3 | Simulator: GBM, ~500 ms ticks, correlated moves, random 2–5 % events, realistic seed prices, in-process | §7 |
| R4 | Massive: REST polling (not WebSocket), one call for all watched tickers, 15 s on free tier, 2–15 s on paid tiers | §8 |
| R5 | Shared in-memory cache holding latest price, previous price, timestamp per ticker | `PriceCache`, §5 |
| R6 | `GET /api/stream/prices` SSE, ~500 ms cadence, each event carries ticker, price, previous price, timestamp, direction | §10 |
| R7 | Watchlist shows **daily change %** | **Δ** `session_open` / `day_change_percent`, §3 |
| R8 | Watchlist add/remove (REST or LLM) changes which tickers are priced | §11.3 |
| R9 | Trades fill at the current cached price; portfolio valuation uses cached prices | §11.4 |
| R10 | Deterministic, fast tests (`LLM_MOCK`-style determinism for E2E) | **Δ** simulator `seed`, §7.3 |

---

## 2. Architecture

```
                         ┌──────────────────────────────┐
  MASSIVE_API_KEY ──────▶│ create_market_data_source()  │
                         └──────────────┬───────────────┘
                                        │ returns one of
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
   ┌───────────────────────────┐               ┌───────────────────────────────┐
   │ SimulatorDataSource       │               │ MassiveDataSource             │
   │  └─ GBMSimulator (numpy)  │               │  └─ massive.RESTClient (sync) │
   │  asyncio task, 0.5 s tick │               │  asyncio task, 15 s poll      │
   └─────────────┬─────────────┘               └──────────────┬────────────────┘
                 │  cache.update(ticker, price, ts, session_open)
                 └──────────────────────┬──────────────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ PriceCache (thread-safe)     │
                         │  dict[ticker → PriceUpdate]  │
                         │  version counter             │
                         └──┬───────────┬───────────┬───┘
                            │           │           │
               SSE /api/stream/prices   │     Portfolio valuation,
               (poll version, push)     │     snapshots, LLM context
                                        │
                              Trade execution (fill price)
```

**Principles**

1. **Producers write, consumers read.** Data sources never return prices to callers; they push into `PriceCache`. Everything else reads the cache. This keeps routes synchronous-cheap and source-agnostic.
2. **One producer at a time.** Exactly one `MarketDataSource` exists per process, created at startup.
3. **`PriceUpdate` is the only type that leaves the package.** It is immutable and serialises itself (`to_dict()`), so the SSE wire format is defined in one place.
4. **Failure is local.** A failed tick or poll is logged and skipped; the background task never dies and the SSE stream keeps serving the last known prices.

### File layout

```
backend/app/market/
  __init__.py         # Public API re-exports
  models.py           # PriceUpdate
  tickers.py          # Δ normalize_ticker(), InvalidTickerError
  cache.py            # PriceCache
  interface.py        # MarketDataSource (ABC)
  seed_prices.py      # Seed prices, GBM params, correlation groups
  simulator.py        # GBMSimulator + SimulatorDataSource
  massive_client.py   # MassiveDataSource + parse_snapshot()
  factory.py          # create_market_data_source(), MarketDataSettings
  stream.py           # create_stream_router() — SSE endpoint
```

---

## 3. Data Model — `models.py`

`PriceUpdate` is a frozen, slotted dataclass. Derived values are properties, so they can never disagree with the stored fields.

**Δ** It gains one optional field, `session_open`: the reference price for "daily change" (R7). The simulator sets it to the ticker's seed price (the price when the session started); Massive sets it to the previous trading day's close, which is how brokers define daily change. All existing fields, properties and `to_dict()` keys are unchanged; two keys are added.

```python
"""Data models for market data."""

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
    session_open: float | None = None  # Δ reference for daily change

    # --- tick-to-tick (drives the green/red flash) ---

    @property
    def change(self) -> float:
        """Absolute price change from the previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from the previous update."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat' relative to the previous update."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    # --- Δ daily (drives the watchlist "Chg %" column) ---

    @property
    def day_change(self) -> float:
        if not self.session_open:
            return 0.0
        return round(self.price - self.session_open, 4)

    @property
    def day_change_percent(self) -> float:
        if not self.session_open:
            return 0.0
        return round((self.price - self.session_open) / self.session_open * 100, 4)

    def to_dict(self) -> dict:
        """Serialise for JSON / SSE. This IS the wire format (see §10.2)."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "session_open": self.session_open,          # Δ
            "day_change_percent": self.day_change_percent,  # Δ
        }
```

Example:

```python
>>> u = PriceUpdate("AAPL", price=191.20, previous_price=191.05,
...                 timestamp=1731240000.0, session_open=190.00)
>>> u.direction, u.change, u.day_change_percent
('up', 0.15, 0.6316)
```

---

## 4. Ticker Normalisation — `tickers.py`

**Δ New module.** Today the Massive source upper-cases tickers but the simulator does not, so `"aapl"` added via the simulator becomes a second, lower-case ticker. Normalisation belongs in one place, applied at the boundary (routes and the LLM action executor) **and** defensively inside both sources.

```python
"""Ticker symbol normalisation and validation."""

from __future__ import annotations

import re

# 1–5 letters, optional class suffix: BRK.B, BF-B. Covers US listed equities.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")


class InvalidTickerError(ValueError):
    """Raised when a ticker symbol is malformed."""


def normalize_ticker(raw: str) -> str:
    """Upper-case and strip a ticker; raise InvalidTickerError if malformed.

    >>> normalize_ticker("  aapl ")
    'AAPL'
    >>> normalize_ticker("brk.b")
    'BRK.B'
    """
    ticker = (raw or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker
```

Routes turn `InvalidTickerError` into HTTP 400 (§11.3). Format validity is all we check: the simulator can price any symbol, and for Massive an unknown symbol simply never appears in the snapshot response (§12.4).

---

## 5. Price Cache — `cache.py`

Thread-safe because the Massive client parses results in a worker thread (`asyncio.to_thread`) while route handlers read on the event loop. The critical sections are a few dict operations, so a plain `threading.Lock` is sufficient.

The `version` counter increases on every write. The SSE generator compares it to the last version it sent, so it skips pushes when nothing changed (common in Massive mode, where data changes every 15 s but SSE ticks every 0.5 s).

**Δ changes:** remembers a per-ticker `session_open`; reads `version` under the lock; treats `timestamp=0.0` as a real value (`is None` instead of `or`).

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest price per ticker.

    Writers: exactly one MarketDataSource.
    Readers: SSE stream, portfolio valuation, trade execution, LLM context.
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
        session_open: float | None = None,
    ) -> PriceUpdate:
        """Record a new price and return the resulting PriceUpdate.

        - First update for a ticker: previous_price == price (direction 'flat').
        - session_open: if None, the previously stored value is kept; if the ticker
          has never had one, the first observed price is used. So the simulator
          only passes it once (at seed time), and Massive passes prev close on
          every poll (it changes at the daily rollover).
        """
        with self._lock:
            ts = time.time() if timestamp is None else timestamp
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            if session_open is None:
                session_open = prev.session_open if prev and prev.session_open else price

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
                session_open=round(session_open, 2),
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
        """Shallow copy — safe to iterate without holding the lock."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1  # Δ so SSE pushes the shrunken set promptly

    @property
    def version(self) -> int:
        with self._lock:  # Δ
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

Usage:

```python
cache = PriceCache()
cache.update("AAPL", 190.00)            # first: flat, session_open=190.00
u = cache.update("AAPL", 190.37)        # up, change=+0.37, day_change_percent=0.1947
cache.get_price("AAPL")                 # 190.37
cache.get_all()                         # {"AAPL": PriceUpdate(...)}
```

---

## 6. Unified Interface — `interface.py`

Unchanged from v1. The contract callers rely on:

| Method | Contract |
|---|---|
| `start(tickers)` | Called once. Starts the background task. **Must leave the cache populated for every ticker it can price before returning** (simulator: seed prices; Massive: one synchronous poll). |
| `stop()` | Cancels the task and awaits it. Idempotent. No cache writes afterwards. |
| `add_ticker(t)` | No-op if present. Simulator prices it immediately; Massive prices it on the next poll (**Δ** which is brought forward, §8.4). |
| `remove_ticker(t)` | No-op if absent. Also removes it from the cache. |
| `get_tickers()` | Snapshot list of tracked tickers. |

```python
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push PriceUpdates into a shared PriceCache on their own
    schedule. Downstream code reads the cache, never the source.

        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL"])
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        await source.stop()
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None: ...

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None: ...

    @abstractmethod
    def get_tickers(self) -> list[str]: ...
```

(Docstrings as in the current file.)

---

## 7. Simulator

### 7.1 Model

Each tick, every ticker's price moves by geometric Brownian motion:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

| Symbol | Meaning | Value |
|---|---|---|
| μ | annualised drift | 0.03–0.08 per ticker |
| σ | annualised volatility | 0.17 (V) – 0.50 (TSLA) |
| dt | tick length as a fraction of a trading year | 0.5 s / (252 · 6.5 · 3600 s) ≈ 8.48e-8 |
| Z | correlated standard normal | `L @ z`, `L = cholesky(C)` |

Per-tick standard deviation is σ·√dt: for AAPL 0.22 · 2.9e-4 ≈ 0.0064 %, about 1.2 ¢ on $190. That is small but visible at 2 ticks/s, and scales to a realistic ~1.4 % daily range. Prices can't go negative because `exp()` is always positive.

**Correlation.** `C` is built pairwise from sector groups: tech–tech 0.6, finance–finance 0.5, TSLA with anything 0.3, everything else 0.3. A matrix with a constant off-diagonal block structure like this is positive definite, so Cholesky always succeeds. Unknown tickers fall into the 0.3 default, which keeps it valid for any number of added tickers.

**Events.** With probability `p = 0.001` per ticker per tick, the price is multiplied by `1 ± U(0.02, 0.05)`. With 10 tickers at 2 ticks/s that is one visible jump roughly every 50 s.

### 7.2 Seed data — `seed_prices.py`

Unchanged from v1:

```python
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 800.00, "META": 500.00, "JPM": 195.00, "V": 280.00, "NFLX": 600.00,
}

TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05}, "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05}, "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03}, "NVDA": {"sigma": 0.40, "mu": 0.08},
    "META": {"sigma": 0.30, "mu": 0.05}, "JPM": {"sigma": 0.18, "mu": 0.04},
    "V": {"sigma": 0.17, "mu": 0.04},    "NFLX": {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}
INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR = 0.3
TSLA_CORR = 0.3
```

Tickers not in `SEED_PRICES` start at a random price in [$50, $300] with `DEFAULT_PARAMS`.

### 7.3 `GBMSimulator` — pure math, no I/O

**Δ changes:** an optional `seed` gives fully reproducible paths (unit tests, E2E screenshots), using private RNG instances instead of the global `np.random` / `random` state; `session_open()` exposes the seed price for the cache; and `add_ticker` normalises case.

```python
"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS, CROSS_GROUP_CORR, DEFAULT_PARAMS, INTRA_FINANCE_CORR,
    INTRA_TECH_CORR, SEED_PRICES, TICKER_PARAMS, TSLA_CORR,
)

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Correlated geometric Brownian motion for a dynamic set of tickers."""

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        seed: int | None = None,  # Δ
    ) -> None:
        self._dt = dt
        self._sqrt_dt = math.sqrt(dt)
        self._event_prob = event_probability
        self._np_rng = np.random.default_rng(seed)  # Δ private RNGs
        self._py_rng = random.Random(seed)

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._opens: dict[str, float] = {}  # Δ seed price per ticker
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick. Returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._np_rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            p = self._params[ticker]
            drift = (p["mu"] - 0.5 * p["sigma"] ** 2) * self._dt
            diffusion = p["sigma"] * self._sqrt_dt * z[i]
            price = self._prices[ticker] * math.exp(drift + diffusion)

            if self._py_rng.random() < self._event_prob:
                shock = self._py_rng.uniform(0.02, 0.05) * self._py_rng.choice((-1, 1))
                price *= 1 + shock
                logger.debug("Event on %s: %+.1f%%", ticker, shock * 100)

            self._prices[ticker] = price  # keep full precision internally
            result[ticker] = round(price, 2)
        return result

    def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()  # Δ
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker], self._opens[ticker], self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def session_open(self, ticker: str) -> float | None:  # Δ
        return self._opens.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker in self._prices:
            return
        seed_price = SEED_PRICES.get(ticker) or round(self._py_rng.uniform(50.0, 300.0), 2)
        self._tickers.append(ticker)
        self._prices[ticker] = seed_price
        self._opens[ticker] = seed_price
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

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
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        tech, finance = CORRELATION_GROUPS["tech"], CORRELATION_GROUPS["finance"]
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR
```

Example:

```python
>>> sim = GBMSimulator(["AAPL", "MSFT", "JPM"], seed=42)
>>> sim.step()
{'AAPL': 190.01, 'MSFT': 420.02, 'JPM': 194.99}   # illustrative values
>>> sim.add_ticker("pypl")      # normalised to PYPL, random seed price, Cholesky rebuilt
>>> sim.session_open("PYPL")
137.84
```

### 7.4 `SimulatorDataSource` — async wrapper

Owns the background task. It writes the seed prices to the cache in `start()` and `add_ticker()`, so the SSE stream and trade endpoint never see a gap.

**Δ changes:** passes `session_open`; uses a fixed-rate loop (sleep for the *remainder* of the interval so step cost doesn't stretch the cadence); accepts `seed`.

```python
class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by GBMSimulator, ticking every `update_interval` s."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed = seed
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(
            tickers=tickers, event_probability=self._event_prob, seed=self._seed
        )
        for ticker in self._sim.get_tickers():
            self._seed_cache(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(self._sim.get_tickers()))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        if self._sim:
            ticker = ticker.upper().strip()
            self._sim.add_ticker(ticker)
            self._seed_cache(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    def _seed_cache(self, ticker: str) -> None:
        price = self._sim.get_price(ticker) if self._sim else None
        if price is not None and ticker not in self._cache:
            self._cache.update(ticker, price, session_open=self._sim.session_open(ticker))

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker, price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(max(0.0, self._interval - (loop.time() - started)))
```

`step()` and `cache.update()` run on the event loop. That's fine: 10–50 tickers take well under 1 ms per step. There's no `await` between `step()` and the cache writes, so a concurrent `remove_ticker()` can't interleave and resurrect a removed ticker.

---

## 8. Massive API Client — `massive_client.py`

### 8.1 Endpoint

One call per poll for all tickers:

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT
```

via `RESTClient.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=[...])`, which returns `list[TickerSnapshot]`. The client is synchronous (urllib3), so it runs in `asyncio.to_thread`.

### 8.2 Response model (verified against the installed `massive` package)

`TickerSnapshot` fields that matter, with their raw JSON keys:

| Attribute | JSON | Type | Notes |
|---|---|---|---|
| `ticker` | `ticker` | `str` | |
| `last_trade.price` | `lastTrade.p` | `float` | Primary price |
| `last_trade.sip_timestamp` | `lastTrade.t` | `int` | **Nanoseconds** since epoch |
| `min.close` / `min.timestamp` | `min.c` / `min.t` | `float` / `int` (ms) | Latest minute bar — fallback |
| `day.close` | `day.c` | `float` | Today's bar so far — fallback |
| `prev_day.close` | `prevDay.c` | `float` | Previous session close → `session_open` |
| `todays_change_percent` | `todaysChangePerc` | `float` | Cross-check only |
| `updated` | `updated` | `int` | Nanoseconds; last update of the snapshot |

> **Δ Bug in the current code.** `massive_client.py` reads `snap.last_trade.timestamp`, but `LastTrade` has **no** `timestamp` attribute (it has `sip_timestamp`, `participant_timestamp`, `trf_timestamp`). Every real snapshot raises `AttributeError`, so it is logged and skipped, and Massive mode never produces a price. The unit tests don't catch this because `MagicMock` invents any attribute you ask for. Verified with:
>
> ```python
> >>> from massive.rest.models import TickerSnapshot
> >>> s = TickerSnapshot.from_dict({"ticker": "AAPL", "lastTrade": {"p": 190.5, "t": 1707580800000000000}})
> >>> s.last_trade.timestamp
> AttributeError: 'LastTrade' object has no attribute 'timestamp'
> ```
>
> The fix is `parse_snapshot()` below; tests must build real `TickerSnapshot` objects with `from_dict` (§13.3).

### 8.3 Parsing — a pure function

Parsing is separated from I/O so it can be tested with real model objects and no network. Price fallback order: last trade → latest minute bar → today's bar → previous close. Early pre-market and holidays often have no `lastTrade` in today's snapshot, so a quiet ticker still gets a price.

```python
"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from massive import RESTClient
from massive.rest.models import SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedQuote:
    ticker: str
    price: float
    timestamp: float            # Unix seconds
    session_open: float | None  # previous close


def _ns_to_s(ns: int | None) -> float | None:
    return ns / 1e9 if ns else None


def _ms_to_s(ms: int | None) -> float | None:
    return ms / 1e3 if ms else None


def parse_snapshot(snap: TickerSnapshot) -> ParsedQuote | None:
    """Extract (ticker, price, timestamp, prev close) or None if unusable."""
    ticker = getattr(snap, "ticker", None)
    if not ticker:
        return None

    lt, mn, day, prev = snap.last_trade, snap.min, snap.day, snap.prev_day
    candidates: list[tuple[float | None, float | None]] = [
        (lt.price if lt else None, _ns_to_s(lt.sip_timestamp) if lt else None),
        (mn.close if mn else None, _ms_to_s(mn.timestamp) if mn else None),
        (day.close if day else None, _ns_to_s(snap.updated)),
        (prev.close if prev else None, _ms_to_s(prev.timestamp) if prev else None),
    ]
    for price, ts in candidates:
        if price is not None and price > 0:
            return ParsedQuote(
                ticker=ticker,
                price=float(price),
                timestamp=ts or time.time(),
                session_open=float(prev.close) if prev and prev.close else None,
            )
    return None
```

Example:

```python
>>> snap = TickerSnapshot.from_dict({
...     "ticker": "AAPL",
...     "lastTrade": {"p": 191.12, "t": 1731243600123456789},
...     "prevDay": {"c": 189.50, "t": 1731042000000},
... })
>>> parse_snapshot(snap)
ParsedQuote(ticker='AAPL', price=191.12, timestamp=1731243600.1234567, session_open=189.5)
```

### 8.4 `MassiveDataSource`

**Δ changes:**

- Uses `parse_snapshot()` (fixes the bug).
- Passes a **copy** of the ticker list to the worker thread, so `add_ticker` on the event loop can't mutate it mid-request.
- `add_ticker` **wakes the poller early** through an `asyncio.Event` instead of leaving a new ticker blank for up to 15 s, while still respecting a minimum gap between calls (`min_poll_gap`, default 12 s, under the free tier's 5 calls/min).
- Backs off exponentially on errors (429 or network), up to 120 s, and resets on success.
- Poll interval comes from config (`MASSIVE_POLL_INTERVAL`).

```python
class MassiveDataSource(MarketDataSource):
    """Polls the Massive snapshot endpoint and writes results to the PriceCache.

    Free tier: 5 req/min → poll_interval 15 s (default), min_poll_gap 12 s.
    Paid tiers: poll_interval 2–5 s, min_poll_gap ~1 s.
    """

    MAX_BACKOFF = 120.0

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
        min_poll_gap: float = 12.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._min_gap = min(min_poll_gap, poll_interval)
        self._tickers: list[str] = []
        self._client: RESTClient | None = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_poll = 0.0  # loop.time() of last API call
        self._failures = 0

    # --- lifecycle ---

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys(t.upper().strip() for t in tickers))
        await self._poll_once()  # populate the cache before serving
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, %.1fs interval",
                    len(self._tickers), self._interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None

    # --- ticker set ---

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            self._wake.set()  # Δ fetch it soon rather than in up to 15 s

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- polling ---

    async def _poll_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            delay = self._interval if self._failures == 0 else min(
                self._interval * 2 ** self._failures, self.MAX_BACKOFF
            )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass
            self._wake.clear()

            # Respect the rate limit even when woken early.
            gap = self._min_gap - (loop.time() - self._last_poll)
            if gap > 0:
                await asyncio.sleep(gap)
            await self._poll_once()

    async def _poll_once(self) -> None:
        tickers = list(self._tickers)  # Δ snapshot for the worker thread
        if not tickers or not self._client:
            return
        self._last_poll = asyncio.get_running_loop().time()
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        except Exception as e:  # 401/403/429/5xx/network — never kill the loop
            self._failures += 1
            logger.error("Massive poll failed (%d in a row): %s", self._failures, e)
            return

        self._failures = 0
        active = set(self._tickers)  # drop tickers removed while we were fetching
        updated = 0
        for snap in snapshots:
            quote = parse_snapshot(snap)
            if quote is None:
                logger.warning("Unusable snapshot for %s", getattr(snap, "ticker", "?"))
                continue
            if quote.ticker not in active:
                continue
            self._cache.update(quote.ticker, quote.price, quote.timestamp, quote.session_open)
            updated += 1

        missing = active - {getattr(s, "ticker", None) for s in snapshots}
        if missing:
            logger.debug("No snapshot returned for: %s", ", ".join(sorted(missing)))
        logger.debug("Massive poll: %d/%d tickers updated", updated, len(tickers))

    def _fetch_snapshots(self, tickers: list[str]) -> list[TickerSnapshot]:
        """Blocking HTTP call — runs in a worker thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
```

Timeline (free tier, `poll_interval=15`, `min_poll_gap=12`):

```
t=0    start(): poll #1 (10 tickers) → cache filled
t=15   poll #2
t=20   user adds PYPL → wake set
t=27   poll #3 (gap from t=15 is 12 s) → PYPL priced 7 s after add, not 10 s later
t=42   poll #4
```

**Entitlements.** Snapshot data availability depends on the Massive plan. If a plan doesn't include snapshots, the call fails with 403. That is logged and backed off like any other error, and the UI keeps showing "no price" for those tickers. The recommended default for students is the simulator (no key).

---

## 9. Factory & Configuration — `factory.py`

**Δ** Optional tuning moves into environment variables, read once by a small settings object. `MASSIVE_API_KEY` remains the only switch that matters; the rest have sensible defaults and don't need adding to `.env.example` unless wanted.

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty → Massive; else simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between polls (use 2–5 on paid tiers) |
| `SIMULATOR_INTERVAL` | `0.5` | Seconds between simulator ticks |
| `SIMULATOR_SEED` | empty | Integer → deterministic simulator (tests / demos) |

```python
"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning("Ignoring invalid %s=%r", name, raw)
        return default


@dataclass(frozen=True)
class MarketDataSettings:
    massive_api_key: str
    massive_poll_interval: float
    simulator_interval: float
    simulator_seed: int | None

    @classmethod
    def from_env(cls) -> MarketDataSettings:
        seed_raw = os.environ.get("SIMULATOR_SEED", "").strip()
        return cls(
            massive_api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
            massive_poll_interval=_env_float("MASSIVE_POLL_INTERVAL", 15.0),
            simulator_interval=_env_float("SIMULATOR_INTERVAL", 0.5),
            simulator_seed=int(seed_raw) if seed_raw.lstrip("-").isdigit() else None,
        )


def create_market_data_source(
    price_cache: PriceCache,
    settings: MarketDataSettings | None = None,
) -> MarketDataSource:
    """Return an unstarted source. Caller must `await source.start(tickers)`."""
    s = settings or MarketDataSettings.from_env()
    if s.massive_api_key:
        logger.info("Market data source: Massive API (poll every %.1fs)", s.massive_poll_interval)
        return MassiveDataSource(
            api_key=s.massive_api_key,
            price_cache=price_cache,
            poll_interval=s.massive_poll_interval,
            min_poll_gap=min(12.0, s.massive_poll_interval),
        )
    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(
        price_cache=price_cache,
        update_interval=s.simulator_interval,
        seed=s.simulator_seed,
    )
```

The existing signature `create_market_data_source(cache)` still works, so the current factory tests pass unchanged.

---

## 10. SSE Streaming — `stream.py`

### 10.1 Endpoint

**Δ changes:**

- The router is created **inside** the factory. Today it's a module-level `router`, so calling `create_stream_router` twice (e.g. two test apps) registers `/prices` twice on the same object.
- A **keep-alive comment** (`: ping`) is sent every 15 s when nothing changed. In Massive mode, data can be unchanged for 15 s or more, and idle connections get closed by some proxies (App Runner, Render).
- Each event carries an `id:` (the cache version), which is useful for debugging in browser DevTools.

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

PUSH_INTERVAL = 0.5     # seconds between cache checks
KEEPALIVE_EVERY = 15.0  # seconds of silence before a comment line
RETRY_MS = 1000         # EventSource reconnect delay


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])  # Δ fresh per call

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = PUSH_INTERVAL,
) -> AsyncGenerator[str, None]:
    yield f"retry: {RETRY_MS}\n\n"

    last_version = -1
    since_last_send = 0.0
    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)
    try:
        while not await request.is_disconnected():
            version = price_cache.version
            if version != last_version:
                last_version = version
                prices = price_cache.get_all()
                payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
                yield f"id: {version}\ndata: {payload}\n\n"
                since_last_send = 0.0
            elif since_last_send >= KEEPALIVE_EVERY:
                yield ": ping\n\n"
                since_last_send = 0.0
            await asyncio.sleep(interval)
            since_last_send += interval
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("SSE client disconnected: %s", client)
```

The stream sends an empty object `{}` when the watchlist is empty, so the frontend can clear its grid. (Today nothing is sent when the cache is empty, which leaves a removed last ticker visible.)

### 10.2 Wire format

One event = the full current snapshot, keyed by ticker. Sending the full map instead of deltas means a reconnecting client is consistent after a single event, and removed tickers disappear naturally.

```
retry: 1000

id: 5121
data: {"AAPL":{"ticker":"AAPL","price":190.37,"previous_price":190.31,"timestamp":1731240000.512,"change":0.06,"change_percent":0.0315,"direction":"up","session_open":190.0,"day_change_percent":0.1947},"TSLA":{...}}

: ping

```

| Field | Use in the UI |
|---|---|
| `price` | Displayed price, sparkline point, main chart point |
| `previous_price`, `direction` | Green/red flash (`direction !== "flat"`) |
| `day_change_percent` | Watchlist "Chg %" column (R7) |
| `timestamp` | Chart x-axis (Unix seconds) |

### 10.3 Frontend consumption (reference)

```ts
type PriceUpdate = {
  ticker: string; price: number; previous_price: number; timestamp: number;
  change: number; change_percent: number; direction: "up" | "down" | "flat";
  session_open: number | null; day_change_percent: number;
};

const es = new EventSource("/api/stream/prices");
es.onopen = () => setStatus("connected");
es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
es.onmessage = (e) => {
  const snapshot: Record<string, PriceUpdate> = JSON.parse(e.data);
  for (const u of Object.values(snapshot)) {
    // Only append to the sparkline when the timestamp advanced — in Massive mode the
    // same quote is re-sent until the next poll.
    appendIfNew(u.ticker, u.timestamp, u.price);
    if (u.direction !== "flat") flash(u.ticker, u.direction);
  }
  setPrices(snapshot);
};
```

---

## 11. FastAPI Integration

### 11.1 Lifespan — `backend/app/main.py`

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import get_tracked_tickers, init_db   # owned by the backend/DB agent
from app.market import PriceCache, create_market_data_source, create_stream_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                                       # lazy schema + seed (PLAN §7)

    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(get_tracked_tickers())       # watchlist ∪ open positions
    app.state.price_cache = cache
    app.state.market_source = source

    snapshot_task = start_snapshot_task(app)        # 30 s portfolio snapshots (PLAN §7)
    try:
        yield
    finally:
        snapshot_task.cancel()
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)
```

The SSE router needs the cache when it's built, but the cache is created inside the lifespan. To avoid that ordering problem, create the cache at module level and pass it into both:

```python
price_cache = PriceCache()                          # module level: one per process
app = FastAPI(title="FinAlly", lifespan=lifespan)   # lifespan uses the same price_cache
app.include_router(create_stream_router(price_cache))
# ... other API routers ...
# Static export mounted LAST so /api/* wins:
app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

### 11.2 Dependencies for routes

```python
# backend/app/deps.py
from fastapi import Request

from app.market import MarketDataSource, PriceCache


def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
```

### 11.3 Which tickers are tracked — watchlist ∪ positions

The source must price every ticker the user **watches or holds**. Otherwise a held position that has been removed from the watchlist has no price, and portfolio value breaks. The rule is:

```
tracked = watchlist_tickers ∪ { ticker | position.quantity > 0 }
```

It is applied in three places: at startup (`get_tracked_tickers()`), when removing from the watchlist, and after a sell closes a position.

```python
# backend/app/routes/watchlist.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.deps import get_market_source, get_price_cache
from app.market import MarketDataSource, PriceCache
from app.market.tickers import InvalidTickerError, normalize_ticker

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistAdd(BaseModel):
    ticker: str


@router.get("")
async def list_watchlist(cache: PriceCache = Depends(get_price_cache)):
    return [
        {"ticker": t, **(u.to_dict() if (u := cache.get(t)) else {"price": None})}
        for t in db.get_watchlist()
    ]


@router.post("", status_code=201)
async def add_ticker(
    body: WatchlistAdd,
    source: MarketDataSource = Depends(get_market_source),
    cache: PriceCache = Depends(get_price_cache),
):
    try:
        ticker = normalize_ticker(body.ticker)
    except InvalidTickerError as e:
        raise HTTPException(400, str(e))
    db.add_watchlist(ticker)                 # idempotent (UNIQUE(user_id, ticker))
    await source.add_ticker(ticker)          # no-op if already tracked
    u = cache.get(ticker)                    # simulator: present; Massive: maybe next poll
    return {"ticker": ticker, "price": u.price if u else None}


@router.delete("/{ticker}")
async def remove_ticker(ticker: str, source: MarketDataSource = Depends(get_market_source)):
    ticker = ticker.strip().upper()
    if not db.remove_watchlist(ticker):
        raise HTTPException(404, f"{ticker} is not in the watchlist")
    if not db.has_open_position(ticker):     # keep pricing held tickers
        await source.remove_ticker(ticker)
    return {"ticker": ticker, "removed": True}
```

The LLM chat flow (PLAN §9) applies `watchlist_changes` through the same helper functions, not by calling the source directly, so the rule holds for both paths.

### 11.4 Trade execution reads the cache

```python
@router.post("/api/portfolio/trade")
async def trade(
    req: TradeRequest,
    cache: PriceCache = Depends(get_price_cache),
    source: MarketDataSource = Depends(get_market_source),
):
    ticker = normalize_ticker(req.ticker)
    if ticker not in source.get_tickers():
        await source.add_ticker(ticker)      # buying an unwatched ticker starts pricing it
    price = cache.get_price(ticker)
    if price is None:                        # Massive: not polled yet
        raise HTTPException(409, f"No price yet for {ticker}; try again in a few seconds")
    result = db.execute_trade(ticker, req.side, req.quantity, price)   # validation lives there
    if result.position_closed and ticker not in db.get_watchlist():
        await source.remove_ticker(ticker)
    record_snapshot_now(cache)               # PLAN §7: snapshot after each trade
    return result
```

Portfolio valuation (`GET /api/portfolio`, the 30 s snapshot task, and the LLM context) uses `cache.get_price(t)`, falling back to `avg_cost` if there is no price, so a missing quote never crashes valuation.

---

## 12. Error Handling & Edge Cases

| # | Situation | Behaviour |
|---|---|---|
| 12.1 | Empty watchlist at startup | Both sources start with no tickers; simulator `step()` returns `{}`; Massive skips the API call; SSE sends `{}`. |
| 12.2 | Simulator step raises | Logged with traceback; the loop continues on the next tick. |
| 12.3 | Massive 401 (bad key) / 403 (plan) | Logged each attempt with exponential backoff to 120 s; cache stays empty for those tickers; UI shows "—". Fix the key and restart. |
| 12.4 | Massive unknown ticker (`ZZZZ`) | Absent from the response → debug log; ticker stays unpriced; trade returns 409. The watchlist add still succeeds (format-valid); the UI shows "—". |
| 12.5 | Massive 429 | Same as any error: backoff, then normal cadence after the next success. |
| 12.6 | Massive market closed | Snapshot still returns the last trade / day close; prices are static and SSE goes quiet apart from keep-alives. Expected. |
| 12.7 | Ticker removed during an in-flight poll | Results for tickers no longer in `self._tickers` are dropped, so the ticker isn't resurrected in the cache. |
| 12.8 | Ticker re-added after removal (simulator) | Restarts from its seed price and resets `session_open`. Acceptable for a demo. |
| 12.9 | Client disconnects SSE | `request.is_disconnected()` ends the generator; Starlette cancels it on shutdown. |
| 12.10 | Server restart | The browser's EventSource reconnects after `retry: 1000` ms; the first event is a full snapshot. |
| 12.11 | Thread safety | Only `PriceCache` is touched by more than one thread (Massive parsing happens on the loop after `to_thread` returns, but the lock makes cache access safe either way). `GBMSimulator` is only touched on the event loop. |

---

## 13. Testing

Run with:

```bash
cd backend && uv run --extra dev pytest -v
```

The code in this document was assembled into a scratch package and checked: all tests below pass, and 71 of the current 73 tests pass unchanged. The two that fail, `test_massive.py::test_malformed_snapshot_skipped` and `::test_timestamp_conversion`, build `MagicMock` snapshots with the non-existent `last_trade.timestamp`. They are replaced by §13.3–13.4 (change list item 3).

The tests below are added for the **Δ** items.

### 13.1 Models / cache

```python
from app.market.cache import PriceCache
from app.market.models import PriceUpdate


def test_day_change_percent():
    u = PriceUpdate("AAPL", 191.90, 191.80, 0.0, session_open=190.00)
    assert u.day_change_percent == 1.0
    assert u.to_dict()["day_change_percent"] == 1.0


def test_session_open_defaults_to_first_price_and_sticks():
    c = PriceCache()
    c.update("AAPL", 190.0)
    assert c.update("AAPL", 192.0).session_open == 190.0


def test_explicit_session_open_overrides():
    c = PriceCache()
    c.update("AAPL", 191.0, session_open=189.5)
    assert c.get("AAPL").day_change_percent == round(1.5 / 189.5 * 100, 4)


def test_remove_bumps_version():
    c = PriceCache()
    c.update("AAPL", 190.0)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
```

### 13.2 Simulator determinism and full default set

```python
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator


def test_seeded_simulator_is_reproducible():
    a = GBMSimulator(list(SEED_PRICES), seed=7)
    b = GBMSimulator(list(SEED_PRICES), seed=7)
    assert [a.step() for _ in range(100)] == [b.step() for _ in range(100)]


def test_cholesky_valid_for_defaults_plus_unknowns():
    sim = GBMSimulator(list(SEED_PRICES) + ["PYPL", "UBER", "BRK.B"], seed=1)
    assert sim._cholesky is not None
    assert len(sim.step()) == 13


def test_add_ticker_normalises_case():
    sim = GBMSimulator(["AAPL"], seed=1)
    sim.add_ticker(" pypl ")
    assert "PYPL" in sim.get_tickers()


def test_correlation_is_observable():
    import numpy as np
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], event_probability=0.0, seed=3)
    rets = {t: [] for t in sim.get_tickers()}
    prev = {t: sim.get_price(t) for t in rets}
    for _ in range(5000):
        for t, p in sim.step().items():
            rets[t].append(np.log(sim.get_price(t) / prev[t]))
            prev[t] = sim.get_price(t)
    rho = np.corrcoef(rets["AAPL"], rets["MSFT"])[0, 1]
    assert 0.5 < rho < 0.7   # target 0.6
```

### 13.3 Massive parsing with **real** model objects (catches the §8.2 bug)

```python
from massive.rest.models import TickerSnapshot

from app.market.massive_client import parse_snapshot


def snap(**d) -> TickerSnapshot:
    return TickerSnapshot.from_dict(d)


def test_parse_last_trade_nanoseconds():
    q = parse_snapshot(snap(ticker="AAPL",
                            lastTrade={"p": 191.12, "t": 1_731_243_600_000_000_000},
                            prevDay={"c": 189.5}))
    assert q.price == 191.12
    assert q.timestamp == 1_731_243_600.0
    assert q.session_open == 189.5


def test_parse_falls_back_to_minute_bar():
    q = parse_snapshot(snap(ticker="AAPL", min={"c": 190.0, "t": 1_731_243_600_000}))
    assert q.price == 190.0 and q.timestamp == 1_731_243_600.0


def test_parse_falls_back_to_prev_close():
    assert parse_snapshot(snap(ticker="AAPL", prevDay={"c": 189.5})).price == 189.5


def test_parse_unusable_returns_none():
    assert parse_snapshot(snap(ticker="AAPL")) is None
    assert parse_snapshot(snap(lastTrade={"p": 1.0})) is None
```

### 13.4 Massive source behaviour (no network)

```python
import asyncio
from unittest.mock import patch

from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource


def real(ticker, price):
    return TickerSnapshot.from_dict({"ticker": ticker, "lastTrade": {"p": price, "t": 1}})


async def test_poll_writes_cache_with_real_models():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60)
    src._client, src._tickers = object(), ["AAPL"]
    with patch.object(src, "_fetch_snapshots", return_value=[real("AAPL", 190.5)]):
        await src._poll_once()
    assert cache.get_price("AAPL") == 190.5


async def test_removed_during_fetch_is_not_resurrected():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60)
    src._client, src._tickers = object(), ["AAPL"]

    def fetch(tickers):
        src._tickers = []            # simulate remove_ticker racing the request
        return [real("AAPL", 190.5)]

    with patch.object(src, "_fetch_snapshots", side_effect=fetch):
        await src._poll_once()
    assert "AAPL" not in cache


async def test_add_ticker_wakes_poller_early():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60, min_poll_gap=0)
    calls = []
    with patch("app.market.massive_client.RESTClient"), \
         patch.object(src, "_fetch_snapshots", side_effect=lambda t: calls.append(t) or []):
        await src.start(["AAPL"])
        await src.add_ticker("PYPL")
        await asyncio.sleep(0.05)
        await src.stop()
    assert calls[-1] == ["AAPL", "PYPL"]
```

### 13.5 SSE

The generator is tested directly with a stand-in request, which avoids running an infinite stream through an HTTP test client.

```python
from types import SimpleNamespace

from app.market import PriceCache, create_stream_router
from app.market.stream import _generate_events


class FakeRequest:
    """Stands in for starlette.Request: connected for `ticks` checks, then gone."""

    def __init__(self, ticks: int) -> None:
        self.client = SimpleNamespace(host="test")
        self._ticks = ticks

    async def is_disconnected(self) -> bool:
        self._ticks -= 1
        return self._ticks < 0


def test_router_factory_is_independent():
    c = PriceCache()
    r1, r2 = create_stream_router(c), create_stream_router(c)
    assert r1 is not r2 and len(r1.routes) == 1


async def test_first_event_is_full_snapshot_then_quiet():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    events = [e async for e in _generate_events(cache, FakeRequest(ticks=3), interval=0.01)]
    assert events[0] == "retry: 1000\n\n"
    assert events[1].startswith("id: 1\ndata: ")
    assert '"AAPL"' in events[1]
    assert len(events) == 2          # version unchanged → nothing more sent


async def test_empty_cache_sends_empty_object():
    events = [e async for e in _generate_events(PriceCache(), FakeRequest(ticks=1), interval=0.01)]
    assert events[1].endswith("data: {}\n\n")
```

---

## 14. Change List vs Current Code

Each item is small and independent. Do them in this order; items 1–3 matter most.

| # | File | Change | Why |
|---|---|---|---|
| 1 | `massive_client.py` | Add `parse_snapshot()`; read `last_trade.sip_timestamp` (ns) with fallbacks to `min`, `day`, `prev_day` | **Bug:** `last_trade.timestamp` doesn't exist, so Massive mode produces no prices |
| 2 | `models.py`, `cache.py` | `session_open` field, `day_change` / `day_change_percent`, new `to_dict` keys; cache keeps per-ticker `session_open` | PLAN requires daily change % in the watchlist |
| 3 | `tests/market/test_massive.py` | Build snapshots with `TickerSnapshot.from_dict` instead of `MagicMock` | Mocks hid bug #1 |
| 4 | `tickers.py` (new), `simulator.py` | `normalize_ticker()`; simulator upper-cases like Massive | Avoids duplicate `aapl` / `AAPL` |
| 5 | `massive_client.py` | Copy ticker list into the thread; drop results for removed tickers; wake on `add_ticker` with a min-gap; exponential backoff | Races, faster first price, rate-limit hygiene |
| 6 | `simulator.py` | `seed` → private `np.random.Generator` / `random.Random`; `session_open()`; fixed-rate loop | Reproducible tests and demos; steady cadence |
| 7 | `stream.py` | Router created per call; keep-alive comments; `id:` lines; send `{}` when empty | Duplicate-route footgun, proxy idle timeouts, cleared UI |
| 8 | `cache.py` | `version` under lock; `remove()` bumps version; `timestamp is None` check | Consistency; prompt SSE push after removal |
| 9 | `factory.py` | `MarketDataSettings.from_env()` with `MASSIVE_POLL_INTERVAL`, `SIMULATOR_INTERVAL`, `SIMULATOR_SEED` | Paid-tier tuning, deterministic E2E without code changes |
| 10 | routes (new) | Track watchlist ∪ open positions (§11.3–11.4) | Held tickers stay priced after removal from the watchlist |

The public API (`from app.market import PriceUpdate, PriceCache, MarketDataSource, create_market_data_source, create_stream_router`) is unchanged.
