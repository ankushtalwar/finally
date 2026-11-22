"""Portfolio endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.services import PortfolioService, TradeError

from .deps import get_portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

HISTORY_LIMIT = 1000


class TradeRequest(BaseModel):
    ticker: str
    quantity: float
    side: str


@router.get("")
def get_portfolio(svc: PortfolioService = Depends(get_portfolio_service)) -> dict:
    return svc.get_portfolio()


@router.post("/trade")
async def trade(body: TradeRequest, svc: PortfolioService = Depends(get_portfolio_service)) -> dict:
    try:
        executed = await svc.execute_trade(body.ticker, body.side, body.quantity)
    except TradeError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"trade": executed, "portfolio": svc.get_portfolio()}


@router.get("/history")
def history() -> dict:
    return {"snapshots": db.get_snapshots(limit=HISTORY_LIMIT)}
