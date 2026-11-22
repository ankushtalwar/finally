"""Additional edge cases for the DB layer: re-init, partial schemas, normalization, limits."""

import sqlite3
import threading

import pytest

from app.db import database


def test_init_on_existing_empty_file(db_path):
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    database.init_db(db_path)
    assert database.get_cash() == 10000.0
    assert database.get_watchlist() == database.DEFAULT_TICKERS


def test_init_recreates_missing_table(db, db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE chat_messages")
    conn.commit()
    conn.close()
    database.init_db(db_path)
    database.add_chat_message("user", "hi")
    assert [m["content"] for m in database.get_chat_messages()] == ["hi"]


def test_reinit_preserves_existing_state(db, db_path):
    db.apply_trade("AAPL", "buy", 2, 100.0)
    db.record_snapshot(10000.0)
    db.add_chat_message("user", "hello")
    db.init_db(db_path)
    assert db.get_cash() == pytest.approx(9800.0)
    assert db.get_position("AAPL")["quantity"] == 2
    assert len(db.get_trades()) == 1
    assert len(db.get_snapshots()) == 1
    assert len(db.get_chat_messages()) == 1


def test_apply_trade_and_lookup_normalize_ticker(db):
    trade = db.apply_trade("  aapl ", "BUY", 1, 100.0)
    assert trade["ticker"] == "AAPL"
    assert trade["side"] == "buy"
    assert db.get_position(" aApL")["quantity"] == 1
    db.apply_trade("Aapl", "sell", 1, 90.0)
    assert db.get_position("AAPL") is None


def test_remove_from_watchlist_normalizes(db):
    assert db.remove_from_watchlist(" aapl ") is True
    assert "AAPL" not in db.get_watchlist()


def test_add_empty_ticker_rejected(db):
    with pytest.raises(ValueError):
        db.add_to_watchlist("   ")


def test_oversell_on_partial_position_writes_nothing(db):
    db.apply_trade("TSLA", "buy", 3, 200.0)
    cash = db.get_cash()
    with pytest.raises(db.TradeRejected, match="Insufficient shares"):
        db.apply_trade("TSLA", "sell", 3.5, 250.0)
    assert db.get_cash() == cash
    assert db.get_position("TSLA")["quantity"] == 3
    assert len(db.get_trades()) == 1


def test_sell_at_loss_after_averaging(db):
    db.apply_trade("NVDA", "buy", 1, 100.0)
    db.apply_trade("NVDA", "buy", 1, 200.0)
    db.apply_trade("NVDA", "sell", 1, 120.0)
    pos = db.get_position("NVDA")
    assert pos["avg_cost"] == pytest.approx(150.0)
    assert pos["quantity"] == pytest.approx(1.0)
    assert db.get_cash() == pytest.approx(10000.0 - 300.0 + 120.0)


def test_repeated_fractional_sells_leave_no_residue(db):
    # 0.1 + 0.2 style float residue must not leave a ghost position.
    db.apply_trade("MSFT", "buy", 0.3, 100.0)
    db.apply_trade("MSFT", "sell", 0.1, 100.0)
    db.apply_trade("MSFT", "sell", 0.2, 100.0)
    assert db.get_position("MSFT") is None
    assert db.get_positions() == []


@pytest.mark.parametrize("fn", ["get_trades", "get_snapshots", "get_chat_messages"])
def test_limit_zero_and_negative_return_empty(db, fn):
    db.apply_trade("AAPL", "buy", 1, 10.0)
    db.record_snapshot(1.0)
    db.add_chat_message("user", "x")
    assert getattr(db, fn)(limit=0) == []
    assert getattr(db, fn)(limit=-5) == []


def test_corrupt_actions_json_does_not_break_history(db, db_path):
    db.add_chat_message("assistant", "ok", {"trades": []})
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
        "VALUES ('bad', 'default', 'assistant', 'broken', '{not json', '9999-01-01')"
    )
    conn.commit()
    conn.close()
    msgs = db.get_chat_messages()
    assert msgs[-1]["content"] == "broken"
    assert msgs[-1]["actions"] is None
    assert msgs[0]["actions"] == {"trades": []}


def test_concurrent_duplicate_watchlist_adds(db):
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(db.add_to_watchlist("PYPL"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert db.get_watchlist().count("PYPL") == 1


def test_concurrent_reads_during_writes(db):
    errors: list[BaseException] = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                db.get_positions()
                db.get_cash()
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    for _ in range(50):
        db.apply_trade("AAPL", "buy", 1, 1.0)
    stop.set()
    for t in readers:
        t.join()
    assert errors == []
    assert db.get_position("AAPL")["quantity"] == 50
