"""Watchlist management, kept in sync with the market data source."""

from __future__ import annotations

import re

from app import db
from app.market import MarketDataSource, PriceCache

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class WatchlistError(Exception):
    """A watchlist change was rejected. `status_code` is 400, 404 or 409."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_ticker(ticker: str) -> str:
    return (ticker or "").strip().upper()


class WatchlistService:
    def __init__(self, cache: PriceCache, source: MarketDataSource) -> None:
        self._cache = cache
        self._source = source

    def item(self, ticker: str) -> dict:
        update = self._cache.get(ticker)
        if update is None:
            return {
                "ticker": ticker,
                "price": None,
                "previous_price": None,
                "change_percent": 0.0,
                "day_change_percent": 0.0,
                "direction": "flat",
                "timestamp": None,
            }
        return {
            "ticker": ticker,
            "price": update.price,
            "previous_price": update.previous_price,
            "change_percent": update.change_percent,
            "day_change_percent": update.day_change_percent,
            "direction": update.direction,
            "timestamp": update.timestamp,
        }

    def get_watchlist(self) -> list[dict]:
        return [self.item(t) for t in db.get_watchlist()]

    async def add(self, ticker: str) -> dict:
        ticker = normalize_ticker(ticker)
        if not TICKER_RE.match(ticker):
            raise WatchlistError(f"Invalid ticker symbol: {ticker or '(empty)'}", 400)
        if not db.add_to_watchlist(ticker):
            raise WatchlistError(f"{ticker} is already on the watchlist", 409)
        await self._source.add_ticker(ticker)
        return self.item(ticker)

    async def remove(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if not db.remove_from_watchlist(ticker):
            raise WatchlistError(f"{ticker} is not on the watchlist", 404)
        # Keep pricing held tickers so P&L stays live.
        if db.get_position(ticker) is None:
            await self._source.remove_ticker(ticker)
