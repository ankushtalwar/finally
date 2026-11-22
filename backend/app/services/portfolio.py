"""Portfolio valuation and trade execution."""

from __future__ import annotations

import logging
import math

from app import db
from app.market import MarketDataSource, PriceCache

logger = logging.getLogger(__name__)

VALID_SIDES = ("buy", "sell")


class TradeError(Exception):
    """A trade was rejected. `message` is safe to show to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PortfolioService:
    def __init__(self, cache: PriceCache, source: MarketDataSource) -> None:
        self._cache = cache
        self._source = source

    def get_portfolio(self) -> dict:
        cash = float(db.get_cash())
        rows = []
        for pos in db.get_positions():
            quantity = float(pos["quantity"])
            avg_cost = float(pos["avg_cost"])
            live = self._cache.get_price(pos["ticker"])
            current_price = float(live) if live is not None else avg_cost
            market_value = quantity * current_price
            cost_basis = quantity * avg_cost
            pnl = market_value - cost_basis
            rows.append(
                {
                    "ticker": pos["ticker"],
                    "quantity": quantity,
                    "avg_cost": avg_cost,
                    "current_price": current_price,
                    "market_value": market_value,
                    "unrealized_pnl": pnl,
                    "unrealized_pnl_percent": (pnl / cost_basis * 100) if cost_basis else 0.0,
                    "_cost_basis": cost_basis,
                }
            )

        positions_value = sum(r["market_value"] for r in rows)
        total_cost = sum(r.pop("_cost_basis") for r in rows)
        total_value = cash + positions_value
        unrealized_pnl = sum(r["unrealized_pnl"] for r in rows)
        for r in rows:
            r["weight"] = (r["market_value"] / total_value) if total_value else 0.0
        rows.sort(key=lambda r: r["market_value"], reverse=True)

        return {
            "cash": cash,
            "positions_value": positions_value,
            "total_value": total_value,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_percent": (unrealized_pnl / total_cost * 100) if total_cost else 0.0,
            "positions": rows,
        }

    async def execute_trade(self, ticker: str, side: str, quantity: float) -> dict:
        ticker = (ticker or "").strip().upper()
        if not ticker:
            raise TradeError("Ticker is required")
        side = (side or "").strip().lower()
        if side not in VALID_SIDES:
            raise TradeError("Side must be 'buy' or 'sell'")
        try:
            qty = float(quantity)
        except (TypeError, ValueError):
            raise TradeError("Quantity must be greater than 0") from None
        if not math.isfinite(qty) or qty <= 0:
            raise TradeError("Quantity must be greater than 0")

        price = self._cache.get_price(ticker)
        if price is None:
            raise TradeError(f"No price available for {ticker} yet")

        try:
            trade = db.apply_trade(ticker, side, qty, float(price))
        except db.TradeRejected as exc:
            raise TradeError(exc.message) from exc

        # Price universe = watchlist ∪ held tickers: drop a fully-sold, unwatched ticker.
        if side == "sell" and db.get_position(ticker) is None and ticker not in db.get_watchlist():
            await self._source.remove_ticker(ticker)

        self.record_snapshot()
        return trade

    def record_snapshot(self) -> None:
        try:
            db.record_snapshot(self.get_portfolio()["total_value"])
        except Exception:
            logger.exception("Failed to record portfolio snapshot")
