"""Fixtures for DB tests: every test gets a fresh, isolated SQLite file."""

import pytest

from app.db import database


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    # Reset module state so nothing leaks between tests (restored by monkeypatch).
    monkeypatch.setattr(database, "_db_path", None)
    monkeypatch.setattr(database, "_initialized", False)
    return path


@pytest.fixture
def db(db_path):
    database.init_db(db_path)
    return database
