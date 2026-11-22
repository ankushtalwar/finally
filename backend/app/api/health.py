"""GET /api/health."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request

from app.market.massive_client import MassiveDataSource

router = APIRouter(prefix="/api", tags=["system"])


def llm_mock_enabled() -> bool:
    return os.environ.get("LLM_MOCK", "").strip().lower() == "true"


@router.get("/health")
def health(request: Request) -> dict:
    source = getattr(request.app.state, "market_source", None)
    is_massive = isinstance(source, MassiveDataSource)
    return {
        "status": "ok",
        "market_source": "massive" if is_massive else "simulator",
        "market_healthy": bool(source.healthy) if is_massive else True,
        "llm_mock": llm_mock_enabled(),
    }
