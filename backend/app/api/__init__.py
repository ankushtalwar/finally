"""REST API routers."""

from .chat import router as chat_router
from .health import router as health_router
from .portfolio import router as portfolio_router
from .watchlist import router as watchlist_router

ROUTERS = [health_router, watchlist_router, portfolio_router, chat_router]

__all__ = ["ROUTERS"]
