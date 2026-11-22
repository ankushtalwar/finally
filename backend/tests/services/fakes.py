"""Test doubles shared by service and API tests."""

from __future__ import annotations

from app.market import MarketDataSource, PriceCache


class FakeSource(MarketDataSource):
    """Deterministic market source: records tickers, never moves prices on its own."""

    def __init__(self, cache: PriceCache, seed_price: float | None = None) -> None:
        self.cache = cache
        self.seed_price = seed_price
        self.tickers: list[str] = []
        self.started = False
        self.stopped = False
        self.removed: list[str] = []

    async def start(self, tickers: list[str]) -> None:
        self.started = True
        for t in tickers:
            await self.add_ticker(t)

    async def stop(self) -> None:
        self.stopped = True

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        if ticker not in self.tickers:
            self.tickers.append(ticker)
        if self.seed_price is not None and ticker not in self.cache:
            self.cache.update(ticker, self.seed_price, day_open=self.seed_price)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        self.removed.append(ticker)
        self.tickers = [t for t in self.tickers if t != ticker]
        self.cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self.tickers)
