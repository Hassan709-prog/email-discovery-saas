"""Privacy tests for stable worker lifecycle event logs."""

import logging

import pytest

from email_discovery_crawl_worker.presence import derive_instance_digest


def test_instance_digest_does_not_reveal_worker_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_name = "customer-a-production-worker"
    digest = derive_instance_digest("fixed-instance")
    with caplog.at_level(logging.INFO):
        logging.getLogger("email_discovery_crawl_worker.worker").info(
            "event_code=WORKER_READY instance_digest=%s concurrency=%d state=ACTIVE",
            digest,
            2,
        )
    output = caplog.text
    assert "event_code=WORKER_READY" in output
    assert digest in output
    assert private_name not in output


@pytest.mark.anyio
async def test_unexpected_scan_execution_exception_logs_with_exc_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that unexpected scan execution exceptions log with exc_info=True."""
    import uuid
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from email_discovery_api.services.crawl_work import URLClaim
    from email_discovery_crawl_worker.worker import CrawlWorker

    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    session_factory.return_value.__aexit__.return_value = None

    mock_orch = AsyncMock()
    mock_orch.scan.side_effect = ValueError("simulated unexpected failure")

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="w1",
        orchestrator_factory=lambda: mock_orch,
    )

    sensitive_url = "https://sensitive-secret-token.com/page"
    claim = URLClaim(
        scan_url_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        original_input=sensitive_url,
        normalized_url=sensitive_url,
        normalized_domain="sensitive-secret-token.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC),
    )

    work_service_mock = AsyncMock()
    work_service_mock.mark_attempt_started.return_value = 1
    persistence_mock = AsyncMock()
    job_service_mock = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="email_discovery_crawl_worker.worker"):
        with (
            patch(
                "email_discovery_crawl_worker.worker.CrawlWorkService",
                return_value=work_service_mock,
            ),
            patch(
                "email_discovery_crawl_worker.worker.ResultPersistenceService",
                return_value=persistence_mock,
            ),
            patch(
                "email_discovery_crawl_worker.worker.ScanJobService", return_value=job_service_mock
            ),
        ):
            await worker._process_claim_task(claim)  # pyright: ignore[reportPrivateUsage]

    warn_records = [
        r for r in caplog.records if "Scan execution exception for URL claim" in r.getMessage()
    ]
    assert len(warn_records) == 1
    record = warn_records[0]
    assert record.exc_info is not None
    assert record.exc_info[0] is ValueError
    assert "error_code=SCAN_EXECUTION_ERROR_VALUEERROR" in record.getMessage()
    assert "error_type=ValueError" in record.getMessage()
    assert sensitive_url not in record.getMessage()
