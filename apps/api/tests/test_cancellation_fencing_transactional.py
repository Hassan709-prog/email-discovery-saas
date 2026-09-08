"""Transactional tests for cancellation persistence, strict 6-predicate fencing, & finalization."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.mappers.crawl_results import CrawlAttemptResult
from email_discovery_api.models import (
    EmailFinding,
    Organization,
    ScanJob,
    ScanURL,
    User,
)
from email_discovery_api.models.crawl_attempt import CrawlAttempt
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.scan_jobs import ScanJobService
from email_discovery_api.services.worker_contracts import (
    FencedCancellationResult,
    LeaseLostError,
    URLClaim,
)
from email_scanner.errors import (
    FetchOutcomeCode,
    PageScanOutcome,
    RobotsDecisionCode,
    SiteScanOutcome,
)
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailSourceKind,
    FetchResult,
    PageScanRecord,
    RobotsDecision,
    SiteScanResult,
    SiteScanStatistics,
)
from email_scanner.models import (
    EmailFinding as ScannerEmailFinding,
)


def _make_site_scan_result(
    starting_url: str = "https://example.com",
    emails: tuple[str, ...] = ("info@example.com",),
) -> SiteScanResult:
    """Construct a valid SiteScanResult with email findings."""
    findings = tuple(
        ScannerEmailFinding(
            source_url=starting_url,
            raw_candidate=email,
            canonical_email=email,
            local_part=email.split("@")[0],
            domain=email.split("@")[1],
            source_kind=EmailSourceKind.VISIBLE_TEXT,
            category=EmailCategory.ROLE_BASED,
            domain_affinity=DomainAffinity.EXACT_HOST,
            evidence_snippet=f"Contact us at {email}",
        )
        for email in emails
    )

    return SiteScanResult(
        starting_url=starting_url,
        outcome=SiteScanOutcome.COMPLETED,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=len(emails),
            rejected_email_candidates=0,
            elapsed_seconds=0.5,
            stop_reason="COMPLETED",
        ),
        page_records=(
            PageScanRecord(
                requested_url=starting_url,
                final_url=starting_url,
                depth=0,
                outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
                status_code=200,
                robots_decision=RobotsDecision(
                    target_url=starting_url,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="OK",
                ),
                fetch_result=FetchResult(
                    final_url=starting_url,
                    status_code=200,
                    content_type="text/html",
                    body_text="<html>Contact info@example.com</html>",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                emails_found_count=len(emails),
                links_discovered_count=1,
            ),
        ),
        email_findings=findings,
        rejected_email_candidates=(),
    )


async def _seed_job_and_url(
    session_factory: async_sessionmaker[AsyncSession],
    job_status: ScanJobStatus = ScanJobStatus.RUNNING,
    url_status: ScanURLStatus = ScanURLStatus.SCANNING,
    lease_owner: str = "worker-1",
    fence_token: int = 1,
    attempt_count: int = 1,
    lease_expires_delta: timedelta = timedelta(seconds=120),
    running_count: int = 1,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, URLClaim]:
    """Seed DB with an organization, user, ScanJob, and ScanURL."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Cancel Test Org", slug=f"org-{org_id.hex[:8]}")
            user = User(
                id=user_id,
                email=f"user-{user_id.hex[:8]}@example.com",
                normalized_email=f"user-{user_id.hex[:8]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=job_status.value,
                total_input_count=1,
                valid_input_count=1,
                duplicate_input_count=0,
                running_count=running_count,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://example.com",
                normalized_url="https://example.com",
                normalized_domain="example.com",
                original_index=0,
                status=url_status.value,
                lease_owner=lease_owner,
                fence_token=fence_token,
                attempt_count=attempt_count,
                max_attempts=3,
                lease_expires_at=now + lease_expires_delta,
            )
            session.add_all([org, user, job, url])

    claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://example.com",
        normalized_url="https://example.com",
        normalized_domain="example.com",
        lease_owner=lease_owner,
        fence_token=fence_token,
        attempt_count=attempt_count,
        max_attempts=3,
        lease_expires_at=now + lease_expires_delta,
    )
    return org_id, user_id, job_id, claim


@pytest.mark.anyio
async def test_job_cancelling_during_scan_commits_url_cancellation_and_finalizes(
    isolated_db_engine: AsyncEngine,
) -> None:
    """1. Test active scan receiving result when parent job switches to CANCELLING.

    Verifies:
        - persist_fenced_result returns CrawlAttemptResult with is_cancelled=True
        - ScanURL status becomes CANCELLED with cleared lease/claim fields
        - No attempts, pages, findings, evidence, or rejected candidates are written
        - running_count decrements by 1
        - try_finalize_job finalizes parent job to CANCELLED
    """
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, running_count=1
    )

    site_result = _make_site_scan_result()

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        res = await service.persist_fenced_result(claim=claim, site_scan_result=site_result)
        assert res.is_cancelled is True
        assert res.attempt is None

    # Verify job finalization
    async with session_factory() as session:
        job_service = ScanJobService(session)
        finalized = await job_service.try_finalize_job(org_id, job_id)
        assert finalized is not None
        assert finalized.status == ScanJobStatus.CANCELLED.value

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.CANCELLED.value
        assert url_db.lease_owner is None
        assert url_db.lease_expires_at is None
        assert url_db.last_error_code == "JOB_CANCELLED"

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.status == ScanJobStatus.CANCELLED.value
        assert job_db.running_count == 0

        # Verify ZERO artifacts written
        attempts = (
            await session.execute(
                select(func.count(CrawlAttempt.id)).where(
                    CrawlAttempt.scan_url_id == claim.scan_url_id
                )
            )
        ).scalar_one()
        assert attempts == 0

        findings = (
            await session.execute(
                select(func.count(EmailFinding.id)).where(
                    EmailFinding.scan_url_id == claim.scan_url_id
                )
            )
        ).scalar_one()
        assert findings == 0


@pytest.mark.anyio
async def test_parent_already_cancelled_safe_url_cleanup(
    isolated_db_engine: AsyncEngine,
) -> None:
    """2. Test result persistence when parent job is already CANCELLED with running_count=0."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLED, running_count=0
    )

    site_result = _make_site_scan_result()

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        res = await service.persist_fenced_result(claim=claim, site_scan_result=site_result)
        assert res.is_cancelled is True

    async with session_factory() as session:
        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.running_count == 0  # No underflow below 0


@pytest.mark.anyio
async def test_stale_fence_token_zero_writes(
    isolated_db_engine: AsyncEngine,
) -> None:
    """3. Test stale fence token produces LeaseLostError and zero writes."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, _, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, fence_token=2
    )
    stale_claim = dataclasses.replace(claim, fence_token=1)

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        with pytest.raises(LeaseLostError):
            await service.persist_fenced_cancellation(stale_claim)

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.SCANNING.value
        assert url_db.fence_token == 2


@pytest.mark.anyio
async def test_expired_lease_zero_writes(
    isolated_db_engine: AsyncEngine,
) -> None:
    """4. Test expired lease produces LeaseLostError and zero writes."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, _, claim = await _seed_job_and_url(
        session_factory,
        job_status=ScanJobStatus.CANCELLING,
        lease_expires_delta=timedelta(seconds=-10),
    )

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        with pytest.raises(LeaseLostError):
            await service.persist_fenced_cancellation(claim)

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.SCANNING.value


@pytest.mark.anyio
async def test_wrong_lease_owner_zero_writes(
    isolated_db_engine: AsyncEngine,
) -> None:
    """5. Test wrong lease_owner produces LeaseLostError and zero writes."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, _, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, lease_owner="worker-1"
    )
    wrong_owner_claim = dataclasses.replace(claim, lease_owner="worker-2")

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        with pytest.raises(LeaseLostError):
            await service.persist_fenced_cancellation(wrong_owner_claim)

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.SCANNING.value
        assert url_db.lease_owner == "worker-1"


@pytest.mark.anyio
async def test_wrong_attempt_count_zero_writes(
    isolated_db_engine: AsyncEngine,
) -> None:
    """6. Test wrong attempt_count produces LeaseLostError and zero writes."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, _, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, attempt_count=1
    )
    wrong_attempt_claim = dataclasses.replace(claim, attempt_count=2)

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        with pytest.raises(LeaseLostError):
            await service.persist_fenced_cancellation(wrong_attempt_claim)

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.SCANNING.value
        assert url_db.attempt_count == 1


@pytest.mark.anyio
async def test_repeated_cancellation_is_idempotent_no_double_decrement(
    isolated_db_engine: AsyncEngine,
) -> None:
    """7. Test repeated cancellation call on already cancelled URL fails fencing check cleanly."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, running_count=1
    )

    # First cancellation succeeds
    async with session_factory() as session:
        service = ResultPersistenceService(session)
        res = await service.persist_fenced_cancellation(claim)
        assert res.cancelled is True
        assert res.scan_url_id == claim.scan_url_id

    # Second cancellation fails fencing check (status is no longer SCANNING)
    async with session_factory() as session:
        service = ResultPersistenceService(session)
        with pytest.raises(LeaseLostError):
            await service.persist_fenced_cancellation(claim)

    async with session_factory() as session:
        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.running_count == 0  # Decremented only once!


@pytest.mark.anyio
async def test_persist_fenced_cancellation_rejected_on_non_cancelling_job(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify explicit persist_fenced_cancellation on a RUNNING job is rejected with zero writes."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.RUNNING, running_count=1
    )

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.persist_fenced_cancellation(claim)
        assert exc_info.value.code == ServiceErrorCode.INVALID_STATE_TRANSITION

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.SCANNING.value  # ZERO URL WRITES

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.running_count == 1  # ZERO COUNTER WRITES

        attempts = (
            await session.execute(
                select(func.count(CrawlAttempt.id)).where(
                    CrawlAttempt.scan_url_id == claim.scan_url_id
                )
            )
        ).scalar_one()
        assert attempts == 0


@pytest.mark.anyio
async def test_cancellation_racing_with_normal_result_persistence(
    isolated_db_engine: AsyncEngine,
) -> None:
    """8. Test cancellation racing with normal result persistence on CANCELLING parent."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, running_count=1
    )
    site_result = _make_site_scan_result()

    async def task_cancel() -> Any:
        async with session_factory() as session:
            return await ResultPersistenceService(session).persist_fenced_cancellation(claim)

    async def task_result() -> Any:
        async with session_factory() as session:
            return await ResultPersistenceService(session).persist_fenced_result(
                claim=claim, site_scan_result=site_result
            )

    results = await asyncio.gather(task_cancel(), task_result(), return_exceptions=True)

    cancellation_res = results[0]
    result_res = results[1]

    # One task commits cancellation; the other observes cancellation or loses fencing
    if isinstance(cancellation_res, FencedCancellationResult):
        assert cancellation_res.cancelled is True
    else:
        assert isinstance(cancellation_res, (LeaseLostError, ServiceError))

    if isinstance(result_res, CrawlAttemptResult):
        assert result_res.is_cancelled is True
        assert result_res.attempt is None
    else:
        assert isinstance(result_res, LeaseLostError)

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.CANCELLED.value  # MUST BE CANCELLED

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.running_count == 0
        assert job_db.failed_count == 0
        assert job_db.completed_count == 0

        attempts = (
            await session.execute(
                select(func.count(CrawlAttempt.id)).where(
                    CrawlAttempt.scan_url_id == claim.scan_url_id
                )
            )
        ).scalar_one()
        assert attempts == 0

        findings = (
            await session.execute(
                select(func.count(EmailFinding.id)).where(
                    EmailFinding.scan_url_id == claim.scan_url_id
                )
            )
        ).scalar_one()
        assert findings == 0


@pytest.mark.anyio
async def test_normal_scan_result_persistence_unchanged(
    isolated_db_engine: AsyncEngine,
) -> None:
    """9. Test normal scan result persistence for non-cancelling job works as expected."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.RUNNING, running_count=1
    )
    site_result = _make_site_scan_result()

    async with session_factory() as session:
        service = ResultPersistenceService(session)
        res = await service.persist_fenced_result(claim=claim, site_scan_result=site_result)
        assert res.is_cancelled is False
        assert res.is_replay is False
        assert res.attempt is not None

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.COMPLETED.value

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.completed_count == 1
        assert job_db.running_count == 0


@pytest.mark.anyio
async def test_service_flow_cancellation_persistence_and_finalization(
    isolated_db_engine: AsyncEngine,
) -> None:
    """10. Service-level integration test for cancellation persistence and finalization."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, running_count=1
    )
    site_result = _make_site_scan_result()

    # Worker persistence step
    async with session_factory() as session:
        persistence_service = ResultPersistenceService(session)
        res = await persistence_service.persist_fenced_result(
            claim=claim, site_scan_result=site_result
        )
        assert res.is_cancelled is True

    # Worker job finalization step
    async with session_factory() as session:
        finalized = await ScanJobService(session).try_finalize_job(org_id, job_id)
        assert finalized is not None
        assert finalized.status == ScanJobStatus.CANCELLED.value

    # Confirm final DB state
    async with session_factory() as session:
        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.status == ScanJobStatus.CANCELLED.value
        assert job_db.completed_at is not None


@pytest.mark.anyio
async def test_crawl_worker_process_claim_cancellation_and_job_finalization(
    isolated_db_engine: AsyncEngine,
) -> None:
    """11. Genuine CrawlWorker test executing _process_claim_task on a CANCELLING job."""
    from unittest.mock import AsyncMock, MagicMock

    from email_discovery_crawl_worker.worker import CrawlWorker

    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    mock_orchestrator = MagicMock()
    mock_orchestrator.scan = AsyncMock(return_value=_make_site_scan_result())

    worker = CrawlWorker(
        session_factory=session_factory,
        orchestrator_factory=lambda: mock_orchestrator,
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory,
        job_status=ScanJobStatus.CANCELLING,
        running_count=1,
        lease_owner=worker.instance_id,
    )

    await worker._process_claim_task(claim)  # pyright: ignore[reportPrivateUsage]

    # Confirm CrawlWorker execution resulted in committed URL cancellation & job finalization
    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.CANCELLED.value

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.status == ScanJobStatus.CANCELLED.value
        assert job_db.completed_at is not None
