# Massive API Reference (formerly Polygon.io)

_Researched 2026-11-10. Sources: the official `massive` Python client v2.2.0 (installed in `backend/.venv`; signatures and models checked by running them), the client repo on GitHub, massive.com REST docs, pricing page and knowledge-base articles (links at the end)._

This is the reference for what FinAlly needs from Massive:

1. **Current prices for several tickers in one call** (the live polling loop).
2. **End-of-day (EOD) prices** (the only data a free key can get, and the reference for "daily change %").

How FinAlly uses these endpoints is designed in [`MARKET_INTERFACE.md`](MARKET_INTERFACE.md).

---

## 1. Basics

| Item | Value |
|---|---|
| Rebrand | Polygon.io became **Massive** on 2025-10-30. Old keys keep working |
| REST base URL | `https://api.massive.com` (legacy `https://api.polygon.io` still accepted) |
| WebSocket URLs | `wss://socket.massive.com/stocks` (real-time), `wss://delayed.massive.com/stocks` (15-min delayed) |
| Auth | `?apiKey=<key>` query param or `Authorization: Bearer <key>` header |
| Python package | `massive` on PyPI (MIT). FinAlly pins `massive>=1.0.0`; the lockfile resolves 2.2.0 |
| Key from env | `RESTClient()` with no argument reads `MASSIVE_API_KEY`, the same variable FinAlly uses |
| Sync or async | **Synchronous** (urllib3). Call it via `asyncio.to_thread(...)` inside FastAPI |

```bash
cd backend
uv add massive        # already a dependency
```

```python
from massive import RESTClient

client = RESTClient()                 # reads MASSIVE_API_KEY; raises AuthError if missing
# RESTClient(api_key=None, connect_timeout=10.0, read_timeout=10.0, retries=3,
#            base="https://api.massive.com", pagination=True, ...)
```

---

## 2. Plans decide everything

The plan behind a key decides which endpoints respond and how fresh the data is. **This is the main constraint on FinAlly's design.**

| Stocks plan | Price* | REST limit | Recency | Snapshots | `lastTrade` in snapshot | WebSocket |
|---|---|---|---|---|---|---|
| **Basic** | $0 | **5 req/min** (429 above) | **End of day** | ❌ (403) | — | ❌ |
| Starter | ~$29/mo | Unlimited (keep < ~100 req/s) | 15-min delayed | ✅ | ⚠️ not guaranteed | ✅ delayed |
| Developer | ~$79/mo | Unlimited | 15-min delayed | ✅ | ✅ | ✅ delayed |
| Advanced | $199/mo | Unlimited | **Real-time** | ✅ | ✅ (+ `lastQuote`) | ✅ real-time |
| Business | contract | Unlimited | Real-time | ✅ | ✅ | with Expansion |

\* Only the Advanced price is printed on massive.com/pricing today; Starter/Developer prices come from secondary sources. Check the pricing page before quoting them.

Consequences for FinAlly:

1. **A free (Basic) key cannot call any snapshot endpoint.** It must use the EOD aggregates in §4.
2. **On Basic, today's bars are not available until just after midnight ET.** Asking for today's date returns **HTTP 403**, not an empty list. Always ask for the *previous* trading day.
3. **`lastTrade` is plan-conditional.** The docs say it "is only returned if your current plan includes trades", and Massive's unified-snapshot docs list trade fields from Developer upward. On Starter the price must come from `min.c` / `day.c` instead (§3.1 fallback chain).
4. Starter and up allow unlimited calls, so a 2–5 s poll is fine. Delayed plans only change every ~minute-bar, so faster polling adds little.

---

## 3. Current prices for several tickers

### 3.1 Full Market Snapshot — the polling endpoint (Starter+)

`GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,TSLA`

- **One request returns every requested ticker.** This is the only endpoint FinAlly's poll loop needs.
- `tickers` is comma-separated and **case-sensitive** (send upper case). Omit it for the whole market (~10k symbols, several MB).
- Unknown tickers are **silently left out** of the response.
- `include_otc` defaults to `false`.
- Data is cleared overnight (docs say ~3:30 AM ET; the client docstring says 12 AM ET) and refills from ~4:00 AM ET. Expect empty `day` bars early in the morning; `prevDay` is still present.

**Response (abridged):**

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [{
    "ticker": "AAPL",
    "todaysChange": 0.98,
    "todaysChangePerc": 0.82,
    "updated": 1605195918306274000,
    "day":     {"o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868, "vw": 119.725},
    "prevDay": {"o": 117.19, "h": 119.63, "l": 116.44, "c": 119.49,   "v": 110597265, "vw": 118.4998},
    "min":     {"t": 1684428720000, "o": 120.435, "h": 120.468, "l": 120.37, "c": 120.4201, "v": 270796, "n": 762},
    "lastTrade": {"p": 120.47, "s": 236, "t": 1605195918306274000, "x": 10},
    "lastQuote": {"p": 120.46, "P": 120.47, "s": 8, "S": 4, "t": 1605195918507251700}
  }]
}
```

| JSON field | Python attribute | Meaning | Units |
|---|---|---|---|
| `lastTrade.p` | `s.last_trade.price` | Last trade price (preferred) | USD |
| `lastTrade.t` | `s.last_trade.sip_timestamp` | SIP time of last trade | **nanoseconds** |
| `min.c` / `min.t` | `s.min.close` / `s.min.timestamp` | Latest minute bar close / start | USD / **ms** |
| `day.c` | `s.day.close` | Today's running close | USD |
| `prevDay.c` | `s.prev_day.close` | Previous session close — **the "daily change %" reference** | USD |
| `todaysChangePerc` | `s.todays_change_percent` | % change vs `prevDay.c` | percent |
| `updated` | `s.updated` | Snapshot last-updated time | **nanoseconds** |
| `lastQuote` | `s.last_quote` | Bid/ask; only on plans with quotes | — |

**Price fallback chain** (works on every snapshot-capable plan):
`last_trade.price` → `min.close` → `day.close` → `prev_day.close`.

```python
import time
from massive import RESTClient
from massive.rest.models import SnapshotMarketType, TickerSnapshot

client = RESTClient()
snaps: list[TickerSnapshot] = client.get_snapshot_all(
    SnapshotMarketType.STOCKS,          # or "stocks"
    tickers=["AAPL", "MSFT", "TSLA"],   # list or "AAPL,MSFT,TSLA"
)

def price_and_time(s: TickerSnapshot) -> tuple[float, float] | None:
    """(price, unix_seconds) using the fallback chain, or None if nothing usable."""
    if s.last_trade and s.last_trade.price:
        ns = s.last_trade.sip_timestamp or s.updated
        return s.last_trade.price, (ns / 1e9 if ns else time.time())
    if s.min and s.min.close:
        return s.min.close, (s.min.timestamp / 1e3 if s.min.timestamp else time.time())
    for bar in (s.day, s.prev_day):
        if bar and bar.close:
            return bar.close, (s.updated / 1e9 if s.updated else time.time())
    return None

for s in snaps:
    pt = price_and_time(s)
    prev_close = s.prev_day.close if s.prev_day else None
    print(s.ticker, pt, prev_close, s.todays_change_percent)
```

> ⚠️ **Field-name gotcha — this breaks the current FinAlly code.**
> `LastTrade` has `price`, `size`, `sip_timestamp`, `participant_timestamp`, `trf_timestamp`, … and **no `timestamp`**. Verified against the installed client:
>
> ```text
> >>> TickerSnapshot.from_dict({"ticker":"AAPL","lastTrade":{"p":190.5,"t":1731230000123456789}}).last_trade
> LastTrade(..., sip_timestamp=1731230000123456789, ..., price=190.5, ...)
> >>> hasattr(_, "timestamp")
> False
> ```
>
> `backend/app/market/massive_client.py` reads `snap.last_trade.timestamp / 1000.0`. That raises `AttributeError` for every ticker, the `except (AttributeError, TypeError)` swallows it, and **no Massive price is ever cached**. The tests miss it because they use `MagicMock` snapshots, which accept any attribute. The fix is in `MARKET_INTERFACE.md` §6.

### 3.2 Single-ticker snapshot

`GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` → one `TickerSnapshot`.

```python
snap = client.get_snapshot_ticker("stocks", "AAPL")
```

Use for one-off lookups (e.g. pricing a ticker the moment it's added). Never in the loop: one request per ticker.

### 3.3 Universal Snapshot (multi-asset)

`GET /v3/snapshot?ticker.any_of=AAPL,MSFT` — up to 250 tickers, all asset classes, paginated (`limit` defaults to 10 in the client).

```python
for s in client.list_universal_snapshots(ticker_any_of=["AAPL", "MSFT"], limit=250):
    print(s.ticker, s.last_trade.price if s.last_trade else None)
```

Not needed while FinAlly is US-stocks only; §3.1 is simpler.

### 3.4 Last trade

`GET /v2/last/trade/{ticker}` → `client.get_last_trade("AAPL")` returns `LastTrade` (`.price`, `.sip_timestamp` in ns). One request per ticker and needs a plan with trades. Not for polling.

### 3.5 WebSocket (Starter+ delayed, Advanced real-time)

```python
from massive import WebSocketClient
from massive.websocket.models import WebSocketMessage

ws = WebSocketClient(subscriptions=["T.AAPL", "T.MSFT"])   # T = trades, AM = minute aggs, A = second aggs

def handle(msgs: list[WebSocketMessage]) -> None:
    for m in msgs:
        print(m.symbol, m.price, m.timestamp)               # trade event: sym, p, s, t (ms)

ws.run(handle_msg=handle)
```

Raw protocol: connect → send `{"action":"auth","params":"<key>"}` → wait for `auth_success` → send `{"action":"subscribe","params":"T.AAPL,T.MSFT"}`.

**FinAlly does not use WebSockets** (PLAN.md §6 chose REST polling). A WebSocket source could be added later behind the same `MarketDataSource` interface.

---

## 4. End-of-day prices (every plan, including Basic)

Basic has 2 years of history. On Basic, a session's bars appear **just after midnight ET** the next day; requesting the current day returns 403.

### 4.1 Grouped Daily — whole market, one date, **one request** (best for free keys)

`GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true`

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def last_sessions(client, wanted: set[str], sessions: int = 2, max_lookback: int = 7):
    """Return up to `sessions` [(date, {ticker: GroupedDailyAgg})], newest first.

    Starts at *yesterday* in New York time (today is 403 on Basic) and steps back
    over weekends/holidays, which return an empty list.
    """
    day = datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    found = []
    for _ in range(max_lookback):
        bars = client.get_grouped_daily_aggs(day, adjusted=True)   # list[GroupedDailyAgg]
        if bars:
            found.append((day, {b.ticker: b for b in bars if b.ticker in wanted}))
            if len(found) == sessions:
                break
        day -= timedelta(days=1)
    return found

(latest_day, latest), (_, prior) = last_sessions(client, {"AAPL", "MSFT"})
for t, bar in latest.items():
    ref = prior.get(t)
    pct = (bar.close / ref.close - 1) * 100 if ref else None
    print(t, latest_day, bar.close, f"{pct:+.2f}%" if pct is not None else "")
```

- `GroupedDailyAgg` fields: `ticker, open, high, low, close, volume, vwap, timestamp (ms), transactions, otc`.
- Each response is ~10k rows (a few MB). Fetch once, filter client-side.
- Budget: two sessions over a long weekend can take ~5 requests — exactly Basic's per-minute budget. Spread retries; the client already backs off on 429.

### 4.2 Previous Day Bar — one ticker

`GET /v2/aggs/ticker/{ticker}/prev`

```python
prev = client.get_previous_close_agg("AAPL")   # PreviousCloseAgg in v2.2.0
prev = prev[0] if isinstance(prev, list) else prev   # older client versions returned a list
print(prev.close, prev.timestamp)              # timestamp in ms
```

Fields: `ticker, open, high, low, close, volume, vwap, timestamp`. One request per ticker — 10 tickers is 2 minutes of Basic quota, so prefer §4.1. Good for validating a single newly-added symbol.

### 4.3 Daily Open/Close — one ticker, one date

`GET /v1/open-close/{ticker}/{date}`

```python
oc = client.get_daily_open_close_agg("AAPL", "2026-11-09")
print(oc.open, oc.close, oc.pre_market, oc.after_hours)
```

### 4.4 Aggregate bars — history for charts

`GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`

```python
for bar in client.list_aggs("AAPL", 1, "day", "2026-10-01", "2026-11-09", limit=50000):
    print(bar.timestamp, bar.close)      # ms
```

Not needed in v1 (sparklines accumulate from SSE). This is how the main chart could be pre-filled later.

---

## 5. Market status

`GET /v1/marketstatus/now` → `client.get_market_status()` returns `.market` (`"open"`, `"closed"`, `"extended-hours"`), `.server_time`, and per-exchange status. Useful to slow the poll to ~60 s while the market is closed, since snapshots barely change overnight.

---

## 6. Errors and retries

| Situation | Client behavior |
|---|---|
| No key at all | `massive.exceptions.AuthError` **at `RESTClient()` construction** |
| HTTP 413, 429, 499, 500, 502, 503, 504 | Retried by urllib3 (`retries=3`, exponential backoff), then raised |
| Other non-200: 401 bad key, 403 `NOT_AUTHORIZED` (endpoint/date not in plan) | `massive.exceptions.BadResponse(<response body text>)` |
| Unknown ticker in `tickers=` | Silently omitted, no error |
| Weekend/holiday date on grouped daily | Empty list, no error |

```python
from massive.exceptions import AuthError, BadResponse

try:
    snaps = client.get_snapshot_all("stocks", tickers=["AAPL"])
except BadResponse as e:
    body = str(e)
    if "NOT_AUTHORIZED" in body or "403" in body:
        ...   # plan lacks snapshots → use EOD (MARKET_INTERFACE.md §6)
    else:
        ...   # transient or bad key → log, retry next cycle
```

Never log the key. `BadResponse` bodies don't contain it, but request URLs with `?apiKey=` do — don't log URLs.

---

## 7. Endpoint choice for FinAlly

| Need | Endpoint | Requests per cycle | Plan |
|---|---|---|---|
| Live prices for all tracked tickers | Full Market Snapshot with `tickers=` (§3.1) | **1** | Starter+ |
| "Daily change %" reference | `prevDay.c` in the same snapshot | 0 extra | Starter+ |
| Prices on a free key | Grouped Daily, last 2 sessions (§4.1) | 2 (≤ ~5 over holidays), **once per day** | All |
| Price a newly added ticker now | Single-ticker snapshot (§3.2) | 1 | Starter+ |
| Validate a symbol on a free key | Previous Day Bar (§4.2) | 1 | All |
| Chart history (future) | `list_aggs` (§4.4) | 1 per ticker | All |

---

## Sources

- [massive-com/client-python (GitHub)](https://github.com/massive-com/client-python) — `massive/rest/snapshot.py`, `rest/aggs.py`, `rest/models/*.py`, `exceptions.py`; also read from the installed v2.2.0 package
- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot)
- [Single Ticker Snapshot](https://massive.com/docs/rest/stocks/snapshots/single-ticker-snapshot)
- [Daily Market Summary (Grouped Daily)](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
- [WebSocket plan recency](https://massive.com/docs/websocket/stocks/aggregates-per-second)
- [Pricing](https://massive.com/pricing)
- [KB: On the free Basic plan, when can I pull the previous trading day's data?](https://massive.com/knowledge-base/article/on-the-free-basic-plan-when-can-i-pull-the-previous-trading-days-data)
- [KB: REST request limits](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis)
