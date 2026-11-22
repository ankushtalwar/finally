"""System prompt and context/history builders for the chat assistant."""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are FinAlly, an AI trading assistant embedded in a simulated trading workstation. \
The user trades a paper portfolio with virtual money; market orders fill instantly at the \
current price with no fees.

What you do:
- Analyze the user's portfolio: composition, concentration risk, cash allocation, and \
unrealized P&L. Use the numbers in the portfolio context you are given; do not invent prices.
- Suggest trades with brief, concrete reasoning.
- Execute trades when the user asks for them or explicitly agrees to a suggestion, by listing \
them in `trades`. Do not place trades the user has not asked for or agreed to.
- Manage the watchlist via `watchlist_changes` (add or remove tickers) when the user asks, \
or proactively when it clearly helps (for example, adding a ticker the user wants to discuss \
or trade). A ticker must be on the watchlist (or already held) before it has a price and can \
be traded, so add it in the same response if needed.
- Be concise and data-driven: a few short sentences, specific figures, no filler.

Rules:
- Only buy with available cash and only sell shares the user holds. If a request is not \
possible, say why instead of listing the trade.
- Quantities are share counts greater than 0; fractional shares are allowed.
- Each action you list is executed automatically after your reply. Describe what you are \
doing in `message` (e.g. "Buying 5 AAPL"). Prior assistant turns show the actual results \
of earlier actions.
- Tickers are uppercase symbols such as AAPL.
"""


def _money(v: Any) -> str:
    return "n/a" if v is None else f"${float(v):,.2f}"


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{float(v):+.2f}%"


def build_context(portfolio: dict, watchlist: list[dict]) -> str:
    """Render the live portfolio + watchlist as compact text for the model."""
    lines = [
        "## Current portfolio",
        f"Cash: {_money(portfolio.get('cash'))}",
        f"Positions value: {_money(portfolio.get('positions_value'))}",
        f"Total value: {_money(portfolio.get('total_value'))}",
        f"Unrealized P&L: {_money(portfolio.get('unrealized_pnl'))} "
        f"({_pct(portfolio.get('unrealized_pnl_percent'))})",
    ]
    positions = portfolio.get("positions") or []
    if positions:
        lines.append("Positions (ticker | qty | avg cost | price | value | P&L | weight):")
        for p in positions:
            lines.append(
                f"- {p['ticker']} | {float(p['quantity']):g} | {_money(p.get('avg_cost'))} | "
                f"{_money(p.get('current_price'))} | {_money(p.get('market_value'))} | "
                f"{_money(p.get('unrealized_pnl'))} ({_pct(p.get('unrealized_pnl_percent'))}) | "
                f"{float(p.get('weight') or 0) * 100:.1f}%"
            )
    else:
        lines.append("Positions: none")

    lines.append("")
    lines.append("## Watchlist (ticker | price | change today)")
    if watchlist:
        for w in watchlist:
            lines.append(
                f"- {w['ticker']} | {_money(w.get('price'))} | {_pct(w.get('day_change_percent'))}"
            )
    else:
        lines.append("(empty)")
    return "\n".join(lines)


def _summarize_actions(actions: dict | None) -> str:
    if not actions:
        return ""
    parts = []
    for t in actions.get("watchlist_changes") or []:
        status = t.get("status")
        suffix = f" ({t.get('error')})" if status == "failed" else ""
        parts.append(f"watchlist {t.get('action')} {t.get('ticker')}: {status}{suffix}")
    for t in actions.get("trades") or []:
        status = t.get("status")
        if status == "executed":
            detail = f" @ {_money(t.get('price'))}"
        else:
            detail = f" ({t.get('error')})"
        parts.append(f"{t.get('side')} {t.get('quantity'):g} {t.get('ticker')}: {status}{detail}")
    return f"\n[Actions: {'; '.join(parts)}]" if parts else ""


def build_messages(history: list[dict], user_message: str, context: str) -> list[dict]:
    """Prior turns (oldest→newest) + the new user turn carrying the live context."""
    messages: list[dict] = []
    for h in history:
        role = h.get("role")
        if role not in ("user", "assistant"):
            continue
        if not messages and role != "user":
            continue  # API requires the first message to be from the user
        content = h.get("content") or ""
        if role == "assistant":
            content += _summarize_actions(h.get("actions"))
        if not content:
            continue
        messages.append({"role": role, "content": content})

    messages.append(
        {
            "role": "user",
            "content": f"<portfolio_context>\n{context}\n</portfolio_context>\n\n{user_message}",
        }
    )
    return messages
