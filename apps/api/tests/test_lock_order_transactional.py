"""Transactional tests for canonical parent-first (ScanJob -> ScanURL) lock ordering."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models import (
    Organization,
    ScanJob,
    ScanURL,
    User,
)
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.worker_contracts import (
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


def is_pg_deadlock(exc: BaseException) -> bool:
    """Check if exception is a PostgreSQL 40P01 deadlock via DBAPIError sqlstate/pgcode."""
    if isinstance(exc, (DBAPIError, IntegrityError, OperationalError)):
        orig = getattr(exc, "orig", None)
        if orig is not None:
            sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            if sqlstate == "40P01":
                return True
    return "40P01" in str(exc)


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
            accepted_email_findings=len(findings),
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
    running_count: int = 1,
    lease_owner: str = "worker-1",
    attempt_count: int = 1,
    fence_token: int = 1,
    lease_expires_delta: timedelta = timedelta(seconds=120),
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, URLClaim]:
    """Helper seeding an Org, User, ScanJob, & ScanURL in SCANNING state."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Lock Order Org", slug=f"org-{org_id.hex[:8]}")
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
                total_input_count=max(1, running_count),
                valid_input_count=max(1, running_count),
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
                status=ScanURLStatus.SCANNING.value,
                lease_owner=lease_owner,
                lease_expires_at=now + lease_expires_delta,
                attempt_count=attempt_count,
                max_attempts=3,
                fence_token=fence_token,
                claimed_from_status=ScanURLStatus.QUEUED.value,
            )
            session.add_all([org, user, job, url])

            claim = URLClaim(
                scan_url_id=url_id,
                organization_id=org_id,
                job_id=job_id,
                original_input=url.original_input,
                normalized_url=url.normalized_url,
                normalized_domain=url.normalized_domain,
                lease_owner=lease_owner,
                fence_token=fence_token,
                attempt_count=attempt_count,
                max_attempts=3,
                lease_expires_at=now + lease_expires_delta,
                claimed_from_status=ScanURLStatus.QUEUED.value,
            )
            return org_id, user_id, job_id, claim


async def _seed_multi_job_urls(
    session_factory: async_sessionmaker[AsyncSession],
    job_spec: list[int],
    lease_expires_delta: timedelta = timedelta(seconds=-10),
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Seed an Org, User, and multiple ScanJobs with expired SCANNING ScanURLs."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_ids: list[uuid.UUID] = []
    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Multi-Job Org", slug=f"org-{org_id.hex[:8]}")
            user = User(
                id=user_id,
                email=f"user-{user_id.hex[:8]}@example.com",
                normalized_email=f"user-{user_id.hex[:8]}@example.com",
                password_hash="hash",
            )
            session.add_all([org, user])

            for url_count in job_spec:
                j_id = uuid.uuid4()
                job_ids.append(j_id)
                job = ScanJob(
                    id=j_id,
                    organization_id=org_id,
                    created_by_user_id=user_id,
                    status=ScanJobStatus.RUNNING.value,
                    total_input_count=url_count,
                    valid_input_count=url_count,
                    duplicate_input_count=0,
                    running_count=url_count,
                    queued_count=0,
                )
                session.add(job)
                for idx in range(url_count):
                    u_id = uuid.uuid4()
                    url = ScanURL(
                        id=u_id,
                        scan_job_id=j_id,
                        original_input=f"https://example.com/j_{j_id.hex[:4]}/{idx}",
                        normalized_url=f"https://example.com/j_{j_id.hex[:4]}/{idx}",
                        normalized_domain="example.com",
                        original_index=idx,
                        status=ScanURLStatus.SCANNING.value,
                        lease_owner="worker-1",
                        lease_expires_at=now + lease_expires_delta,
                        attempt_count=1,
                        max_attempts=3,
                        fence_token=1,
                        claimed_from_status=ScanURLStatus.QUEUED.value,
                    )
                    session.add(url)

    return org_id, job_ids


@pytest.mark.anyio
async def test_lock_order_verification_parent_before_child(
    isolated_db_engine: AsyncEngine,
) -> None:
    """1. Prove lock order ScanJob FOR UPDATE -> ScanURL FOR UPDATE across all services."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, _, claim = await _seed_job_and_url(session_factory, running_count=1)

    executed_statements: list[str] = []

    def before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        clean_stmt = statement.strip().upper()
        if "SCAN_JOBS" in clean_stmt or "SCAN_URLS" in clean_stmt:
            executed_statements.append(clean_stmt)

    raw_engine = isolated_db_engine.sync_engine
    event.listen(raw_engine, "before_cursor_execute", before_cursor_execute)

    try:
        executed_statements.clear()
        async with session_factory() as session:
            service = ResultPersistenceService(session)
            await service.persist_transient_failure(claim, "ERR_TEST", "Test transient error")

        job_lock_idx = next(
            i for i, s in enumerate(executed_statements) if "SCAN_JOBS" in s and "FOR UPDATE" in s
        )
        url_update_idx = next(
            i for i, s in enumerate(executed_statements) if "UPDATE SCAN_URLS" in s
        )
        assert job_lock_idx < url_update_idx, (
            "ScanJob lock must precede ScanURL update in persist_transient_failure"
        )

        _, _, _, claim_release = await _seed_job_and_url(session_factory, running_count=1)
        executed_statements.clear()
        async with session_factory() as session:
            work_service = CrawlWorkService(session)
            await work_service.release_fenced_claim(
                claim_release.scan_url_id, claim_release.lease_owner, claim_release.fence_token
            )

        job_lock_idx = next(
            i for i, s in enumerate(executed_statements) if "SCAN_JOBS" in s and "FOR UPDATE" in s
        )
        url_lock_idx = next(
            i for i, s in enumerate(executed_statements) if "SCAN_URLS" in s and "FOR UPDATE" in s
        )
        assert job_lock_idx < url_lock_idx, (
            "ScanJob lock must precede ScanURL lock in release_fenced_claim"
        )

        await _seed_job_and_url(
            session_factory, running_count=1, lease_expires_delta=timedelta(seconds=-10)
        )
        executed_statements.clear()
        async with session_factory() as session:
            work_service = CrawlWorkService(session)
            await work_service.recover_expired_leases(batch_size=10)

        job_lock_idx = next(
            i for i, s in enumerate(executed_statements) if "SCAN_JOBS" in s and "FOR UPDATE" in s
        )
        url_lock_idx = next(
            i
            for i, s in enumerate(executed_statements)
            if "SCAN_URLS" in s and ("FOR UPDATE" in s or "FOR SHARE" in s)
        )
        assert job_lock_idx < url_lock_idx, (
            "ScanJob lock must precede ScanURL lock in recover_expired_leases"
        )

    finally:
        event.remove(raw_engine, "before_cursor_execute", before_cursor_execute)


@pytest.mark.anyio
async def test_concurrent_transient_failure_vs_job_cancellation(
    isolated_db_engine: AsyncEngine,
) -> None:
    """2. Concurrent transient failure vs cancellation completes without deadlock (40P01)."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, running_count=1
    )

    async def task_transient() -> Any:
        async with session_factory() as session:
            return await ResultPersistenceService(session).persist_transient_failure(
                claim=claim, error_code="ERR_500", error_message="Internal Server Error"
            )

    async def task_cancel() -> Any:
        async with session_factory() as session:
            return await ResultPersistenceService(session).persist_fenced_cancellation(claim)

    results = await asyncio.wait_for(
        asyncio.gather(task_transient(), task_cancel(), return_exceptions=True),
        timeout=10.0,
    )

    for r in results:
        if isinstance(r, BaseException):
            assert not is_pg_deadlock(r), f"Deadlock 40P01 encountered: {r}"

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.CANCELLED.value

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.running_count == 0
        assert job_db.failed_count == 0


@pytest.mark.anyio
async def test_concurrent_result_persistence_vs_lease_recovery(
    isolated_db_engine: AsyncEngine,
) -> None:
    """3. Concurrent result persistence vs lease recovery completes without deadlock."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, _, job_id, claim = await _seed_job_and_url(
        session_factory, running_count=1, lease_expires_delta=timedelta(seconds=-10)
    )
    site_result = _make_site_scan_result()

    async def task_persist() -> Any:
        async with session_factory() as session:
            return await ResultPersistenceService(session).persist_fenced_result(
                claim=claim, site_scan_result=site_result
            )

    async def task_recover() -> Any:
        async with session_factory() as session:
            return await CrawlWorkService(session).recover_expired_leases(batch_size=10)

    results = await asyncio.wait_for(
        asyncio.gather(task_persist(), task_recover(), return_exceptions=True),
        timeout=10.0,
    )

    for r in results:
        if isinstance(r, BaseException):
            assert not is_pg_deadlock(r), f"Deadlock 40P01 encountered: {r}"

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status in (
            ScanURLStatus.COMPLETED.value,
            ScanURLStatus.QUEUED.value,
            ScanURLStatus.RETRY_WAIT.value,
        )

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.running_count == 0


@pytest.mark.anyio
async def test_concurrent_lease_recovery_deterministic_overlap(
    isolated_db_engine: AsyncEngine,
) -> None:
    """4. Force deterministic overlap between 2 workers to prove SKIP LOCKED and job isolation."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id, job_ids = await _seed_multi_job_urls(session_factory, job_spec=[4, 4, 4])
    j1_id, j2_id = job_ids[0], job_ids[1]

    w1_holding = asyncio.Event()
    w2_finished = asyncio.Event()

    async def worker_1_loop() -> int:
        async with session_factory() as session:
            async with session.begin():
                job1 = (
                    await session.execute(
                        select(ScanJob).where(ScanJob.id == j1_id).with_for_update(skip_locked=True)
                    )
                ).scalar_one()
                assert job1 is not None

                w1_holding.set()
                await w2_finished.wait()

                work_service = CrawlWorkService(session)
                return await work_service.recover_expired_leases(batch_size=10)

    async def worker_2_loop() -> int:
        await w1_holding.wait()
        async with session_factory() as session:
            work_service = CrawlWorkService(session)
            rec2 = await work_service.recover_expired_leases(batch_size=10)
            w2_finished.set()
            return rec2

    results = await asyncio.wait_for(
        asyncio.gather(worker_1_loop(), worker_2_loop(), return_exceptions=True),
        timeout=10.0,
    )

    for r in results:
        if isinstance(r, BaseException):
            assert not is_pg_deadlock(r), f"Deadlock 40P01 encountered: {r}"

    rec1 = results[0] if isinstance(results[0], int) else 0
    rec2 = results[1] if isinstance(results[1], int) else 0

    assert rec1 > 0, "Worker 1 must recover URLs"
    assert rec2 > 0, "Worker 2 must recover URLs by skipping Job 1"

    async with session_factory() as session:
        j1_urls = list(
            (await session.execute(select(ScanURL).where(ScanURL.scan_job_id == j1_id)))
            .scalars()
            .all()
        )
        j2_urls = list(
            (await session.execute(select(ScanURL).where(ScanURL.scan_job_id == j2_id)))
            .scalars()
            .all()
        )

        for u in j1_urls:
            assert u.status in (ScanURLStatus.QUEUED.value, ScanURLStatus.RETRY_WAIT.value)
        for u in j2_urls:
            assert u.status in (ScanURLStatus.QUEUED.value, ScanURLStatus.RETRY_WAIT.value)

        sample_url = j1_urls[0]
        stale_claim = URLClaim(
            scan_url_id=sample_url.id,
            organization_id=org_id,
            job_id=sample_url.scan_job_id,
            original_input=sample_url.original_input,
            normalized_url=sample_url.normalized_url,
            normalized_domain=sample_url.normalized_domain,
            lease_owner="worker-1",
            fence_token=1,
            attempt_count=1,
            max_attempts=3,
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=10),
            claimed_from_status=ScanURLStatus.QUEUED.value,
        )
        work_service = CrawlWorkService(session)
        released = await work_service.release_fenced_claim(
            stale_claim.scan_url_id, stale_claim.lease_owner, stale_claim.fence_token
        )
        assert released is False


@pytest.mark.anyio
async def test_repeated_small_batches_fairness_and_starvation_prevention(
    isolated_db_engine: AsyncEngine,
) -> None:
    """5. Prove last_claimed_at rotation prevents single large job starvation."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    _, job_ids = await _seed_multi_job_urls(session_factory, job_spec=[20, 2, 2])
    j3_id = job_ids[2]

    async with session_factory() as session:
        rec1 = await CrawlWorkService(session).recover_expired_leases(batch_size=4)
        assert rec1 == 4

    async with session_factory() as session:
        rec2 = await CrawlWorkService(session).recover_expired_leases(batch_size=4)
        assert rec2 > 0

    async with session_factory() as session:
        j3_urls = list(
            (
                await session.execute(
                    select(ScanURL).where(
                        ScanURL.scan_job_id == j3_id,
                        ScanURL.status.in_(
                            (ScanURLStatus.QUEUED.value, ScanURLStatus.RETRY_WAIT.value)
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(j3_urls) > 0, "Job 3 must make progress due to last_claimed_at rotation"


@pytest.mark.anyio
async def test_deprecated_persistence_adapter_concurrent_with_cancellation(
    isolated_db_engine: AsyncEngine,
) -> None:
    """6. Deprecated persist_site_scan_result concurrent with cancellation has 0 deadlock."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id, _, job_id, claim = await _seed_job_and_url(
        session_factory, job_status=ScanJobStatus.CANCELLING, running_count=1
    )
    site_result = _make_site_scan_result()

    async def task_adapter() -> Any:
        async with session_factory() as session:
            return await ResultPersistenceService(session).persist_site_scan_result(
                organization_id=org_id,
                job_id=job_id,
                scan_url_id=claim.scan_url_id,
                attempt_number=claim.attempt_count,
                site_scan_result=site_result,
            )

    async def task_cancel() -> Any:
        async with session_factory() as session:
            return await ResultPersistenceService(session).persist_fenced_cancellation(claim)

    results = await asyncio.wait_for(
        asyncio.gather(task_adapter(), task_cancel(), return_exceptions=True),
        timeout=10.0,
    )

    for r in results:
        if isinstance(r, BaseException):
            assert not is_pg_deadlock(r), f"Deadlock 40P01 encountered: {r}"

    async with session_factory() as session:
        url_db = (
            await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        ).scalar_one()
        assert url_db.status == ScanURLStatus.CANCELLED.value

        job_db = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
        assert job_db.running_count == 0


@pytest.mark.anyio
async def test_deterministic_100_url_recovery_regression(
    isolated_db_engine: AsyncEngine,
) -> None:
    """7. Deterministic 100-URL recovery regression test across 10 jobs under 120s timeout."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    job_spec = [10] * 10
    _, job_ids = await _seed_multi_job_urls(session_factory, job_spec=job_spec)

    async def worker_loop() -> int:
        total_worker_rec = 0
        while True:
            async with session_factory() as session:
                rec = await CrawlWorkService(session).recover_expired_leases(batch_size=15)
                total_worker_rec += rec
                if rec == 0:
                    break
        return total_worker_rec

    results = await asyncio.wait_for(
        asyncio.gather(worker_loop(), worker_loop(), worker_loop(), return_exceptions=True),
        timeout=120.0,
    )

    for r in results:
        if isinstance(r, BaseException):
            assert not is_pg_deadlock(r), f"Deadlock 40P01 encountered: {r}"

    total_recovered = sum(r for r in results if isinstance(r, int))
    assert total_recovered == 100, f"Expected 100 total recovered URLs, got {total_recovered}"

    async with session_factory() as session:
        for j_id in job_ids:
            job_db = (await session.execute(select(ScanJob).where(ScanJob.id == j_id))).scalar_one()
            assert job_db.running_count == 0, f"Job {j_id} running_count must be 0"

        urls_db = (
            (await session.execute(select(ScanURL).where(ScanURL.scan_job_id.in_(job_ids))))
            .scalars()
            .all()
        )
        assert len(urls_db) == 100
        for u in urls_db:
            assert u.status in (ScanURLStatus.QUEUED.value, ScanURLStatus.RETRY_WAIT.value)
            assert u.lease_owner is None
            assert u.lease_expires_at is None
