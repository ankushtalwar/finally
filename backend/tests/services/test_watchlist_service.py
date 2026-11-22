"""WatchlistService unit tests."""

from __future__ import annotations

import pytest

from app import db
from app.services import PortfolioService, WatchlistError, WatchlistService


@pytest.fixture
def svc(temp_db, cache, source) -> WatchlistService:
    return WatchlistService(cache, source)


def test_default_watchlist_items(svc, cache):
    cache.update("AAPL", 190.0, day_open=180.0)
    items = svc.get_watchlist()
    assert [i["ticker"] for i in items][:2] == ["AAPL", "GOOGL"]
    assert len(items) == 10
    aapl = items[0]
    assert aapl["price"] == 190.0
    assert aapl["direction"] == "flat"
    assert aapl["day_change_percent"] > 0
    googl = items[1]
    assert googl["price"] is None and googl["timestamp"] is None
    assert googl["change_percent"] == 0 and googl["day_change_percent"] == 0


async def test_add_normalizes_and_starts_pricing(svc, source):
    item = await svc.add(" pypl ")
    assert item["ticker"] == "PYPL"
    assert "PYPL" in source.tickers
    assert "PYPL" in db.get_watchlist()


async def test_add_duplicate_is_409(svc):
    with pytest.raises(WatchlistError) as exc:
        await svc.add("aapl")
    assert exc.value.status_code == 409


@pytest.mark.parametrize("bad", ["", "  ", "1ABC", "TOOLONGTICKER", "AB CD", "A$"])
async def test_add_invalid_is_400(svc, bad):
    with pytest.raises(WatchlistError) as exc:
        await svc.add(bad)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("ok", ["BRK.B", "BF-B", "X"])
async def test_add_valid_symbols(svc, ok):
    assert (await svc.add(ok))["ticker"] == ok


async def test_remove_unwatched_is_404(svc):
    with pytest.raises(WatchlistError) as exc:
        await svc.remove("ZZZZ")
    assert exc.value.status_code == 404


async def test_remove_stops_pricing(svc, source):
    await svc.remove("aapl")
    assert "AAPL" not in db.get_watchlist()
    assert source.removed == ["AAPL"]


async def test_remove_while_held_keeps_pricing(svc, cache, source):
    cache.update("AAPL", 100.0)
    await PortfolioService(cache, source).execute_trade("AAPL", "buy", 1)
    await svc.remove("AAPL")
    assert "AAPL" not in db.get_watchlist()
    assert source.removed == []
    assert cache.get_price("AAPL") == 100.0
