"""Observational worker presence manager using Redis TTL keys."""

from __future__ import annotations

import hashlib
import json
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from email_discovery_crawl_worker.config import WorkerSettings

logger = logging.getLogger(__name__)


def derive_instance_digest(instance_id: str) -> str:
    """Return 16-char hex digest of 128-bit instance ID for safe key storage."""
    return hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:16]


class WorkerPresenceManager:
    """Manages non-authoritative worker presence records in Redis."""

    def __init__(
        self,
        redis_client: redis.Redis,
        settings: WorkerSettings,
    ) -> None:
        self.redis = redis_client
        self.settings = settings
        self.instance_digest = derive_instance_digest(settings.instance_id)
        self.key_prefix = settings.redis_key_prefix
        self.presence_key = f"{self.key_prefix}:workers:{self.instance_digest}"
        self.worker_label = settings.worker_label or settings.worker_id or "unnamed_worker"

    async def update_presence(
        self,
        state: str,
        active_claims_count: int,
        ttl_seconds: int = 30,
    ) -> bool:
        """Update worker presence key in Redis with 30s TTL."""
        try:
            time_res = await self.redis.time()  # pyright: ignore[reportUnknownMemberType]
            now_ms = int(time_res[0] * 1000 + time_res[1] / 1000)

            payload = {
                "instance_id_digest": self.instance_digest,
                "worker_label": self.worker_label,
                "state": state,
                "concurrency": self.settings.concurrency,
                "active_claims": active_claims_count,
                "last_seen_redis_time_ms": now_ms,
            }

            await self.redis.set(
                self.presence_key,
                json.dumps(payload),
                ex=ttl_seconds,
            )
            return True
        except RedisError as exc:
            logger.warning(
                "Failed to update Redis presence key for instance %s: %s",
                self.instance_digest,
                type(exc).__name__,
            )
            return False

    async def remove_presence(self) -> None:
        """Remove worker presence key on graceful shutdown."""
        try:
            await self.redis.delete(self.presence_key)
        except RedisError:
            pass
