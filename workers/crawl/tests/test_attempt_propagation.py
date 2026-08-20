"""Focused tests proving authoritative attempt number propagation and fencing safety."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import select

from email_discovery_api.models import CrawlAttempt, ScanJob, ScanURL
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.worker_contracts import URLClaim
from email_scanner.errors import SiteScanOutcome
from email_scanner.models import SiteScanResult, SiteScanStatistics

pytestmark = pytest.mark.anyio


def make_fake_result(url: str) -> SiteScanResult:
    """Helper creating a dummy SiteScanResult."""
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
            elapsed_seconds=0.1,
            stop_reason="QUEUE_EXHAUSTED",
        ),
    )


async def test_new_claim_starts_at_attempt_one(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> None:
    """Verify fresh claim starts at attempt 1 after mark_attempt_started."""
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
                queued_count=1,
                running_count=0,
                completed_count=0,
                failed_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://test1.org",
                normalized_url="https://test1.org/",
                normalized_domain="test1.org",
                status=ScanURLStatus.QUEUED.value,
            )
            session.add_all([job, url])

    async with session_factory() as session:
        work_service = CrawlWorkService(session)
        claim = await work_service.claim_next_url(
            lease_owner="worker-1", lease_duration_seconds=120.0
        )
        assert claim is not None
        assert claim.attempt_count == 0  # Initial claim snapshot before attempt start

        started_attempt = await work_service.mark_attempt_started(
            scan_url_id=claim.scan_url_id,
            lease_owner="worker-1",
            fence_token=claim.fence_token,
        )
        assert started_attempt == 1

        active_claim = dataclasses.replace(claim, attempt_count=started_attempt)
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(
            claim=active_claim,
            site_scan_result=make_fake_result("https://test1.org/"),
        )

    async with session_factory() as session:
        res = await session.execute(select(CrawlAttempt).where(CrawlAttempt.scan_url_id == url_id))
        attempt_row = res.scalar_one()
        assert attempt_row.attempt_number == 1


async def test_retry_claim_writes_correct_next_attempt_number(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> None:
    """Verify retry claim increments and persists attempt_number = 2."""
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
                running_count=0,
                completed_count=0,
                failed_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://test-retry.org",
                normalized_url="https://test-retry.org/",
                normalized_domain="test-retry.org",
                status=ScanURLStatus.RETRY_WAIT.value,
                attempt_count=1,
                next_retry_at=datetime.now(UTC) - timedelta(seconds=10),
            )
            session.add_all([job, url])

    async with session_factory() as session:
        work_service = CrawlWorkService(session)
        claim = await work_service.claim_next_url(
            lease_owner="worker-1", lease_duration_seconds=120.0
        )
        assert claim is not None

        started_attempt = await work_service.mark_attempt_started(
            scan_url_id=claim.scan_url_id,
            lease_owner="worker-1",
            fence_token=claim.fence_token,
        )
        assert started_attempt == 2

        active_claim = dataclasses.replace(claim, attempt_count=started_attempt)
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(
            claim=active_claim,
            site_scan_result=make_fake_result("https://test-retry.org/"),
        )

    async with session_factory() as session:
        res = await session.execute(
            select(CrawlAttempt).where(
                CrawlAttempt.scan_url_id == url_id, CrawlAttempt.attempt_number == 2
            )
        )
        attempt_row = res.scalar_one_or_none()
        assert attempt_row is not None


async def test_repeated_mark_attempt_started_is_idempotent(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> None:
    """Verify repeated mark_attempt_started calls for same fence return same attempt_number."""
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
                queued_count=1,
                running_count=0,
                completed_count=0,
                failed_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://idempotent.org",
                normalized_url="https://idempotent.org/",
                normalized_domain="idempotent.org",
                status=ScanURLStatus.QUEUED.value,
            )
            session.add_all([job, url])

    async with session_factory() as session:
        work_service = CrawlWorkService(session)
        claim = await work_service.claim_next_url(
            lease_owner="worker-1", lease_duration_seconds=120.0
        )
        assert claim is not None

        attempt1 = await work_service.mark_attempt_started(
            scan_url_id=claim.scan_url_id,
            lease_owner="worker-1",
            fence_token=claim.fence_token,
        )
        attempt2 = await work_service.mark_attempt_started(
            scan_url_id=claim.scan_url_id,
            lease_owner="worker-1",
            fence_token=claim.fence_token,
        )
        assert attempt1 == 1
        assert attempt2 == 1


async def test_attempt_number_zero_cannot_reach_persistence(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> None:
    """Verify attempting persistence with attempt_count = 0 raises AssertionError."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    stale_claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://zero-attempt.org",
        normalized_url="https://zero-attempt.org/",
        normalized_domain="zero-attempt.org",
        lease_owner="worker-1",
        fence_token=1,
        attempt_count=0,
        max_attempts=3,
        lease_expires_at=None,  # type: ignore
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        with pytest.raises(AssertionError, match="Authoritative attempt_number must be >= 1"):
            await persistence.persist_fenced_result(
                claim=stale_claim,
                site_scan_result=make_fake_result("https://zero-attempt.org/"),
            )
