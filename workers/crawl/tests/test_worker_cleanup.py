"""Unit tests for scanner HTTP resource cleanup in CrawlWorker."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from email_discovery_crawl_worker.worker import CrawlWorker

from email_discovery_api.services.worker_contracts import URLClaim


@pytest.mark.anyio
async def test_worker_owned_orchestrator_closed_in_finally() -> None:
    """Verify worker-owned orchestrator.close() is called in finally block."""
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock()
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    claim = URLClaim(
        scan_url_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        original_input="https://example.com",
        normalized_url="https://example.com",
        normalized_domain="example.com",
        lease_owner="w1",
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC),
    )

    worker = CrawlWorker(
        session_factory=mock_session_factory,
        concurrency=1,
    )

    mock_orchestrator = MagicMock()
    mock_orchestrator.scan = AsyncMock(side_effect=RuntimeError("Network error"))
    mock_orchestrator.close = AsyncMock()

    with (
        patch(
            "email_discovery_crawl_worker.worker.SiteScanOrchestrator",
            return_value=mock_orchestrator,
        ),
        patch("email_discovery_crawl_worker.worker.ResultPersistenceService"),
        patch("email_discovery_crawl_worker.worker.ScanJobService"),
    ):
        await worker._process_claim_task(claim)  # pyright: ignore[reportPrivateUsage]
        mock_orchestrator.close.assert_called_once()
