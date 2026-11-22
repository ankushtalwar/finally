"""Structured output schema: every valid shape parses; malformed JSON is rejected."""

from __future__ import annotations

import json

import pydantic
import pytest
from anthropic.lib._parse._transform import to_json_output_format

from app.llm import ChatResponse


@pytest.mark.parametrize(
    "payload, n_trades, n_changes",
    [
        ({"message": "Hi"}, 0, 0),
        ({"message": "Hi", "trades": [], "watchlist_changes": []}, 0, 0),
        ({"message": "Buying", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5}]}, 1, 0),
        ({"message": "Sell", "trades": [{"ticker": "AAPL", "side": "sell", "quantity": 0.5}]}, 1, 0),
        ({"message": "Watching", "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}, 0, 1),
        (
            {
                "message": "Both",
                "trades": [
                    {"ticker": "AAPL", "side": "buy", "quantity": 1},
                    {"ticker": "MSFT", "side": "sell", "quantity": 2.25},
                ],
                "watchlist_changes": [
                    {"ticker": "PYPL", "action": "add"},
                    {"ticker": "NFLX", "action": "remove"},
                ],
            },
            2,
            2,
        ),
    ],
)
def test_valid_shapes(payload, n_trades, n_changes):
    r = ChatResponse.model_validate_json(json.dumps(payload))
    assert r.message == payload["message"]
    assert len(r.trades) == n_trades
    assert len(r.watchlist_changes) == n_changes


def test_nonpositive_quantity_parses_so_only_that_trade_fails_later():
    # Rejected per trade by the trade service, not by schema validation.
    r = ChatResponse.model_validate_json(
        '{"message":"x","trades":[{"ticker":"AAPL","side":"buy","quantity":0}]}'
    )
    assert r.trades[0].quantity == 0


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        '{"message": "trunc',
        '{"trades": []}',  # message missing
        '{"message": "x", "trades": [{"ticker": "AAPL", "side": "hold", "quantity": 1}]}',
        '{"message": "x", "trades": [{"ticker": "AAPL", "side": "buy"}]}',
        '{"message": "x", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": "lots"}]}',
        '{"message": "x", "watchlist_changes": [{"ticker": "PYPL", "action": "watch"}]}',
    ],
)
def test_malformed_rejected(text):
    with pytest.raises(pydantic.ValidationError):
        ChatResponse.model_validate_json(text)


def test_wire_schema_is_strict_object():
    schema = to_json_output_format(ChatResponse)["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["message"]
    trade = schema["$defs"]["TradeInstruction"]
    assert trade["additionalProperties"] is False
    assert set(trade["required"]) == {"ticker", "side", "quantity"}
    assert trade["properties"]["side"]["enum"] == ["buy", "sell"]
    assert schema["$defs"]["WatchlistChange"]["properties"]["action"]["enum"] == ["add", "remove"]
