"""Shared fixtures for service and API tests."""

from __future__ import annotations

import pytest

from app import db
from app.market import PriceCache

from .fakes import FakeSource


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    db.init_db(path)
    return path


@pytest.fixture
def cache() -> PriceCache:
    return PriceCache()


@pytest.fixture
def source(cache) -> FakeSource:
    return FakeSource(cache)
