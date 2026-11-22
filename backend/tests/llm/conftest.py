"""Fixtures for LLM tests: temp DB, fake services, fake Anthropic client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import db
from app.llm import chat as chat_mod
from app.services.portfolio import TradeError
from app.services.watchlist import WatchlistError


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    db.init_db(path)
    yield path


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    chat_mod.set_client(None)
    yield
    chat_mod.set_client(None)


class FakePortfolio:
    def __init__(self, prices=None, cash=10000.0):
        self.prices = prices or {"AAPL": 190.0, "MSFT": 400.0}
        self.cash = cash
        self.calls: list[tuple] = []
        self.holdings: dict[str, float] = {}

    def get_portfolio(self) -> dict:
        positions = [
            {
                "ticker": t,
                "quantity": q,
                "avg_cost": 180.0,
                "current_price": self.prices.get(t, 180.0),
                "market_value": q * self.prices.get(t, 180.0),
                "unrealized_pnl": q * (self.prices.get(t, 180.0) - 180.0),
                "unrealized_pnl_percent": 5.56,
                "weight": 0.1,
            }
            for t, q in self.holdings.items()
        ]
        pv = sum(p["market_value"] for p in positions)
        return {
            "cash": self.cash,
            "positions_value": pv,
            "total_value": self.cash + pv,
            "unrealized_pnl": sum(p["unrealized_pnl"] for p in positions),
            "unrealized_pnl_percent": 5.56 if positions else 0.0,
            "positions": positions,
        }

    async def execute_trade(self, ticker, side, quantity):
        self.calls.append(("trade", ticker, side, quantity))
        price = self.prices.get(ticker)
        if price is None:
            raise TradeError(f"No price available for {ticker} yet")
        if side == "buy":
            if quantity * price > self.cash:
                raise TradeError("Insufficient cash")
            self.cash -= quantity * price
            self.holdings[ticker] = self.holdings.get(ticker, 0) + quantity
        else:
            if self.holdings.get(ticker, 0) < quantity:
                raise TradeError("Insufficient shares")
            self.cash += quantity * price
            self.holdings[ticker] -= quantity
        return {
            "id": "t1",
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "executed_at": "2026-11-13T10:00:00+00:00",
        }


class FakeWatchlist:
    def __init__(self, portfolio: FakePortfolio, tickers=("AAPL", "MSFT")):
        self.portfolio = portfolio
        self.tickers = list(tickers)
        self.calls: list[tuple] = []

    def get_watchlist(self) -> list[dict]:
        return [
            {
                "ticker": t,
                "price": self.portfolio.prices.get(t),
                "previous_price": None,
                "change_percent": 0,
                "day_change_percent": 1.25,
                "direction": "flat",
                "timestamp": None,
            }
            for t in self.tickers
        ]

    async def add(self, ticker):
        self.calls.append(("add", ticker))
        if ticker == "BAD!":
            raise WatchlistError("Invalid ticker symbol", 400)
        if ticker in self.tickers:
            raise WatchlistError(f"{ticker} is already on the watchlist", 409)
        self.tickers.append(ticker)
        self.portfolio.prices.setdefault(ticker, 50.0)
        return {"ticker": ticker}

    async def remove(self, ticker):
        self.calls.append(("remove", ticker))
        if ticker not in self.tickers:
            raise WatchlistError(f"{ticker} is not on the watchlist", 404)
        self.tickers.remove(ticker)


@pytest.fixture
def portfolio():
    return FakePortfolio()


@pytest.fixture
def watchlist(portfolio):
    return FakeWatchlist(portfolio)


def make_response(parsed=None, stop_reason="end_turn"):
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason)


class FakeRawResponse:
    """Mimics the SDK's AsyncAPIResponse: `json()` is the wire body, `parse()` the model."""

    def __init__(self, response):
        self._response = response

    async def json(self):
        return {"stop_reason": getattr(self._response, "stop_reason", None)}

    async def parse(self):
        return self._response


@pytest.fixture
def fake_client():
    """Fake AsyncAnthropic. `messages.parse` is the AsyncMock tests configure/inspect;
    `with_raw_response.parse` (what chat.py calls, on both beta and plain) wraps it."""
    parse = AsyncMock()

    async def raw_parse(**kwargs):
        return FakeRawResponse(await parse(**kwargs))

    raw = SimpleNamespace(parse=raw_parse)
    client = SimpleNamespace(
        messages=SimpleNamespace(parse=parse, with_raw_response=raw),
        beta=SimpleNamespace(messages=SimpleNamespace(parse=parse, with_raw_response=raw)),
    )
    chat_mod.set_client(client)
    return client
