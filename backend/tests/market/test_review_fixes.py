"""Tests covering the fixes from planning/MARKET_DATA_REVIEW.md."""

import asyncio
import random
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.models import PriceUpdate
from app.market.simulator import GBMSimulator, SimulatorDataSource, default_price_for
from app.market.stream import _generate_events, create_stream_router


def snap(ticker, price, prev_close=None, ts=1_700_000_000_000):
    """Real Massive snapshot; `ts` is Unix ms, stored as sip_timestamp in ns."""
    d = {"ticker": ticker, "lastTrade": {"p": price, "t": ts * 1_000_000}}
    if prev_close is not None:
        d["prevDay"] = {"c": prev_close}
    return TickerSnapshot.from_dict(d)


# --- Cache ---


def test_cache_zero_timestamp_preserved():
    assert PriceCache().update("AAPL", 10, timestamp=0.0).timestamp == 0.0


def test_cache_remove_bumps_version():
    c = PriceCache()
    c.update("AAPL", 10)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")  # no-op does not bump
    assert c.version == v + 1


def test_cache_normalizes_tickers():
    c = PriceCache()
    c.update(" aapl ", 10)
    assert "AAPL" in c and c.get("aapl").ticker == "AAPL"
    c.remove("aapl")
    assert len(c) == 0


def test_cache_day_open_carried_over():
    c = PriceCache()
    c.update("AAPL", 100, day_open=95)
    u = c.update("AAPL", 105)
    assert u.day_open == 95
    assert u.day_change_percent == pytest.approx(10.5263, abs=1e-3)
    assert u.to_dict()["day_open"] == 95


def test_day_change_percent_without_open():
    assert PriceUpdate("X", 1, 1).day_change_percent == 0.0


# --- Simulator ---


def test_simulator_normalizes_and_dedupes():
    sim = GBMSimulator(["aapl", "AAPL"])
    assert sim.get_tickers() == ["AAPL"]
    assert sim.get_price("aapl") == sim.get_price("AAPL")


def test_unknown_ticker_price_deterministic():
    assert default_price_for("ZZZZ") == default_price_for("ZZZZ")
    assert 50 <= default_price_for("ZZZZ") < 300
    assert GBMSimulator(["ZZZZ"]).get_price("ZZZZ") == default_price_for("ZZZZ")


def test_simulator_reproducible_with_rngs():
    def run():
        s = GBMSimulator(["AAPL", "MSFT"], rng=np.random.default_rng(1), py_rng=random.Random(1))
        return [s.step() for _ in range(20)]

    assert run() == run()


def test_simulator_day_open_is_seed():
    assert GBMSimulator(["AAPL"]).get_day_open("AAPL") == 190.0


def test_cholesky_fallback_on_bad_matrix():
    sim = GBMSimulator(["AAPL", "MSFT"])
    with patch.object(GBMSimulator, "_pairwise_correlation", return_value=2.0):
        sim._rebuild_cholesky()
    assert sim._cholesky is not None
    assert len(sim.step()) == 2


def test_simulator_gbm_volatility_magnitude():
    sim = GBMSimulator(
        ["AAPL"], event_probability=0.0, rng=np.random.default_rng(0), py_rng=random.Random(0)
    )
    n = 20000
    prices = [sim.step()["AAPL"] for _ in range(n)]
    rets = np.diff(np.log(prices))
    expected = 0.22 * np.sqrt(GBMSimulator.DEFAULT_DT)
    assert rets.std() == pytest.approx(expected, rel=0.15)


async def test_source_buffers_before_start_and_rejects_double_start():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.05)
    await src.add_ticker("tsla")
    assert src.get_tickers() == ["TSLA"]
    await src.start(["AAPL"])
    try:
        assert set(src.get_tickers()) == {"AAPL", "TSLA"}
        assert cache.get("TSLA").day_open == 250.0
        with pytest.raises(RuntimeError):
            await src.start(["AAPL"])
    finally:
        await src.stop()


async def test_source_remove_ticker_clears_cache_case_insensitive():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.05)
    await src.start(["AAPL"])
    await src.remove_ticker("aapl")
    await src.stop()
    assert "AAPL" not in cache


# --- Massive ---


async def make_source(snaps_fn):
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=3600)
    with patch("app.market.massive_client.RESTClient") as rc:
        rc.return_value.get_snapshot_all.side_effect = snaps_fn
        await src.start(["aapl"])
    return src, cache


async def test_massive_normalizes_and_sets_day_open():
    src, cache = await make_source(lambda **kw: [snap("AAPL", 101, prev_close=100)])
    try:
        assert src.get_tickers() == ["AAPL"]
        assert cache.get("AAPL").day_open == 100
        assert src.healthy
    finally:
        await src.stop()


async def test_massive_add_ticker_polls_immediately():
    calls = []

    def fn(**kw):
        calls.append(list(kw["tickers"]))
        return [snap(t, 10.0) for t in kw["tickers"]]

    src, cache = await make_source(fn)
    try:
        await src.add_ticker("tsla")
        assert "TSLA" in cache
        assert calls[-1] == ["AAPL", "TSLA"]
    finally:
        await src.stop()


async def test_massive_inflight_removed_ticker_not_written():
    src, cache = await make_source(lambda **kw: [snap("AAPL", 1.0)])
    await src.remove_ticker("AAPL")

    def fetch_stale(tickers):
        return [snap("AAPL", 5.0)]

    src._tickers = ["AAPL", "MSFT"]
    real = src._fetch_snapshots

    def racing(tickers):
        src._tickers = ["MSFT"]  # removed while request is in flight
        return fetch_stale(tickers)

    src._fetch_snapshots = racing
    await src._poll_once()
    src._fetch_snapshots = real
    assert "AAPL" not in cache
    await src.stop()


async def test_massive_failure_flags_health_and_recovers():
    state = {"fail": True}

    def fn(**kw):
        if state["fail"]:
            raise RuntimeError("401")
        return [snap("AAPL", 1.0)]

    src, cache = await make_source(fn)
    try:
        assert not src.healthy and src.last_error == "401"
        state["fail"] = False
        await src._poll_once()
        assert src.healthy and src.last_error is None
    finally:
        await src.stop()


async def test_massive_double_start_rejected():
    src, _ = await make_source(lambda **kw: [])
    try:
        with patch("app.market.massive_client.RESTClient"):
            with pytest.raises(RuntimeError):
                await src.start(["AAPL"])
    finally:
        await src.stop()


# --- SSE ---


def test_routers_are_independent():
    r1 = create_stream_router(PriceCache())
    r2 = create_stream_router(PriceCache())
    assert r1 is not r2
    assert len(r1.routes) == 1 and len(r2.routes) == 1


class FakeRequest:
    def __init__(self, disconnect_after=3):
        self.client = SimpleNamespace(host="test")
        self._n = 0
        self._after = disconnect_after

    async def is_disconnected(self):
        self._n += 1
        return self._n > self._after


async def collect(cache, **kw):
    return [e async for e in _generate_events(cache, FakeRequest(), interval=0.001, **kw)]


async def test_sse_format_and_change_suppression():
    cache = PriceCache()
    cache.update("AAPL", 190)
    events = await collect(cache)
    assert events[0] == "retry: 1000\n\n"
    data = [e for e in events if e.startswith("data: ")]
    assert len(data) == 1  # unchanged version suppressed
    assert '"AAPL"' in data[0] and data[0].endswith("\n\n")


async def test_sse_emits_empty_after_last_removal():
    cache = PriceCache()
    cache.update("AAPL", 190)
    req = FakeRequest(disconnect_after=50)
    gen = _generate_events(cache, req, interval=0.001)
    assert await anext(gen) == "retry: 1000\n\n"
    assert "AAPL" in await anext(gen)
    cache.remove("AAPL")
    assert await anext(gen) == "data: {}\n\n"
    await gen.aclose()


async def test_sse_keepalive():
    cache = PriceCache()
    cache.update("AAPL", 190)
    events = await collect(cache, keepalive_interval=0.0)
    assert ": keepalive\n\n" in events


async def test_sse_cancellation_propagates():
    cache = PriceCache()
    req = FakeRequest(disconnect_after=10**9)

    async def consume():
        async for _ in _generate_events(cache, req, interval=0.01):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_sse_route_headers():
    router = create_stream_router(PriceCache())
    endpoint = router.routes[0].endpoint
    resp = await endpoint(FakeRequest())
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"
    assert "connection" not in resp.headers
    await resp.body_iterator.aclose()
