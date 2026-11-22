"""Real-mode chat flow with a mocked Anthropic client (never calls the API)."""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from app import db
from app.llm import MODEL, ChatResponse, TradeInstruction, WatchlistChange, handle_chat
from app.llm import chat as chat_mod
from app.llm.prompts import SYSTEM_PROMPT, build_messages

from .conftest import make_response

_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, code):
    return cls("boom", response=httpx2.Response(code, request=_REQ), body=None)


def _reply(**kw):
    return ChatResponse(message=kw.pop("message", "ok"), **kw)


class TestSdkCall:
    async def test_call_shape(self, portfolio, watchlist, fake_client):
        fake_client.messages.parse.return_value = make_response(_reply(message="Hi!"))
        resp = await handle_chat("hello", portfolio, watchlist)
        assert resp["content"] == "Hi!"

        kwargs = fake_client.messages.parse.call_args.kwargs
        assert kwargs["model"] == MODEL == "claude-opus-5-5"
        assert kwargs["output_format"] is ChatResponse
        assert kwargs["output_config"] == {"effort": "low"}
        assert kwargs["max_tokens"] == 4000
        assert kwargs["system"] == SYSTEM_PROMPT
        assert "FinAlly, an AI trading assistant" in kwargs["system"]
        # Refusal fallback is on by default (beta endpoint).
        assert kwargs["fallbacks"] == "default"
        assert kwargs["betas"] == [chat_mod.REFUSAL_FALLBACK_BETA]

    async def test_plain_endpoint_when_fallback_disabled(
        self, portfolio, watchlist, fake_client, monkeypatch
    ):
        monkeypatch.setattr(chat_mod, "USE_REFUSAL_FALLBACK", False)
        fake_client.messages.parse.return_value = make_response(_reply())
        await handle_chat("hello", portfolio, watchlist)
        kwargs = fake_client.messages.parse.call_args.kwargs
        assert "fallbacks" not in kwargs and "betas" not in kwargs

    async def test_prompt_includes_context_and_history(self, portfolio, watchlist, fake_client):
        portfolio.holdings["AAPL"] = 10
        db.add_chat_message("user", "earlier question")
        db.add_chat_message(
            "assistant",
            "earlier answer",
            {
                "trades": [
                    {
                        "ticker": "AAPL",
                        "side": "buy",
                        "quantity": 10,
                        "status": "executed",
                        "price": 180.0,
                        "error": None,
                    }
                ],
                "watchlist_changes": [],
            },
        )
        fake_client.messages.parse.return_value = make_response(_reply())
        await handle_chat("how is my portfolio?", portfolio, watchlist)

        messages = fake_client.messages.parse.call_args.kwargs["messages"]
        assert messages[0] == {"role": "user", "content": "earlier question"}
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"].startswith("earlier answer")
        assert "buy 10 AAPL: executed @ $180.00" in messages[1]["content"]
        last = messages[-1]
        assert last["role"] == "user"
        assert last["content"].endswith("how is my portfolio?")
        assert "Cash: $10,000.00" in last["content"]
        assert "- AAPL | 10 | $180.00 | $190.00" in last["content"]
        assert "## Watchlist" in last["content"]
        assert "MSFT | $400.00 | +1.25%" in last["content"]
        # The new user message must not be duplicated from history.
        assert sum("how is my portfolio?" in m["content"] for m in messages) == 1

    async def test_history_limited_to_20(self, portfolio, watchlist, fake_client):
        for i in range(30):
            db.add_chat_message("user" if i % 2 == 0 else "assistant", f"m{i}")
        fake_client.messages.parse.return_value = make_response(_reply())
        await handle_chat("now", portfolio, watchlist)
        messages = fake_client.messages.parse.call_args.kwargs["messages"]
        # last 20 = m10..m29 -> starts with user m10; plus the new turn
        assert len(messages) == 21
        assert messages[0]["content"] == "m10"


class TestBuildMessages:
    def test_drops_leading_assistant(self):
        msgs = build_messages(
            [
                {"role": "assistant", "content": "a", "actions": None},
                {"role": "user", "content": "u", "actions": None},
            ],
            "new",
            "ctx",
        )
        assert msgs[0] == {"role": "user", "content": "u"}
        assert len(msgs) == 2


class TestActionExecution:
    async def test_order_and_partial_failure(self, portfolio, watchlist, fake_client):
        portfolio.holdings["MSFT"] = 1
        reply = _reply(
            message="Doing several things",
            watchlist_changes=[
                WatchlistChange(ticker="bad!", action="add"),
                WatchlistChange(ticker="aapl", action="add"),  # already watched -> executed
                WatchlistChange(ticker="pypl", action="add"),
                WatchlistChange(ticker="ZZZ", action="remove"),  # not watched -> failed
            ],
            trades=[
                TradeInstruction(ticker="aapl", side="buy", quantity=1000),  # insufficient cash
                TradeInstruction(ticker="pypl", side="buy", quantity=2),
                TradeInstruction(ticker="MSFT", side="sell", quantity=1),
                TradeInstruction(ticker="XYZQ", side="buy", quantity=1),  # no price
            ],
        )
        fake_client.messages.parse.return_value = make_response(reply)
        resp = await handle_chat("go", portfolio, watchlist)

        assert watchlist.calls == [
            ("add", "BAD!"),
            ("add", "AAPL"),
            ("add", "PYPL"),
            ("remove", "ZZZ"),
        ]
        assert [c[1] for c in portfolio.calls] == ["AAPL", "PYPL", "MSFT", "XYZQ"]

        wl = resp["actions"]["watchlist_changes"]
        assert [(w["ticker"], w["status"]) for w in wl] == [
            ("BAD!", "failed"),
            ("AAPL", "executed"),
            ("PYPL", "executed"),
            ("ZZZ", "failed"),
        ]
        assert wl[0]["error"] == "Invalid ticker symbol"
        assert wl[1]["error"] is None
        assert wl[3]["error"] == "ZZZ is not on the watchlist"

        tr = resp["actions"]["trades"]
        assert [(t["ticker"], t["status"]) for t in tr] == [
            ("AAPL", "failed"),
            ("PYPL", "executed"),
            ("MSFT", "executed"),
            ("XYZQ", "failed"),
        ]
        assert tr[0]["error"] == "Insufficient cash" and tr[0]["price"] is None
        assert tr[1]["price"] == 50.0 and tr[1]["error"] is None
        assert tr[3]["error"] == "No price available for XYZQ yet"

        saved = db.get_chat_messages(limit=1)[0]
        assert saved["actions"] == resp["actions"]

    async def test_unexpected_service_exception_is_contained(
        self, portfolio, watchlist, fake_client, monkeypatch
    ):
        async def explode(*a, **k):
            raise RuntimeError("db locked")

        monkeypatch.setattr(portfolio, "execute_trade", explode)
        fake_client.messages.parse.return_value = make_response(
            _reply(trades=[TradeInstruction(ticker="AAPL", side="buy", quantity=1)])
        )
        resp = await handle_chat("buy", portfolio, watchlist)
        assert resp["actions"]["trades"][0]["status"] == "failed"
        assert resp["actions"]["trades"][0]["error"] == "Unexpected error"


class TestErrorPaths:
    @pytest.mark.parametrize(
        "exc, expected",
        [
            (_status_error(anthropic.RateLimitError, 429), chat_mod.MSG_RATE_LIMIT),
            (_status_error(anthropic.AuthenticationError, 401), chat_mod.MSG_NOT_CONFIGURED),
            (_status_error(anthropic.InternalServerError, 500), chat_mod.MSG_GENERIC_ERROR),
            (_status_error(anthropic.BadRequestError, 400), chat_mod.MSG_GENERIC_ERROR),
            (anthropic.APIConnectionError(request=_REQ), chat_mod.MSG_GENERIC_ERROR),
            (anthropic.APITimeoutError(request=_REQ), chat_mod.MSG_GENERIC_ERROR),
            (ValueError("bad json"), chat_mod.MSG_PARSE),
            (anthropic.AnthropicError("no credentials"), chat_mod.MSG_NOT_CONFIGURED),
            (RuntimeError("weird"), chat_mod.MSG_GENERIC_ERROR),
        ],
    )
    async def test_sdk_exceptions(self, portfolio, watchlist, fake_client, exc, expected):
        fake_client.messages.parse.side_effect = exc
        resp = await handle_chat("buy 5 AAPL", portfolio, watchlist)
        self._assert_friendly(resp, expected, portfolio, watchlist)

    async def test_pydantic_validation_error(self, portfolio, watchlist, fake_client):
        try:
            TradeInstruction(ticker="AAPL", side="buy", quantity=-1)
        except Exception as e:  # pydantic.ValidationError
            fake_client.messages.parse.side_effect = e
        resp = await handle_chat("buy", portfolio, watchlist)
        self._assert_friendly(resp, chat_mod.MSG_PARSE, portfolio, watchlist)

    @pytest.mark.parametrize(
        "stop_reason, expected",
        [("refusal", chat_mod.MSG_REFUSAL), ("max_tokens", chat_mod.MSG_TRUNCATED)],
    )
    async def test_stop_reasons(self, portfolio, watchlist, fake_client, stop_reason, expected):
        # Even if a parsed output is present, it must not be acted on.
        fake_client.messages.parse.return_value = make_response(
            _reply(trades=[TradeInstruction(ticker="AAPL", side="buy", quantity=1)]),
            stop_reason=stop_reason,
        )
        resp = await handle_chat("buy 1 AAPL", portfolio, watchlist)
        self._assert_friendly(resp, expected, portfolio, watchlist)

    async def test_missing_parsed_output(self, portfolio, watchlist, fake_client):
        fake_client.messages.parse.return_value = make_response(None)
        resp = await handle_chat("hi", portfolio, watchlist)
        self._assert_friendly(resp, chat_mod.MSG_PARSE, portfolio, watchlist)

    async def test_client_construction_failure(self, portfolio, watchlist, monkeypatch):
        def boom():
            raise anthropic.AnthropicError("missing api key")

        monkeypatch.setattr(chat_mod, "get_client", boom)
        resp = await handle_chat("hi", portfolio, watchlist)
        self._assert_friendly(resp, chat_mod.MSG_NOT_CONFIGURED, portfolio, watchlist)

    @staticmethod
    def _assert_friendly(resp, expected, portfolio, watchlist):
        assert resp["role"] == "assistant"
        assert resp["content"] == expected
        assert resp["actions"] == {"trades": [], "watchlist_changes": []}
        assert portfolio.calls == [] and watchlist.calls == []
        msgs = db.get_chat_messages(limit=5)
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["content"] == expected
