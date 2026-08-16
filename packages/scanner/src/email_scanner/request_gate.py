"""Shared in-process domain request gate for rate-limiting and politeness."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from email_scanner.models import NormalizedURL


def get_domain_key(url: NormalizedURL) -> str:
    """Extract domain rate-limiting key (registrable domain or exact IP hostname)."""
    if url.registrable_domain is not None:
        return url.registrable_domain.lower()
    return url.hostname.lower()


class RequestGateProtocol(Protocol):
    """Protocol for shared request rate-limiting gates."""

    async def acquire(self, target_url: NormalizedURL) -> None:
        """Acquire permission to send an HTTP request to the target URL."""
        ...

    def update_domain_interval(
        self,
        target_url: NormalizedURL,
        crawl_delay: float | None,
    ) -> None:
        """Update domain interval to safer/larger value when robots crawl-delay is discovered."""
        ...


class DomainRequestGate:
    """Shared in-process per-domain request rate-limiting gate.

    Atomically reserves future request slots under per-domain locks and sleeps
    outside the locks to prevent request bursts and simultaneous callers.
    """

    def __init__(
        self,
        default_minimum_interval_seconds: float = 1.0,
        clock: Callable[[], float] | None = None,
        async_sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._default_min_interval = default_minimum_interval_seconds
        self._clock = clock or time.monotonic
        self._sleeper = async_sleeper

        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._next_allowed_at: dict[str, float] = {}
        self._current_interval: dict[str, float] = {}
        self._scheduled_times: dict[str, list[float]] = {}  # Audit log for testing

    def set_sleeper(self, sleeper: Callable[[float], Awaitable[None]] | None) -> None:
        """Inject or update async sleeper for domain gate."""
        self._sleeper = sleeper

    def set_clock(self, clock: Callable[[], float] | None) -> None:
        """Inject or update clock function for domain gate."""
        if clock is not None:
            self._clock = clock

    def _get_domain_lock(self, domain_key: str) -> asyncio.Lock:
        if domain_key not in self._domain_locks:
            self._domain_locks[domain_key] = asyncio.Lock()
        return self._domain_locks[domain_key]

    def update_domain_interval(
        self,
        target_url: NormalizedURL,
        crawl_delay: float | None,
    ) -> None:
        """Update domain request interval to max(current, crawl_delay, default)."""
        if crawl_delay is None or math.isnan(crawl_delay) or math.isinf(crawl_delay):
            return

        domain_key = get_domain_key(target_url)
        safer_interval = max(
            self._current_interval.get(domain_key, self._default_min_interval),
            crawl_delay,
            self._default_min_interval,
        )
        self._current_interval[domain_key] = safer_interval

    def get_scheduled_times(self, domain_key: str) -> list[float]:
        """Return scheduled request timestamps for audit/testing."""
        return list(self._scheduled_times.get(domain_key, []))

    async def acquire(self, target_url: NormalizedURL) -> None:
        """Atomically reserve a request slot and sleep until the scheduled time."""
        domain_key = get_domain_key(target_url)
        lock = self._get_domain_lock(domain_key)

        async with lock:
            now = self._clock()
            scheduled_at = max(now, self._next_allowed_at.get(domain_key, now))
            interval = self._current_interval.get(domain_key, self._default_min_interval)

            self._next_allowed_at[domain_key] = scheduled_at + interval

            if domain_key not in self._scheduled_times:
                self._scheduled_times[domain_key] = []
            self._scheduled_times[domain_key].append(scheduled_at)

        sleep_sec = scheduled_at - now
        if sleep_sec > 0.0 and self._sleeper is not None:
            await self._sleeper(sleep_sec)
