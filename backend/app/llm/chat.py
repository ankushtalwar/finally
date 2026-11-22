"""Chat orchestration: context → LLM (or mock) → execute actions → persist → response."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import anthropic
import pydantic

from app import db

from .mock import mock_response
from .prompts import SYSTEM_PROMPT, build_context, build_messages
from .schema import ChatResponse

if TYPE_CHECKING:
    from app.services import PortfolioService, WatchlistService

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MAX_TOKENS = 4000
EFFORT = "low"
HISTORY_LIMIT = 20
REQUEST_TIMEOUT_S = 60.0
# Server-side refusal fallback (beta): if the model declines, the API re-runs the
# request on a fallback model (routed by refusal category) within the same call.
# Set False to send neither the beta header nor `fallbacks`.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
USE_REFUSAL_FALLBACK = True

MSG_GENERIC_ERROR = "Sorry, I couldn't reach the AI service just now. Please try again in a moment."
MSG_RATE_LIMIT = "I'm getting too many requests right now. Please wait a few seconds and try again."
MSG_NOT_CONFIGURED = (
    "The AI assistant isn't configured (no Anthropic API key). "
    "Set ANTHROPIC_API_KEY or LLM_MOCK=true to enable chat."
)
MSG_REFUSAL = "Sorry, I can't help with that request. No actions were taken."
MSG_TRUNCATED = "My response was cut off before I could finish. Please try a shorter request."
MSG_PARSE = "I had trouble formatting my response. Please try rephrasing. No actions were taken."

_client: anthropic.AsyncAnthropic | None = None


class LLMError(Exception):
    """An LLM call failed; `message` is user-facing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def is_mock_mode() -> bool:
    return os.environ.get("LLM_MOCK", "").strip().lower() == "true"


_CREDENTIAL_ATTRS = ("api_key", "auth_token", "credentials", "_custom_auth")


def _has_credentials(client: Any) -> bool:
    """False only for a real SDK client that resolved no credential source.

    With no credentials the SDK constructs fine but raises a plain ``TypeError`` at
    request time, so we detect it up front and report "not configured".
    """
    if not isinstance(client, anthropic.AsyncAnthropic):
        return True  # test doubles / custom clients
    return any(getattr(client, a, None) is not None for a in _CREDENTIAL_ATTRS)


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        client = anthropic.AsyncAnthropic(timeout=REQUEST_TIMEOUT_S, max_retries=2)
        if not _has_credentials(client):
            # Don't cache: a key added later (e.g. tests, reload) is picked up next call.
            raise anthropic.AnthropicError("No Anthropic credentials configured")
        _client = client
    return _client


def set_client(client: anthropic.AsyncAnthropic | None) -> None:
    """Override (or reset) the shared client — used by tests."""
    global _client
    _client = client


async def _parse_request(client: Any, kwargs: dict[str, Any]) -> Any:
    """`messages.parse` via `.with_raw_response` so `stop_reason` is checked first.

    `messages.parse` validates the text against the schema while the response is
    being processed, so a refusal or a `max_tokens` cut-off (truncated JSON) would
    otherwise surface as a validation error before we ever see `stop_reason`.

    Only the beta messages resource exposes `parse` on `.with_raw_response`, so it is
    used in both modes; with the fallback off no beta header or `fallbacks` is sent.
    """
    if USE_REFUSAL_FALLBACK:
        kwargs = {**kwargs, "betas": [REFUSAL_FALLBACK_BETA], "fallbacks": "default"}
    raw = await client.beta.messages.with_raw_response.parse(**kwargs)

    body = await raw.json()
    stop = body.get("stop_reason") if isinstance(body, dict) else None
    if stop == "refusal":
        details = body.get("stop_details") or {}
        logger.warning("LLM refused (category=%s)", details.get("category"))
        raise LLMError(MSG_REFUSAL)
    if stop == "max_tokens":
        logger.warning("LLM response hit max_tokens (%s)", MAX_TOKENS)
        raise LLMError(MSG_TRUNCATED)
    return await raw.parse()  # validates against ChatResponse


async def call_llm(messages: list[dict]) -> ChatResponse:
    """Call Claude with structured output. Raises LLMError on every failure path."""
    try:
        client = get_client()
        response = await _parse_request(
            client,
            {
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": messages,
                "output_format": ChatResponse,
                "output_config": {"effort": EFFORT},
            },
        )
    except LLMError:
        raise
    except anthropic.AuthenticationError:
        logger.warning("Anthropic authentication failed")
        raise LLMError(MSG_NOT_CONFIGURED) from None
    except anthropic.RateLimitError:
        logger.warning("Anthropic rate limit hit")
        raise LLMError(MSG_RATE_LIMIT) from None
    except anthropic.APIStatusError as e:
        logger.warning("Anthropic API error %s: %s", e.status_code, e.message)
        raise LLMError(MSG_GENERIC_ERROR) from None
    except anthropic.APIConnectionError:
        logger.warning("Anthropic connection error")
        raise LLMError(MSG_GENERIC_ERROR) from None
    except (pydantic.ValidationError, ValueError) as e:
        logger.warning("Could not parse structured LLM output: %s", e)
        raise LLMError(MSG_PARSE) from None
    except anthropic.AnthropicError as e:
        # e.g. no credentials configured at all
        logger.warning("Anthropic client error: %s", type(e).__name__)
        raise LLMError(MSG_NOT_CONFIGURED) from None
    except Exception:
        logger.exception("Unexpected error calling the LLM")
        raise LLMError(MSG_GENERIC_ERROR) from None

    parsed = getattr(response, "parsed_output", None)
    if not isinstance(parsed, ChatResponse):
        logger.warning("LLM returned no parsed output (stop_reason=%s)", response.stop_reason)
        raise LLMError(MSG_PARSE)
    return parsed


def _empty_actions() -> dict:
    return {"trades": [], "watchlist_changes": []}


def _error_text(exc: Exception) -> str | None:
    """User-facing text for TradeError / WatchlistError (both carry `.message`)."""
    msg = getattr(exc, "message", None)
    return msg if isinstance(msg, str) and msg else None


async def execute_actions(
    reply: ChatResponse, portfolio: PortfolioService, watchlist: WatchlistService
) -> dict:
    """Watchlist changes first, then trades, each in order; failures don't stop the rest."""
    actions = _empty_actions()

    for change in reply.watchlist_changes:
        ticker = change.ticker.strip().upper()
        result = {"ticker": ticker, "action": change.action, "status": "executed", "error": None}
        try:
            if change.action == "add":
                await watchlist.add(ticker)
            else:
                await watchlist.remove(ticker)
        except Exception as e:
            text = _error_text(e)
            if change.action == "add" and getattr(e, "status_code", None) == 409:
                pass  # already watched counts as executed
            else:
                if text is None:
                    logger.exception("Unexpected watchlist error for %s", ticker)
                result.update(status="failed", error=text or "Unexpected error")
        actions["watchlist_changes"].append(result)

    for trade in reply.trades:
        ticker = trade.ticker.strip().upper()
        result = {
            "ticker": ticker,
            "side": trade.side,
            "quantity": trade.quantity,
            "status": "executed",
            "price": None,
            "error": None,
        }
        try:
            executed = await portfolio.execute_trade(ticker, trade.side, trade.quantity)
            result["price"] = executed.get("price")
            result["quantity"] = executed.get("quantity", trade.quantity)
        except Exception as e:
            text = _error_text(e)
            if text is None:
                logger.exception("Unexpected trade error for %s", ticker)
            result.update(status="failed", error=text or "Unexpected error")
        actions["trades"].append(result)

    return actions


async def handle_chat(
    message: str, portfolio: PortfolioService, watchlist: WatchlistService
) -> dict:
    """Run one chat turn. Always returns the POST /api/chat response body."""
    message = (message or "").strip()
    history = db.get_chat_messages(limit=HISTORY_LIMIT)
    db.add_chat_message("user", message)

    port = portfolio.get_portfolio()

    if is_mock_mode():
        reply: ChatResponse | None = mock_response(message, float(port.get("total_value") or 0))
        content = reply.message
    else:
        context = build_context(port, watchlist.get_watchlist())
        try:
            reply = await call_llm(build_messages(history, message, context))
            content = reply.message
        except LLMError as e:
            reply, content = None, e.message

    actions = (
        await execute_actions(reply, portfolio, watchlist)
        if reply is not None
        else _empty_actions()
    )
    saved = db.add_chat_message("assistant", content, actions)
    return {
        "id": saved["id"],
        "role": "assistant",
        "content": content,
        "created_at": saved["created_at"],
        "actions": actions,
    }
