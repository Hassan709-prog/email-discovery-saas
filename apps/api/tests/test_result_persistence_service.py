"""Unit tests for ResultPersistenceService, locking, replay idempotency, and counters."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_discovery_api.models import EmailFinding, ScanJob
from email_discovery_api.models.crawl_attempt import CrawlAttempt
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.result_persistence import (
    ResultPersistenceService,
    map_outcome_to_url_status,
)
from email_discovery_api.services.scan_jobs import ScanJobService
from email_discovery_api.services.worker_contracts import URLClaim
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


def make_sample_site_scan_result(
    starting_url: str = "https://example.com",
    outcome: SiteScanOutcome = SiteScanOutcome.COMPLETED,
    emails: tuple[str, ...] = ("info@example.com",),
) -> SiteScanResult:
    """Helper creating a sample SiteScanResult."""
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
        outcome=outcome,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=len(emails),
            rejected_email_candidates=0,
            elapsed_seconds=1.2,
            stop_reason=str(outcome),
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
                    body_text="<html>sample page</html>",
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


def test_map_outcome_completed_no_emails() -> None:
    """Verify map_outcome_to_url_status(COMPLETED_NO_EMAILS, 0) returns (NO_EMAIL, None)."""
    status, err_code = map_outcome_to_url_status(SiteScanOutcome.COMPLETED_NO_EMAILS, 0)
    assert status == ScanURLStatus.NO_EMAIL
    assert err_code is None


@pytest.mark.anyio
async def test_persist_fenced_result_completed_no_emails(
    isolated_db_engine: Any, test_user_and_token: dict[str, Any]
) -> None:
    """Verify persisting COMPLETED_NO_EMAILS sets ScanURL NO_EMAIL and job status COMPLETED."""
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
                original_input="https://no-emails.com",
                normalized_url="https://no-emails.com/",
                normalized_domain="no-emails.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([job, url])

    claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://no-emails.com",
        normalized_url="https://no-emails.com/",
        normalized_domain="no-emails.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    result_no_emails = SiteScanResult(
        starting_url="https://no-emails.com/",
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
            elapsed_seconds=0.2,
            stop_reason="QUEUE_EXHAUSTED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        res = await persistence.persist_fenced_result(claim, result_no_emails)

    assert res.is_replay is False

    # Assert ScanURL status NO_EMAIL, completed_count 1, failed_count 0
    async with session_factory() as session:
        url_obj = (await session.execute(select(ScanURL).where(ScanURL.id == url_id))).scalar_one()
        job_obj = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()

        assert url_obj.status == ScanURLStatus.NO_EMAIL.value
        assert url_obj.lease_owner is None
        assert url_obj.lease_expires_at is None
        assert job_obj.completed_count == 1
        assert job_obj.failed_count == 0
        assert job_obj.running_count == 0

    # Authoritative finalization produces ScanJobStatus.COMPLETED in fresh transaction
    async with session_factory() as session:
        job_service = ScanJobService(session)
        finalized = await job_service.try_finalize_job(org_id, job_id)
        assert finalized is not None
        assert finalized.status == ScanJobStatus.COMPLETED.value


@pytest.mark.anyio
async def test_persist_site_scan_result_success_first_delivery() -> None:
    """Verify first delivery locks ScanURL and persists result via adapter."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    scan_url_id = uuid.uuid4()

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    service = ResultPersistenceService(session=mock_session)
    service_any: Any = service

    service_any._scan_url_repo.get_url_for_update = AsyncMock(
        return_value=ScanURL(
            id=scan_url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            normalized_url="https://example.com",
            status=ScanURLStatus.SCANNING.value,
            lease_owner="test-owner",
            lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            attempt_count=1,
            max_attempts=3,
        )
    )
    service_any._attempt_repo.get_by_scan_url_and_attempt = AsyncMock(return_value=None)
    service_any._scan_job_repo.get_job_for_update = AsyncMock(return_value=None)

    created_attempt = CrawlAttempt(
        id=uuid.uuid4(),
        scan_url_id=scan_url_id,
        attempt_number=1,
        outcome="COMPLETED",
        retryable=False,
        requested_url="https://example.com",
        result_checksum="a" * 64,
    )
    service_any._attempt_repo.create = AsyncMock(return_value=created_attempt)
    service_any._page_repo.create_many = AsyncMock(
        return_value={"https://example.com": uuid.uuid4()}
    )

    finding_id = uuid.uuid4()
    service_any._finding_repo.upsert_findings = AsyncMock(
        return_value=(
            [
                EmailFinding(
                    id=finding_id,
                    scan_job_id=job_id,
                    canonical_email="info@example.com",
                    email_domain="example.com",
                    classification="ROLE_BASED",
                    is_role_based=True,
                    validation_status="UNVERIFIED",
                    first_found_at=datetime.now(UTC),
                    last_found_at=datetime.now(UTC),
                )
            ],
            [],
        )
    )
    service_any._finding_repo.increment_evidence_count = AsyncMock()
    service_any._evidence_repo.add_evidence = AsyncMock(return_value=[])
    service_any._rejected_repo.add_rejected_candidates = AsyncMock(return_value=[])

    sample_result = make_sample_site_scan_result()
    result_container = await service.persist_site_scan_result(
        organization_id=org_id,
        job_id=job_id,
        scan_url_id=scan_url_id,
        attempt_number=1,
        site_scan_result=sample_result,
    )

    assert result_container.is_replay is False
    assert result_container.attempt == created_attempt


@pytest.mark.anyio
async def test_persist_site_scan_result_cannot_create_or_repair_lease() -> None:
    """Regression test confirming adapter cannot create/repair an expired lease."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    scan_url_id = uuid.uuid4()

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    service = ResultPersistenceService(session=mock_session)
    service_any: Any = service
    sample_result = make_sample_site_scan_result()

    # 1. Missing lease owner raises INVALID_RESULT_STATE
    service_any._scan_url_repo.get_url_for_update = AsyncMock(
        return_value=ScanURL(
            id=scan_url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.SCANNING.value,
            lease_owner=None,
            lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(ServiceError) as exc_info:
        await service.persist_site_scan_result(
            organization_id=org_id,
            job_id=job_id,
            scan_url_id=scan_url_id,
            attempt_number=1,
            site_scan_result=sample_result,
        )
    assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # 2. Expired lease raises LEASE_LOST
    service_any._scan_url_repo.get_url_for_update = AsyncMock(
        return_value=ScanURL(
            id=scan_url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.SCANNING.value,
            lease_owner="w1",
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
    )

    with pytest.raises(ServiceError) as exc_info:
        await service.persist_site_scan_result(
            organization_id=org_id,
            job_id=job_id,
            scan_url_id=scan_url_id,
            attempt_number=1,
            site_scan_result=sample_result,
        )
    assert exc_info.value.code == ServiceErrorCode.LEASE_LOST


@pytest.mark.anyio
async def test_persist_fenced_result_with_valid_claim() -> None:
    """Verify persist_fenced_result succeeds with a valid URLClaim."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    scan_url_id = uuid.uuid4()

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    claim = URLClaim(
        scan_url_id=scan_url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://example.com",
        normalized_url="https://example.com",
        normalized_domain="example.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    service = ResultPersistenceService(session=mock_session)
    service_any: Any = service

    service_any._attempt_repo.get_by_scan_url_and_attempt = AsyncMock(return_value=None)
    service_any._scan_job_repo.get_job_for_update = AsyncMock(return_value=None)
    created_attempt = CrawlAttempt(
        id=uuid.uuid4(),
        scan_url_id=scan_url_id,
        attempt_number=1,
        outcome="COMPLETED",
        retryable=False,
        requested_url="https://example.com",
        result_checksum="a" * 64,
    )
    service_any._attempt_repo.create = AsyncMock(return_value=created_attempt)
    service_any._page_repo.create_many = AsyncMock(return_value={})
    service_any._finding_repo.upsert_findings = AsyncMock(return_value=([], []))
    service_any._evidence_repo.add_evidence = AsyncMock(return_value=[])
    service_any._rejected_repo.add_rejected_candidates = AsyncMock(return_value=[])

    sample_result = make_sample_site_scan_result()
    res = await service.persist_fenced_result(claim, sample_result)
    assert res.is_replay is False
    assert res.attempt == created_attempt
