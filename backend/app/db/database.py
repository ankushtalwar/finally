"""SQLite persistence layer for FinAlly (see planning/CONTRACT.md section 2).

Design:
- stdlib ``sqlite3``, synchronous, one short-lived connection per call.
- All writes are serialized through a module-level re-entrant lock and run inside
  ``BEGIN IMMEDIATE`` transactions, so multi-statement operations are atomic.
- ``init_db()`` is idempotent and is invoked lazily by every public function.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_USER = "default"
DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]
DEFAULT_CASH = 10000.0
QTY_EPSILON = 1e-9
# Tolerance for float rounding when comparing cash against trade cost.
_CASH_EPSILON = 1e-9

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# backend/app/db/database.py -> parents[3] is the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB_PATH = _REPO_ROOT / "db" / "finally.db"

_lock = threading.RLock()
_db_path: Path | None = None
_initialized = False


class TradeRejected(Exception):  # noqa: N818 - name fixed by CONTRACT.md
    """A trade failed validation. ``message`` is safe to show to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now() -> str:
    # Fixed-width microseconds keep the format stable and lexically sortable.
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _new_id() -> str:
    return str(uuid.uuid4())


def _norm(ticker: str) -> str:
    return ticker.strip().upper()


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("FINALLY_DB_PATH", "").strip()
    return Path(env) if env else _DEFAULT_DB_PATH


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _ensure_init() -> Path:
    """Initialize lazily; re-initialize if the DB file disappeared."""
    path = _db_path
    if _initialized and path is not None and path.exists():
        return path
    init_db(path)
    assert _db_path is not None
    return _db_path


@contextmanager
def _read() -> Iterator[sqlite3.Connection]:
    conn = _connect(_ensure_init())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _write() -> Iterator[sqlite3.Connection]:
    """Serialized, atomic write transaction."""
    path = _ensure_init()
    with _lock:
        conn = _connect(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_qty(value: float) -> str:
    return f"{value:,.6f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------- #
# Initialization
# --------------------------------------------------------------------------- #


def init_db(path: str | Path | None = None) -> None:
    """Create schema and seed default data if needed. Safe to call repeatedly.

    ``path`` sets the module's DB path; ``None`` uses ``FINALLY_DB_PATH`` or
    ``<repo>/db/finally.db``.
    """
    global _db_path, _initialized
    resolved = _resolve_path(path)
    with _lock:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(resolved)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA_PATH.read_text())
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT 1 FROM users_profile WHERE id = ?", (DEFAULT_USER,)
                ).fetchone()
                if row is None:
                    now = _now()
                    conn.execute(
                        "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
                        (DEFAULT_USER, DEFAULT_CASH, now),
                    )
                    conn.executemany(
                        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) "
                        "VALUES (?, ?, ?, ?)",
                        [(_new_id(), DEFAULT_USER, t, now) for t in DEFAULT_TICKERS],
                    )
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()
        _db_path = resolved
        _initialized = True


# --------------------------------------------------------------------------- #
# Cash
# --------------------------------------------------------------------------- #


def get_cash(*, user_id: str = DEFAULT_USER) -> float:
    with _read() as conn:
        row = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()
    return float(row["cash_balance"]) if row else 0.0


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #


def get_watchlist(*, user_id: str = DEFAULT_USER) -> list[str]:
    with _read() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
            (user_id,),
        ).fetchall()
    return [r["ticker"] for r in rows]


def add_to_watchlist(ticker: str, *, user_id: str = DEFAULT_USER) -> bool:
    """Add a ticker. Returns False if it was already present."""
    t = _norm(ticker)
    if not t:
        raise ValueError("Ticker must not be empty")
    with _write() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (_new_id(), user_id, t, _now()),
        )
        return cur.rowcount > 0


def remove_from_watchlist(ticker: str, *, user_id: str = DEFAULT_USER) -> bool:
    """Remove a ticker. Returns False if it was not present."""
    with _write() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, _norm(ticker))
        )
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Positions & trades
# --------------------------------------------------------------------------- #


def _position_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "quantity": float(row["quantity"]),
        "avg_cost": float(row["avg_cost"]),
        "updated_at": row["updated_at"],
    }


def get_positions(*, user_id: str = DEFAULT_USER) -> list[dict]:
    with _read() as conn:
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()
    return [_position_dict(r) for r in rows]


def get_position(ticker: str, *, user_id: str = DEFAULT_USER) -> dict | None:
    with _read() as conn:
        row = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? AND ticker = ?",
            (user_id, _norm(ticker)),
        ).fetchone()
    return _position_dict(row) if row else None


def apply_trade(
    ticker: str, side: str, quantity: float, price: float, *, user_id: str = DEFAULT_USER
) -> dict:
    """Validate and execute a market trade atomically.

    Returns ``{"id","ticker","side","quantity","price","executed_at"}``.
    Raises ``TradeRejected`` (nothing is written) on any validation failure.
    """
    t = _norm(ticker) if isinstance(ticker, str) else ""
    s = side.strip().lower() if isinstance(side, str) else ""
    if not t:
        raise TradeRejected("Ticker is required")
    if s not in ("buy", "sell"):
        raise TradeRejected("Side must be 'buy' or 'sell'")
    try:
        qty = float(quantity)
        px = float(price)
    except (TypeError, ValueError):
        raise TradeRejected("Quantity and price must be numbers") from None
    if not math.isfinite(qty) or qty <= 0:
        raise TradeRejected("Quantity must be greater than 0")
    if not math.isfinite(px) or px <= 0:
        raise TradeRejected(f"Invalid price for {t}")

    now = _now()
    trade_id = _new_id()
    with _write() as conn:
        profile = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()
        if profile is None:
            raise TradeRejected(f"Unknown user: {user_id}")
        cash = float(profile["cash_balance"])
        pos = conn.execute(
            "SELECT quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, t),
        ).fetchone()
        held = float(pos["quantity"]) if pos else 0.0
        notional = qty * px

        if s == "buy":
            if notional > cash + _CASH_EPSILON:
                raise TradeRejected(
                    f"Insufficient cash: need {_fmt_money(notional)}, have {_fmt_money(cash)}"
                )
            new_cash = max(cash - notional, 0.0)
            if pos:
                new_qty = held + qty
                new_avg = (held * float(pos["avg_cost"]) + notional) / new_qty
                conn.execute(
                    "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? "
                    "WHERE user_id = ? AND ticker = ?",
                    (new_qty, new_avg, now, user_id, t),
                )
            else:
                conn.execute(
                    "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (_new_id(), user_id, t, qty, px, now),
                )
        else:
            if pos is None or held < qty - QTY_EPSILON:
                raise TradeRejected(
                    f"Insufficient shares: need {_fmt_qty(qty)} {t}, have {_fmt_qty(held)}"
                )
            new_cash = cash + notional
            remaining = held - qty
            if remaining <= QTY_EPSILON:
                conn.execute(
                    "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, t)
                )
            else:
                conn.execute(
                    "UPDATE positions SET quantity = ?, updated_at = ? "
                    "WHERE user_id = ? AND ticker = ?",
                    (remaining, now, user_id, t),
                )

        conn.execute(
            "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (new_cash, user_id)
        )
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade_id, user_id, t, s, qty, px, now),
        )

    return {
        "id": trade_id,
        "ticker": t,
        "side": s,
        "quantity": qty,
        "price": px,
        "executed_at": now,
    }


def get_trades(*, limit: int = 100, user_id: str = DEFAULT_USER) -> list[dict]:
    """Most recent trades, newest first."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, quantity, price, executed_at FROM trades "
            "WHERE user_id = ? ORDER BY executed_at DESC, rowid DESC LIMIT ?",
            (user_id, max(int(limit), 0)),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "ticker": r["ticker"],
            "side": r["side"],
            "quantity": float(r["quantity"]),
            "price": float(r["price"]),
            "executed_at": r["executed_at"],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Portfolio snapshots
# --------------------------------------------------------------------------- #


def record_snapshot(total_value: float, *, user_id: str = DEFAULT_USER) -> None:
    with _write() as conn:
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (_new_id(), user_id, float(total_value), _now()),
        )


def get_snapshots(*, limit: int = 1000, user_id: str = DEFAULT_USER) -> list[dict]:
    """The newest ``limit`` snapshots, returned oldest -> newest."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots "
            "WHERE user_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
            (user_id, max(int(limit), 0)),
        ).fetchall()
    return [
        {"total_value": float(r["total_value"]), "recorded_at": r["recorded_at"]}
        for r in reversed(rows)
    ]


# --------------------------------------------------------------------------- #
# Chat messages
# --------------------------------------------------------------------------- #


def _decode_actions(raw: str | None) -> dict | None:
    """Decode a stored ``actions`` JSON value; a corrupt row must not break history reads."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _chat_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "actions": _decode_actions(row["actions"]),
        "created_at": row["created_at"],
    }


def add_chat_message(
    role: str, content: str, actions: dict | None = None, *, user_id: str = DEFAULT_USER
) -> dict:
    if role not in ("user", "assistant"):
        raise ValueError("role must be 'user' or 'assistant'")
    msg = {
        "id": _new_id(),
        "role": role,
        "content": content,
        "actions": actions,
        "created_at": _now(),
    }
    actions_json = json.dumps(actions) if actions is not None else None
    with _write() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg["id"], user_id, role, content, actions_json, msg["created_at"]),
        )
    return msg


def get_chat_messages(*, limit: int = 20, user_id: str = DEFAULT_USER) -> list[dict]:
    """The newest ``limit`` messages, returned oldest -> newest."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT id, role, content, actions, created_at FROM chat_messages "
            "WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, max(int(limit), 0)),
        ).fetchall()
    return [_chat_dict(r) for r in reversed(rows)]
