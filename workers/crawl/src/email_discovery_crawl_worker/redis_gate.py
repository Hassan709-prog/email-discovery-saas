"""Redis-Backed Distributed Domain Request Gate implementing RequestGateProtocol."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

import redis.asyncio as redis
from redis.exceptions import NoScriptError

from email_scanner.models import NormalizedURL
from email_scanner.request_gate import DomainRequestGate, RequestGateProtocol, get_domain_key

if TYPE_CHECKING:
    from email_discovery_crawl_worker.config import WorkerSettings

logger = logging.getLogger(__name__)


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


DOMAIN_RESERVATION_LUA = """
local key = KEYS[1]
local min_interval_ms = math.max(100, math.min(60000, tonumber(ARGV[1]) or 1000))
local crawl_delay_ms = math.max(0, math.min(60000, tonumber(ARGV[2]) or 0))
local max_interval_ms = math.max(min_interval_ms, math.min(300000, tonumber(ARGV[3]) or 60000))
local max_horizon_ms = math.max(1000, math.min(300000, tonumber(ARGV[4]) or 60000))
local min_ttl_ms = math.max(60000, tonumber(ARGV[5]) or 60000)
local max_ttl_ms = math.max(min_ttl_ms, tonumber(ARGV[6]) or 86400000)

-- Obtain Redis server time in milliseconds
local time_res = redis.call('TIME')
local now_ms = (tonumber(time_res[1]) * 1000) + math.floor(tonumber(time_res[2]) / 1000)

-- Read existing state stored as "next_allowed_ms:current_interval_ms"
local raw = redis.call('GET', key)
local last_allowed_ms = 0
local existing_interval_ms = 0

if raw then
    local sep = string.find(raw, ":")
    if sep then
        last_allowed_ms = tonumber(string.sub(raw, 1, sep - 1)) or 0
        existing_interval_ms = tonumber(string.sub(raw, sep + 1)) or 0
    end
end

-- NEVER DECREASE RULE: interval can only stay equal or increase
local effective_interval_ms = math.max(existing_interval_ms, min_interval_ms, crawl_delay_ms)
effective_interval_ms = math.min(effective_interval_ms, max_interval_ms)

local scheduled_ms = math.max(now_ms, last_allowed_ms)
local proposed_next_allowed_ms = scheduled_ms + effective_interval_ms
local reservation_horizon_ms = proposed_next_allowed_ms - now_ms

-- Check if proposed reservation horizon exceeds safe maximum
if reservation_horizon_ms > max_horizon_ms then
    local retry_after_ms = math.max(1000, scheduled_ms - now_ms)
    return { "DEFER", tostring(retry_after_ms), tostring(effective_interval_ms) }
end

-- Compute TTL from reservation horizon + effective interval + 5s safety margin
local safety_margin_ms = 5000
local computed_ttl_ms = reservation_horizon_ms + effective_interval_ms + safety_margin_ms
local ttl_ms = math.max(min_ttl_ms, math.min(max_ttl_ms, computed_ttl_ms))

-- Store reservation atomically
local new_val = tostring(proposed_next_allowed_ms) .. ":" .. tostring(effective_interval_ms)
redis.call('SET', key, new_val, 'PX', ttl_ms)

local delay_ms = math.max(0, scheduled_ms - now_ms)
return { "RESERVED", tostring(delay_ms), tostring(effective_interval_ms) }
"""


class RedisRateLimitPausedError(Exception):
    """Raised in strict_pause mode when Redis rate limiting is unavailable."""

    pass


class InvalidDomainError(ValueError):
    """Raised when target domain identity cannot be derived or is malformed."""

    pass


def derive_domain_digest(target_url: NormalizedURL) -> str:
    """Normalize target domain identity and derive stable SHA-256 digest for Redis key."""
    raw_domain = get_domain_key(target_url)
    if not raw_domain or not raw_domain.strip():
        raise InvalidDomainError("Target domain identity cannot be empty or whitespace.")

    normalized = raw_domain.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class RedisDomainRequestGate(RequestGateProtocol):
    """Distributed Redis-backed domain request rate-limiting gate."""

    def __init__(
        self,
        redis_client: redis.Redis | None,
        settings: WorkerSettings,
        local_fallback_gate: DomainRequestGate | None = None,
        async_sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.client = redis_client
        self.settings = settings
        self.local_gate = local_fallback_gate or DomainRequestGate(
            default_minimum_interval_seconds=settings.min_domain_interval_ms / 1000.0
        )
        self.sleeper = async_sleeper or asyncio.sleep

        self.prefix = settings.redis_key_prefix
        self.fallback_mode = settings.redis_rate_limit_fallback_mode.lower()
        self.script_sha: str | None = None
        self._learned_crawl_delays: dict[str, float] = {}

    async def _ensure_script_loaded(self) -> str:
        """Load Lua script onto Redis server and cache SHA digest."""
        if self.script_sha is not None:
            return self.script_sha

        if self.client is None:
            raise RedisRateLimitPausedError("Redis client is not initialized.")

        self.script_sha = str(await self.client.script_load(DOMAIN_RESERVATION_LUA))
        return self.script_sha

    def update_domain_interval(
        self,
        target_url: NormalizedURL,
        crawl_delay: float | None,
    ) -> None:
        """Update scan-local learned crawl delay to ensure interval never decreases."""
        if crawl_delay is None or math.isnan(crawl_delay) or math.isinf(crawl_delay):
            return

        try:
            digest = derive_domain_digest(target_url)
            current = self._learned_crawl_delays.get(digest, 0.0)
            safer = max(current, float(crawl_delay))
            self._learned_crawl_delays[digest] = safer
            # Update local gate fallback as well
            self.local_gate.update_domain_interval(target_url, crawl_delay)
        except InvalidDomainError:
            pass

    async def acquire(
        self,
        target_url: NormalizedURL,
        recorder: Any | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Atomically reserve domain request slot via Redis TIME Lua script or handle fallback."""
        domain_digest = derive_domain_digest(target_url)
        redis_key = f"{self.prefix}:rate_limit:{domain_digest}"

        start_t = time.monotonic()

        # If Redis is unavailable
        if self.client is None:
            if self.fallback_mode == "single_worker_local":
                # Conservative local gate with jitter
                learned_delay = self._learned_crawl_delays.get(domain_digest, 0.0)
                if learned_delay > 0.0:
                    self.local_gate.update_domain_interval(target_url, learned_delay)
                await self.local_gate.acquire(target_url, recorder=recorder)
                jitter = random.uniform(0.0, 0.5)
                if jitter > 0.0:
                    await self.sleeper(jitter)
                return
            else:
                # strict_pause mode
                raise RedisRateLimitPausedError(
                    "Redis rate limit coordination is unavailable in strict_pause mode."
                )

        learned_delay_ms = int(self._learned_crawl_delays.get(domain_digest, 0.0) * 1000)

        try:
            sha = await self._ensure_script_loaded()
            keys = [redis_key]
            argv = [
                str(self.settings.min_domain_interval_ms),
                str(learned_delay_ms),
                str(self.settings.max_domain_interval_ms),
                str(self.settings.max_reservation_horizon_ms),
                str(self.settings.min_ttl_ms),
                str(self.settings.max_ttl_ms),
            ]
            try:
                raw_res = await asyncio.wait_for(
                    cast(Any, self.client.evalsha(sha, len(keys), *keys, *argv)),
                    timeout=self.settings.redis_operation_timeout,
                )
            except NoScriptError:
                # Reload script once on NOSCRIPT error
                self.script_sha = None
                sha = await self._ensure_script_loaded()
                raw_res = await asyncio.wait_for(
                    cast(Any, self.client.evalsha(sha, len(keys), *keys, *argv)),
                    timeout=self.settings.redis_operation_timeout,
                )

            raw_list = cast(list[Any], raw_res) if isinstance(raw_res, list) else []
            res_status = str(raw_list[0]) if raw_list else str(cast(object, raw_res))
            delay_ms = int(raw_list[1]) if len(raw_list) > 1 else 0

            if res_status == "DEFER":
                retry_after_sec = delay_ms / 1000.0
                await self.sleeper(min(retry_after_sec, 30.0))
                # Re-try reservation recursively after deferral sleep
                await self.acquire(target_url, recorder=recorder)
                return

            if res_status == "RESERVED":
                sleep_sec = delay_ms / 1000.0
                if sleep_sec > 0.0:
                    await self.sleeper(sleep_sec)
                if recorder is not None:
                    recorder.gate_wait_duration_seconds += max(0.0, time.monotonic() - start_t)
                return

            raise ValueError(f"Unexpected Redis rate limit Lua return status: {res_status}")

        except Exception as exc:
            logger.warning(
                "Redis domain rate limit operation failed [code=REDIS_GATE_FAILED, error_type=%s]",
                type(exc).__name__,
            )
            if self.fallback_mode == "single_worker_local":
                await self.local_gate.acquire(target_url, recorder=recorder)
            else:
                raise RedisRateLimitPausedError(
                    "Redis connection dropped during rate limit acquisition in strict_pause mode."
                ) from exc
