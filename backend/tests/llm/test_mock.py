"""Mock-mode parsing (CONTRACT §5) and end-to-end mock chat turns."""

from __future__ import annotations

import pytest

from app import db
from app.llm import handle_chat
from app.llm.mock import mock_response


class TestMockParsing:
    def test_buy(self):
        r = mock_response("please buy 5 aapl now", 10000)
        assert r.message == "Mock: executing buy 5 AAPL."
        assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [("AAPL", "buy", 5.0)]
        assert r.watchlist_changes == []

    def test_sell_with_shares_of(self):
        r = mock_response("SELL 2.5 shares of msft", 10000)
        assert r.message == "Mock: executing sell 2.5 MSFT."
        assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [("MSFT", "sell", 2.5)]

    def test_single_share_of(self):
        r = mock_response("buy 1 share of nvda", 10000)
        assert r.trades[0].ticker == "NVDA"
        assert r.message == "Mock: executing buy 1 NVDA."

    @pytest.mark.parametrize(
        "text", ["add pypl", "Watch PYPL please", "can you ADD pypl to my list"]
    )
    def test_add(self, text):
        r = mock_response(text, 10000)
        assert r.message == "Mock: updating watchlist for PYPL."
        assert [(c.ticker, c.action) for c in r.watchlist_changes] == [("PYPL", "add")]
        assert r.trades == []

    def test_remove(self):
        r = mock_response("remove nflx", 10000)
        assert r.message == "Mock: updating watchlist for NFLX."
        assert [(c.ticker, c.action) for c in r.watchlist_changes] == [("NFLX", "remove")]

    def test_fallback(self):
        r = mock_response("how am I doing?", 12345.678)
        assert r.message == (
            "Mock: I'm FinAlly, your AI trading assistant. Your portfolio is worth $12,345.68."
        )
        assert r.trades == [] and r.watchlist_changes == []

    def test_address_is_not_add(self):
        r = mock_response("what is your address", 10000)
        assert r.watchlist_changes == []

    def test_zero_quantity_is_ignored(self):
        r = mock_response("buy 0 AAPL", 10000)
        assert r.trades == []
        assert r.message.startswith("Mock: I'm FinAlly")


class TestMockChat:
    @pytest.fixture(autouse=True)
    def mock_on(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "TRUE")

    async def test_buy_executes_and_persists(self, portfolio, watchlist):
        resp = await handle_chat("buy 5 AAPL", portfolio, watchlist)
        assert resp["role"] == "assistant"
        assert resp["content"] == "Mock: executing buy 5 AAPL."
        assert resp["actions"]["trades"] == [
            {
                "ticker": "AAPL",
                "side": "buy",
                "quantity": 5.0,
                "status": "executed",
                "price": 190.0,
                "error": None,
            }
        ]
        assert resp["actions"]["watchlist_changes"] == []
        assert resp["id"] and resp["created_at"]

        msgs = db.get_chat_messages(limit=10)
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "buy 5 AAPL"
        assert msgs[0]["actions"] is None
        assert msgs[1]["actions"] == resp["actions"]
        assert msgs[1]["id"] == resp["id"]

    async def test_failed_trade_reported(self, portfolio, watchlist):
        resp = await handle_chat("sell 3 AAPL", portfolio, watchlist)
        t = resp["actions"]["trades"][0]
        assert t["status"] == "failed"
        assert t["error"] == "Insufficient shares"
        assert t["price"] is None

    async def test_add_then_buy_same_message(self, portfolio, watchlist):
        resp = await handle_chat("add PYPL and buy 2 PYPL", portfolio, watchlist)
        # Watchlist change executes before the trade so the ticker gets a price.
        assert watchlist.calls == [("add", "PYPL")]
        assert portfolio.calls == [("trade", "PYPL", "buy", 2.0)]
        assert resp["actions"]["trades"][0]["status"] == "executed"
        assert resp["actions"]["watchlist_changes"][0]["status"] == "executed"

    async def test_fallback_uses_total_value(self, portfolio, watchlist):
        resp = await handle_chat("hello", portfolio, watchlist)
        assert resp["content"].endswith("Your portfolio is worth $10,000.00.")
        assert resp["actions"] == {"trades": [], "watchlist_changes": []}

    async def test_mock_never_touches_client(self, portfolio, watchlist, fake_client):
        await handle_chat("buy 1 AAPL", portfolio, watchlist)
        fake_client.messages.parse.assert_not_called()
