"""Unit tests for ResultPersistenceService, locking, replay idempotency, and counters."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.mappers.crawl_results import map_site_scan_result
from email_discovery_api.models.crawl_attempt import CrawlAttempt
from email_discovery_api.models.email_evidence import EmailEvidence
from email_discovery_api.models.email_finding import EmailFinding
from email_discovery_api.models.enums import ScanURLStatus
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.result_policies import ResultPersistencePolicy
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


@pytest.mark.anyio
async def test_persist_site_scan_result_success_first_delivery() -> None:
    """Verify first delivery locks ScanURL, updates SCANNING -> COMPLETED, and appends event."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    scan_url_id = uuid.uuid4()

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    service = ResultPersistenceService(session=mock_session)
    service_any: Any = service

    # Mock repositories
    service_any._scan_url_repo.get_url_for_update = AsyncMock(
        return_value=ScanURL(
            id=scan_url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            normalized_url="https://example.com",
            status=ScanURLStatus.SCANNING.value,
        )
    )
    service_any._scan_url_repo.update_status_conditional = AsyncMock(return_value=True)
    service_any._attempt_repo.get_by_scan_url_and_attempt = AsyncMock(return_value=None)

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

    service_any._evidence_repo.add_evidence = AsyncMock(
        return_value=[
            EmailEvidence(
                id=uuid.uuid4(),
                email_finding_id=finding_id,
                crawled_page_id=uuid.uuid4(),
                source_type="VISIBLE_TEXT",
                page_url="https://example.com",
                candidate_hash="b" * 64,
            )
        ]
    )
    service_any._rejected_repo.add_rejected_candidates = AsyncMock(return_value=[])

    service_any._scan_job_repo.increment_completed_urls = AsyncMock()
    service_any._scan_job_repo.increment_email_findings = AsyncMock()
    service_any._scan_job_repo.allocate_event_sequence = AsyncMock(return_value=1)
    service_any._event_repo.append_event = MagicMock()

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
    service_any._scan_url_repo.update_status_conditional.assert_called_once_with(
        organization_id=org_id,
        job_id=job_id,
        scan_url_id=scan_url_id,
        expected_status="SCANNING",
        new_status="COMPLETED",
    )
    service_any._scan_job_repo.increment_completed_urls.assert_called_once_with(
        org_id, job_id, delta=1
    )
    service_any._scan_job_repo.increment_email_findings.assert_called_once_with(
        org_id, job_id, delta=1
    )
    service_any._event_repo.append_event.assert_called_once()


@pytest.mark.anyio
async def test_persist_site_scan_result_idempotent_replay() -> None:
    """Verify matching attempt replay returns is_replay=True without side effects."""
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

    mapped_att, _, _, _, _ = map_site_scan_result(
        sample_result, attempt_number=1, now=datetime.now(UTC)
    )

    existing_attempt = CrawlAttempt(
        id=uuid.uuid4(),
        scan_url_id=scan_url_id,
        attempt_number=1,
        outcome="COMPLETED",
        retryable=False,
        requested_url="https://example.com",
        result_checksum=mapped_att.result_checksum,
    )

    service_any._scan_url_repo.get_url_for_update = AsyncMock(
        return_value=ScanURL(
            id=scan_url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.COMPLETED.value,
        )
    )
    service_any._attempt_repo.get_by_scan_url_and_attempt = AsyncMock(return_value=existing_attempt)

    service_any._attempt_repo.create = AsyncMock()
    service_any._page_repo.create_many = AsyncMock()
    service_any._scan_job_repo.increment_completed_urls = AsyncMock()
    service_any._scan_job_repo.allocate_event_sequence = AsyncMock()

    result_container = await service.persist_site_scan_result(
        organization_id=org_id,
        job_id=job_id,
        scan_url_id=scan_url_id,
        attempt_number=1,
        site_scan_result=sample_result,
    )

    assert result_container.is_replay is True
    assert result_container.attempt == existing_attempt
    service_any._attempt_repo.create.assert_not_called()
    service_any._page_repo.create_many.assert_not_called()
    service_any._scan_job_repo.increment_completed_urls.assert_not_called()
    service_any._scan_job_repo.allocate_event_sequence.assert_not_called()


@pytest.mark.anyio
async def test_persist_site_scan_result_conflict_replay_raises_error() -> None:
    """Verify replaying attempt number with mismatched result_checksum raises RESULT_CONFLICT."""
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

    existing_attempt = CrawlAttempt(
        id=uuid.uuid4(),
        scan_url_id=scan_url_id,
        attempt_number=1,
        outcome="COMPLETED",
        retryable=False,
        requested_url="https://example.com",
        result_checksum="c" * 64,
    )

    service_any._scan_url_repo.get_url_for_update = AsyncMock(
        return_value=ScanURL(
            id=scan_url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.COMPLETED.value,
        )
    )
    service_any._attempt_repo.get_by_scan_url_and_attempt = AsyncMock(return_value=existing_attempt)

    with pytest.raises(ServiceError) as exc_info:
        await service.persist_site_scan_result(
            organization_id=org_id,
            job_id=job_id,
            scan_url_id=scan_url_id,
            attempt_number=1,
            site_scan_result=sample_result,
        )

    assert exc_info.value.code == ServiceErrorCode.RESULT_CONFLICT


@pytest.mark.anyio
async def test_persist_site_scan_result_requires_scanning_state() -> None:
    """Verify attempting result persistence on a PENDING ScanURL raises INVALID_RESULT_STATE."""
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

    service_any._scan_url_repo.get_url_for_update = AsyncMock(
        return_value=ScanURL(
            id=scan_url_id,
            scan_job_id=job_id,
            original_index=0,
            original_input="https://example.com",
            status=ScanURLStatus.PENDING.value,
        )
    )
    service_any._attempt_repo.get_by_scan_url_and_attempt = AsyncMock(return_value=None)

    with pytest.raises(ServiceError) as exc_info:
        await service.persist_site_scan_result(
            organization_id=org_id,
            job_id=job_id,
            scan_url_id=scan_url_id,
            attempt_number=1,
            site_scan_result=sample_result,
        )

    assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE


@pytest.mark.anyio
async def test_persist_site_scan_result_too_large_raises_error() -> None:
    """Verify results exceeding policy bounds raise RESULT_TOO_LARGE before DB interaction."""
    mock_session = AsyncMock(spec=AsyncSession)
    strict_policy = ResultPersistencePolicy(max_pages_per_result=1)
    service = ResultPersistenceService(session=mock_session, policy=strict_policy)

    sample_result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.COMPLETED,
        statistics=SiteScanStatistics(
            pages_queued=2,
            pages_attempted=2,
            pages_fetched=2,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=0,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=1.0,
            stop_reason="COMPLETED",
        ),
        page_records=(
            PageScanRecord(
                requested_url="https://example.com/1",
                final_url="https://example.com/1",
                depth=0,
                outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
                status_code=200,
                robots_decision=RobotsDecision(
                    target_url="https://example.com/1",
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="OK",
                ),
                fetch_result=None,
                emails_found_count=0,
                links_discovered_count=0,
            ),
            PageScanRecord(
                requested_url="https://example.com/2",
                final_url="https://example.com/2",
                depth=1,
                outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
                status_code=200,
                robots_decision=RobotsDecision(
                    target_url="https://example.com/2",
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="OK",
                ),
                fetch_result=None,
                emails_found_count=0,
                links_discovered_count=0,
            ),
        ),
        email_findings=(),
        rejected_email_candidates=(),
    )

    with pytest.raises(ServiceError) as exc_info:
        await service.persist_site_scan_result(
            organization_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            scan_url_id=uuid.uuid4(),
            attempt_number=1,
            site_scan_result=sample_result,
        )

    assert exc_info.value.code == ServiceErrorCode.RESULT_TOO_LARGE
