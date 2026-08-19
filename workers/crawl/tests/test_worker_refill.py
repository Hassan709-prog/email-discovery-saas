"""Unit test proving immediate event-driven worker capacity refilling."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from email_discovery_crawl_worker.worker import CrawlWorker

from email_discovery_api.services.worker_contracts import URLClaim
from email_scanner.models import SiteScanResult, SiteScanStatistics


@pytest.mark.anyio
async def test_worker_refills_capacity_immediately_on_task_completion() -> None:
    """Verify worker with concurrency 2 immediately claims next URL when task 1 completes."""
    claimed_urls: list[str] = []

    async def mock_claim_next_url(lease_owner: str, **kwargs: Any) -> URLClaim | None:
        idx = len(claimed_urls)
        if idx >= 4:
            return None
        url_str = f"http://site-{idx}.org"
        claimed_urls.append(url_str)
        return URLClaim(
            scan_url_id=MagicMock(),
            organization_id=MagicMock(),
            job_id=MagicMock(),
            original_input=url_str,
            normalized_url=url_str,
            normalized_domain=f"site-{idx}.org",
            lease_owner=lease_owner,
            attempt_count=1,
            max_attempts=3,
            lease_expires_at=MagicMock(),
        )

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock()
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    async def mock_scan(url: str) -> SiteScanResult:
        # Fast task for item 0, slightly slower for item 1
        if "site-0" in url:
            await asyncio.sleep(0.01)
        else:
            await asyncio.sleep(0.05)

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

    mock_orchestrator = MagicMock()
    mock_orchestrator.scan = AsyncMock(side_effect=mock_scan)
    mock_orchestrator.close = AsyncMock()

    worker = CrawlWorker(
        session_factory=mock_session_factory,
        concurrency=2,
        poll_interval_seconds=10.0,  # Long poll interval to prove immediate refill
        orchestrator_factory=lambda: mock_orchestrator,
        max_scans=4,
    )

    with (
        patch("email_discovery_crawl_worker.worker.CrawlWorkService") as mock_work_cls,
        patch("email_discovery_crawl_worker.worker.ResultPersistenceService"),
        patch("email_discovery_crawl_worker.worker.ScanJobService"),
    ):
        mock_service_inst = MagicMock()
        mock_service_inst.claim_next_url = AsyncMock(side_effect=mock_claim_next_url)
        mock_service_inst.recover_expired_leases = AsyncMock(return_value=0)
        mock_work_cls.return_value = mock_service_inst

        # Run worker.start() as a task and stop after max_scans
        worker_task = asyncio.create_task(worker.start())

        # Wait for worker to finish max_scans
        await asyncio.wait_for(worker_task, timeout=2.0)

        assert worker.claimed_count == 4
        assert len(claimed_urls) == 4
