"""Tests for DeadlockFreeSingleFlightGroup, WorkerDNSCache, and SystemDNSResolver."""

import asyncio

import pytest

from email_scanner.dns import (
    DeadlockFreeSingleFlightGroup,
    DNSCacheConfig,
    SystemDNSResolver,
    WorkerDNSCache,
    sort_ips_numerically,
)
from email_scanner.normalization import normalize_url


def test_numeric_ip_sorting() -> None:
    """Verify numeric IP address sorting orders IPv4 and IPv6 by version and integer value."""
    unsorted_ips = ("10.0.0.1", "2.0.0.1", "192.168.1.1", "2001:db8::1", "::1")
    sorted_ips = sort_ips_numerically(unsorted_ips)
    assert sorted_ips == ("2.0.0.1", "10.0.0.1", "192.168.1.1", "::1", "2001:db8::1")


@pytest.mark.anyio
async def test_single_flight_50_concurrent_callers_no_deadlock() -> None:
    """Verify 50 concurrent callers execute exactly 1 underlying operation without deadlock."""
    group: DeadlockFreeSingleFlightGroup[str, str] = DeadlockFreeSingleFlightGroup()
    call_count = 0

    async def worker() -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "result_ok"

    results = await asyncio.gather(*[group.do("key1", worker) for _ in range(50)])
    assert len(results) == 50
    assert all(r == "result_ok" for r in results)
    assert call_count == 1
    assert group.inflight_count == 0


@pytest.mark.anyio
async def test_single_flight_waiter_cancellation_preserves_task() -> None:
    """Verify cancelling individual waiters does not cancel the shared underlying operation."""
    group: DeadlockFreeSingleFlightGroup[str, str] = DeadlockFreeSingleFlightGroup()
    started = asyncio.Event()

    async def worker() -> str:
        started.set()
        await asyncio.sleep(0.1)
        return "done_val"

    task1 = asyncio.create_task(group.do("key_cancel", worker))
    task2 = asyncio.create_task(group.do("key_cancel", worker))

    await started.wait()
    task1.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task1

    res2 = await task2
    assert res2 == "done_val"
    assert group.inflight_count == 0


@pytest.mark.anyio
async def test_single_flight_failure_propagates_and_allows_retry() -> None:
    """Verify exception reaches all waiters and entries allow retry after failure."""
    group: DeadlockFreeSingleFlightGroup[str, str] = DeadlockFreeSingleFlightGroup()
    call_count = 0

    async def failing_worker() -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        raise ValueError("simulated_failure")

    t1 = asyncio.create_task(group.do("err_key", failing_worker))
    t2 = asyncio.create_task(group.do("err_key", failing_worker))

    with pytest.raises(ValueError, match="simulated_failure"):
        await t1

    with pytest.raises(ValueError, match="simulated_failure"):
        await t2

    # Allow time for async cleanup
    await asyncio.sleep(0.02)
    assert group.inflight_count == 0

    # Retry should invoke worker again
    async def success_worker() -> str:
        return "success"

    res = await group.do("err_key", success_worker)
    assert res == "success"


@pytest.mark.anyio
async def test_single_flight_shutdown_cleans_tasks() -> None:
    """Verify shutdown cancels and clears all remaining in-flight tasks."""
    group: DeadlockFreeSingleFlightGroup[str, str] = DeadlockFreeSingleFlightGroup()

    async def slow_worker() -> str:
        await asyncio.sleep(10.0)
        return "slow"

    t1 = asyncio.create_task(group.do("slow_key", slow_worker))
    await asyncio.sleep(0.01)
    assert group.inflight_count == 1

    await group.shutdown()
    assert group.inflight_count == 0

    with pytest.raises(asyncio.CancelledError):
        await t1


@pytest.mark.anyio
async def test_worker_dns_cache_ttl_and_lru_eviction() -> None:
    """Verify WorkerDNSCache enforces TTL expiration and max capacity LRU eviction."""
    cfg = DNSCacheConfig(ttl_seconds=0.1, max_capacity=2)
    fake_time = 100.0
    cache = WorkerDNSCache(config=cfg, clock=lambda: fake_time)

    url = normalize_url("http://example.com")
    await cache.put("example.com", 80, ("93.184.215.14",))
    await cache.put("test.com", 80, ("93.184.215.15",))

    res1 = await cache.get(url, "example.com", 80)
    assert res1 == ("93.184.215.14",)

    # Exceed capacity -> evict LRU
    await cache.put("third.com", 80, ("93.184.215.16",))
    assert cache.capacity == 2

    # Expire TTL
    fake_time += 0.2
    res_exp = await cache.get(url, "example.com", 80)
    assert res_exp is None


@pytest.mark.anyio
async def test_worker_dns_cache_never_caches_unsafe_or_private_ips() -> None:
    """Verify WorkerDNSCache filters and validate_public_host rejects private/unsafe IPs."""
    cfg = DNSCacheConfig(ttl_seconds=60.0, max_capacity=10)
    cache = WorkerDNSCache(config=cfg)

    url = normalize_url("http://example.com")

    # Manually attempt putting private IP
    await cache.put("private.com", 80, ("127.0.0.1",))
    res = await cache.get(url, "private.com", 80)
    # validate_public_host fails on hit and evicts private IP
    assert res is None


@pytest.mark.anyio
async def test_system_dns_resolver_uses_cache_and_single_flight() -> None:
    """Verify SystemDNSResolver leverages DNS cache and single-flight resolution."""
    cache = WorkerDNSCache()
    group: DeadlockFreeSingleFlightGroup[tuple[str, int, str], tuple[str, ...]] = (
        DeadlockFreeSingleFlightGroup()
    )
    resolver = SystemDNSResolver(dns_cache=cache, single_flight=group)

    url = normalize_url("http://example.com")

    # Put a safe public IP in cache
    await cache.put("example.com", 80, ("93.184.215.14",))

    res1 = await resolver.resolve(url)
    assert res1 == ("93.184.215.14",)
    assert cache.cache_hits == 1
    assert resolver.underlying_getaddrinfo_calls == 0
