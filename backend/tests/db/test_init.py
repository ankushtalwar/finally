"""Initialization, seeding, path handling and idempotency."""

import sqlite3
from pathlib import Path

from app import db as db_pkg
from app.db import database


def _count(path, table):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_init_creates_parent_dir_and_file(db_path):
    assert not db_path.parent.exists()
    database.init_db(db_path)
    assert db_path.exists()


def test_seed_defaults(db):
    assert db.get_cash() == 10000.0
    assert db.get_watchlist() == db.DEFAULT_TICKERS
    assert db.get_positions() == []
    assert db.get_trades() == []
    assert db.get_snapshots() == []
    assert db.get_chat_messages() == []


def test_all_tables_exist(db, db_path):
    conn = sqlite3.connect(db_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {
        "users_profile",
        "watchlist",
        "positions",
        "trades",
        "portfolio_snapshots",
        "chat_messages",
    } <= names


def test_wal_mode(db, db_path):
    conn = sqlite3.connect(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_init_is_idempotent(db, db_path):
    db.add_to_watchlist("PYPL")
    db.apply_trade("AAPL", "buy", 1, 100.0)
    for _ in range(3):
        database.init_db(db_path)
    assert _count(db_path, "users_profile") == 1
    assert _count(db_path, "watchlist") == 11
    assert db.get_cash() == 9900.0
    assert "PYPL" in db.get_watchlist()


def test_reseed_does_not_restore_removed_ticker(db, db_path):
    db.remove_from_watchlist("AAPL")
    database.init_db(db_path)
    assert "AAPL" not in db.get_watchlist()


def test_lazy_init_uses_env_path(db_path):
    # No explicit init_db call: first access initializes at FINALLY_DB_PATH.
    assert database.get_cash() == 10000.0
    assert db_path.exists()
    assert database._db_path == db_path


def test_init_none_uses_env(db_path):
    database.init_db()
    assert database._db_path == db_path


def test_default_path_is_repo_db(monkeypatch):
    monkeypatch.delenv("FINALLY_DB_PATH", raising=False)
    resolved = database._resolve_path(None)
    repo_root = Path(database.__file__).resolve().parents[3]
    assert resolved == repo_root / "db" / "finally.db"
    assert (repo_root / "backend" / "app" / "db").is_dir()


def test_reinit_when_file_deleted(db, db_path):
    db.add_to_watchlist("PYPL")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    assert db.get_cash() == 10000.0
    assert db.get_watchlist() == db.DEFAULT_TICKERS


def test_package_exports():
    for name in (
        "DEFAULT_USER",
        "DEFAULT_TICKERS",
        "QTY_EPSILON",
        "TradeRejected",
        "init_db",
        "get_cash",
        "get_watchlist",
        "add_to_watchlist",
        "remove_from_watchlist",
        "get_positions",
        "get_position",
        "apply_trade",
        "get_trades",
        "record_snapshot",
        "get_snapshots",
        "add_chat_message",
        "get_chat_messages",
    ):
        assert hasattr(db_pkg, name), name
    assert db_pkg.DEFAULT_USER == "default"
    assert db_pkg.QTY_EPSILON == 1e-9
