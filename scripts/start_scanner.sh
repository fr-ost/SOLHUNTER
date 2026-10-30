#!/usr/bin/env bash
# Production start command for the gemfinder-scanner Railway service (persistent worker, NOT a cron job).
set -euo pipefail
export SERVICE_ROLE=scanner
echo "[start_scanner] starting GemFinder scanner worker"
exec python -m backend.scanner.main
