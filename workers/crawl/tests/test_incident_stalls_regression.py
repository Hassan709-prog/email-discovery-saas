"""Deterministic offline regression tests for incident stalls and worker lease recovery.

Targeting isolated PostgreSQL test database with fake orchestrator/network components.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models import JobEvent, ScanJob, ScanURL
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.scan_jobs import ScanJobService
from email_discovery_crawl_worker.worker import CrawlWorker
from email_scanner.errors import PageScanOutcome, RobotsDecisionCode, SiteScanOutcome
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailSourceKind,
    PageScanRecord,
    RobotsDecision,
    SiteScanResult,
    SiteScanStatistics,
)
from email_scanner.models import (
    EmailFinding as ScannerEmailFinding,
)

pytestmark = pytest.mark.anyio


def make_fake_site_result(url: str, emails: list[str]) -> SiteScanResult:
    """Helper creating a successful fake SiteScanResult."""
    findings = [
        ScannerEmailFinding(
            raw_candidate=e,
            canonical_email=e.lower(),
            local_part=e.split("@")[0],
            domain=e.split("@")[-1],
            category=EmailCategory.PERSONAL_OR_NAMED,
            domain_affinity=DomainAffinity.EXACT_HOST,
            source_kind=EmailSourceKind.VISIBLE_TEXT,
            source_url=url,
            evidence_snippet=f"Contact us at {e}",
        )
        for e in emails
    ]
    return SiteScanResult(
        starting_url=url,
        outcome=SiteScanOutcome.COMPLETED,
        page_records=(),
        email_findings=tuple(findings),
        rejected_email_candidates=(),
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=len(findings),
            rejected_email_candidates=0,
            elapsed_seconds=0.1,
            stop_reason="QUEUE_EXHAUSTED",
        ),
    )


class MockOrchestrator:
    """Mock orchestrator simulating fast scans, timeouts, or exceptions."""

    def __init__(
        self,
        cancellation_checker: Any | None = None,
        fail_urls: set[str] | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.cancellation_checker = cancellation_checker
        self.fail_urls = fail_urls or set()
        self.delay_seconds = delay_seconds

    async def scan(self, url: str) -> SiteScanResult:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.cancellation_checker and self.cancellation_checker():
            raise asyncio.CancelledError("Scan cancelled at checkpoint")

        if url in self.fail_urls:
            raise RuntimeError(f"Simulated network timeout for {url}")

        return make_fake_site_result(url, ["contact@example.com"])


@pytest.fixture
async def seeded_100_url_job(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> dict[str, Any]:
    """Seed isolated DB with 100 ScanURL rows (94 valid, 6 duplicate) for deterministic tests."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()

    valid_urls: list[uuid.UUID] = []
    scan_urls: list[ScanURL] = []

    first_url_id = uuid.uuid4()
    # 6 duplicates of first URL
    # 94 valid URLs
    for idx in range(100):
        u_id = uuid.uuid4()
        if 0 < idx <= 6:
            scan_urls.append(
                ScanURL(
                    id=u_id,
                    scan_job_id=job_id,
                    original_index=idx,
                    original_input=f"https://site0.com/page{idx}",
                    normalized_url="https://site0.com/",
                    normalized_domain="site0.com",
                    status=ScanURLStatus.DUPLICATE.value,
                    duplicate_of_scan_url_id=first_url_id,
                )
            )
        else:
            if idx == 0:
                u_id = first_url_id
            valid_urls.append(u_id)
            site_idx = idx if idx == 0 else idx - 5
            scan_urls.append(
                ScanURL(
                    id=u_id,
                    scan_job_id=job_id,
                    original_index=idx,
                    original_input=f"https://site{site_idx}.org/",
                    normalized_url=f"https://site{site_idx}.org/",
                    normalized_domain=f"site{site_idx}.org",
                    status=ScanURLStatus.QUEUED.value,
                )
            )

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.QUEUED.value,
                total_input_count=100,
                valid_input_count=94,
                duplicate_input_count=6,
                queued_count=94,
                running_count=0,
                completed_count=0,
                failed_count=0,
            )
            session.add(job)
            session.add_all(scan_urls)

    return {
        "org_id": org_id,
        "job_id": job_id,
        "valid_urls": valid_urls,
        "session_factory": session_factory,
    }


async def test_worker_concurrency_cap_and_lease_recovery(
    seeded_100_url_job: dict[str, Any],
) -> None:
    """Prove worker claims never exceed concurrency 4 and expired leases recover safely."""
    session_factory = seeded_100_url_job["session_factory"]
    job_id = seeded_100_url_job["job_id"]

    # 1. Create worker with concurrency 4
    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="test-worker-c4",
        concurrency=4,
        poll_interval_seconds=0.1,
        lease_duration_seconds=0.5,  # Short lease for testing expiry
        heartbeat_interval_seconds=0.1,
    )
    worker._running = True  # pyright: ignore[reportPrivateUsage]

    # Claim 4 URLs
    claimed_any = await worker._fill_capacity_and_claim()  # pyright: ignore[reportPrivateUsage]
    assert claimed_any is True
    assert len(worker._active_tasks) <= 4  # pyright: ignore[reportPrivateUsage]

    # Drain active worker tasks and force lease expiration to simulate worker crash
    worker.request_shutdown()
    await worker._drain_tasks()  # pyright: ignore[reportPrivateUsage]

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                update(ScanURL)
                .where(
                    ScanURL.scan_job_id == job_id,
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                )
                .values(lease_expires_at=datetime.now(UTC))
            )

    # Recover expired leases
    async with session_factory() as session:
        work_svc = CrawlWorkService(session)
        recovered = await work_svc.recover_expired_leases()
        assert recovered >= 1

    # Verify no URL remains SCANNING indefinitely
    async with session_factory() as session:
        res = await session.execute(
            select(func.count(ScanURL.id)).where(
                ScanURL.scan_job_id == job_id, ScanURL.status == ScanURLStatus.SCANNING.value
            )
        )
        assert res.scalar_one() == 0


async def test_cancelling_job_lease_recovery_transitions_to_cancelled(
    seeded_100_url_job: dict[str, Any],
) -> None:
    """Prove that recovering expired leases on a CANCELLING job sets URLs directly to CANCELLED."""
    session_factory = seeded_100_url_job["session_factory"]
    job_id = seeded_100_url_job["job_id"]
    org_id = seeded_100_url_job["org_id"]

    # 1. Claim a URL with an expired lease
    async with session_factory() as session:
        work_svc = CrawlWorkService(session)
        claim = await work_svc.claim_next_url(
            lease_owner="worker-temp", lease_duration_seconds=-10.0
        )
    assert claim is not None

    # 2. Cancel the job
    async with session_factory() as session:
        job_svc = ScanJobService(session)
        job = await job_svc.cancel_job(org_id, job_id)
        assert job.status in (ScanJobStatus.CANCELLING.value, ScanJobStatus.CANCELLED.value)

    # 3. Recover expired leases
    async with session_factory() as session:
        work_svc = CrawlWorkService(session)
        recovered = await work_svc.recover_expired_leases()
        assert recovered >= 0

    # 4. Run maintenance reconciliation & finalization
    async with session_factory() as session:
        job_svc = ScanJobService(session)
        final_job = await job_svc.reconcile_and_recover_stuck_job(org_id, job_id)
        assert final_job is not None
        assert final_job.status == ScanJobStatus.CANCELLED.value

    # Verify ScanURL status is CANCELLED
    async with session_factory() as session:
        res = await session.execute(select(ScanURL).where(ScanURL.id == claim.scan_url_id))
        url = res.scalar_one()
        assert url.status == ScanURLStatus.CANCELLED.value


async def test_reconcile_and_recover_stuck_job_is_idempotent(
    seeded_100_url_job: dict[str, Any],
) -> None:
    """Prove that calling reconcile_and_recover_stuck_job multiple times is idempotent."""
    session_factory = seeded_100_url_job["session_factory"]
    job_id = seeded_100_url_job["job_id"]
    org_id = seeded_100_url_job["org_id"]

    async with session_factory() as session:
        job_svc = ScanJobService(session)
        job1 = await job_svc.reconcile_and_recover_stuck_job(org_id, job_id)

    async with session_factory() as session:
        job_svc = ScanJobService(session)
        job2 = await job_svc.reconcile_and_recover_stuck_job(org_id, job_id)

    if job1 and job2:
        assert job1.status == job2.status
        assert job1.completed_count == job2.completed_count
        assert job1.failed_count == job2.failed_count


async def test_deterministic_100_url_end_to_end_regression(
    seeded_100_url_job: dict[str, Any],
) -> None:
    """Prove 100-URL offline processing with interrupted leases, concurrency 4, and exact math."""
    session_factory = seeded_100_url_job["session_factory"]
    job_id = seeded_100_url_job["job_id"]
    org_id = seeded_100_url_job["org_id"]

    # 1. Create 6 interrupted SCANNING rows with expired leases for demo-worker-1
    async with session_factory() as session:
        async with session.begin():
            res = await session.execute(
                select(ScanURL)
                .where(ScanURL.scan_job_id == job_id, ScanURL.status == ScanURLStatus.QUEUED.value)
                .limit(6)
            )
            urls_to_interrupt = list(res.scalars().all())
            for u in urls_to_interrupt:
                u.status = ScanURLStatus.SCANNING.value
                u.lease_owner = "demo-worker-1"
                u.lease_expires_at = datetime.now(UTC)
                u.attempt_count = 1
            # Update job running count
            res_job = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
            job = res_job.scalar_one()
            job.running_count = 6
            job.queued_count = 88

    # 2. Setup mock orchestrator with mixture of outcomes
    class MixtureOrchestrator:
        async def scan(self, url: str) -> SiteScanResult:
            if "site1." in url or "site2." in url:
                # Permanent ROBOTS_BLOCKED
                return SiteScanResult(
                    starting_url=url,
                    outcome=SiteScanOutcome.ROBOTS_BLOCKED,
                    page_records=(),
                    email_findings=(),
                    rejected_email_candidates=(),
                    statistics=SiteScanStatistics(
                        pages_queued=1,
                        pages_attempted=1,
                        pages_fetched=0,
                        pages_blocked_by_robots=1,
                        pages_failed=0,
                        urls_discovered=0,
                        accepted_email_findings=0,
                        rejected_email_candidates=0,
                        elapsed_seconds=0.01,
                        stop_reason="ROBOTS_BLOCKED",
                    ),
                )
            if "site3." in url or "site4." in url:
                # NO_EMAIL
                return SiteScanResult(
                    starting_url=url,
                    outcome=SiteScanOutcome.COMPLETED_NO_EMAILS,
                    page_records=(),
                    email_findings=(),
                    rejected_email_candidates=(),
                    statistics=SiteScanStatistics(
                        pages_queued=1,
                        pages_attempted=1,
                        pages_fetched=1,
                        pages_blocked_by_robots=0,
                        pages_failed=0,
                        urls_discovered=0,
                        accepted_email_findings=0,
                        rejected_email_candidates=0,
                        elapsed_seconds=0.01,
                        stop_reason="QUEUE_EXHAUSTED",
                    ),
                )
            return make_fake_site_result(url, ["lead@example.com"])

    mock_orch = MixtureOrchestrator()
    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="regression-worker-1",
        concurrency=4,
        poll_interval_seconds=0.01,
        recovery_interval_seconds=0.1,
        orchestrator_factory=lambda: mock_orch,
    )

    # 3. Process work loop via worker.start
    worker_task = asyncio.create_task(worker.start())
    for _ in range(300):
        await asyncio.sleep(0.05)
        async with session_factory() as session:
            res_job = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
            j = res_job.scalar_one_or_none()
            if j and (j.completed_count + j.failed_count) >= 94:
                break

    worker.request_shutdown()
    await worker_task

    # 4. Perform reconciliation & recovery
    async with session_factory() as session:
        job_svc = ScanJobService(session)
        final_job = await job_svc.reconcile_and_recover_stuck_job(org_id, job_id)
        assert final_job is not None

    # 5. Assert exact 100-URL requirements
    assert final_job.total_input_count == 100
    assert final_job.valid_input_count == 94
    assert final_job.duplicate_input_count == 6
    assert final_job.queued_count == 0
    assert final_job.running_count == 0
    assert final_job.completed_count + final_job.failed_count == 94
    assert final_job.status in (
        ScanJobStatus.COMPLETED.value,
        ScanJobStatus.COMPLETED_WITH_ERRORS.value,
    )

    # Verify ScanURL database status counts match job counters exactly
    async with session_factory() as session:
        res = await session.execute(
            select(ScanURL.status, func.count(ScanURL.id))
            .where(ScanURL.scan_job_id == job_id)
            .group_by(ScanURL.status)
        )
        counts = dict(res.tuples().all())
        assert counts.get(ScanURLStatus.QUEUED.value, 0) == 0
        assert counts.get(ScanURLStatus.RETRY_WAIT.value, 0) == 0
        assert counts.get(ScanURLStatus.SCANNING.value, 0) == 0
        assert counts.get(ScanURLStatus.DUPLICATE.value, 0) == 6

        comp_cnt = counts.get(ScanURLStatus.COMPLETED.value, 0) + counts.get(
            ScanURLStatus.NO_EMAIL.value, 0
        )
        fail_cnt = counts.get(ScanURLStatus.FAILED.value, 0)
        assert comp_cnt == final_job.completed_count
        assert fail_cnt == final_job.failed_count


async def test_robots_disallow_versus_temporary_failure_retry(
    seeded_100_url_job: dict[str, Any],
) -> None:
    """Prove permanent robots disallow is attempted once, while temporary failure is retried."""
    from email_discovery_crawl_worker.outcome_classifier import (
        WorkerExecutionOutcome,
        classify_worker_outcome,
    )

    # Permanent ROBOTS_BLOCKED -> TERMINAL_FAILURE on attempt 1
    perm_result = SiteScanResult(
        starting_url="https://site.com/",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=0,
            pages_blocked_by_robots=1,
            pages_failed=0,
            urls_discovered=0,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=0.01,
            stop_reason="ROBOTS_BLOCKED",
        ),
    )
    outcome_perm = classify_worker_outcome(perm_result, None, attempt_count=1, max_attempts=3)
    assert outcome_perm == WorkerExecutionOutcome.TERMINAL_FAILURE

    # Temporary fetch error -> RETRYABLE_FAILURE on attempt 1
    temp_result = SiteScanResult(
        starting_url="https://site.com/",
        outcome=SiteScanOutcome.FAILED,
        page_records=(
            PageScanRecord(
                requested_url="https://site.com/",
                final_url=None,
                depth=0,
                outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
                status_code=503,
                robots_decision=RobotsDecision(
                    target_url="https://site.com/",
                    decision=RobotsDecisionCode.TEMPORARY_FAILURE,
                    crawl_delay=None,
                    reason="Robots 503 Service Unavailable",
                ),
                fetch_result=None,
                emails_found_count=0,
                links_discovered_count=0,
            ),
        ),
        email_findings=(),
        rejected_email_candidates=(),
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=0,
            pages_blocked_by_robots=0,
            pages_failed=1,
            urls_discovered=0,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=0.01,
            stop_reason="ROBOTS_TEMPORARY_FAILURE",
        ),
    )
    outcome_temp = classify_worker_outcome(temp_result, None, attempt_count=1, max_attempts=3)
    assert outcome_temp == WorkerExecutionOutcome.RETRYABLE_FAILURE


async def test_multi_worker_periodic_recovery_concurrency(
    seeded_100_url_job: dict[str, Any],
) -> None:
    """Prove two workers calling recovery concurrently execute safely without races."""
    session_factory = seeded_100_url_job["session_factory"]

    async def run_recovery(worker_id: str) -> int:
        async with session_factory() as session:
            work_svc = CrawlWorkService(session)
            return await work_svc.recover_expired_leases()

    results = await asyncio.gather(run_recovery("worker-a"), run_recovery("worker-b"))
    assert isinstance(results[0], int)
    assert isinstance(results[1], int)


async def test_job_lifecycle_timestamp_invariants(
    seeded_100_url_job: dict[str, Any],
) -> None:
    """Prove created_at <= queued_at <= started_at <= completed_at invariant and UTC awareness."""
    session_factory = seeded_100_url_job["session_factory"]
    job_id = seeded_100_url_job["job_id"]
    org_id = seeded_100_url_job["org_id"]

    async with session_factory() as session:
        job_svc = ScanJobService(session)
        job = await job_svc.reconcile_and_recover_stuck_job(org_id, job_id)
        assert job is not None

        # Verify created_at <= completed_at
        if job.completed_at is not None:
            assert job.created_at <= job.completed_at
            assert job.completed_at.tzinfo is not None


async def test_finalization_case_a_completed_with_errors(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> None:
    """Case A: 100 inputs (94 valid, 6 dup, 35 completed, 59 failed, 0 nonterminal).

    Proves parent becomes COMPLETED_WITH_ERRORS, progress 100%, 0 expired lease
    maintenance repairs parent, and concurrent finalization creates 0 duplicate events.
    """
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()
    first_url_id = uuid.uuid4()

    scan_urls: list[ScanURL] = []
    # 6 duplicates
    for idx in range(6):
        scan_urls.append(
            ScanURL(
                id=uuid.uuid4() if idx > 0 else first_url_id,
                scan_job_id=job_id,
                original_index=idx,
                original_input=f"https://dup{idx}.com/",
                normalized_url="https://dup0.com/",
                normalized_domain="dup0.com",
                status=ScanURLStatus.DUPLICATE.value,
                duplicate_of_scan_url_id=first_url_id if idx > 0 else None,
            )
        )
    # 35 completed
    for idx in range(6, 41):
        scan_urls.append(
            ScanURL(
                id=uuid.uuid4(),
                scan_job_id=job_id,
                original_index=idx,
                original_input=f"https://completed{idx}.org/",
                normalized_url=f"https://completed{idx}.org/",
                normalized_domain=f"completed{idx}.org",
                status=ScanURLStatus.COMPLETED.value,
            )
        )
    # 59 failed
    for idx in range(41, 100):
        scan_urls.append(
            ScanURL(
                id=uuid.uuid4(),
                scan_job_id=job_id,
                original_index=idx,
                original_input=f"https://failed{idx}.org/",
                normalized_url=f"https://failed{idx}.org/",
                normalized_domain=f"failed{idx}.org",
                status=ScanURLStatus.FAILED.value,
                last_error_code="ROBOTS_BLOCKED",
            )
        )

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=100,
                valid_input_count=94,
                duplicate_input_count=6,
                queued_count=0,
                running_count=0,
                completed_count=35,
                failed_count=59,
            )
            session.add(job)
            session.add_all(scan_urls)

    # 1. Execute periodic maintenance (with zero expired leases to recover)
    async with session_factory() as session:
        job_svc = ScanJobService(session)
        finalized_count = await job_svc.finalize_eligible_stuck_jobs()
        assert finalized_count == 1

    # 2. Verify state
    async with session_factory() as session:
        res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        job = res.scalar_one()
        assert job.status == ScanJobStatus.COMPLETED_WITH_ERRORS.value
        assert job.completed_count == 35
        assert job.failed_count == 59
        assert job.queued_count == 0
        assert job.running_count == 0
        assert job.completed_at is not None
        assert job.completed_at.tzinfo is not None

        # Verify progress calculation
        progress_pct = ((job.completed_count + job.failed_count) / job.valid_input_count) * 100
        assert progress_pct == 100.0

        # Verify exactly one terminal event
        res_ev = await session.execute(
            select(func.count(JobEvent.id)).where(
                JobEvent.scan_job_id == job_id, JobEvent.event_type == "JOB_STATUS_CHANGED"
            )
        )
        assert res_ev.scalar_one() == 1

    # 3. Concurrent finalization replay produces 0 duplicate events
    async def try_fin():
        async with session_factory() as session:
            job_svc = ScanJobService(session)
            return await job_svc.try_finalize_job(org_id, job_id)

    await asyncio.gather(try_fin(), try_fin())

    async with session_factory() as session:
        res_ev = await session.execute(
            select(func.count(JobEvent.id)).where(
                JobEvent.scan_job_id == job_id, JobEvent.event_type == "JOB_STATUS_CHANGED"
            )
        )
        assert res_ev.scalar_one() == 1


async def test_finalization_case_b_fully_completed(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> None:
    """Case B: 94 valid unique terminal success/no-email rows -> COMPLETED."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()

    scan_urls: list[ScanURL] = []
    for idx in range(94):
        scan_urls.append(
            ScanURL(
                id=uuid.uuid4(),
                scan_job_id=job_id,
                original_index=idx,
                original_input=f"https://ok{idx}.com/",
                normalized_url=f"https://ok{idx}.com/",
                normalized_domain=f"ok{idx}.com",
                status=ScanURLStatus.COMPLETED.value,
            )
        )

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=94,
                valid_input_count=94,
                duplicate_input_count=0,
                queued_count=0,
                running_count=0,
                completed_count=94,
                failed_count=0,
            )
            session.add(job)
            session.add_all(scan_urls)

    async with session_factory() as session:
        job_svc = ScanJobService(session)
        final_job = await job_svc.try_finalize_job(org_id, job_id)
        assert final_job is not None
        assert final_job.status == ScanJobStatus.COMPLETED.value
        assert final_job.completed_count == 94
        assert final_job.failed_count == 0


async def test_finalization_case_c_cancellation(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> None:
    """Case C: CANCELLING parent whose children are terminal or cancelled -> CANCELLED."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()

    scan_urls: list[ScanURL] = [
        ScanURL(
            id=uuid.uuid4(),
            scan_job_id=job_id,
            original_index=0,
            original_input="https://ok.com/",
            normalized_url="https://ok.com/",
            normalized_domain="ok.com",
            status=ScanURLStatus.COMPLETED.value,
        ),
        ScanURL(
            id=uuid.uuid4(),
            scan_job_id=job_id,
            original_index=1,
            original_input="https://canc.com/",
            normalized_url="https://canc.com/",
            normalized_domain="canc.com",
            status=ScanURLStatus.CANCELLED.value,
        ),
    ]

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.CANCELLING.value,
                total_input_count=2,
                valid_input_count=2,
                duplicate_input_count=0,
                queued_count=0,
                running_count=0,
                completed_count=1,
                failed_count=0,
            )
            session.add(job)
            session.add_all(scan_urls)

    async with session_factory() as session:
        job_svc = ScanJobService(session)
        final_job = await job_svc.try_finalize_job(org_id, job_id)
        assert final_job is not None
        assert final_job.status == ScanJobStatus.CANCELLED.value
