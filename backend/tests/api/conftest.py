"""Fixtures for API tests: a full app (real lifespan, temp DB, fake market source)."""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest
from fastapi.testclient import TestClient

from tests.services.fakes import FakeSource

# If the LLM module hasn't landed yet, install a placeholder so app.main imports.
if importlib.util.find_spec("app.llm") is None:  # pragma: no cover - depends on build timing
    _stub = types.ModuleType("app.llm")
    _stub.MODEL = "stub"

    async def _stub_handle_chat(message, portfolio, watchlist):
        raise RuntimeError("app.llm not available")

    _stub.handle_chat = _stub_handle_chat
    sys.modules["app.llm"] = _stub
    import app as _app_pkg

    _app_pkg.llm = _stub

SEED_PRICE = 100.0


def _make_client(monkeypatch, tmp_path, *, fake_source: bool = True, static_dir=None):
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "finally.db"))
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    if static_dir is None:
        monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path / "no-static"))
    else:
        monkeypatch.setenv("FINALLY_STATIC_DIR", str(static_dir))

    import app.main as main

    if fake_source:
        monkeypatch.setattr(
            main, "create_market_data_source", lambda cache: FakeSource(cache, SEED_PRICE)
        )
    return TestClient(main.create_app())


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    def factory(**kwargs):
        return _make_client(monkeypatch, tmp_path, **kwargs)

    return factory


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c
