"""Wire-level tests: the real AsyncAnthropic client against an in-process mock transport.

These verify the exact HTTP request the SDK sends and how real SDK responses/errors
flow through `handle_chat`. No network access and no real API key are used.
"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from app import db
from app.llm import MODEL, handle_chat
from app.llm import chat as chat_mod

FAKE_KEY = "test-key-not-real"


def _message_body(payload: dict | None, stop_reason: str = "end_turn") -> dict:
    content = [] if payload is None else [{"type": "text", "text": json.dumps(payload)}]
    body = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }
    if stop_reason == "refusal":
        body["stop_details"] = {"type": "refusal", "category": None, "explanation": None}
    return body


class Recorder:
    def __init__(self, status: int = 200, body: dict | None = None, headers=None):
        self.status, self.body, self.headers = status, body, headers or {}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body, headers=self.headers)

    @property
    def last_json(self) -> dict:
        return json.loads(self.requests[-1].content)


def _install(recorder: Recorder) -> None:
    client = anthropic.AsyncAnthropic(
        api_key=FAKE_KEY,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
    )
    chat_mod.set_client(client)


OK_PAYLOAD = {
    "message": "Buying 2 AAPL and watching PYPL.",
    "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 2}],
    "watchlist_changes": [{"ticker": "PYPL", "action": "add"}],
}


class TestWireRequest:
    @pytest.mark.parametrize("fallback", [True, False])
    async def test_request_body(self, portfolio, watchlist, monkeypatch, fallback):
        monkeypatch.setattr(chat_mod, "USE_REFUSAL_FALLBACK", fallback)
        rec = Recorder(body=_message_body(OK_PAYLOAD))
        _install(rec)

        resp = await handle_chat("buy 2 AAPL and watch PYPL", portfolio, watchlist)

        req = rec.requests[-1]
        assert req.url.path == "/v1/messages"
        body = rec.last_json
        assert body["model"] == "claude-opus-5-5"
        assert body["max_tokens"] == 4000
        assert body["output_config"]["effort"] == "low"
        fmt = body["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        assert set(fmt["schema"]["properties"]) == {"message", "trades", "watchlist_changes"}
        assert "FinAlly, an AI trading assistant" in body["system"]
        assert "thinking" not in body  # Opus 5.5: effort is the only control
        # No assistant prefill: the conversation always ends on a user turn.
        assert body["messages"][-1]["role"] == "user"
        beta_header = req.headers.get("anthropic-beta", "")
        if fallback:
            assert body["fallbacks"] == "default"
            assert chat_mod.REFUSAL_FALLBACK_BETA in beta_header
        else:
            assert "fallbacks" not in body
            assert chat_mod.REFUSAL_FALLBACK_BETA not in beta_header

        # Parsed output flowed through to execution: watchlist first, then trade.
        assert watchlist.calls == [("add", "PYPL")]
        assert portfolio.calls == [("trade", "AAPL", "buy", 2.0)]
        assert resp["content"] == OK_PAYLOAD["message"]
        assert resp["actions"]["trades"][0]["status"] == "executed"
        assert resp["actions"]["watchlist_changes"][0]["status"] == "executed"


class TestWirePartialFailure:
    async def test_bad_quantity_fails_only_that_trade(self, portfolio, watchlist):
        from app.services.portfolio import TradeError

        real = portfolio.execute_trade

        async def strict(ticker, side, quantity):  # mirrors the real service rule
            if quantity <= 0:
                portfolio.calls.append(("trade", ticker, side, quantity))
                raise TradeError("Quantity must be greater than 0")
            return await real(ticker, side, quantity)

        portfolio.execute_trade = strict
        payload = {
            "message": "Placing two orders.",
            "trades": [
                {"ticker": "AAPL", "side": "buy", "quantity": 0},
                {"ticker": "MSFT", "side": "buy", "quantity": 1},
            ],
        }
        _install(Recorder(body=_message_body(payload)))
        resp = await handle_chat("buy stuff", portfolio, watchlist)
        trades = resp["actions"]["trades"]
        assert [t["status"] for t in trades] == ["failed", "executed"]
        assert trades[0]["error"] == "Quantity must be greater than 0"
        assert trades[1]["price"] == 400.0
        stored = db.get_chat_messages(limit=1)[0]
        assert stored["role"] == "assistant" and stored["actions"] == resp["actions"]


class TestWireErrors:
    async def _run(self, portfolio, watchlist, rec, expected):
        _install(rec)
        resp = await handle_chat("buy 2 AAPL", portfolio, watchlist)
        assert resp["content"] == expected
        assert resp["actions"] == {"trades": [], "watchlist_changes": []}
        assert portfolio.calls == [] and watchlist.calls == []
        assert [m["role"] for m in db.get_chat_messages(limit=5)] == ["user", "assistant"]

    async def test_refusal(self, portfolio, watchlist):
        rec = Recorder(body=_message_body(None, stop_reason="refusal"))
        await self._run(portfolio, watchlist, rec, chat_mod.MSG_REFUSAL)

    async def test_max_tokens(self, portfolio, watchlist):
        rec = Recorder(body=_message_body(None, stop_reason="max_tokens"))
        await self._run(portfolio, watchlist, rec, chat_mod.MSG_TRUNCATED)

    async def test_max_tokens_with_truncated_json(self, portfolio, watchlist):
        # parse() validates eagerly; stop_reason must still win over the parse error.
        body = _message_body(None, stop_reason="max_tokens")
        body["content"] = [{"type": "text", "text": '{"message": "Buying 2 AA'}]
        await self._run(portfolio, watchlist, Recorder(body=body), chat_mod.MSG_TRUNCATED)

    async def test_refusal_with_partial_text(self, portfolio, watchlist):
        body = _message_body(None, stop_reason="refusal")
        body["content"] = [{"type": "text", "text": '{"message": "I'}]
        await self._run(portfolio, watchlist, Recorder(body=body), chat_mod.MSG_REFUSAL)

    async def test_refusal_with_valid_json_executes_nothing(self, portfolio, watchlist):
        rec = Recorder(body=_message_body(OK_PAYLOAD, stop_reason="refusal"))
        await self._run(portfolio, watchlist, rec, chat_mod.MSG_REFUSAL)

    async def test_schema_violation(self, portfolio, watchlist):
        bad = {"message": "x", "trades": [{"ticker": "AAPL", "side": "hold", "quantity": 1}]}
        rec = Recorder(body=_message_body(bad))
        await self._run(portfolio, watchlist, rec, chat_mod.MSG_PARSE)

    @pytest.mark.parametrize(
        "status, err_type, expected",
        [
            (429, "rate_limit_error", chat_mod.MSG_RATE_LIMIT),
            (401, "authentication_error", chat_mod.MSG_NOT_CONFIGURED),
            (529, "overloaded_error", chat_mod.MSG_GENERIC_ERROR),
            (400, "invalid_request_error", chat_mod.MSG_GENERIC_ERROR),
        ],
    )
    async def test_http_errors(self, portfolio, watchlist, status, err_type, expected):
        body = {"type": "error", "error": {"type": err_type, "message": "test"}}
        rec = Recorder(status=status, body=body)
        await self._run(portfolio, watchlist, rec, expected)


class TestMissingCredentials:
    async def test_no_key_is_friendly_and_makes_no_request(
        self, portfolio, watchlist, monkeypatch, tmp_path
    ):
        import os

        for k in list(os.environ):
            if k.startswith("ANTHROPIC_"):
                monkeypatch.delenv(k)
        # Isolate from any on-disk CLI profile.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        resp = await handle_chat("buy 2 AAPL", portfolio, watchlist)
        assert resp["content"] == chat_mod.MSG_NOT_CONFIGURED
        assert resp["actions"] == {"trades": [], "watchlist_changes": []}
        assert portfolio.calls == []
        assert chat_mod._client is None  # not cached; a later key will be picked up
