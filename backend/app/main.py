"""FinAlly FastAPI application: API routes, SSE stream and static frontend."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

_env_file = REPO_ROOT / ".env"
if _env_file.is_file():
    load_dotenv(_env_file)

from app import db  # noqa: E402
from app.api import ROUTERS  # noqa: E402
from app.market import PriceCache, create_market_data_source, create_stream_router  # noqa: E402
from app.services import PortfolioService, WatchlistService  # noqa: E402

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SECONDS = 30.0


def static_dir() -> Path:
    return Path(os.environ.get("FINALLY_STATIC_DIR") or BACKEND_DIR / "static")


async def _snapshot_loop(portfolio: PortfolioService, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        portfolio.record_snapshot()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()

    cache: PriceCache = app.state.price_cache
    source = create_market_data_source(cache)
    universe = list(
        dict.fromkeys([*db.get_watchlist(), *(p["ticker"] for p in db.get_positions())])
    )
    await source.start(universe)

    portfolio = PortfolioService(cache, source)
    watchlist = WatchlistService(cache, source)
    app.state.market_source = source
    app.state.portfolio_service = portfolio
    app.state.watchlist_service = watchlist

    portfolio.record_snapshot()
    snapshot_task = asyncio.create_task(
        _snapshot_loop(portfolio, SNAPSHOT_INTERVAL_SECONDS), name="portfolio-snapshots"
    )
    try:
        yield
    finally:
        snapshot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await snapshot_task
        await source.stop()


class SPAStaticFiles(StaticFiles):
    """StaticFiles that serves index.html for unknown non-API paths."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        request_path = scope.get("path", "")
        if request_path == "/api" or request_path.startswith("/api/"):
            # Unknown API routes stay JSON 404s, never the HTML shell.
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = None
        if response is not None and response.status_code != 404:
            return response
        # A missing asset (e.g. a stale /_next/*.js chunk) must 404, not get the HTML shell.
        suffix = Path(path).suffix
        if suffix and suffix != ".html":
            raise StarletteHTTPException(status_code=404)
        # Next.js export emits `/route.html` for `/route`; otherwise fall back to the SPA shell.
        for candidate in (f"{path.rstrip('/')}.html", "index.html"):
            full_path, stat_result = self.lookup_path(candidate)
            if stat_result is not None and stat.S_ISREG(stat_result.st_mode):
                return self.file_response(full_path, stat_result, scope)
        raise StarletteHTTPException(status_code=404)


def create_app() -> FastAPI:
    app = FastAPI(title="FinAlly", lifespan=lifespan)
    # The cache exists before startup so the SSE router can bind to it.
    app.state.price_cache = PriceCache()

    for router in ROUTERS:
        app.include_router(router)
    app.include_router(create_stream_router(app.state.price_cache))

    directory = static_dir()
    if directory.is_dir():
        app.mount("/", SPAStaticFiles(directory=directory, html=True), name="static")
    else:
        logger.info("Static dir %s not found; serving API only", directory)
    return app


app = create_app()
