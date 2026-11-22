"""Atomicity, timestamp format and concurrency guarantees of the DB layer."""

import re
import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from app.db import database

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")


def _install_failing_trade_insert(db_path):
    """Make the final INSERT of apply_trade fail inside the transaction."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TRIGGER fail_trade BEFORE INSERT ON trades "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    conn.commit()
    conn.close()


def test_buy_rolls_back_cash_and_position_on_mid_transaction_failure(db, db_path):
    _install_failing_trade_insert(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.apply_trade("AAPL", "buy", 5, 100.0)
    assert db.get_cash() == 10000.0
    assert db.get_position("AAPL") is None
    assert db.get_trades() == []


def test_sell_rolls_back_on_mid_transaction_failure(db, db_path):
    db.apply_trade("AAPL", "buy", 5, 100.0)
    _install_failing_trade_insert(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.apply_trade("AAPL", "sell", 5, 120.0)  # would delete the position row
    assert db.get_cash() == pytest.approx(9500.0)
    assert db.get_position("AAPL")["quantity"] == pytest.approx(5)
    assert len(db.get_trades()) == 1


def test_lock_released_after_failed_transaction(db, db_path):
    _install_failing_trade_insert(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.apply_trade("AAPL", "buy", 1, 100.0)
    # A subsequent write must not deadlock or see a dangling transaction.
    assert db.add_to_watchlist("PYPL") is True


def test_now_always_has_microseconds(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 13, 10, 0, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(database, "datetime", FrozenDatetime)
    assert database._now() == "2026-11-13T10:00:00.000000+00:00"


def test_all_written_timestamps_are_iso_utc(db):
    trade = db.apply_trade("AAPL", "buy", 1, 100.0)
    db.record_snapshot(10000.0)
    msg = db.add_chat_message("user", "hi")
    stamps = [
        trade["executed_at"],
        db.get_position("AAPL")["updated_at"],
        db.get_snapshots()[-1]["recorded_at"],
        msg["created_at"],
        db.get_chat_messages()[-1]["created_at"],
    ]
    assert all(ISO_RE.match(s) for s in stamps), stamps


def test_concurrent_lazy_init_on_fresh_path(db_path):
    errors: list[BaseException] = []

    def worker():
        try:
            database.get_watchlist()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # Seeded exactly once despite racing initializers.
    assert database.get_watchlist() == database.DEFAULT_TICKERS
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM users_profile").fetchone()[0] == 1
    conn.close()


def test_concurrent_mixed_buys_and_sells_conserve_value(db):
    db.apply_trade("AAPL", "buy", 50, 100.0)
    barrier = threading.Barrier(20)
    rejected = []

    def worker(i):
        barrier.wait()
        try:
            db.apply_trade("AAPL", "buy" if i % 2 else "sell", 1, 100.0)
        except database.TradeRejected:
            rejected.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert rejected == []
    # At a constant price, cash + position value is conserved exactly.
    qty = db.get_position("AAPL")["quantity"]
    assert qty == pytest.approx(50)
    assert db.get_cash() + qty * 100.0 == pytest.approx(10000.0)
    assert len(db.get_trades(limit=1000)) == 21
