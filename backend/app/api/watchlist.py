"""Watchlist endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.services import WatchlistError, WatchlistService

from .deps import get_watchlist_service

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AddTickerRequest(BaseModel):
    ticker: str


@router.get("")
def list_watchlist(svc: WatchlistService = Depends(get_watchlist_service)) -> dict:
    return {"tickers": svc.get_watchlist()}


@router.post("", status_code=201)
async def add_ticker(
    body: AddTickerRequest, svc: WatchlistService = Depends(get_watchlist_service)
) -> dict:
    try:
        return await svc.add(body.ticker)
    except WatchlistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/{ticker}", status_code=204)
async def remove_ticker(
    ticker: str, svc: WatchlistService = Depends(get_watchlist_service)
) -> Response:
    try:
        await svc.remove(ticker)
    except WatchlistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return Response(status_code=204)
