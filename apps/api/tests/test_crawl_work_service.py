"""Tests for CrawlWorkService transaction primitives targeting isolated PostgreSQL test DB."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import select

from email_discovery_api.models import ScanJob, ScanURL
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.worker_contracts import HeartbeatStatus

pytestmark = pytest.mark.anyio


@pytest.fixture
async def seeded_job_and_urls(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> dict[str, Any]:
    """Seed isolated database with a ScanJob and ScanURLs for worker testing."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()
    url_id1 = uuid.uuid4()
    url_id2 = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.QUEUED.value,
                total_input_count=2,
                valid_input_count=2,
                queued_count=2,
                running_count=0,
                completed_count=0,
                failed_count=0,
            )
            url1 = ScanURL(
                id=url_id1,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://example.com",
                normalized_url="https://example.com/",
                normalized_domain="example.com",
                status=ScanURLStatus.QUEUED.value,
            )
            url2 = ScanURL(
                id=url_id2,
                scan_job_id=job_id,
                original_index=1,
                original_input="https://test.org",
                normalized_url="https://test.org/",
                normalized_domain="test.org",
                status=ScanURLStatus.QUEUED.value,
            )
            session.add_all([job, url1, url2])

    return {
        "org_id": org_id,
        "job_id": job_id,
        "url_id1": url_id1,
        "url_id2": url_id2,
        "session_factory": session_factory,
    }


async def test_claim_next_url_updates_status_and_counters(
    seeded_job_and_urls: dict[str, Any],
) -> None:
    """Verify claim_next_url claims a QUEUED URL, sets lease, and updates counters."""
    session_factory = seeded_job_and_urls["session_factory"]
    job_id = seeded_job_and_urls["job_id"]

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim = await service.claim_next_url(lease_owner="worker-1", lease_duration_seconds=120.0)

    assert claim is not None
    assert claim.job_id == job_id
    assert claim.lease_owner == "worker-1"
    assert claim.attempt_count == 1
    assert claim.lease_expires_at is not None

    async with session_factory() as session:
        url_res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        url = url_res.scalar_one()
        assert url.status == ScanURLStatus.SCANNING.value
        assert url.lease_owner == "worker-1"

        job_res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        job = job_res.scalar_one()
        assert job.status == ScanJobStatus.RUNNING.value
        assert job.queued_count == 1
        assert job.running_count == 1


async def test_renew_lease_fencing_and_expiry_check(
    seeded_job_and_urls: dict[str, Any],
) -> None:
    """Verify renew_lease extends lease for active worker but fails on mismatch."""
    session_factory = seeded_job_and_urls["session_factory"]

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim = await service.claim_next_url(lease_owner="worker-1", lease_duration_seconds=120.0)
    assert claim is not None

    # 1. Valid renewal succeeds
    async with session_factory() as session:
        service = CrawlWorkService(session)
        res = await service.renew_lease(
            scan_url_id=claim.scan_url_id,
            lease_owner="worker-1",
            attempt_count=claim.attempt_count,
            lease_duration_seconds=60.0,
        )
    assert res.status == HeartbeatStatus.RENEWED
    assert res.lease_expires_at is not None

    # 2. Mismatched owner fails with LEASE_LOST
    async with session_factory() as session:
        service = CrawlWorkService(session)
        res_lost = await service.renew_lease(
            scan_url_id=claim.scan_url_id,
            lease_owner="worker-wrong",
            attempt_count=claim.attempt_count,
        )
    assert res_lost.status == HeartbeatStatus.LEASE_LOST


async def test_recover_expired_leases_reclaims_expired_urls(
    seeded_job_and_urls: dict[str, Any],
) -> None:
    """Verify recover_expired_leases transitions expired SCANNING URLs to RETRY_WAIT."""
    session_factory = seeded_job_and_urls["session_factory"]

    async with session_factory() as session:
        service = CrawlWorkService(session)
        # Claim with very short lease duration
        claim = await service.claim_next_url(
            lease_owner="worker-temp", lease_duration_seconds=-10.0
        )
    assert claim is not None

    async with session_factory() as session:
        service = CrawlWorkService(session)
        recovered = await service.recover_expired_leases()

    assert recovered == 1

    async with session_factory() as session:
        url_res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        url = url_res.scalar_one()
        assert url.status == ScanURLStatus.RETRY_WAIT.value
        assert url.lease_owner is None
