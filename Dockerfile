# syntax=docker/dockerfile:1
# FinAlly — single-container build: Next.js static export served by FastAPI.

# ---------------------------------------------------------------------------
# Stage 1: build the frontend static export (frontend/out)
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /app/frontend
ENV NEXT_TELEMETRY_DISABLED=1

# Install dependencies first for layer caching. The lockfile is optional.
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
RUN npm run build && test -f out/index.html

# ---------------------------------------------------------------------------
# Stage 2: Python runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app/backend

# Dependencies only (cached unless pyproject.toml / uv.lock change).
# UV_HTTP_TIMEOUT is raised from the 30s default so slow networks don't fail
# mid-download; it applies to these build steps only, not the runtime image.
COPY backend/pyproject.toml backend/uv.lock ./
RUN UV_HTTP_TIMEOUT=120 uv sync --frozen --no-dev --no-install-project

# Application code, then install the project itself into the venv.
COPY backend/ ./
RUN UV_HTTP_TIMEOUT=120 uv sync --frozen --no-dev

# Frontend static export.
COPY --from=frontend /app/frontend/out /app/backend/static

RUN mkdir -p /app/db

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    FINALLY_DB_PATH=/app/db/finally.db \
    FINALLY_STATIC_DIR=/app/backend/static \
    LLM_MOCK=false

VOLUME ["/app/db"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
