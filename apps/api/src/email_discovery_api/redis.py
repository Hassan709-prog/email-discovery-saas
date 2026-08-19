"""API Process Redis Client and Wake-Up Publisher."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Protocol

import redis.asyncio as redis

if TYPE_CHECKING:
    from email_discovery_api.config import Settings

logger = logging.getLogger(__name__)

WORK_AVAILABLE_PAYLOAD = json.dumps({"event": "work_available", "v": "1.0.0"})


class RedisPublisherProtocol(Protocol):
    """Protocol for sending queue wake-up broadcasts."""

    async def publish_work_available(self) -> None:
        """Publish a work_available wake-up ping."""
        ...


def sanitize_redis_url(url: str) -> str:
    """Mask credentials in Redis connection URL for safe logging."""
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        if parts.password or parts.username:
            hostname = parts.hostname or ""
            port_str = f":{parts.port}" if parts.port else ""
            netloc = f"***{port_str}" if not hostname else f"***@{hostname}{port_str}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return url
    except Exception:
        return "[sanitized_redis_url]"


class APIRedisClient:
    """Process-level Redis client for FastAPI application.

    Manages bounded connection pool, post-commit queue wake-up publishing,
    and single-flight cached readiness probes.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.raw_url = settings.redis_url.get_secret_value()
        self.sanitized_url = sanitize_redis_url(self.raw_url)
        self.prefix = settings.redis_key_prefix
        self.channel_name = f"{self.prefix}:events:work_available"

        self.pool: redis.ConnectionPool | None = None
        self.client: redis.Redis | None = None
        self.is_available: bool = False

        # Single-flight probe caching state
        self._health_lock = asyncio.Lock()
        self._cached_health_status: bool | None = None
        self._cached_health_time: float = 0.0
        self._cache_ttl_seconds: float = 5.0

    async def start(self) -> None:
        """Initialize connection pool and perform startup probe."""
        if self.pool is not None:
            return

        try:
            self.pool = redis.ConnectionPool.from_url(  # pyright: ignore[reportUnknownMemberType]
                self.raw_url,
                max_connections=self.settings.redis_max_connections,
                socket_timeout=self.settings.redis_socket_timeout,
                socket_connect_timeout=self.settings.redis_connect_timeout,
            )
            self.client = redis.Redis(connection_pool=self.pool)

            # Startup ping probe
            await asyncio.wait_for(
                self.client.ping(),  # pyright: ignore[reportUnknownMemberType]
                timeout=self.settings.redis_connect_timeout,
            )
            self.is_available = True
            logger.info("API Redis client connected successfully.")
        except Exception as exc:
            self.is_available = False
            logger.warning(
                "Redis probe failed on startup [code=REDIS_STARTUP_FAILED, error_type=%s]",
                type(exc).__name__,
            )
            if self.settings.redis_required:
                raise RuntimeError("Redis connection required but startup probe failed.") from exc

    async def close(self) -> None:
        """Close Redis client and connection pool cleanly on shutdown."""
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception as exc:
                logger.warning(
                    "Error closing Redis client [code=REDIS_CLOSE_FAILED, error_type=%s]",
                    type(exc).__name__,
                )
            self.client = None

        if self.pool is not None:
            try:
                await self.pool.disconnect()
            except Exception as exc:
                logger.warning(
                    "Error disconnecting Redis pool [code=REDIS_POOL_CLOSE_FAILED, error_type=%s]",
                    type(exc).__name__,
                )
            self.pool = None

        self.is_available = False

    async def publish_work_available(self) -> None:
        """Publish minimal work_available notification payload to Redis Pub/Sub."""
        if not self.is_available or self.client is None:
            logger.debug("Redis unavailable; skipping wake-up publish.")
            return

        try:
            await self.client.publish(self.channel_name, WORK_AVAILABLE_PAYLOAD)  # pyright: ignore[reportUnknownMemberType]
        except Exception as exc:
            self.is_available = False
            logger.warning(
                "Redis wake-up publish failed [code=REDIS_PUBLISH_FAILED, error_type=%s]",
                type(exc).__name__,
            )

    async def check_health(self) -> bool:
        """Perform cached 5s single-flight readiness probe."""
        now = time.monotonic()
        if (
            self._cached_health_status is not None
            and (now - self._cached_health_time) < self._cache_ttl_seconds
        ):
            return self._cached_health_status

        async with self._health_lock:
            # Double check after acquiring lock
            now = time.monotonic()
            if (
                self._cached_health_status is not None
                and (now - self._cached_health_time) < self._cache_ttl_seconds
            ):
                return self._cached_health_status

            if self.client is None:
                self._cached_health_status = False
                self._cached_health_time = now
                return False

            try:
                res = await asyncio.wait_for(
                    self.client.ping(),  # pyright: ignore[reportUnknownMemberType]
                    timeout=self.settings.redis_health_timeout,
                )
                status_ok = bool(res)
                self.is_available = status_ok
                self._cached_health_status = status_ok
                self._cached_health_time = now
                return status_ok
            except Exception as exc:
                self.is_available = False
                self._cached_health_status = False
                self._cached_health_time = now
                logger.warning(
                    "Redis health probe failed [code=REDIS_HEALTH_FAILED, error_type=%s]",
                    type(exc).__name__,
                )
                return False
