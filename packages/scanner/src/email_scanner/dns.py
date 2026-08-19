"""Async DNS resolver abstraction for scanner-core.

Pre-resolving DNS addresses allows checking against host safety policies
(e.g., blocking private IP ranges and local hostnames) prior to making HTTP requests.

Note on Security Boundaries (DNS-Rebinding / TOCTOU):
Pre-resolving hostnames alone does not fully eliminate DNS-rebinding or
Time-of-Check to Time-of-Use (TOCTOU) risks because standard HTTP transports
may re-resolve hostnames when establishing sockets. This resolver interface
and returned IP list are designed so that future transports can consume the
pre-resolved addresses directly for socket connection pinning.
"""

import asyncio
import socket
import time
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Generic, Protocol, TypeVar

from email_scanner.errors import HostSafetyError, HostSafetyErrorCode
from email_scanner.host_safety import validate_public_host
from email_scanner.models import HostType, NormalizedURL

K = TypeVar("K")
V = TypeVar("V")


class DeadlockFreeSingleFlightGroup(Generic[K, V]):  # noqa: UP046
    """Race-safe, deadlock-free single-flight execution manager."""

    def __init__(self) -> None:
        self._inflight: dict[K, asyncio.Task[V]] = {}
        self._lock = asyncio.Lock()
        self._is_shutting_down = False

    @property
    def inflight_count(self) -> int:
        """Return total active in-flight task count."""
        return len(self._inflight)

    async def do(self, key: K, worker_factory: Callable[[], Coroutine[Any, Any, V]]) -> V:
        """Execute or join an in-flight operation for key without lock deadlocks."""
        async with self._lock:
            if self._is_shutting_down:
                raise RuntimeError("SingleFlightGroup is shutting down")
            if key in self._inflight:
                task: asyncio.Task[V] = self._inflight[key]
            else:
                task = asyncio.create_task(worker_factory())
                self._inflight[key] = task

                def _on_task_done(completed_task: asyncio.Task[V]) -> None:
                    if not completed_task.cancelled():
                        _ = completed_task.exception()
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._cleanup_entry(key, completed_task))
                    except RuntimeError:
                        pass

                task.add_done_callback(_on_task_done)

        try:
            return await asyncio.shield(task)
        except BaseException:
            if task.done() and not task.cancelled() and task.exception() is not None:
                _ = task.exception()
            raise

    async def _cleanup_entry(self, key: K, completed_task: asyncio.Task[V]) -> None:
        async with self._lock:
            if self._inflight.get(key) is completed_task:
                self._inflight.pop(key, None)

    async def shutdown(self) -> None:
        """Cancel and drain all in-flight tasks during worker shutdown."""
        async with self._lock:
            self._is_shutting_down = True
            tasks = list(self._inflight.values())
            self._inflight.clear()

        for task in tasks:
            if not task.done():
                task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def sort_ips_numerically(ips: tuple[str, ...]) -> tuple[str, ...]:
    """Sort IP address strings deterministically by (ip_version, numeric_value)."""
    return tuple(
        sorted(
            ips,
            key=lambda ip: (ip_address(ip).version, int(ip_address(ip))),
        )
    )


@dataclass(frozen=True, slots=True)
class DNSCacheConfig:
    """Validated configuration settings for worker DNS cache."""

    ttl_seconds: float = 60.0
    max_capacity: int = 1000

    def __post_init__(self) -> None:
        if not (0.001 <= self.ttl_seconds <= 3600.0):
            raise ValueError("ttl_seconds must be between 0.001 and 3600.0 seconds")
        if not (1 <= self.max_capacity <= 100000):
            raise ValueError("max_capacity must be between 1 and 100000 entries")


@dataclass(frozen=True, slots=True)
class _CachedDNSResult:
    validated_public_ips: tuple[str, ...]
    resolved_at: float
    expires_at: float


class WorkerDNSCache:
    """Worker-scoped bounded DNS TTL cache with LRU eviction and public host re-validation."""

    def __init__(
        self,
        config: DNSCacheConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or DNSCacheConfig()
        self._clock = clock or time.monotonic
        self._cache: OrderedDict[tuple[str, int, str], _CachedDNSResult] = OrderedDict()
        self._lock = asyncio.Lock()
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    @property
    def cache_misses(self) -> int:
        return self._cache_misses

    @property
    def capacity(self) -> int:
        return len(self._cache)

    async def get(
        self,
        url: NormalizedURL,
        hostname: str,
        port: int,
        resolver_mode: str = "default",
    ) -> tuple[str, ...] | None:
        """Lookup cached IPs and re-run public host validation on hit."""
        key = (hostname.lower(), port, resolver_mode)
        now = self._clock()

        async with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                self._cache_misses += 1
                return None

            if now >= cached.expires_at:
                self._cache.pop(key, None)
                self._cache_misses += 1
                return None

            # Move to end (LRU)
            self._cache.move_to_end(key)

        # Re-run public host validation on hit outside cache lock
        try:
            validated = validate_public_host(url, cached.validated_public_ips)
            async with self._lock:
                self._cache_hits += 1
            return validated
        except HostSafetyError:
            async with self._lock:
                self._cache.pop(key, None)
                self._cache_misses += 1
            return None

    async def put(
        self,
        hostname: str,
        port: int,
        validated_ips: tuple[str, ...],
        resolver_mode: str = "default",
    ) -> None:
        """Store validated public IPs deterministically with TTL and LRU eviction."""
        if not validated_ips:
            return

        key = (hostname.lower(), port, resolver_mode)
        now = self._clock()
        expires_at = now + self.config.ttl_seconds
        sorted_ips = sort_ips_numerically(validated_ips)

        entry = _CachedDNSResult(
            validated_public_ips=sorted_ips,
            resolved_at=now,
            expires_at=expires_at,
        )

        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = entry

            while len(self._cache) > self.config.max_capacity:
                self._cache.popitem(last=False)

    async def clear(self) -> None:
        """Clear all cached entries cleanly."""
        async with self._lock:
            self._cache.clear()


class AsyncDNSResolver(Protocol):
    """Protocol for asynchronous DNS resolution and host safety checking."""

    async def resolve(
        self,
        url: NormalizedURL,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[str, ...]:
        """Resolve host for a NormalizedURL and return safe IP addresses."""
        ...


class SystemDNSResolver:
    """Production DNS resolver using socket.getaddrinfo via asyncio.to_thread."""

    def __init__(
        self,
        dns_cache: WorkerDNSCache | None = None,
        single_flight: DeadlockFreeSingleFlightGroup[tuple[str, int, str], tuple[str, ...]]
        | None = None,
    ) -> None:
        self.dns_cache = dns_cache
        self.single_flight = single_flight or DeadlockFreeSingleFlightGroup()
        self._underlying_getaddrinfo_calls = 0

    @property
    def underlying_getaddrinfo_calls(self) -> int:
        return self._underlying_getaddrinfo_calls

    async def resolve(
        self,
        url: NormalizedURL,
        recorder: Any | None = None,
        clock: Any | None = None,
    ) -> tuple[str, ...]:
        if url.host_type in {HostType.IPV4, HostType.IPV6}:
            return validate_public_host(url, ())

        port = url.port or (443 if url.scheme == "https" else 80)
        return await self.resolve_host(
            url.hostname, port, target_url=url, recorder=recorder, clock=clock
        )

    async def resolve_host(
        self,
        hostname: str,
        port: int = 80,
        target_url: NormalizedURL | None = None,
        recorder: Any | None = None,
        clock: Any | None = None,
    ) -> tuple[str, ...]:
        cleaned_host = hostname.strip().strip("[]")
        if not cleaned_host:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message="Hostname string is empty",
            )

        # Check if hostname is an IP literal directly
        try:
            ip_obj = ip_address(cleaned_host)
            fake_url = NormalizedURL(
                original_url=f"http://{cleaned_host}",
                normalized_url=f"http://{cleaned_host}",
                scheme="https" if port == 443 else "http",
                hostname=cleaned_host,
                port=port if port not in (80, 443) else None,
                path="/",
                query="",
                host_type=HostType.IPV6 if ip_obj.version == 6 else HostType.IPV4,
                registrable_domain=None,
            )
            return validate_public_host(fake_url, ())
        except ValueError:
            pass

        norm_url = target_url or NormalizedURL(
            original_url=f"http://{cleaned_host}",
            normalized_url=f"http://{cleaned_host}",
            scheme="https" if port == 443 else "http",
            hostname=cleaned_host,
            port=port if port not in (80, 443) else None,
            path="/",
            query="",
            host_type=HostType.DOMAIN,
            registrable_domain=None,
        )

        # 1. Check WorkerDNSCache hit if configured
        if self.dns_cache is not None:
            cached = await self.dns_cache.get(norm_url, cleaned_host, port)
            if cached is not None:
                return cached

        # 2. Use single-flight manager for underlying getaddrinfo calls
        flight_key = (cleaned_host.lower(), port, "default")

        async def _perform_underlying_resolution() -> tuple[str, ...]:
            get_time = clock or time.monotonic
            start_t = get_time()
            try:
                results = await asyncio.to_thread(
                    socket.getaddrinfo,
                    cleaned_host,
                    port,
                    type=socket.SOCK_STREAM,
                )
                self._underlying_getaddrinfo_calls += 1
            except socket.gaierror as err:
                if recorder is not None:
                    recorder.dns_resolution_duration_seconds += max(0.0, get_time() - start_t)
                raise HostSafetyError(
                    code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                    message=f"DNS resolution failed for {cleaned_host}: {err}",
                ) from err

            if recorder is not None:
                recorder.dns_resolution_duration_seconds += max(0.0, get_time() - start_t)

            addresses = tuple(str(res[4][0]) for res in results if res[4])
            if not addresses:
                raise HostSafetyError(
                    code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                    message=f"No IP addresses resolved for {cleaned_host}",
                )

            validated = validate_public_host(norm_url, addresses)
            sorted_validated = sort_ips_numerically(validated)

            if self.dns_cache is not None:
                await self.dns_cache.put(cleaned_host, port, sorted_validated)

            return sorted_validated

        return await self.single_flight.do(flight_key, _perform_underlying_resolution)
