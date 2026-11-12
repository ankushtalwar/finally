"""Tests for the SSE price stream (stream.py)."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router


class FakeRequest:
    """Minimal stand-in for a Starlette Request: disconnects after N checks."""

    def __init__(self, connected_checks: int) -> None:
        self._remaining = connected_checks
        self.client = None

    async def is_disconnected(self) -> bool:
        self._remaining -= 1
        return self._remaining < 0


async def _collect(gen) -> list[str]:
    return [frame async for frame in gen]


def _payload(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: ") : -2])


@pytest.mark.asyncio
class TestGenerateEvents:
    async def test_first_frame_is_retry_directive(self):
        frames = await _collect(_generate_events(PriceCache(), FakeRequest(0), interval=0))
        assert frames == ["retry: 1000\n\n"]

    async def test_empty_cache_sends_no_data(self):
        frames = await _collect(_generate_events(PriceCache(), FakeRequest(3), interval=0))
        assert frames == ["retry: 1000\n\n"]

    async def test_sends_all_prices_with_full_payload(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0, timestamp=1.0)
        cache.update("AAPL", 191.0, timestamp=2.0)
        cache.update("MSFT", 420.0, timestamp=3.0)

        frames = await _collect(_generate_events(cache, FakeRequest(1), interval=0))

        assert len(frames) == 2
        data = _payload(frames[1])
        assert set(data) == {"AAPL", "MSFT"}
        assert data["AAPL"] == {
            "ticker": "AAPL",
            "price": 191.0,
            "previous_price": 190.0,
            "timestamp": 2.0,
            "change": 1.0,
            "change_percent": 0.5263,
            "direction": "up",
        }

    async def test_unchanged_version_sends_nothing_new(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        frames = await _collect(_generate_events(cache, FakeRequest(5), interval=0))

        assert len(frames) == 2  # retry + one data frame, despite 5 loop iterations

    async def test_new_frame_per_version_bump(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        gen = _generate_events(cache, FakeRequest(10), interval=0)

        assert await anext(gen) == "retry: 1000\n\n"
        assert _payload(await anext(gen))["AAPL"]["price"] == 190.0
        cache.update("AAPL", 189.0)
        second = _payload(await anext(gen))
        assert second["AAPL"]["price"] == 189.0
        assert second["AAPL"]["direction"] == "down"
        await gen.aclose()

    async def test_removing_last_ticker_sends_empty_object(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        gen = _generate_events(cache, FakeRequest(10), interval=0)

        await anext(gen)  # retry
        assert "AAPL" in _payload(await anext(gen))
        cache.remove("AAPL")
        assert _payload(await anext(gen)) == {}
        await gen.aclose()

    async def test_removal_is_streamed_when_prices_are_static(self):
        """With no other updates (e.g. EOD data), a removal alone must reach clients."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.update("MSFT", 420.0)
        gen = _generate_events(cache, FakeRequest(10), interval=0)

        await anext(gen)
        await anext(gen)
        cache.remove("MSFT")
        assert set(_payload(await anext(gen))) == {"AAPL"}
        await gen.aclose()


class TestStreamRouter:
    def test_route_and_headers(self):
        cache = PriceCache()
        app = FastAPI()
        app.include_router(create_stream_router(cache))
        route = next(r for r in app.routes if getattr(r, "path", None) == "/api/stream/prices")
        assert "GET" in route.methods

    def test_factory_returns_independent_routers(self):
        """Calling the factory twice must not stack duplicate routes on one shared router."""
        r1 = create_stream_router(PriceCache())
        r2 = create_stream_router(PriceCache())
        assert r1 is not r2
        assert len(r1.routes) == 1
        assert len(r2.routes) == 1

    def test_endpoint_returns_event_stream(self, monkeypatch):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        async def short_stream(price_cache, request, interval=0.5):
            yield "retry: 1000\n\n"
            yield f"data: {json.dumps({'AAPL': price_cache.get('AAPL').to_dict()})}\n\n"

        monkeypatch.setattr("app.market.stream._generate_events", short_stream)
        app = FastAPI()
        app.include_router(create_stream_router(cache))

        with TestClient(app) as client:
            resp = client.get("/api/stream/prices")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-cache"
        assert "retry: 1000" in resp.text
        assert '"AAPL"' in resp.text
