"""Structured output schema for the FinAlly chat assistant."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TradeInstruction(BaseModel):
    ticker: str = Field(description="Stock ticker symbol, e.g. AAPL")
    side: Literal["buy", "sell"]
    # No `gt=0` here: a bad quantity in one trade must fail only that trade (the trade
    # service rejects it and the error goes into `actions`), not discard the whole reply.
    quantity: float = Field(description="Number of shares (fractional allowed), must be > 0")


class WatchlistChange(BaseModel):
    ticker: str = Field(description="Stock ticker symbol, e.g. PYPL")
    action: Literal["add", "remove"]


class ChatResponse(BaseModel):
    """What the LLM returns each turn."""

    message: str = Field(description="Conversational reply shown to the user")
    trades: list[TradeInstruction] = Field(
        default_factory=list,
        description="Trades to execute now. Empty unless the user asked for or agreed to them.",
    )
    watchlist_changes: list[WatchlistChange] = Field(
        default_factory=list, description="Watchlist additions/removals to apply now."
    )
