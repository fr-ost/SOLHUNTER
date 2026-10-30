#!/usr/bin/env bash
# Production start command for the gemfinder-api Railway service.
# Railway injects PORT; we bind 0.0.0.0 and run migrations (advisory-locked) when RUN_MIGRATIONS_ON_START=true.
set -euo pipefail
export SERVICE_ROLE=api
export PORT="${PORT:-8080}"
export WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
echo "[start_api] starting GemFinder API on 0.0.0.0:${PORT} (workers=${WEB_CONCURRENCY}, migrations=${RUN_MIGRATIONS_ON_START:-false})"
exec python -m uvicorn backend.api.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --no-access-log \
  --timeout-graceful-shutdown 20
