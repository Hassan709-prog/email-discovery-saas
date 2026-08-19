"""Unit test proving immediate concurrency filling without polling delays."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from email_discovery_api.services.worker_contracts import URLClaim
from email_discovery_crawl_worker.worker import CrawlWorker
from email_scanner.models import SiteScanResult, SiteScanStatistics


@pytest.mark.anyio
async def test_worker_fills_concurrency_capacity_immediately() -> None:
    """Verify worker with concurrency 5 claims 5 URLs in single polling cycle."""
    claims_to_return = [
        URLClaim(
            scan_url_id=MagicMock(),
            organization_id=MagicMock(),
            job_id=MagicMock(),
            original_input=f"https://example.com/{i}",
            normalized_url=f"https://example.com/{i}",
            normalized_domain="example.com",
            lease_owner="worker-test",
            attempt_count=1,
            max_attempts=3,
            lease_expires_at=MagicMock(),
        )
        for i in range(5)
    ]
    claims_iter = iter(claims_to_return)

    async def mock_claim_next_url(**kwargs: Any) -> URLClaim | None:
        return next(claims_iter, None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock()
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    dummy_result = SiteScanResult(
        starting_url="https://example.com",
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
            elapsed_seconds=0.1,
            stop_reason="COMPLETED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    mock_orchestrator = MagicMock()
    mock_orchestrator.scan = AsyncMock(return_value=dummy_result)
    mock_orchestrator.close = AsyncMock()

    worker = CrawlWorker(
        session_factory=mock_session_factory,
        concurrency=5,
        poll_interval_seconds=10.0,  # Long poll interval
        orchestrator_factory=lambda: mock_orchestrator,
    )

    # Patch CrawlWorkService in worker's module
    from unittest.mock import patch

    with (
        patch("email_discovery_crawl_worker.worker.CrawlWorkService") as mock_work_cls,
        patch("email_discovery_crawl_worker.worker.ResultPersistenceService"),
        patch("email_discovery_crawl_worker.worker.ScanJobService"),
    ):
        mock_service_inst = MagicMock()
        mock_service_inst.claim_next_url = AsyncMock(side_effect=mock_claim_next_url)
        mock_work_cls.return_value = mock_service_inst

        worker._running = True  # pyright: ignore[reportPrivateUsage]
        claimed_any = await worker._fill_capacity_and_claim()  # pyright: ignore[reportPrivateUsage]
        assert claimed_any is True
        assert worker.claimed_count == 5

        # Drain spawned tasks
        await worker._drain_tasks()  # pyright: ignore[reportPrivateUsage]
        assert worker.processed_count == 5
