"""Dependency helpers: services live on app.state (set in the lifespan)."""

from __future__ import annotations

from fastapi import Request

from app.services import PortfolioService, WatchlistService


def get_portfolio_service(request: Request) -> PortfolioService:
    return request.app.state.portfolio_service


def get_watchlist_service(request: Request) -> WatchlistService:
    return request.app.state.watchlist_service
