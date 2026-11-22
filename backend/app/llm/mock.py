"""Deterministic mock LLM (LLM_MOCK=true) — no network. See CONTRACT §5."""

from __future__ import annotations

import re

from .schema import ChatResponse, TradeInstruction, WatchlistChange

_TICKER = r"([A-Za-z][A-Za-z0-9.\-]{0,9})"

TRADE_RE = re.compile(
    rf"\b(buy|sell)\s+(\d+(?:\.\d+)?)\s+(?:shares?\s+of\s+)?{_TICKER}\b", re.IGNORECASE
)
ADD_RE = re.compile(rf"\b(?:add|watch)\s+{_TICKER}\b", re.IGNORECASE)
REMOVE_RE = re.compile(rf"\bremove\s+{_TICKER}\b", re.IGNORECASE)


def _fmt_qty(q: float) -> str:
    return f"{q:g}"


def mock_response(message: str, total_value: float) -> ChatResponse:
    trade_m = TRADE_RE.search(message)
    add_m = ADD_RE.search(message)
    remove_m = REMOVE_RE.search(message)

    trades: list[TradeInstruction] = []
    changes: list[WatchlistChange] = []
    content: str | None = None

    if trade_m:
        side = trade_m.group(1).lower()
        qty = float(trade_m.group(2))
        ticker = trade_m.group(3).upper()
        if qty > 0:
            trades.append(TradeInstruction(ticker=ticker, side=side, quantity=qty))
            content = f"Mock: executing {side} {_fmt_qty(qty)} {ticker}."

    wl_m, action = (add_m, "add") if add_m else (remove_m, "remove")
    if wl_m:
        ticker = wl_m.group(1).upper()
        changes.append(WatchlistChange(ticker=ticker, action=action))
        if content is None:
            content = f"Mock: updating watchlist for {ticker}."

    if content is None:
        content = (
            "Mock: I'm FinAlly, your AI trading assistant. "
            f"Your portfolio is worth ${total_value:,.2f}."
        )
    return ChatResponse(message=content, trades=trades, watchlist_changes=changes)
