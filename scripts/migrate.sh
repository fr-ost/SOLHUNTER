#!/usr/bin/env bash
# Run database migrations manually (e.g. `railway run ./scripts/migrate.sh` or a one-off deploy).
set -euo pipefail
exec python -m alembic upgrade head
