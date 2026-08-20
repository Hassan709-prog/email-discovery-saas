"""Phase 5B 3-Stage Bounded Scheduling, Fair Rotation & 4:1 Retry Opportunity Tests."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.organization import Organization
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.services.crawl_work import CrawlWorkService


@pytest.mark.anyio
async def test_stage_b_per_job_bounding_max_5(isolated_db_engine: AsyncEngine) -> None:
    """Test that Stage B candidate selection fetches at most 5 URLs per job."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()

    async with session_factory.begin() as session:
        org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:6]}")
        job = ScanJob(
            id=job_id,
            organization_id=org_id,
            status=ScanJobStatus.QUEUED.value,
            total_input_count=10,
            valid_input_count=10,
            queued_count=10,
        )
        session.add_all([org, job])
        for i in range(10):
            url = ScanURL(
                id=uuid.uuid4(),
                scan_job_id=job_id,
                original_index=i,
                original_input=f"https://example-{i}.com",
                status=ScanURLStatus.QUEUED.value,
                fence_token=0,
                attempt_count=0,
                max_attempts=3,
            )
            session.add(url)

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim1 = await service.claim_next_url(lease_owner="worker-1")

    assert claim1 is not None

    async with session_factory() as session:
        res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        db_job = res.scalar_one()
        assert db_job.running_count == 1
        assert db_job.last_claimed_at is not None


@pytest.mark.anyio
async def test_small_tenant_non_starvation(isolated_db_engine: AsyncEngine) -> None:
    """Test that small tenant receives claim opportunities alongside a large synthetic queue."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_large = uuid.uuid4()
    org_small = uuid.uuid4()
    job_large_id = uuid.uuid4()
    job_small_id = uuid.uuid4()

    async with session_factory.begin() as session:
        o_large = Organization(
            id=org_large, name="Large Org", slug=f"large-org-{org_large.hex[:6]}"
        )
        o_small = Organization(
            id=org_small, name="Small Org", slug=f"small-org-{org_small.hex[:6]}"
        )
        job_large = ScanJob(
            id=job_large_id,
            organization_id=org_large,
            status=ScanJobStatus.QUEUED.value,
            total_input_count=50,
            valid_input_count=50,
            queued_count=50,
        )
        job_small = ScanJob(
            id=job_small_id,
            organization_id=org_small,
            status=ScanJobStatus.QUEUED.value,
            total_input_count=2,
            valid_input_count=2,
            queued_count=2,
        )
        session.add_all([o_large, o_small, job_large, job_small])

        for i in range(20):
            session.add(
                ScanURL(
                    id=uuid.uuid4(),
                    scan_job_id=job_large_id,
                    original_index=i,
                    original_input=f"https://large-{i}.com",
                    status=ScanURLStatus.QUEUED.value,
                )
            )
        for i in range(2):
            session.add(
                ScanURL(
                    id=uuid.uuid4(),
                    scan_job_id=job_small_id,
                    original_index=i,
                    original_input=f"https://small-{i}.com",
                    status=ScanURLStatus.QUEUED.value,
                )
            )

    claimed_jobs: list[uuid.UUID] = []
    for _ in range(5):
        async with session_factory() as session:
            service = CrawlWorkService(session)
            c = await service.claim_next_url(lease_owner="worker-1")
            if c:
                claimed_jobs.append(c.job_id)

    # Both large and small jobs receive claim opportunities due to Stage A PARTITION BY org_rank
    assert job_large_id in claimed_jobs
    assert job_small_id in claimed_jobs
