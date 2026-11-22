# Market Simulator: Approach and Code Structure

_Updated 2026-11-10. Describes `backend/app/market/simulator.py` and `seed_prices.py` as implemented; every figure in §2.4 was recomputed by running the math. Changes still to make are marked 🔧 and collected in §6. Supersedes `planning/archive/MARKET_SIMULATOR.md`._

The simulator is the default `MarketDataSource` (contract in [`MARKET_INTERFACE.md`](MARKET_INTERFACE.md)), used whenever `MASSIVE_API_KEY` is empty — which is most users.

## 1. Goals and non-goals

| Goal | How it's met |
|---|---|
| Looks like a live market | Every ticker ticks every 500 ms with small continuous moves |
| Plausible statistics | Geometric Brownian Motion: log-normal, never negative, per-ticker volatility and drift |
| Stocks move together | Correlated shocks via the Cholesky factor of a sector correlation matrix |
| Some drama for the demo | Rare 2–5 % jump "events" |
| Zero setup | Pure Python + NumPy, in-process asyncio task, no network |
| Swappable with real data | Implements `MarketDataSource`, writes only to `PriceCache` |

**Not modeled** (irrelevant for market-orders-only): order books, bid/ask spread, volume, trading hours, mean reversion, overnight gaps.

---

## 2. The math

### 2.1 GBM step

For ticker *i* on each tick:

```
S_i(t+Δt) = S_i(t) · exp( (μ_i − σ_i²/2)·Δt + σ_i·√Δt·Z_i )
```

| Symbol | Meaning | Source |
|---|---|---|
| `S` | price | starts at the seed price |
| `μ` | annualized drift | `TICKER_PARAMS[t]["mu"]` |
| `σ` | annualized volatility | `TICKER_PARAMS[t]["sigma"]` |
| `Δt` | tick length as a fraction of a trading year | `0.5 / (252 × 6.5 × 3600) ≈ 8.48e-8` |
| `Z_i` | correlated standard normal | §2.2 |

- `exp(...)` keeps prices strictly positive.
- `−σ²/2` (Itô correction) makes the *expected* price grow at rate `μ`.
- `Δt` is calibrated to **trading time**, so one simulated second has the volatility of one real trading second.

### 2.2 Correlated shocks

1. Build `C` (n × n) from sector rules (§3.2).
2. Factor once: `L = cholesky(C)`, so `L·Lᵀ = C`.
3. Each tick draw `z ~ N(0, Iₙ)` and use `Z = L·z`; then `Cov(Z) = C`.

`L` is rebuilt only when tickers are added or removed — O(n³), trivial for n < 50. Per tick costs one O(n²) mat-vec.

`C` stays positive definite as long as every within-group ρ ≥ the cross-group ρ and all ρ < 1 (it is then a positive combination of an all-ones matrix, block matrices and a positive diagonal). Keep that invariant if anyone retunes the constants; otherwise `np.linalg.cholesky` raises `LinAlgError`.

### 2.3 Jump events

After the GBM step, each ticker independently, with probability `p` per tick:

```
S ← S · (1 ± U(0.02, 0.05))      # sign 50/50
```

### 2.4 What the numbers mean (recomputed)

| Quantity (AAPL, σ = 0.22, $190) | Value |
|---|---|
| GBM σ per tick | 0.0064 % ≈ **$0.012** |
| GBM σ over 1 hour | **0.54 %** |
| Jumps per ticker-hour at `p = 0.001` (current) | 7.2 → hourly jump σ **≈ 9.7 %** |
| Jumps per ticker-hour at `p = 0.0002` (🔧 proposed) | 1.44 → hourly jump σ **≈ 4.3 %** |
| Across 10 tickers | one jump every ~50 s (current) vs ~4 min (proposed) |

At the current `p = 0.001` jumps move prices ~18× more than diffusion: after an hour the demo looks like a casino and the P&L chart is noise. 🔧 Lower the default to `0.0002` (change S2).

Because the per-tick move is ~1 cent, about a third of AAPL ticks round to the same cent and arrive as `direction == "flat"`. That's fine — no flash on those ticks. If the market looks too quiet, scale `dt` (e.g. `dt = DEFAULT_DT * 10`); no new parameter is needed.

---

## 3. Parameters (`seed_prices.py`)

### 3.1 Seeds, volatility, drift

| Ticker | Seed $ | σ | μ | Character |
|---|---|---|---|---|
| AAPL | 190 | 0.22 | 0.05 | large-cap tech |
| GOOGL | 175 | 0.25 | 0.05 | |
| MSFT | 420 | 0.20 | 0.05 | steadiest tech |
| AMZN | 185 | 0.28 | 0.05 | |
| TSLA | 250 | **0.50** | 0.03 | very volatile, idiosyncratic |
| NVDA | 800 | **0.40** | **0.08** | volatile, strong drift |
| META | 500 | 0.30 | 0.05 | |
| JPM | 195 | 0.18 | 0.04 | bank, calm |
| V | 280 | 0.17 | 0.04 | payments, calmest |
| NFLX | 600 | 0.35 | 0.05 | |

**Tickers added at runtime** (e.g. `PYPL`) get `DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}` and a seed price:
- today: `random.uniform(50, 300)` — different on every restart, and evaluated eagerly even for known tickers (it consumes RNG state);
- 🔧 instead: deterministic from the symbol, `50 + (zlib.crc32(t.encode()) % 25_000) / 100` → $50.00–$299.99, the same across restarts and in tests (change S4).

### 3.2 Correlation rules

| Pair | ρ | Constant |
|---|---|---|
| Both in `tech` = {AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX} | 0.6 | `INTRA_TECH_CORR` |
| Both in `finance` = {JPM, V} | 0.5 | `INTRA_FINANCE_CORR` |
| Anything else — including TSLA and unknown tickers | 0.3 | `CROSS_GROUP_CORR` |

TSLA is deliberately **not** in `tech`, so it already gets 0.3. The code's separate `TSLA_CORR` constant and `if t1 == "TSLA"` branch are dead logic (same value, and the comment "TSLA is in tech set" is wrong). 🔧 Delete them (change S3). If TSLA ever needs its own behavior, give it its own group.

---

## 4. Code structure

```
backend/app/market/
├── seed_prices.py   # data only: SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS, CORRELATION_GROUPS, ρ constants
├── simulator.py
│   ├── GBMSimulator          # pure, synchronous math engine — no asyncio, no cache
│   └── SimulatorDataSource   # MarketDataSource adapter — asyncio loop + PriceCache writes
├── cache.py         # PriceCache (shared with Massive)
└── interface.py     # MarketDataSource ABC
```

The key design choice is the split: `GBMSimulator` is a pure state machine (deterministic once seeded) that can be unit-tested without an event loop; `SimulatorDataSource` holds only scheduling and I/O.

### 4.1 `GBMSimulator` — math engine

```python
class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600     # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR      # ≈ 8.48e-8

    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT,
                 event_probability: float = 0.0002,          # 🔧 was 0.001
                 seed: int | None = None) -> None            # 🔧 new
    def step(self) -> dict[str, float]          # advance one tick → {ticker: price rounded to 2 dp}
    def add_ticker(self, ticker: str) -> None   # idempotent; rebuilds Cholesky
    def remove_ticker(self, ticker: str) -> None
    def get_price(self, ticker: str) -> float | None
    def get_tickers(self) -> list[str]

    # state
    _tickers: list[str]                  # order == row order of the Cholesky factor
    _prices: dict[str, float]            # full precision; rounding only on output
    _params: dict[str, dict[str, float]]
    _cholesky: np.ndarray | None         # None when n <= 1
    _last_price: dict[str, float]        # 🔧 price at removal, reused on re-add
    _rng: np.random.Generator            # 🔧 replaces global np.random / random
```

`step()` hot path (🔧 shown with the private RNG):

```python
n = len(self._tickers)
if n == 0:
    return {}
z = self._rng.standard_normal(n)
if self._cholesky is not None:
    z = self._cholesky @ z
out = {}
for i, t in enumerate(self._tickers):
    mu, sigma = self._params[t]["mu"], self._params[t]["sigma"]
    self._prices[t] *= math.exp((mu - 0.5 * sigma**2) * self._dt + sigma * math.sqrt(self._dt) * z[i])
    if self._rng.random() < self._event_prob:
        sign = 1 if self._rng.random() < 0.5 else -1
        self._prices[t] *= 1 + sign * self._rng.uniform(0.02, 0.05)
    out[t] = round(self._prices[t], 2)
return out
```

State is kept at full precision; rounding it would drop every sub-cent move and bias the walk.

Adding/removing:

```python
def _add_ticker_internal(self, t: str) -> None:
    if t in self._prices:
        return
    self._tickers.append(t)
    self._prices[t] = self._last_price.pop(t, None) or SEED_PRICES.get(t) or _symbol_seed(t)
    self._params[t] = TICKER_PARAMS.get(t, DEFAULT_PARAMS)

def remove_ticker(self, t: str) -> None:
    if t not in self._prices:
        return
    self._last_price[t] = self._prices.pop(t)        # 🔧 remembered for re-add
    self._tickers.remove(t)
    del self._params[t]
    self._rebuild_cholesky()
```

Remembering the last price means un-watching and re-watching AAPL doesn't snap it back to $190 and draw a fake jump on the chart.

### 4.2 `SimulatorDataSource` — adapter

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5,
                 event_probability: float = 0.0002, seed: int | None = None) -> None

    description = "simulator"

    async def start(self, tickers):
        # build GBMSimulator from tickers + any pending add_ticker() calls,
        # seed cache: cache.update(t, p, reference_price=p) for every ticker,
        # spawn _run_loop task. Raise RuntimeError if already started.
    async def stop(self):             # cancel + await task; idempotent
    async def add_ticker(self, t):    # before start → pending list; after → sim.add_ticker
                                      # + cache.update(t, p, reference_price=p) so it shows at once
    async def remove_ticker(self, t): # sim.remove_ticker + cache.remove
    def get_tickers(self)

    async def _run_loop(self):
        while True:
            try:
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker=ticker, price=price)   # reference kept from seed
            except Exception:
                logger.exception("Simulator step failed")              # never kill the loop
            await asyncio.sleep(self._interval)
```

The loop runs on the event-loop thread; `step()` for 10 tickers takes microseconds, so `to_thread` isn't needed. Using `asyncio.sleep(interval)` after the work means the real period is slightly over 500 ms — acceptable; no need for drift-corrected scheduling.

---

## 5. Configuration

The simulator needs **no configuration**. One optional env var, read only in the factory:

| Variable | Default | Use |
|---|---|---|
| `SIM_EVENT_PROBABILITY` | `0.0002` | Set `0` in E2E tests to remove jumps |

`seed` is a constructor argument for unit tests only — not an env var. E2E tests can't get deterministic prices anyway (the number of ticks before an assertion depends on wall-clock timing), so they should assert on structure: a price exists, it changes, a flash class appears.

---

## 6. Changes to make 🔧

Do them in this order. All are small and backward-compatible.

| # | Change | Why | Depends on |
|---|---|---|---|
| S1 | **Reference price:** `start()` and `add_ticker()` call `cache.update(t, p, reference_price=p)` | Defines "daily change %" as change since the ticker entered the session | `MARKET_INTERFACE.md` §10 #3 (`reference_price` in cache/models) |
| S2 | **Default `event_probability = 0.0002`** in both constructors; factory reads `SIM_EVENT_PROBABILITY` | Jumps currently dominate the chart (§2.4) | — |
| S3 | **Delete `TSLA_CORR`** and the TSLA branch; fix the wrong comment | Dead logic (§3.2) | — |
| S4 | **Private RNG + deterministic unknown-ticker seeds:** `seed` arg → `np.random.default_rng(seed)` used for all draws; `_symbol_seed()` via CRC32 | Reproducible unit tests; no interference with global RNGs; stable prices across restarts | Do together |
| S5 | **Remember last price on remove** (`_last_price`) | No fake snap-back on re-add | — |
| S6 | **Lifecycle:** pending tickers before `start()`; `start()` twice raises; `description = "simulator"` | Interface guarantees in `MARKET_INTERFACE.md` §3.4 | — |
| S7 | **Defensive normalization** `t.strip().upper()` in `add/remove/start` | Real validation lives in the app layer's `normalize_ticker`; this just prevents `aapl` and `AAPL` becoming two stocks | — |
| S8 | Fix `_rebuild_cholesky` docstring: O(n³), not O(n²) | Accuracy | — |

Not doing: a price floor after jumps. Unknown seeds are ≥ $50 and reaching < $0.005 takes ~180 net −5 % jumps. `PriceUpdate.change_percent` already guards a zero denominator, which also covers bad Massive data.

---

## 7. Testing

Current suite: 19 `GBMSimulator` tests and 10 `SimulatorDataSource` tests (all passing). After S4, all statistical tests use a fixed `seed` and fixed N so they can't flake.

| Property | Test |
|---|---|
| Prices stay positive | 10,000 steps, all > 0 |
| Output rounded to 2 dp | `round(p, 2) == p` |
| Correlation | seed=0, `event_probability=0`, N = 20,000 steps; sample ρ of log-returns: AAPL–MSFT ≈ 0.6 ± 0.05, AAPL–JPM ≈ 0.3 ± 0.05, TSLA–AAPL ≈ 0.3 ± 0.05 (std error ≈ 0.005) |
| Volatility calibration | `dt = 1/252`, events off, N = 20,000: annualized std of log-returns ≈ σ ± 5 % |
| Drift | large μ, many steps → positive mean log-return |
| Jumps | `p = 1.0`: every tick moves ≥ 2 %; `p = 0`: no tick moves > 1 % |
| Determinism | same seed → identical 100-step path; unknown ticker seed identical across instances |
| Add/remove | idempotent; Cholesky shape tracks n; down to 1 ticker → `_cholesky is None`; re-add restores last price |
| Positive definiteness | all combinations of default tickers + 5 unknowns → `cholesky` succeeds |
| Adapter | `start` seeds cache with `reference_price`; loop survives an exception from `step`; `stop` idempotent; add before start honored; double start raises |

---

## 8. How it fits the system

- The simulator never decides which tickers to track. The app's price universe (watchlist ∪ open positions, `MARKET_INTERFACE.md` §7) tells it.
- The SSE stream sees every tick through `PriceCache.version`: ~2 events/s, each containing every tracked ticker.
- Portfolio snapshots, trade fills and LLM context read `cache.get_price(t)` and behave identically with Massive.
