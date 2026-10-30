"""Recent structured logs from the API process and the scanner (via Redis)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.api.deps import require_api_key
from backend.config.logging import recent_log_records
from backend.services import keys
from backend.services.redis_client import get_redis

router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(require_api_key)])


@router.get("")
async def logs(
    source: str = Query("all", pattern="^(all|api|scanner)$"),
    level: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if source in {"all", "api"}:
        items.extend({**r, "source": "api"} for r in recent_log_records(limit))
    if source in {"all", "scanner"}:
        try:
            raw = await get_redis().lrange(keys.SCANNER_LOGS, -limit, -1)
            for line in raw:
                try:
                    items.append({**json.loads(line), "source": "scanner"})
                except ValueError:
                    continue
        except Exception as exc:  # noqa: BLE001
            items.append({"event": "scanner logs unavailable", "error": str(exc)[:100], "level": "warning", "source": "api"})
    if level:
        items = [i for i in items if str(i.get("level", "")).lower() == level.lower()]
    items.sort(key=lambda i: str(i.get("timestamp", "")))
    return {"items": items[-limit:]}
