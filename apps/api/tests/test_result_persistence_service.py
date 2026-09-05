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
    SiteScanDiagnostics,
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


def test_map_outcome_robots_blocked_tls_failure() -> None:
    """Verify ROBOTS_BLOCKED with TLS_VERIFICATION_FAILED maps to TLS_VERIFICATION_FAILED."""
    status, err_code = map_outcome_to_url_status(
        SiteScanOutcome.ROBOTS_BLOCKED, 0, "TLS_VERIFICATION_FAILED"
    )
    assert status == ScanURLStatus.FAILED
    assert err_code == "TLS_VERIFICATION_FAILED"


def test_map_outcome_robots_blocked_explicit_disallow() -> None:
    """Verify SiteScanOutcome.ROBOTS_BLOCKED with ROBOTS_BLOCKED or None maps to ROBOTS_BLOCKED."""
    status, err_code = map_outcome_to_url_status(
        SiteScanOutcome.ROBOTS_BLOCKED, 0, "ROBOTS_BLOCKED"
    )
    assert status == ScanURLStatus.FAILED
    assert err_code == "ROBOTS_BLOCKED"

    status_none, err_code_none = map_outcome_to_url_status(SiteScanOutcome.ROBOTS_BLOCKED, 0, None)
    assert status_none == ScanURLStatus.FAILED
    assert err_code_none == "ROBOTS_BLOCKED"


def test_map_outcome_robots_blocked_fetch_errors() -> None:
    """Verify ROBOTS_BLOCKED with transient failure codes maps to ROBOTS_FETCH_ERROR."""
    codes = (
        "ROBOTS_TEMPORARY_FAILURE",
        "ROBOTS_FETCH_ERROR",
        "TRANSPORT_ERROR",
        "DNS_RESOLUTION_FAILED",
        "CONNECT_TIMEOUT",
        "READ_TIMEOUT",
        "GENERIC_TIMEOUT",
    )
    for code in codes:
        status, err_code = map_outcome_to_url_status(SiteScanOutcome.ROBOTS_BLOCKED, 0, code)
        assert status == ScanURLStatus.FAILED
        assert err_code == "ROBOTS_FETCH_ERROR"


@pytest.mark.anyio
async def test_persist_fenced_result_robots_blocked_classifications(
    isolated_db_engine: Any, test_user_and_token: dict[str, Any]
) -> None:
    """Verify persistence of ROBOTS_BLOCKED with TLS, disallow, and transport error."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()
    url_id_tls = uuid.uuid4()
    url_id_disallow = uuid.uuid4()
    url_id_timeout = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=3,
                valid_input_count=3,
                queued_count=0,
                running_count=3,
                completed_count=0,
                failed_count=0,
            )
            url_tls = ScanURL(
                id=url_id_tls,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://tls-fail.com",
                normalized_url="https://tls-fail.com/",
                normalized_domain="tls-fail.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                max_attempts=3,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            url_disallow = ScanURL(
                id=url_id_disallow,
                scan_job_id=job_id,
                original_index=1,
                original_input="https://disallow.com",
                normalized_url="https://disallow.com/",
                normalized_domain="disallow.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                max_attempts=3,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            url_timeout = ScanURL(
                id=url_id_timeout,
                scan_job_id=job_id,
                original_index=2,
                original_input="https://timeout.com",
                normalized_url="https://timeout.com/",
                normalized_domain="timeout.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                max_attempts=3,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([job, url_tls, url_disallow, url_timeout])

    def make_robots_scan_result(
        start_url: str,
        failure_code: str,
        page_outcome: PageScanOutcome,
        robots_decision_code: RobotsDecisionCode,
    ) -> SiteScanResult:
        page_record = PageScanRecord(
            requested_url=start_url,
            final_url=None,
            depth=0,
            outcome=page_outcome,
            status_code=None,
            robots_decision=RobotsDecision(
                target_url=start_url,
                decision=robots_decision_code,
                crawl_delay=None,
                reason="Robots decision test",
            ),
            fetch_result=None,
            emails_found_count=0,
            links_discovered_count=0,
            error_message="Robots test error",
        )
        return SiteScanResult(
            starting_url=start_url,
            outcome=SiteScanOutcome.ROBOTS_BLOCKED,
            statistics=SiteScanStatistics(
                pages_queued=1,
                pages_attempted=1,
                pages_fetched=0,
                pages_blocked_by_robots=1,
                pages_failed=0,
                urls_discovered=0,
                accepted_email_findings=0,
                rejected_email_candidates=0,
                elapsed_seconds=0.5,
                stop_reason="ROBOTS_BLOCKED",
            ),
            page_records=(page_record,),
            email_findings=(),
            rejected_email_candidates=(),
            diagnostics=SiteScanDiagnostics(
                total_duration_seconds=0.5,
                dns_resolution_duration_seconds=0.05,
                gate_wait_duration_seconds=0.0,
                robots_fetch_duration_seconds=0.45,
                robots_evaluation_duration_seconds=0.0,
                http_fetch_duration_seconds=0.0,
                page_processing_duration_seconds=0.0,
                retry_count=0,
                total_retry_delay_seconds=0.0,
                redirect_count=0,
                http_status=None,
                failure_code=failure_code,
                time_budget_exhausted=False,
                cancellation_occurred=False,
                retry_budget_exhausted=False,
            ),
        )

    # 1. TLS verification failure
    claim_tls = URLClaim(
        scan_url_id=url_id_tls,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://tls-fail.com",
        normalized_url="https://tls-fail.com/",
        normalized_domain="tls-fail.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    res_tls = make_robots_scan_result(
        "https://tls-fail.com/",
        "TLS_VERIFICATION_FAILED",
        PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        RobotsDecisionCode.TEMPORARY_FAILURE,
    )

    # 2. Explicit robots disallow
    claim_disallow = URLClaim(
        scan_url_id=url_id_disallow,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://disallow.com",
        normalized_url="https://disallow.com/",
        normalized_domain="disallow.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    res_disallow = make_robots_scan_result(
        "https://disallow.com/",
        "ROBOTS_BLOCKED",
        PageScanOutcome.ROBOTS_DISALLOWED,
        RobotsDecisionCode.DISALLOWED,
    )

    # 3. Timeout / transport error
    claim_timeout = URLClaim(
        scan_url_id=url_id_timeout,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://timeout.com",
        normalized_url="https://timeout.com/",
        normalized_domain="timeout.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    res_timeout = make_robots_scan_result(
        "https://timeout.com/",
        "GENERIC_TIMEOUT",
        PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        RobotsDecisionCode.TEMPORARY_FAILURE,
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(claim_tls, res_tls)
        await persistence.persist_fenced_result(claim_disallow, res_disallow)
        await persistence.persist_fenced_result(claim_timeout, res_timeout)

    async with session_factory() as session:
        u_tls = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id_tls))
        ).scalar_one()
        u_disallow = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id_disallow))
        ).scalar_one()
        u_timeout = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id_timeout))
        ).scalar_one()

        # Terminal TLS failure produces TLS_VERIFICATION_FAILED for both codes
        assert u_tls.status == ScanURLStatus.FAILED.value
        assert u_tls.last_error_code == "TLS_VERIFICATION_FAILED"
        assert u_tls.last_failure_code == "TLS_VERIFICATION_FAILED"

        # Explicit robots disallow persists as ROBOTS_BLOCKED
        assert u_disallow.status == ScanURLStatus.FAILED.value
        assert u_disallow.last_error_code == "ROBOTS_BLOCKED"
        assert u_disallow.last_failure_code == "ROBOTS_BLOCKED"

        # Timeout/transport maps to ROBOTS_FETCH_ERROR
        assert u_timeout.status == ScanURLStatus.FAILED.value
        assert u_timeout.last_error_code == "ROBOTS_FETCH_ERROR"
        assert u_timeout.last_failure_code == "GENERIC_TIMEOUT"


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
    service_any._scan_job_repo.get_job_for_update = AsyncMock()
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


def test_derive_diagnostic_message_page_level_fallback() -> None:
    """Verify top-level absent error_message falls back to first page-level failure info."""
    from email_discovery_api.services.result_persistence import (
        _derive_diagnostic_message,  # pyright: ignore[reportPrivateUsage]
    )

    # 1. Page with fetch_result error_message
    robots_ok = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.ALLOWED,
        crawl_delay=None,
        reason="OK",
    )
    page_fail = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=500,
        robots_decision=robots_ok,
        fetch_result=FetchResult(
            final_url="https://example.com",
            status_code=500,
            content_type="text/html",
            body_text="",
            redirect_history=(),
            outcome=FetchOutcomeCode.HTTP_ERROR,
            error_message="Internal Server Error on backend",
        ),
        emails_found_count=0,
        links_discovered_count=0,
    )
    site_res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_sample_site_scan_result().statistics,
        page_records=(page_fail,),
        email_findings=(),
        rejected_email_candidates=(),
        error_message=None,
    )
    derived = _derive_diagnostic_message(site_res)
    assert derived == "Internal Server Error on backend"

    # 2. Page with robots_decision reason
    robots_denied = RobotsDecision(
        target_url="https://example.com/admin",
        decision=RobotsDecisionCode.DISALLOWED,
        crawl_delay=None,
        reason="Disallowed by User-agent rule in robots.txt",
    )
    page_robots = PageScanRecord(
        requested_url="https://example.com/admin",
        final_url="https://example.com/admin",
        depth=0,
        outcome=PageScanOutcome.ROBOTS_DISALLOWED,
        status_code=None,
        robots_decision=robots_denied,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    site_res_robots = SiteScanResult(
        starting_url="https://example.com/admin",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_sample_site_scan_result().statistics,
        page_records=(page_robots,),
        email_findings=(),
        rejected_email_candidates=(),
        error_message=None,
    )
    derived_robots = _derive_diagnostic_message(site_res_robots)
    assert derived_robots == "Disallowed by User-agent rule in robots.txt"


def test_sanitize_diagnostic_message_removes_secrets_urls_and_stacktraces() -> None:
    """Verify unsafe query params, secrets, authorization headers, and stack traces are stripped."""
    from email_discovery_api.services.result_persistence import (
        _sanitize_diagnostic_message,  # pyright: ignore[reportPrivateUsage]
    )

    raw_unsafe = (
        "Failed fetching https://api.example.com/data?token=secret123&key=xyz#frag with "
        "Authorization: Bearer secret_access_token_abc and Cookie: session=12345. "
        "Traceback (most recent call last):\n  File 'foo.py', line 12\n"
    )
    sanitized = _sanitize_diagnostic_message(raw_unsafe)
    assert sanitized is not None
    assert "token=secret123" not in sanitized
    assert "https://api.example.com/data?token=" not in sanitized
    assert "https://api.example.com/data" in sanitized
    assert "secret_access_token_abc" not in sanitized
    assert "session=12345" not in sanitized
    assert "[REDACTED]" in sanitized
    assert "Traceback" not in sanitized
    assert "File 'foo.py'" not in sanitized


def test_derive_diagnostic_message_robots_temporary_failure_priority() -> None:
    """Verify ROBOTS_TEMPORARY_FAILURE prefers robots_decision.reason over error_message."""
    from email_discovery_api.services.result_persistence import (
        _derive_diagnostic_message,  # pyright: ignore[reportPrivateUsage]
    )

    robots_temp = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.TEMPORARY_FAILURE,
        crawl_delay=None,
        reason="robots.txt fetch timeout after 5.0s for https://example.com/robots.txt?auth=secret123",
    )
    page_record = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        status_code=None,
        robots_decision=robots_temp,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
        error_message="Generic page processing failure",
    )
    site_res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_sample_site_scan_result().statistics,
        page_records=(page_record,),
        email_findings=(),
        rejected_email_candidates=(),
        error_message=None,
    )
    derived = _derive_diagnostic_message(site_res)
    assert derived is not None
    assert "robots.txt fetch timeout after 5.0s" in derived
    assert "auth=secret123" not in derived
    assert "Generic page processing failure" not in derived


def test_derive_diagnostic_message_fetch_failure_priority() -> None:
    """Verify fetch failure page prefers fetch_result.error_message over page
    error_message."""
    from email_discovery_api.services.result_persistence import (
        _derive_diagnostic_message,  # pyright: ignore[reportPrivateUsage]
    )

    robots_ok = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.ALLOWED,
        crawl_delay=None,
        reason="OK",
    )
    page_record = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=502,
        robots_decision=robots_ok,
        fetch_result=FetchResult(
            final_url="https://example.com",
            status_code=502,
            content_type="text/html",
            body_text="",
            redirect_history=(),
            outcome=FetchOutcomeCode.HTTP_ERROR,
            error_message="Bad Gateway from upstream proxy at https://backend.internal/api?key=secret_pass",
        ),
        emails_found_count=0,
        links_discovered_count=0,
        error_message="Generic page failure text",
    )
    site_res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_sample_site_scan_result().statistics,
        page_records=(page_record,),
        email_findings=(),
        rejected_email_candidates=(),
        error_message=None,
    )
    derived = _derive_diagnostic_message(site_res)
    assert derived is not None
    assert "Bad Gateway from upstream proxy" in derived
    assert "key=secret_pass" not in derived
    assert "Generic page failure text" not in derived


@pytest.mark.anyio
async def test_persist_fenced_result_passes_derived_sanitized_message_to_scan_url() -> None:
    """Verify persist_fenced_result passes derived message to ScanURL update query."""
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
        outcome="FAILED",
        retryable=False,
        requested_url="https://example.com",
        result_checksum="b" * 64,
    )
    service_any._attempt_repo.create = AsyncMock(return_value=created_attempt)
    service_any._page_repo.create_many = AsyncMock(return_value={})
    service_any._finding_repo.upsert_findings = AsyncMock(return_value=([], []))
    service_any._evidence_repo.add_evidence = AsyncMock(return_value=[])
    service_any._rejected_repo.add_rejected_candidates = AsyncMock(return_value=[])

    robots_ok = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.ALLOWED,
        crawl_delay=None,
        reason="OK",
    )
    page_fail = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=500,
        robots_decision=robots_ok,
        fetch_result=FetchResult(
            final_url="https://example.com",
            status_code=500,
            content_type="text/html",
            body_text="",
            redirect_history=(),
            outcome=FetchOutcomeCode.HTTP_ERROR,
            error_message="Database connection failed with password=supersecret123 at https://db.internal/query?api_key=privkey#hash",
        ),
        emails_found_count=0,
        links_discovered_count=0,
        error_message="Generic page failed",
    )
    site_result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_sample_site_scan_result().statistics,
        page_records=(page_fail,),
        email_findings=(),
        rejected_email_candidates=(),
        error_message=None,
    )

    await service.persist_fenced_result(claim, site_result)

    executed_statements = [call.args[0] for call in mock_session.execute.call_args_list]
    assert len(executed_statements) >= 2

    update_params = executed_statements[-1].compile().params
    assert "last_error_message" in update_params
    persisted_msg = update_params["last_error_message"]

    assert persisted_msg is not None
    assert "Database connection failed with" in persisted_msg
    assert "supersecret123" not in persisted_msg
    assert "password=[REDACTED]" in persisted_msg
    assert "api_key=privkey" not in persisted_msg
    assert "https://db.internal/query" in persisted_msg
    assert "Generic page failed" not in persisted_msg


@pytest.mark.anyio
async def test_successful_result_persistence_clean_diagnostics(
    isolated_db_engine: Any, test_user_and_token: dict[str, Any]
) -> None:
    """Verify COMPLETED_NO_EMAILS and COMPLETED store clean success diagnostics."""
    from email_scanner.models import SiteScanDiagnostics

    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()
    url_id_1 = uuid.uuid4()
    url_id_2 = uuid.uuid4()
    url_id_3 = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=3,
                valid_input_count=3,
                queued_count=0,
                running_count=3,
                completed_count=0,
                failed_count=0,
            )
            url1 = ScanURL(
                id=url_id_1,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://site1.com",
                normalized_url="https://site1.com/",
                normalized_domain="site1.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            url2 = ScanURL(
                id=url_id_2,
                scan_job_id=job_id,
                original_index=1,
                original_input="https://site2.com",
                normalized_url="https://site2.com/",
                normalized_domain="site2.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            url3 = ScanURL(
                id=url_id_3,
                scan_job_id=job_id,
                original_index=2,
                original_input="https://site3.com",
                normalized_url="https://site3.com/",
                normalized_domain="site3.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([job, url1, url2, url3])

    diag_stale = SiteScanDiagnostics(
        total_duration_seconds=1.0,
        dns_resolution_duration_seconds=0.1,
        gate_wait_duration_seconds=0.0,
        robots_fetch_duration_seconds=0.1,
        robots_evaluation_duration_seconds=0.1,
        http_fetch_duration_seconds=0.5,
        page_processing_duration_seconds=0.2,
        retry_count=2,
        total_retry_delay_seconds=0.5,
        redirect_count=0,
        http_status=200,
        failure_code="UNEXPECTED_INTERNAL_ERROR",
        time_budget_exhausted=False,
        cancellation_occurred=False,
        retry_budget_exhausted=False,
    )

    page_rec_200 = PageScanRecord(
        requested_url="https://site1.com/",
        final_url="https://site1.com/",
        depth=0,
        outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
        status_code=200,
        robots_decision=RobotsDecision(
            target_url="https://site1.com/",
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="OK",
        ),
        fetch_result=FetchResult(
            final_url="https://site1.com/",
            status_code=200,
            content_type="text/html",
            body_text="<html>no email here</html>",
            redirect_history=(),
            outcome=FetchOutcomeCode.SUCCESS,
        ),
        emails_found_count=0,
        links_discovered_count=0,
    )

    claim1 = URLClaim(
        scan_url_id=url_id_1,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://site1.com",
        normalized_url="https://site1.com/",
        normalized_domain="site1.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    result_no_emails = SiteScanResult(
        starting_url="https://site1.com/",
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
            elapsed_seconds=1.0,
            stop_reason="QUEUE_EXHAUSTED",
        ),
        page_records=(page_rec_200,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=diag_stale,
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(claim1, result_no_emails)

    async with session_factory() as session:
        url_obj = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id_1))
        ).scalar_one()
        attempt_obj = (
            await session.execute(select(CrawlAttempt).where(CrawlAttempt.scan_url_id == url_id_1))
        ).scalar_one()

        assert url_obj.status == ScanURLStatus.NO_EMAIL.value
        assert url_obj.last_error_code is None
        assert url_obj.last_error_message is None
        assert url_obj.last_failure_code is None
        assert attempt_obj.failure_code is None

    # Test 2: COMPLETED outcome with findings
    claim2 = URLClaim(
        scan_url_id=url_id_2,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://site2.com",
        normalized_url="https://site2.com/",
        normalized_domain="site2.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    result_completed = make_sample_site_scan_result(
        starting_url="https://site2.com/",
        outcome=SiteScanOutcome.COMPLETED,
        emails=("found@site2.com",),
    )
    result_completed = SiteScanResult(
        starting_url=result_completed.starting_url,
        outcome=result_completed.outcome,
        statistics=result_completed.statistics,
        page_records=result_completed.page_records,
        email_findings=result_completed.email_findings,
        rejected_email_candidates=result_completed.rejected_email_candidates,
        diagnostics=diag_stale,
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(claim2, result_completed)

    async with session_factory() as session:
        url_obj2 = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id_2))
        ).scalar_one()
        attempt_obj2 = (
            await session.execute(select(CrawlAttempt).where(CrawlAttempt.scan_url_id == url_id_2))
        ).scalar_one()

        assert url_obj2.status == ScanURLStatus.COMPLETED.value
        assert url_obj2.last_error_code is None
        assert url_obj2.last_error_message is None
        assert url_obj2.last_failure_code is None
        assert attempt_obj2.failure_code is None

    # Test 3: PARTIAL outcome retains diagnostics
    claim3 = URLClaim(
        scan_url_id=url_id_3,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://site3.com",
        normalized_url="https://site3.com/",
        normalized_domain="site3.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    result_partial = SiteScanResult(
        starting_url="https://site3.com/",
        outcome=SiteScanOutcome.PARTIAL,
        statistics=SiteScanStatistics(
            pages_queued=2,
            pages_attempted=2,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=1,
            urls_discovered=2,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=1.0,
            stop_reason="MAX_PAGES_REACHED",
        ),
        page_records=(page_rec_200,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=diag_stale,
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(claim3, result_partial)

    async with session_factory() as session:
        url_obj3 = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id_3))
        ).scalar_one()
        attempt_obj3 = (
            await session.execute(select(CrawlAttempt).where(CrawlAttempt.scan_url_id == url_id_3))
        ).scalar_one()

        assert url_obj3.status == ScanURLStatus.COMPLETED.value
        assert url_obj3.last_error_code == "PARTIAL_SCAN"
        assert url_obj3.last_failure_code == "UNEXPECTED_INTERNAL_ERROR"
        assert attempt_obj3.failure_code == "UNEXPECTED_INTERNAL_ERROR"


@pytest.mark.anyio
async def test_persist_fenced_result_out_of_scope_redirect_typed_target(
    isolated_db_engine: Any, test_user_and_token: dict[str, Any]
) -> None:
    """Verify out-of-scope redirect persists destination domain/URL from typed target."""
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
                original_input="https://carefreeair.com",
                normalized_url="https://carefreeair.com/",
                normalized_domain="carefreeair.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                max_attempts=3,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([job, url])

    fetch_res = FetchResult(
        final_url="https://carefreeair.com/",
        status_code=301,
        content_type=None,
        body_text=None,
        redirect_history=(),
        outcome=FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT,
        error_message="Redirect rejected by scope policy",
        redirect_target_url="https://carefreeacandheating.com/landing?token=123#sec",
    )
    page_rec = PageScanRecord(
        requested_url="https://carefreeair.com/",
        final_url="https://carefreeair.com/",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=301,
        robots_decision=RobotsDecision(
            target_url="https://carefreeair.com/",
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="OK",
        ),
        fetch_result=fetch_res,
        emails_found_count=0,
        links_discovered_count=0,
    )
    scan_res = SiteScanResult(
        starting_url="https://carefreeair.com/",
        outcome=SiteScanOutcome.FAILED,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=0,
            pages_blocked_by_robots=0,
            pages_failed=1,
            urls_discovered=0,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=0.5,
            stop_reason="FAILED",
        ),
        page_records=(page_rec,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(failure_code="OUT_OF_SCOPE_REDIRECT"),
    )

    claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://carefreeair.com",
        normalized_url="https://carefreeair.com/",
        normalized_domain="carefreeair.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(claim, scan_res)

    async with session_factory() as session:
        persisted = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        ).scalar_one()
        assert persisted.status == ScanURLStatus.FAILED.value
        assert persisted.redirect_target_domain == "carefreeacandheating.com"
        assert persisted.redirect_target_url == "https://carefreeacandheating.com/landing"


@pytest.mark.anyio
async def test_persist_fenced_result_out_of_scope_missing_target_no_fallback(
    isolated_db_engine: Any, test_user_and_token: dict[str, Any]
) -> None:
    """Verify missing typed redirect_target_url leaves fields null without fallback."""
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
                original_input="https://carefreeair.com",
                normalized_url="https://carefreeair.com/",
                normalized_domain="carefreeair.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                max_attempts=3,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([job, url])

    fetch_res = FetchResult(
        final_url="https://carefreeair.com/",
        status_code=301,
        content_type=None,
        body_text=None,
        redirect_history=(),
        outcome=FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT,
        error_message="Redirect rejected by scope policy",
        redirect_target_url=None,
    )
    page_rec = PageScanRecord(
        requested_url="https://carefreeair.com/",
        final_url="https://carefreeair.com/",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=301,
        robots_decision=RobotsDecision(
            target_url="https://carefreeair.com/",
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="OK",
        ),
        fetch_result=fetch_res,
        emails_found_count=0,
        links_discovered_count=0,
    )
    scan_res = SiteScanResult(
        starting_url="https://carefreeair.com/",
        outcome=SiteScanOutcome.FAILED,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=0,
            pages_blocked_by_robots=0,
            pages_failed=1,
            urls_discovered=0,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=0.5,
            stop_reason="FAILED",
        ),
        page_records=(page_rec,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(failure_code="OUT_OF_SCOPE_REDIRECT"),
    )

    claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://carefreeair.com",
        normalized_url="https://carefreeair.com/",
        normalized_domain="carefreeair.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(claim, scan_res)

    async with session_factory() as session:
        persisted = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        ).scalar_one()
        assert persisted.status == ScanURLStatus.FAILED.value
        assert persisted.redirect_target_domain is None
        assert persisted.redirect_target_url is None


@pytest.mark.anyio
async def test_persist_fenced_result_same_domain_redirect_does_not_populate_pending_approval(
    isolated_db_engine: Any, test_user_and_token: dict[str, Any]
) -> None:
    """Verify same-domain redirects succeed and do not populate redirect_target fields."""
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
                original_input="https://example.com/start",
                normalized_url="https://example.com/start",
                normalized_domain="example.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="w1",
                fence_token=1,
                attempt_count=1,
                max_attempts=3,
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([job, url])

    fetch_res = FetchResult(
        final_url="https://example.com/finish",
        status_code=200,
        content_type="text/html",
        body_text="<html>OK</html>",
        redirect_history=(),
        outcome=FetchOutcomeCode.SUCCESS,
        redirect_target_url=None,
    )
    page_rec = PageScanRecord(
        requested_url="https://example.com/start",
        final_url="https://example.com/finish",
        depth=0,
        outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
        status_code=200,
        robots_decision=RobotsDecision(
            target_url="https://example.com/start",
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="OK",
        ),
        fetch_result=fetch_res,
        emails_found_count=0,
        links_discovered_count=0,
    )
    scan_res = SiteScanResult(
        starting_url="https://example.com/start",
        outcome=SiteScanOutcome.COMPLETED_NO_EMAILS,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=0,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=0.5,
            stop_reason="COMPLETED",
        ),
        page_records=(page_rec,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(),
    )

    claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://example.com/start",
        normalized_url="https://example.com/start",
        normalized_domain="example.com",
        lease_owner="w1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async with session_factory() as session:
        persistence = ResultPersistenceService(session)
        await persistence.persist_fenced_result(claim, scan_res)

    async with session_factory() as session:
        persisted = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        ).scalar_one()
        assert persisted.status == ScanURLStatus.NO_EMAIL.value
        assert persisted.redirect_target_domain is None
        assert persisted.redirect_target_url is None
