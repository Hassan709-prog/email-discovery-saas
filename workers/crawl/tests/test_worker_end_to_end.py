"""End-to-end integration tests for CrawlWorker engine against email_discovery_test."""

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from email_discovery_crawl_worker.worker import CrawlWorker
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import select

from email_discovery_api.models import EmailFinding, ScanJob, ScanURL
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_scanner.errors import SiteScanOutcome
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailSourceKind,
    SiteScanResult,
    SiteScanStatistics,
)
from email_scanner.models import (
    EmailFinding as ScannerEmailFinding,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def seeded_queued_job(
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
                status=ScanJobStatus.QUEUED.value,
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
                original_input="https://e2e-test.com",
                normalized_url="https://e2e-test.com/",
                normalized_domain="e2e-test.com",
                status=ScanURLStatus.QUEUED.value,
            )
            session.add_all([job, url1])

    return {
        "org_id": org_id,
        "job_id": job_id,
        "url_id1": url_id1,
        "session_factory": session_factory,
    }


async def test_crawl_worker_e2e_successful_scan(
    seeded_queued_job: dict[str, Any],
) -> None:
    """Verify CrawlWorker polls QUEUED job, executes scan, persists findings, and finalizes job."""
    session_factory = seeded_queued_job["session_factory"]
    job_id = seeded_queued_job["job_id"]
    url_id1 = seeded_queued_job["url_id1"]

    finding = ScannerEmailFinding(
        raw_candidate="contact@e2e-test.com",
        canonical_email="contact@e2e-test.com",
        local_part="contact",
        domain="e2e-test.com",
        category=EmailCategory.PERSONAL_OR_NAMED,
        domain_affinity=DomainAffinity.EXACT_HOST,
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        source_url="https://e2e-test.com/",
        evidence_snippet="Contact us at contact@e2e-test.com",
    )
    mock_scan_result = SiteScanResult(
        starting_url="https://e2e-test.com/",
        outcome=SiteScanOutcome.COMPLETED,
        page_records=(),
        email_findings=(finding,),
        rejected_email_candidates=(),
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=1,
            rejected_email_candidates=0,
            elapsed_seconds=0.1,
            stop_reason="QUEUE_EXHAUSTED",
        ),
    )

    mock_orchestrator = AsyncMock()
    mock_orchestrator.scan = AsyncMock(return_value=mock_scan_result)

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="e2e-worker-1",
        poll_interval_seconds=0.05,
        heartbeat_interval_seconds=10.0,
        max_scans=1,
        orchestrator_factory=lambda: mock_orchestrator,
    )

    await worker.start()

    # Verify DB assertions
    async with session_factory() as session:
        job_res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        job = job_res.scalar_one()
        assert job.status == ScanJobStatus.COMPLETED.value
        assert job.completed_count == 1
        assert job.failed_count == 0
        assert job.running_count == 0
        assert job.email_finding_count == 1

        url_res = await session.execute(select(ScanURL).where(ScanURL.id == url_id1))
        url = url_res.scalar_one()
        assert url.status == ScanURLStatus.COMPLETED.value
        assert url.lease_owner is None
        assert url.lease_expires_at is None

        finding_res = await session.execute(
            select(EmailFinding).where(EmailFinding.scan_job_id == job_id)
        )
        findings = finding_res.scalars().all()
        assert len(findings) == 1
        assert findings[0].canonical_email == "contact@e2e-test.com"
