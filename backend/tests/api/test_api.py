"""REST API tests against the full app (lifespan runs; market source is faked)."""

from __future__ import annotations

import math

import pytest

from app import db
from app.market.massive_client import MassiveDataSource

from .conftest import SEED_PRICE

WATCH_ITEM_KEYS = {
    "ticker",
    "price",
    "previous_price",
    "change_percent",
    "day_change_percent",
    "direction",
    "timestamp",
}
PORTFOLIO_KEYS = {
    "cash",
    "positions_value",
    "total_value",
    "unrealized_pnl",
    "unrealized_pnl_percent",
    "positions",
}
POSITION_KEYS = {
    "ticker",
    "quantity",
    "avg_cost",
    "current_price",
    "market_value",
    "unrealized_pnl",
    "unrealized_pnl_percent",
    "weight",
}


# --- health / lifespan ------------------------------------------------------ #


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "market_source": "simulator",
        "market_healthy": True,
        "llm_mock": True,
    }


def test_health_real_simulator(make_client):
    with make_client(fake_source=False) as c:
        body = c.get("/api/health").json()
        assert body["market_source"] == "simulator"
        assert body["market_healthy"] is True
        # Simulator seeds the cache on start, so watchlist prices exist immediately.
        prices = [t["price"] for t in c.get("/api/watchlist").json()["tickers"]]
        assert all(p is not None for p in prices)


def test_health_reports_massive(client, monkeypatch):
    src = MassiveDataSource(api_key="test", price_cache=client.app.state.price_cache)
    src._consecutive_failures = 2
    monkeypatch.setattr(client.app.state, "market_source", src)
    body = client.get("/api/health").json()
    assert body["market_source"] == "massive"
    assert body["market_healthy"] is False


def test_llm_mock_false(make_client, monkeypatch):
    with make_client() as c:
        monkeypatch.setenv("LLM_MOCK", "false")
        assert c.get("/api/health").json()["llm_mock"] is False


def test_startup_snapshot_and_universe(make_client):
    with make_client() as c:
        snaps = c.get("/api/portfolio/history").json()["snapshots"]
        assert len(snaps) >= 1
        assert snaps[-1]["total_value"] == 10000.0
        assert set(snaps[-1]) >= {"total_value", "recorded_at"}
        source = c.app.state.market_source
        assert source.started
        assert set(source.tickers) == set(db.DEFAULT_TICKERS)
    assert source.stopped


def test_startup_universe_includes_held_unwatched(make_client):
    with make_client() as c:
        c.post("/api/watchlist", json={"ticker": "PYPL"})
        c.post("/api/portfolio/trade", json={"ticker": "PYPL", "quantity": 1, "side": "buy"})
        c.delete("/api/watchlist/PYPL")
    with make_client() as c:
        assert "PYPL" in c.app.state.market_source.tickers


# --- watchlist ------------------------------------------------------------- #


def test_get_watchlist_shape(client):
    r = client.get("/api/watchlist")
    assert r.status_code == 200
    tickers = r.json()["tickers"]
    assert [t["ticker"] for t in tickers] == db.DEFAULT_TICKERS
    for t in tickers:
        assert set(t) == WATCH_ITEM_KEYS
    assert tickers[0]["price"] == SEED_PRICE


def test_add_watchlist_201(client):
    r = client.post("/api/watchlist", json={"ticker": "pypl"})
    assert r.status_code == 201
    body = r.json()
    assert body["ticker"] == "PYPL"
    assert set(body) == WATCH_ITEM_KEYS
    assert "PYPL" in [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]


def test_add_watchlist_409(client):
    r = client.post("/api/watchlist", json={"ticker": "AAPL"})
    assert r.status_code == 409
    assert "detail" in r.json()


def test_add_watchlist_400(client):
    r = client.post("/api/watchlist", json={"ticker": "not a ticker!"})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_add_watchlist_422(client):
    assert client.post("/api/watchlist", json={}).status_code == 422


def test_delete_watchlist_204_and_404(client):
    r = client.delete("/api/watchlist/aapl")
    assert r.status_code == 204
    assert r.content == b""
    assert "AAPL" not in client.app.state.market_source.tickers
    r = client.delete("/api/watchlist/AAPL")
    assert r.status_code == 404
    assert "detail" in r.json()


def test_delete_watchlist_while_held_keeps_price(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 2, "side": "buy"})
    assert client.delete("/api/watchlist/AAPL").status_code == 204
    assert "AAPL" in client.app.state.market_source.tickers
    assert client.app.state.price_cache.get_price("AAPL") == SEED_PRICE
    pos = client.get("/api/portfolio").json()["positions"][0]
    assert pos["ticker"] == "AAPL"
    assert pos["current_price"] == SEED_PRICE


# --- portfolio / trade ----------------------------------------------------- #


def test_get_portfolio_initial(client):
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == PORTFOLIO_KEYS
    assert body["cash"] == 10000.0
    assert body["total_value"] == 10000.0
    assert body["positions"] == []


def test_buy_and_sell(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "aapl", "quantity": 10, "side": "buy"})
    assert r.status_code == 200
    body = r.json()
    trade = body["trade"]
    assert set(trade) >= {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert trade["ticker"] == "AAPL" and trade["side"] == "buy"
    assert trade["price"] == SEED_PRICE
    portfolio = body["portfolio"]
    assert set(portfolio) == PORTFOLIO_KEYS
    assert math.isclose(portfolio["cash"], 9000.0)
    assert math.isclose(portfolio["total_value"], 10000.0)
    pos = portfolio["positions"][0]
    assert set(pos) == POSITION_KEYS
    assert pos["quantity"] == 10
    assert math.isclose(pos["weight"], 0.1)

    client.app.state.price_cache.update("AAPL", 110.0)
    r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "sell"})
    assert r.status_code == 200
    portfolio = r.json()["portfolio"]
    assert portfolio["positions"] == []
    assert math.isclose(portfolio["cash"], 10100.0)


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"ticker": "AAPL", "quantity": 1000, "side": "buy"}, "Insufficient cash"),
        ({"ticker": "AAPL", "quantity": 1, "side": "sell"}, "Insufficient shares"),
        ({"ticker": "ZZZZ", "quantity": 1, "side": "buy"}, "No price available for ZZZZ yet"),
        ({"ticker": "AAPL", "quantity": 0, "side": "buy"}, "Quantity must be greater than 0"),
        ({"ticker": "AAPL", "quantity": -5, "side": "buy"}, "Quantity must be greater than 0"),
    ],
)
def test_trade_errors_400(client, payload, detail):
    r = client.post("/api/portfolio/trade", json=payload)
    assert r.status_code == 400
    assert detail in r.json()["detail"]
    assert client.get("/api/portfolio").json()["cash"] == 10000.0


def test_trade_body_validation_422(client):
    r = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": "lots", "side": "buy"}
    )
    assert r.status_code == 422


def test_history_grows_after_trade(client):
    before = len(client.get("/api/portfolio/history").json()["snapshots"])
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    snaps = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(snaps) == before + 1
    assert [s["recorded_at"] for s in snaps] == sorted(s["recorded_at"] for s in snaps)


# --- chat ------------------------------------------------------------------ #


def test_chat_delegates_to_llm(client, monkeypatch):
    import app.api.chat as chat_api

    seen = {}

    async def fake_handle_chat(message, portfolio, watchlist):
        seen.update(message=message, portfolio=portfolio, watchlist=watchlist)
        return {
            "id": "x",
            "role": "assistant",
            "content": "hi",
            "created_at": "now",
            "actions": {"trades": [], "watchlist_changes": []},
        }

    monkeypatch.setattr(chat_api.llm, "handle_chat", fake_handle_chat)
    r = client.post("/api/chat", json={"message": "  hello  "})
    assert r.status_code == 200
    assert r.json()["content"] == "hi"
    assert seen["message"] == "hello"
    assert seen["portfolio"] is client.app.state.portfolio_service
    assert seen["watchlist"] is client.app.state.watchlist_service


@pytest.mark.parametrize("payload", [{"message": ""}, {"message": "   "}, {}])
def test_chat_empty_message_422(client, payload):
    assert client.post("/api/chat", json=payload).status_code == 422


def test_chat_history_shape(client):
    db.add_chat_message("user", "hello")
    db.add_chat_message("assistant", "hi", {"trades": [], "watchlist_changes": []})
    r = client.get("/api/chat/history")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["actions"] is None
    for m in msgs:
        assert set(m) >= {"id", "role", "content", "actions", "created_at"}


def test_chat_mock_end_to_end(client):
    """Runs against the real app.llm in mock mode once that module exists."""
    import app.llm as llm

    if getattr(llm, "MODEL", None) == "stub":
        pytest.skip("app.llm not available yet")
    r = client.post("/api/chat", json={"message": "buy 2 AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "assistant"
    trade = body["actions"]["trades"][0]
    assert trade["ticker"] == "AAPL" and trade["status"] == "executed"
    assert client.get("/api/portfolio").json()["positions"][0]["quantity"] == 2
    roles = [m["role"] for m in client.get("/api/chat/history").json()["messages"]]
    assert roles[-2:] == ["user", "assistant"]


# --- static / SPA ---------------------------------------------------------- #


@pytest.fixture
def static_client(make_client, tmp_path):
    static = tmp_path / "static"
    (static / "_next").mkdir(parents=True)
    (static / "index.html").write_text("<html>INDEX</html>")
    (static / "404.html").write_text("<html>NOTFOUND</html>")
    (static / "about.html").write_text("<html>ABOUT</html>")
    (static / "_next" / "app.js").write_text("console.log(1)")
    with make_client(static_dir=static) as c:
        yield c


def test_static_index(static_client):
    r = static_client.get("/")
    assert r.status_code == 200
    assert "INDEX" in r.text


def test_static_asset(static_client):
    r = static_client.get("/_next/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_spa_fallback(static_client):
    r = static_client.get("/some/client/route")
    assert r.status_code == 200
    assert "INDEX" in r.text


def test_html_route(static_client):
    assert "ABOUT" in static_client.get("/about").text


def test_unknown_api_is_json_404(static_client):
    r = static_client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_api_still_works_with_static(static_client):
    assert static_client.get("/api/health").status_code == 200


def test_no_static_dir_api_only(client):
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


# --- added: chat always-200 fallback, periodic snapshots, shutdown --------- #


def test_chat_unexpected_error_still_200(client, monkeypatch):
    import app.api.chat as chat_api

    async def boom(message, portfolio, watchlist):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(chat_api.llm, "handle_chat", boom)
    r = client.post("/api/chat", json={"message": "buy 1 AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "assistant"
    assert body["content"] == chat_api.FALLBACK_MESSAGE
    assert body["actions"] == {"trades": [], "watchlist_changes": []}
    assert set(body) >= {"id", "role", "content", "actions", "created_at"}
    assert "kaboom" not in r.text
    assert client.get("/api/portfolio").json()["positions"] == []


def test_chat_fallback_survives_db_failure(client, monkeypatch):
    import app.api.chat as chat_api

    async def boom(message, portfolio, watchlist):
        raise RuntimeError("llm down")

    def db_boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_api.llm, "handle_chat", boom)
    monkeypatch.setattr(chat_api.db, "add_chat_message", db_boom)
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert r.json()["content"] == chat_api.FALLBACK_MESSAGE


def test_periodic_snapshots_and_clean_shutdown(make_client, monkeypatch):
    import time

    import app.main as main

    monkeypatch.setattr(main, "SNAPSHOT_INTERVAL_SECONDS", 0.05)
    with make_client() as c:
        time.sleep(0.4)
        snaps = c.get("/api/portfolio/history").json()["snapshots"]
        assert len(snaps) >= 3  # startup + periodic ticks
        source = c.app.state.market_source
    assert source.stopped
    after_shutdown = len(db.get_snapshots())
    time.sleep(0.2)
    assert len(db.get_snapshots()) == after_shutdown  # loop cancelled


def test_unknown_api_json_404_without_static(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_spa_fallback_nested_path_and_post_not_html(static_client):
    r = static_client.get("/some/deep/route")
    assert r.status_code == 200 and "INDEX" in r.text
    r = static_client.post("/api/unknown", json={})
    assert r.status_code in (404, 405)
    assert r.headers["content-type"].startswith("application/json")


def test_trade_ticker_not_in_universe_rejected(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "zzzz", "quantity": 1, "side": "buy"})
    assert r.status_code == 400
    assert r.json() == {"detail": "No price available for ZZZZ yet"}


def test_sell_after_unwatch_drops_from_universe(client):
    client.post("/api/watchlist", json={"ticker": "PYPL"})
    client.post("/api/portfolio/trade", json={"ticker": "PYPL", "quantity": 2, "side": "buy"})
    assert client.delete("/api/watchlist/PYPL").status_code == 204
    source = client.app.state.market_source
    assert "PYPL" in source.tickers  # still held -> still priced
    r = client.post("/api/portfolio/trade", json={"ticker": "PYPL", "quantity": 2, "side": "sell"})
    assert r.status_code == 200
    assert r.json()["portfolio"]["positions"] == []
    assert "PYPL" not in source.tickers


def test_missing_static_asset_is_404_not_html(static_client):
    r = static_client.get("/_next/missing-chunk.js")
    assert r.status_code == 404
    assert "INDEX" not in r.text


def test_lazy_db_init_creates_nested_path(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main
    from tests.services.fakes import FakeSource

    db_path = tmp_path / "nested" / "dir" / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(db_path))
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path / "no-static"))
    monkeypatch.setattr(main, "create_market_data_source", lambda cache: FakeSource(cache, 50.0))
    with TestClient(main.create_app()) as c:
        assert db_path.is_file()
        assert c.get("/api/portfolio").json()["cash"] == 10000.0
        assert len(c.get("/api/watchlist").json()["tickers"]) == len(db.DEFAULT_TICKERS)


# --- SSE ------------------------------------------------------------------- #


async def _read_sse(app, *, max_events: int = 1, timeout: float = 5.0) -> tuple[dict, list[str]]:
    """Drive GET /api/stream/prices over raw ASGI; disconnect after `max_events` data events.

    TestClient/httpx ASGITransport buffer the whole body, so an endless stream needs this.
    """
    import asyncio
    import json

    start: dict = {}
    events: list[str] = []
    done = asyncio.Event()
    buffer = ""
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal buffer
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body":
            buffer += message.get("body", b"").decode()
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                events.append(frame)
                if sum(f.startswith("data: ") for f in events) >= max_events:
                    done.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/stream/prices",
        "raw_path": b"/api/stream/prices",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": app,
    }
    await asyncio.wait_for(app(scope, receive, send), timeout)
    data = [json.loads(f[len("data: ") :]) for f in events if f.startswith("data: ")]
    return {"start": start, "data": data}, events


def test_sse_stream_emits_price_event(client):
    result, frames = client.portal.call(_read_sse, client.app)
    start = result["start"]
    assert start["status"] == 200
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert frames[0] == "retry: 1000"

    event = result["data"][0]
    assert set(event) == set(db.DEFAULT_TICKERS)
    aapl = event["AAPL"]
    assert aapl["ticker"] == "AAPL"
    assert aapl["price"] == SEED_PRICE
    assert {
        "previous_price",
        "timestamp",
        "direction",
        "change_percent",
        "day_change_percent",
    } <= set(aapl)


def test_sse_stream_reflects_watchlist_changes(client):
    assert client.post("/api/watchlist", json={"ticker": "PYPL"}).status_code == 201
    assert client.delete("/api/watchlist/NFLX").status_code == 204
    result, _ = client.portal.call(_read_sse, client.app)
    event = result["data"][0]
    assert "PYPL" in event
    assert "NFLX" not in event
