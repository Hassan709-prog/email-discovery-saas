"""Tests proving expired leases cannot renew or persist results."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import select

from email_discovery_api.models import ScanJob, ScanURL
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.worker_contracts import HeartbeatStatus, LeaseLostError, URLClaim
from email_scanner.errors import SiteScanOutcome
from email_scanner.models import SiteScanResult, SiteScanStatistics

pytestmark = pytest.mark.anyio


@pytest.fixture
async def expired_lease_url(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> dict[str, Any]:
    """Seed database with a SCANNING URL whose lease expired in the past."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=1,
                valid_input_count=1,
                queued_count=0,
                running_count=1,
                completed_count=0,
                failed_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://expired-lease.com",
                normalized_url="https://expired-lease.com/",
                normalized_domain="expired-lease.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="worker-expired",
                attempt_count=1,
            )
            session.add_all([job, url])
            # Set lease_expires_at to 60 seconds in the past
            await session.execute(
                text(
                    "UPDATE scan_urls SET lease_expires_at = clock_timestamp() - interval "
                    "'60 seconds' WHERE id = :id"
                ),
                {"id": url_id},
            )

    claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://expired-lease.com",
        normalized_url="https://expired-lease.com/",
        normalized_domain="expired-lease.com",
        lease_owner="worker-expired",
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=None,  # type: ignore
    )

    return {
        "org_id": org_id,
        "job_id": job_id,
        "url_id": url_id,
        "claim": claim,
        "session_factory": session_factory,
    }


async def test_expired_lease_cannot_renew(expired_lease_url: dict[str, Any]) -> None:
    """Verify an expired but not-yet-recovered lease returns LEASE_LOST on renewal."""
    session_factory = expired_lease_url["session_factory"]
    claim: URLClaim = expired_lease_url["claim"]

    async with session_factory() as session:
        service = CrawlWorkService(session)
        res = await service.renew_lease(
            scan_url_id=claim.scan_url_id,
            lease_owner=claim.lease_owner,
            attempt_count=claim.attempt_count,
        )

    assert res.status == HeartbeatStatus.LEASE_LOST


async def test_expired_lease_cannot_persist_results_and_makes_zero_changes(
    expired_lease_url: dict[str, Any],
) -> None:
    """Verify an expired lease raises LeaseLostError and modifies 0 DB rows/counters."""
    session_factory = expired_lease_url["session_factory"]
    claim: URLClaim = expired_lease_url["claim"]
    job_id = expired_lease_url["job_id"]

    mock_result = SiteScanResult(
        starting_url="https://expired-lease.com/",
        outcome=SiteScanOutcome.COMPLETED_NO_EMAILS,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=0.5,
            stop_reason="QUEUE_EXHAUSTED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        with pytest.raises(LeaseLostError):
            await persistence.persist_fenced_result(claim, mock_result)

    # Verify zero DB changes were committed
    async with session_factory() as session:
        url_res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        url = url_res.scalar_one()
        assert url.status == ScanURLStatus.SCANNING.value  # Status unmodified!

        job_res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        job = job_res.scalar_one()
        assert job.completed_count == 0
        assert job.running_count == 1
