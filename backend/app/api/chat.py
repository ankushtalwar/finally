"""Chat endpoints (delegates to app.llm)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from app import db, llm
from app.services import PortfolioService, WatchlistService

from .deps import get_portfolio_service, get_watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

HISTORY_LIMIT = 50
FALLBACK_MESSAGE = (
    "Sorry, something went wrong on my side. No trades or watchlist changes were made."
)


class ChatRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        return v


@router.post("")
async def chat(
    body: ChatRequest,
    portfolio: PortfolioService = Depends(get_portfolio_service),
    watchlist: WatchlistService = Depends(get_watchlist_service),
) -> dict:
    # Contract: /api/chat always returns 200. app.llm handles LLM failures itself;
    # this guards anything unexpected (e.g. a DB error while persisting).
    try:
        return await llm.handle_chat(body.message, portfolio, watchlist)
    except Exception:
        logger.exception("Chat handling failed")
        return _fallback_response()


def _fallback_response() -> dict:
    try:
        return db.add_chat_message("assistant", FALLBACK_MESSAGE, _no_actions())
    except Exception:
        logger.exception("Could not persist fallback chat message")
        return {
            "id": str(uuid4()),
            "role": "assistant",
            "content": FALLBACK_MESSAGE,
            "actions": _no_actions(),
            "created_at": datetime.now(UTC).isoformat(),
        }


def _no_actions() -> dict:
    return {"trades": [], "watchlist_changes": []}


@router.get("/history")
def chat_history() -> dict:
    return {"messages": db.get_chat_messages(limit=HISTORY_LIMIT)}
