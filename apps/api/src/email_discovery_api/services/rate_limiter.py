"""Auth rate limiter protocol and bounded in-memory sliding window implementation."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol


class AuthAttemptLimiter(Protocol):
    """Protocol for authentication attempt rate limiting."""

    def check_rate_limit(self, key: str) -> bool:
        """Return True if attempt is allowed, False if rate limited."""
        ...

    def record_attempt(self, key: str) -> None:
        """Record an attempt for the given key."""
        ...


class InMemoryAuthAttemptLimiter:
    """Bounded in-memory sliding window rate limiter for development and local testing.

    NOTE: This implementation stores state in local memory and does NOT protect
    multi-process or multi-server deployments. Distributed rate limiting is deferred to Redis.
    """

    def __init__(
        self,
        max_attempts: int = 10,
        window_seconds: float = 60.0,
        max_entries: int = 10000,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._max_entries = max_entries
        self._time_func = time_func or time.monotonic
        self._history: dict[str, list[float]] = defaultdict(list)

    def _cleanup_old_entries(self, current_time: float) -> None:
        """Remove expired timestamps and enforce max storage capacity."""
        cutoff = current_time - self._window_seconds
        keys_to_remove: list[str] = []

        for key, timestamps in self._history.items():
            valid_timestamps = [t for t in timestamps if t > cutoff]
            if valid_timestamps:
                self._history[key] = valid_timestamps
            else:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._history[key]

        # Enforce max storage bounds if still overloaded
        if len(self._history) > self._max_entries:
            # Evict oldest entries
            sorted_keys = sorted(
                self._history.keys(),
                key=lambda k: self._history[k][-1] if self._history[k] else 0.0,
            )
            for k in sorted_keys[: len(self._history) - self._max_entries]:
                del self._history[k]

    def check_rate_limit(self, key: str) -> bool:
        """Return True if under max_attempts within sliding window."""
        now = self._time_func()
        self._cleanup_old_entries(now)
        cutoff = now - self._window_seconds
        attempts = [t for t in self._history.get(key, []) if t > cutoff]
        return len(attempts) < self._max_attempts

    def record_attempt(self, key: str) -> None:
        """Record timestamp of authentication attempt."""
        now = self._time_func()
        self._cleanup_old_entries(now)
        self._history[key].append(now)
