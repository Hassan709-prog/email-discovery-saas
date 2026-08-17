"""Unit and integration tests for RetryBackoffPolicy and retry scheduling."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models import ScanJob
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.retry_policy import RetryBackoffPolicy
from email_discovery_api.services.worker_contracts import URLClaim


def test_retry_backoff_policy_validation() -> None:
    """Verify bounds and validation for RetryBackoffPolicy."""
    policy = RetryBackoffPolicy(
        base_delay_seconds=5.0, backoff_factor=2.0, max_delay_seconds=3600.0
    )
    assert policy.compute_delay_seconds(1) == 5.0
    assert policy.compute_delay_seconds(2) == 10.0
    assert policy.compute_delay_seconds(3) == 20.0
    assert policy.compute_delay_seconds(10) <= 3600.0

    with pytest.raises(ValueError, match="must be greater than zero"):
        RetryBackoffPolicy(base_delay_seconds=0.0)

    with pytest.raises(ValueError, match="must be at least 1.0"):
        RetryBackoffPolicy(backoff_factor=0.5)

    with pytest.raises(ValueError, match="must be greater than or equal to"):
        RetryBackoffPolicy(base_delay_seconds=100.0, max_delay_seconds=10.0)


@pytest.fixture
async def seeded_job_and_urls(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> dict[str, Any]:
    """Seed database with a QUEUED ScanJob and URL targets."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()
    url_id1 = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=1,
                valid_input_count=1,
                queued_count=1,
                running_count=0,
                completed_count=0,
                failed_count=0,
            )
            url1 = ScanURL(
                id=url_id1,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://retry-test.com",
                normalized_url="https://retry-test.com/",
                normalized_domain="retry-test.com",
                status=ScanURLStatus.QUEUED.value,
            )
            session.add_all([job, url1])

    return {
        "org_id": org_id,
        "job_id": job_id,
        "url_id1": url_id1,
        "session_factory": session_factory,
    }


@pytest.mark.anyio
async def test_transient_failure_scheduling_and_claiming(
    seeded_job_and_urls: dict[str, Any],
) -> None:
    """Verify transient failure sets next_retry_at in future, preventing reclaim until passed."""
    session_factory = seeded_job_and_urls["session_factory"]

    # 1. Claim URL attempt 1
    async with session_factory() as session:
        work_service = CrawlWorkService(session)
        claim = await work_service.claim_next_url(lease_owner="w1", lease_duration_seconds=120.0)
    assert claim is not None

    # 2. Persist transient failure with attempt 1 < max_attempts 3
    async with session_factory() as session:
        persistence = ResultPersistenceService(
            session, retry_policy=RetryBackoffPolicy(base_delay_seconds=300.0)
        )
        res = await persistence.persist_transient_failure(
            claim=claim,
            error_code="TIMEOUT",
            error_message="Connection timed out",
        )
    assert res.is_replay is False

    # 3. URL should be in RETRY_WAIT and not claimable immediately
    async with session_factory() as session:
        work_service = CrawlWorkService(session)
        reclaim_immediate = await work_service.claim_next_url(lease_owner="w2")
    assert reclaim_immediate is None

    # 4. Artificially set next_retry_at to past timestamp
    async with session_factory() as session:
        async with session.begin():
            url_res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
            url_obj = url_res.scalar_one()
            assert url_obj.status == ScanURLStatus.RETRY_WAIT.value
            url_obj.next_retry_at = datetime.now(UTC) - timedelta(seconds=10)

    # 5. URL is now claimable for attempt 2
    async with session_factory() as session:
        work_service = CrawlWorkService(session)
        claim2 = await work_service.claim_next_url(lease_owner="w3")
    assert claim2 is not None
    assert claim2.attempt_count == 2

    # 6. Transient failure for attempt 2 with claim2.max_attempts reached
    claim2_max = URLClaim(
        scan_url_id=claim2.scan_url_id,
        organization_id=claim2.organization_id,
        job_id=claim2.job_id,
        original_input=claim2.original_input,
        normalized_url=claim2.normalized_url,
        normalized_domain=claim2.normalized_domain,
        lease_owner=claim2.lease_owner,
        attempt_count=2,
        max_attempts=2,
        lease_expires_at=claim2.lease_expires_at,
    )
    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        res2 = await persistence.persist_transient_failure(
            claim=claim2_max,
            error_code="TIMEOUT",
            error_message="Connection timed out",
        )
    assert res2.is_replay is False

    # 7. URL should now be in terminal FAILED status
    async with session_factory() as session:
        url_res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        url_obj = url_res.scalar_one()
        assert url_obj.status == ScanURLStatus.FAILED.value
