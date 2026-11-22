"""PortfolioService unit tests."""

from __future__ import annotations

import math

import pytest

from app import db
from app.services import PortfolioService, TradeError


@pytest.fixture
def svc(temp_db, cache, source) -> PortfolioService:
    return PortfolioService(cache, source)


def test_empty_portfolio(svc):
    p = svc.get_portfolio()
    assert p == {
        "cash": 10000.0,
        "positions_value": 0.0,
        "total_value": 10000.0,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_percent": 0.0,
        "positions": [],
    }


async def test_buy_then_valuation(svc, cache):
    cache.update("AAPL", 100.0)
    trade = await svc.execute_trade(" aapl ", "BUY", 10)
    assert trade["ticker"] == "AAPL"
    assert trade["side"] == "buy"
    assert trade["price"] == 100.0
    assert set(trade) >= {"id", "ticker", "side", "quantity", "price", "executed_at"}

    cache.update("AAPL", 110.0)
    p = svc.get_portfolio()
    assert math.isclose(p["cash"], 9000.0)
    assert math.isclose(p["positions_value"], 1100.0)
    assert math.isclose(p["total_value"], 10100.0)
    assert math.isclose(p["unrealized_pnl"], 100.0)
    assert math.isclose(p["unrealized_pnl_percent"], 10.0)
    pos = p["positions"][0]
    assert pos["ticker"] == "AAPL"
    assert math.isclose(pos["current_price"], 110.0)
    assert math.isclose(pos["unrealized_pnl_percent"], 10.0)
    assert math.isclose(pos["weight"], 1100.0 / 10100.0)


async def test_positions_sorted_by_market_value(svc, cache):
    cache.update("AAPL", 100.0)
    cache.update("MSFT", 300.0)
    await svc.execute_trade("AAPL", "buy", 1)
    await svc.execute_trade("MSFT", "buy", 2)
    assert [p["ticker"] for p in svc.get_portfolio()["positions"]] == ["MSFT", "AAPL"]


async def test_missing_price_uses_avg_cost(svc, cache):
    cache.update("AAPL", 100.0)
    await svc.execute_trade("AAPL", "buy", 2)
    cache.remove("AAPL")
    pos = svc.get_portfolio()["positions"][0]
    assert pos["current_price"] == 100.0
    assert pos["unrealized_pnl"] == 0.0


@pytest.mark.parametrize("qty", [0, -1, float("nan"), float("inf"), "abc"])
async def test_invalid_quantity(svc, cache, qty):
    cache.update("AAPL", 100.0)
    with pytest.raises(TradeError, match="Quantity must be greater than 0"):
        await svc.execute_trade("AAPL", "buy", qty)


async def test_invalid_side(svc, cache):
    cache.update("AAPL", 100.0)
    with pytest.raises(TradeError, match="Side"):
        await svc.execute_trade("AAPL", "short", 1)


async def test_no_price(svc):
    with pytest.raises(TradeError, match="No price available for ZZZZ yet"):
        await svc.execute_trade("zzzz", "buy", 1)


async def test_insufficient_cash(svc, cache):
    cache.update("AAPL", 100.0)
    with pytest.raises(TradeError, match="Insufficient cash"):
        await svc.execute_trade("AAPL", "buy", 1000)
    assert db.get_cash() == 10000.0


async def test_insufficient_shares(svc, cache):
    cache.update("AAPL", 100.0)
    with pytest.raises(TradeError, match="Insufficient shares"):
        await svc.execute_trade("AAPL", "sell", 1)


async def test_trade_records_snapshot(svc, cache):
    cache.update("AAPL", 100.0)
    before = len(db.get_snapshots())
    await svc.execute_trade("AAPL", "buy", 1)
    assert len(db.get_snapshots()) == before + 1


async def test_full_sell_of_unwatched_ticker_leaves_universe(svc, cache, source):
    cache.update("PYPL", 50.0)
    await svc.execute_trade("PYPL", "buy", 2)
    await svc.execute_trade("PYPL", "sell", 2)
    assert db.get_position("PYPL") is None
    assert "PYPL" in source.removed


async def test_full_sell_of_watched_ticker_keeps_pricing(svc, cache, source):
    cache.update("AAPL", 100.0)
    await svc.execute_trade("AAPL", "buy", 2)
    await svc.execute_trade("AAPL", "sell", 2)
    assert "AAPL" not in source.removed
