#!/usr/bin/env bash
# Start FinAlly in Docker (macOS / Linux). Idempotent: safe to run repeatedly.
#
# Usage: scripts/start_mac.sh [--build] [--no-open]
#   --build    Rebuild the image even if it already exists
#   --no-open  Do not open the browser
set -euo pipefail

IMAGE="finally:latest"
CONTAINER="finally"
VOLUME="finally-data"
PORT="${FINALLY_PORT:-8000}"
URL="http://localhost:${PORT}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUILD=0
OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --no-open) OPEN_BROWSER=0 ;;
    -h|--help)
      sed -n '2,6p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Error: cannot talk to the Docker daemon. Is Docker running?" >&2
  exit 1
fi

if [[ "$BUILD" -eq 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building image $IMAGE ..."
  docker build -t "$IMAGE" "$ROOT_DIR"
fi

# Remove any existing container (running or stopped) so the run is fresh.
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Removing existing container $CONTAINER ..."
  docker rm -f "$CONTAINER" >/dev/null
fi

RUN_ARGS=(-d --name "$CONTAINER" -v "${VOLUME}:/app/db" -p "${PORT}:8000")
if [[ -f "$ROOT_DIR/.env" ]]; then
  RUN_ARGS+=(--env-file "$ROOT_DIR/.env")
else
  echo "Warning: $ROOT_DIR/.env not found; starting without it (copy .env.example to .env to configure keys)." >&2
fi

echo "Starting container $CONTAINER ..."
docker run "${RUN_ARGS[@]}" "$IMAGE" >/dev/null

# Prefer curl against the API; fall back to Docker's own HEALTHCHECK status.
app_healthy() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "${URL}/api/health" >/dev/null 2>&1
  else
    [[ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null)" == "healthy" ]]
  fi
}

echo -n "Waiting for the app to become healthy"
healthy=0
for _ in $(seq 1 90); do
  if app_healthy; then
    healthy=1
    break
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]]; then
    break
  fi
  echo -n "."
  sleep 1
done
echo

if [[ "$healthy" -ne 1 ]]; then
  echo "Error: the app did not become healthy. Recent logs:" >&2
  docker logs --tail 50 "$CONTAINER" >&2 || true
  exit 1
fi

echo "FinAlly is running at ${URL}"
echo "Stop it with: scripts/stop_mac.sh"

if [[ "$OPEN_BROWSER" -eq 1 ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
