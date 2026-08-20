"""Tests for production CrawlWorker DNS cache and single-flight wiring."""

# pyright: reportPrivateUsage=false

import asyncio
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from email_discovery_api.services.worker_contracts import URLClaim
from email_discovery_crawl_worker.worker import CrawlWorker
from email_scanner.dns import (
    DeadlockFreeSingleFlightGroup,
    DNSCacheConfig,
    SystemDNSResolver,
    WorkerDNSCache,
)
from email_scanner.models import SiteScanResult, SiteScanStatistics


@pytest.mark.anyio
async def test_default_crawl_worker_wires_shared_dns_cache_and_single_flight() -> None:
    """Verify default CrawlWorker wires worker-owned DNS cache and single-flight group."""
    getaddrinfo_calls: list[tuple[str, int]] = []

    def mock_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        getaddrinfo_calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock()
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    worker = CrawlWorker(
        session_factory=mock_session_factory,
        concurrency=2,
        poll_interval_seconds=1.0,
        max_scans=2,
    )

    assert isinstance(worker.dns_cache, WorkerDNSCache)

    claim1 = URLClaim(
        scan_url_id=MagicMock(),
        organization_id=MagicMock(),
        job_id=MagicMock(),
        original_input="http://example.com/page1",
        normalized_url="http://example.com/page1",
        normalized_domain="example.com",
        lease_owner=worker.worker_id,
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=MagicMock(),
    )

    claim2 = URLClaim(
        scan_url_id=MagicMock(),
        organization_id=MagicMock(),
        job_id=MagicMock(),
        original_input="http://example.com/page2",
        normalized_url="http://example.com/page2",
        normalized_domain="example.com",
        lease_owner=worker.worker_id,
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=MagicMock(),
    )

    async def mock_scan(url: str) -> SiteScanResult:
        return SiteScanResult(
            starting_url=url,
            outcome=MagicMock(),
            statistics=SiteScanStatistics(
                pages_queued=0,
                pages_attempted=0,
                pages_fetched=0,
                pages_blocked_by_robots=0,
                pages_failed=0,
                urls_discovered=0,
                accepted_email_findings=0,
                rejected_email_candidates=0,
                elapsed_seconds=0.01,
                stop_reason="COMPLETED",
            ),
            page_records=(),
            email_findings=(),
            rejected_email_candidates=(),
        )

    with (
        patch("socket.getaddrinfo", side_effect=mock_getaddrinfo),
        patch("email_scanner.orchestration.SiteScanOrchestrator.scan", side_effect=mock_scan),
        patch(
            "email_discovery_crawl_worker.worker.ResultPersistenceService"
        ) as mock_persistence_cls,
        patch("email_discovery_crawl_worker.worker.CrawlWorkService") as mock_work_cls,
        patch("email_discovery_crawl_worker.worker.ScanJobService") as mock_job_cls,
    ):
        mock_work = MagicMock()
        mock_work.claim_next_url = AsyncMock(side_effect=[claim1, claim2, None])
        mock_work.recover_expired_leases = AsyncMock(return_value=0)
        mock_work.mark_attempt_started = AsyncMock(return_value=1)
        mock_work_cls.return_value = mock_work

        mock_persist = MagicMock()
        mock_persist.persist_fenced_result = AsyncMock()
        mock_persistence_cls.return_value = mock_persist
        mock_job_cls.return_value.try_finalize_job = AsyncMock()

        # Run claim task 1
        await worker._process_claim_task(claim1)
        # Run claim task 2 (same domain example.com)
        await worker._process_claim_task(claim2)

        # Worker DNS cache and single flight exist on worker
        assert worker.dns_cache is not None
        assert worker.single_flight is not None


@pytest.mark.anyio
async def test_concurrent_dns_misses_coalesce_single_flight() -> None:
    """Verify concurrent resolutions for the same key coalesce into one underlying DNS lookup."""
    call_count = 0

    def mock_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        nonlocal call_count
        call_count += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    mock_session_factory = MagicMock()
    worker = CrawlWorker(session_factory=mock_session_factory, concurrency=5)

    single_flight: DeadlockFreeSingleFlightGroup[tuple[str, int, str], tuple[str, ...]] = (
        worker.single_flight
    )

    resolver1 = SystemDNSResolver(dns_cache=worker.dns_cache, single_flight=single_flight)
    resolver2 = SystemDNSResolver(dns_cache=worker.dns_cache, single_flight=single_flight)

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        res1, res2 = await asyncio.gather(
            resolver1.resolve_host("coalesce-test.org", 80),
            resolver2.resolve_host("coalesce-test.org", 80),
        )

    assert res1 == ("93.184.216.34",)
    assert res2 == ("93.184.216.34",)
    assert call_count == 1


@pytest.mark.anyio
async def test_cache_entries_expire_after_ttl() -> None:
    """Verify cache entries expire and trigger a fresh resolution after TTL."""
    call_count = 0

    def mock_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        nonlocal call_count
        call_count += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"93.184.216.{call_count}", port))]

    cache = WorkerDNSCache(config=DNSCacheConfig(ttl_seconds=0.05, max_capacity=10))
    resolver = SystemDNSResolver(dns_cache=cache)

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        res1 = await resolver.resolve_host("ttl-test.org", 80)
        assert res1 == ("93.184.216.1",)

        # Immediate second call hit cache
        res2 = await resolver.resolve_host("ttl-test.org", 80)
        assert res2 == ("93.184.216.1",)
        assert call_count == 1

        # Sleep past TTL
        await asyncio.sleep(0.08)

        res3 = await resolver.resolve_host("ttl-test.org", 80)
        assert res3 == ("93.184.216.2",)
        assert call_count == 2


@pytest.mark.anyio
async def test_unsafe_or_failed_dns_results_not_reused() -> None:
    """Verify private/unsafe IP addresses and failed resolutions are never cached or reused."""
    from email_scanner.errors import HostSafetyError

    cache = WorkerDNSCache()
    resolver = SystemDNSResolver(dns_cache=cache)

    # Return private IP 127.0.0.1
    def mock_private_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    with (
        patch("socket.getaddrinfo", side_effect=mock_private_getaddrinfo),
        pytest.raises(HostSafetyError),
    ):
        await resolver.resolve_host("private-test.org", 80)

    # Private result must not be cached
    assert cache.capacity == 0

    # Socket failure
    with (
        patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failed")),
        pytest.raises((HostSafetyError, socket.gaierror)),
    ):
        await resolver.resolve_host("failed-test.org", 80)

    assert cache.capacity == 0


@pytest.mark.anyio
async def test_different_ports_remain_separate_cache_keys() -> None:
    """Verify port 80 and port 443 for the same hostname create separate cache entries."""
    cache = WorkerDNSCache()
    resolver = SystemDNSResolver(dns_cache=cache)

    def mock_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        res_80 = await resolver.resolve_host("ports-test.org", 80)
        res_443 = await resolver.resolve_host("ports-test.org", 443)

    assert res_80 == ("93.184.216.34",)
    assert res_443 == ("93.184.216.34",)
    assert cache.capacity == 2


@pytest.mark.anyio
async def test_worker_shutdown_cleans_up_single_flight_and_cache() -> None:
    """Verify worker _drain_tasks shuts down single-flight group and clears DNS cache."""
    mock_session_factory = MagicMock()
    worker = CrawlWorker(session_factory=mock_session_factory)

    # Populate cache
    await worker.dns_cache.put("shutdown-test.org", 80, ("93.184.216.34",))
    assert worker.dns_cache.capacity == 1

    # Drain tasks / shutdown
    await worker._drain_tasks()

    assert worker.dns_cache.capacity == 0
    assert worker.single_flight.inflight_count == 0

    # New work entering single_flight should fail after shutdown
    with pytest.raises(RuntimeError, match="SingleFlightGroup is shutting down"):

        async def dummy_factory() -> tuple[str, ...]:
            return ("93.184.216.34",)

        await worker.single_flight.do(("shutdown-test.org", 80, "default"), dummy_factory)


@pytest.mark.anyio
async def test_custom_orchestrator_factory_compatibility() -> None:
    """Verify CrawlWorker with custom orchestrator_factory continues to work as expected."""
    mock_orchestrator = MagicMock()

    async def mock_scan(url: str) -> SiteScanResult:
        return SiteScanResult(
            starting_url=url,
            outcome=MagicMock(),
            statistics=SiteScanStatistics(
                pages_queued=0,
                pages_attempted=0,
                pages_fetched=0,
                pages_blocked_by_robots=0,
                pages_failed=0,
                urls_discovered=0,
                accepted_email_findings=0,
                rejected_email_candidates=0,
                elapsed_seconds=0.01,
                stop_reason="COMPLETED",
            ),
            page_records=(),
            email_findings=(),
            rejected_email_candidates=(),
        )

    mock_orchestrator.scan = AsyncMock(side_effect=mock_scan)
    mock_orchestrator.close = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock()
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    worker = CrawlWorker(
        session_factory=mock_session_factory,
        orchestrator_factory=lambda: mock_orchestrator,
        max_scans=1,
    )

    claim = URLClaim(
        scan_url_id=MagicMock(),
        organization_id=MagicMock(),
        job_id=MagicMock(),
        original_input="http://custom-factory.org",
        normalized_url="http://custom-factory.org",
        normalized_domain="custom-factory.org",
        lease_owner=worker.worker_id,
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=MagicMock(),
    )

    with (
        patch("email_discovery_crawl_worker.worker.ResultPersistenceService") as mock_persist_cls,
        patch("email_discovery_crawl_worker.worker.CrawlWorkService") as mock_work_cls,
        patch("email_discovery_crawl_worker.worker.ScanJobService") as mock_job_cls,
    ):
        mock_work_cls.return_value.claim_next_url = AsyncMock(return_value=claim)
        mock_work_cls.return_value.recover_expired_leases = AsyncMock(return_value=0)
        mock_work_cls.return_value.mark_attempt_started = AsyncMock(return_value=1)
        mock_persist_cls.return_value.persist_fenced_result = AsyncMock()
        mock_job_cls.return_value.try_finalize_job = AsyncMock()

        await worker._process_claim_task(claim)

        assert mock_orchestrator.scan.called
        assert worker.processed_count == 1
