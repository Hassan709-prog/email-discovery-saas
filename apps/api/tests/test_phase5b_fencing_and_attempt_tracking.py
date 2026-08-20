"""Phase 5B Fencing, Per-Fence Attempt Tracking & Claim-Origin Persistence Tests."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.organization import Organization
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.worker_contracts import LeaseLostError


@pytest.mark.anyio
async def test_dedicated_fence_token_atomicity_and_claim_origin(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Test claim_next_url() increments fence_token and persists claim origin cleanly."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory.begin() as session:
        org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:6]}")
        job = ScanJob(
            id=job_id,
            organization_id=org_id,
            status=ScanJobStatus.QUEUED.value,
            total_input_count=1,
            valid_input_count=1,
            queued_count=1,
        )
        url = ScanURL(
            id=url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.QUEUED.value,
            fence_token=0,
            attempt_count=0,
            max_attempts=3,
        )
        session.add_all([org, job, url])

    instance_id = "test-instance-128bit-uuid4"

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim = await service.claim_next_url(lease_owner=instance_id, lease_duration_seconds=120.0)

    assert claim is not None
    assert claim.scan_url_id == url_id
    assert claim.fence_token == 1
    assert claim.attempt_count == 0  # 0 attempts consumed on claim!
    assert claim.claimed_from_status == ScanURLStatus.QUEUED.value

    # Verify DB state
    async with session_factory() as session:
        res = await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        db_url = res.scalar_one()
        assert db_url.status == ScanURLStatus.SCANNING.value
        assert db_url.lease_owner == instance_id
        assert db_url.fence_token == 1
        assert db_url.attempt_count == 0
        assert db_url.claimed_from_status == ScanURLStatus.QUEUED.value
        assert db_url.attempt_started_at is None
        assert db_url.attempt_started_fence_token is None


@pytest.mark.anyio
async def test_mark_attempt_started_idempotency_for_same_fence(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Test mark_attempt_started() increments attempt_count once and is idempotent."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory.begin() as session:
        org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:6]}")
        job = ScanJob(
            id=job_id,
            organization_id=org_id,
            status=ScanJobStatus.QUEUED.value,
            total_input_count=1,
            valid_input_count=1,
            queued_count=1,
        )
        url = ScanURL(
            id=url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.QUEUED.value,
            fence_token=0,
            attempt_count=0,
            max_attempts=3,
        )
        session.add_all([org, job, url])

    instance_id = "test-instance-128bit-uuid4"

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim = await service.claim_next_url(lease_owner=instance_id)
    assert claim is not None

    # First mark attempt started
    async with session_factory() as session:
        service = CrawlWorkService(session)
        att1 = await service.mark_attempt_started(
            scan_url_id=claim.scan_url_id,
            lease_owner=instance_id,
            fence_token=claim.fence_token,
        )
    assert att1 == 1

    # Idempotent second call
    async with session_factory() as session:
        service = CrawlWorkService(session)
        att2 = await service.mark_attempt_started(
            scan_url_id=claim.scan_url_id,
            lease_owner=instance_id,
            fence_token=claim.fence_token,
        )
    assert att2 == 1

    # Verify DB state
    async with session_factory() as session:
        res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        db_url = res.scalar_one()
        assert db_url.attempt_count == 1
        assert db_url.attempt_started_fence_token == claim.fence_token
        assert db_url.attempt_started_at is not None


@pytest.mark.anyio
async def test_pre_attempt_release_restores_claimed_from_status_unconsumed(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Test release_fenced_claim() before mark_attempt_started() restores status."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory.begin() as session:
        org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:6]}")
        job = ScanJob(
            id=job_id,
            organization_id=org_id,
            status=ScanJobStatus.QUEUED.value,
            total_input_count=1,
            valid_input_count=1,
            queued_count=1,
        )
        url = ScanURL(
            id=url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.QUEUED.value,
            fence_token=0,
            attempt_count=0,
            max_attempts=3,
        )
        session.add_all([org, job, url])

    instance_id = "test-instance-128bit-uuid4"

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim = await service.claim_next_url(lease_owner=instance_id)
    assert claim is not None

    # Release BEFORE mark_attempt_started()
    async with session_factory() as session:
        service = CrawlWorkService(session)
        success = await service.release_fenced_claim(
            scan_url_id=claim.scan_url_id,
            lease_owner=instance_id,
            fence_token=claim.fence_token,
        )
    assert success is True

    # Verify DB state restored to QUEUED with 0 attempt consumption
    async with session_factory() as session:
        res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        db_url = res.scalar_one()
        assert db_url.status == ScanURLStatus.QUEUED.value
        assert db_url.attempt_count == 0
        assert db_url.lease_owner is None
        assert db_url.claimed_from_status is None


@pytest.mark.anyio
async def test_stale_worker_fence_rejected(isolated_db_engine: AsyncEngine) -> None:
    """Test stale worker with an older fence_token is rejected by worker calls."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory.begin() as session:
        org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:6]}")
        job = ScanJob(
            id=job_id,
            organization_id=org_id,
            status=ScanJobStatus.QUEUED.value,
            total_input_count=1,
            valid_input_count=1,
            queued_count=1,
        )
        url = ScanURL(
            id=url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.QUEUED.value,
            fence_token=0,
            attempt_count=0,
            max_attempts=3,
        )
        session.add_all([org, job, url])

    worker_1 = "worker-instance-1"
    worker_2 = "worker-instance-2"

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim_1 = await service.claim_next_url(lease_owner=worker_1)
    assert claim_1 is not None
    assert claim_1.fence_token == 1

    # Simulate worker 1 lease expiry and reclaim by worker 2
    async with session_factory.begin() as session:
        db_url_res = await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        db_url = db_url_res.scalar_one()
        db_url.status = ScanURLStatus.QUEUED.value
        db_job_res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        db_job = db_job_res.scalar_one()
        db_job.running_count = 0
        db_job.queued_count = 1

    async with session_factory() as session:
        service = CrawlWorkService(session)
        claim_2 = await service.claim_next_url(lease_owner=worker_2)
    assert claim_2 is not None
    assert claim_2.fence_token == 2

    # Worker 1 attempts to mark attempt started with old fence 1 -> raises LeaseLostError
    async with session_factory() as session:
        service = CrawlWorkService(session)
        with pytest.raises(LeaseLostError):
            await service.mark_attempt_started(
                scan_url_id=claim_1.scan_url_id,
                lease_owner=worker_1,
                fence_token=claim_1.fence_token,
            )

    # Worker 1 attempts release with old fence 1 -> returns False
    async with session_factory() as session:
        service = CrawlWorkService(session)
        rel_res = await service.release_fenced_claim(
            scan_url_id=claim_1.scan_url_id,
            lease_owner=worker_1,
            fence_token=claim_1.fence_token,
        )
    assert rel_res is False
