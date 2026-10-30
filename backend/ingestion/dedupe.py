"""Two-level event deduplication: in-process LRU set first, then Redis (shared across restarts)."""

from __future__ import annotations

from collections import OrderedDict

from backend.services import keys
from backend.services.redis_client import mark_seen


class Deduplicator:
    def __init__(self, local_capacity: int = 200_000, redis_ttl_seconds: int = 6 * 3600) -> None:
        self._local: OrderedDict[str, None] = OrderedDict()
        self._capacity = local_capacity
        self._ttl = redis_ttl_seconds
        self.duplicates = 0

    def _local_seen(self, key: str) -> bool:
        if key in self._local:
            self._local.move_to_end(key)
            return True
        self._local[key] = None
        if len(self._local) > self._capacity:
            self._local.popitem(last=False)
        return False

    async def is_new(self, key: str, use_redis: bool = True) -> bool:
        if self._local_seen(key):
            self.duplicates += 1
            return False
        if use_redis and not await mark_seen(keys.dedupe_key("evt", key), self._ttl):
            self.duplicates += 1
            return False
        return True
