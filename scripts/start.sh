#!/usr/bin/env bash
# Single entrypoint. SERVICE_ROLE decides what this container runs:
#   all     (default) API + dashboard + scanner in one process  -> simplest Railway setup (one service)
#   api     API + dashboard only
#   scanner scanner worker only (persistent, never a cron job)
set -euo pipefail
ROLE="${SERVICE_ROLE:-all}"
export SERVICE_ROLE="$ROLE"
case "$ROLE" in
  all|api)
    export PORT="${PORT:-8080}"
    echo "[solhunter] role=$ROLE listening on 0.0.0.0:${PORT} (migrations=${RUN_MIGRATIONS_ON_START:-true})"
    exec python -m uvicorn backend.api.main:app --host 0.0.0.0 --port "${PORT}" --workers 1 \
      --proxy-headers --forwarded-allow-ips='*' --no-access-log --timeout-graceful-shutdown 25
    ;;
  scanner)
    echo "[solhunter] role=scanner starting persistent worker"
    exec python -m backend.scanner.main
    ;;
  *)
    echo "unknown SERVICE_ROLE=$ROLE (expected all|api|scanner)" >&2; exit 1
    ;;
esac
