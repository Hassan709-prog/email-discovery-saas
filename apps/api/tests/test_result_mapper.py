"""Unit tests for deterministic scanner result mapper, URL privacy, and candidate masking."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from email_discovery_api.mappers.crawl_results import (
    map_site_scan_result,
    mask_email_candidate,
    sanitize_text,
    sanitize_url,
)
from email_scanner.errors import (
    EmailRejectionCode,
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
    RedirectHop,
    RobotsDecision,
    SiteScanResult,
    SiteScanStatistics,
)
from email_scanner.models import (
    EmailFinding as ScannerEmailFinding,
)
from email_scanner.models import (
    RejectedEmailCandidate as ScannerRejectedCandidate,
)


def test_sanitize_text_normalizes_whitespace_and_removes_control_chars() -> None:
    """Verify control characters and whitespace runs are sanitized and truncated."""
    raw = "Hello\r\n\t World!\x00\x1f   This is a   test.   "
    clean = sanitize_text(raw, max_length=20)
    assert clean == "Hello World! This is"


def test_sanitize_url_removes_userinfo_query_and_fragment() -> None:
    """Verify URL privacy rules strip sensitive userinfo, query strings, and fragments."""
    sensitive_url = (
        "HTTPS://user:password@Sub.Example.com:8080/path/to/page?token=secret123#section2"
    )
    clean_url = sanitize_url(sensitive_url)
    assert clean_url == "https://sub.example.com:8080/path/to/page"

    standard_https = "https://example.com:443/about?query=param#top"
    assert sanitize_url(standard_https) == "https://example.com/about"


def test_mask_email_candidate_privacy_formatting() -> None:
    """Verify candidate email addresses are masked without exposing raw local parts."""
    assert mask_email_candidate("john.doe@company.com") == "j***e@company.com"
    assert mask_email_candidate("abc@company.com") == "a*c@company.com"
    assert mask_email_candidate("ab@company.com") == "a*@company.com"
    assert mask_email_candidate("a@company.com") == "*@company.com"
    assert mask_email_candidate("invalid-no-at-sign") == "***"
    assert mask_email_candidate(None) is None


def test_result_mapper_deterministic_checksum_and_ordering() -> None:
    """Verify identical scanner output produces identical result_checksum and DTO ordering."""
    now = datetime.now(UTC)
    result = SiteScanResult(
        starting_url="https://user:secret@example.com/start?token=xyz#top",
        outcome=SiteScanOutcome.COMPLETED,
        statistics=SiteScanStatistics(
            pages_queued=2,
            pages_attempted=2,
            pages_fetched=2,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=5,
            accepted_email_findings=2,
            rejected_email_candidates=1,
            elapsed_seconds=3.5,
            stop_reason="COMPLETED",
        ),
        page_records=(
            PageScanRecord(
                requested_url="https://example.com/start?token=xyz",
                final_url="https://example.com/start",
                depth=0,
                outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
                status_code=200,
                robots_decision=RobotsDecision(
                    target_url="https://example.com/start",
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="OK",
                ),
                fetch_result=FetchResult(
                    final_url="https://example.com/start",
                    status_code=200,
                    content_type="text/html; charset=utf-8",
                    body_text="<html>contact info</html>",
                    redirect_history=(
                        RedirectHop(
                            url="http://example.com/start?token=xyz",
                            status_code=301,
                            location="https://example.com/start?token=xyz",
                        ),
                    ),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                emails_found_count=2,
                links_discovered_count=1,
            ),
        ),
        email_findings=(
            ScannerEmailFinding(
                source_url="https://example.com/start",
                raw_candidate="sales@example.com",
                canonical_email="sales@example.com",
                local_part="sales",
                domain="example.com",
                source_kind=EmailSourceKind.VISIBLE_TEXT,
                category=EmailCategory.ROLE_BASED,
                domain_affinity=DomainAffinity.EXACT_HOST,
                evidence_snippet="Contact us at sales@example.com",
            ),
            ScannerEmailFinding(
                source_url="https://example.com/start",
                raw_candidate="john.doe@example.com",
                canonical_email="john.doe@example.com",
                local_part="john.doe",
                domain="example.com",
                source_kind=EmailSourceKind.MAILTO,
                category=EmailCategory.PERSONAL_OR_NAMED,
                domain_affinity=DomainAffinity.EXACT_HOST,
                evidence_snippet="Email john.doe@example.com directly",
            ),
        ),
        rejected_email_candidates=(
            ScannerRejectedCandidate(
                source_url="https://example.com/start",
                raw_candidate="test@dummy.com",
                rejection_code=EmailRejectionCode.DUMMY_TEST_ADDRESS,
                reason="Dummy candidate",
                source_kind=EmailSourceKind.VISIBLE_TEXT,
                evidence_snippet="test@dummy.com",
            ),
        ),
    )

    attempt1, _pages1, findings1, _evidence1, rejected1 = map_site_scan_result(
        result, attempt_number=1, now=now
    )
    attempt2, _pages2, _findings2, _evidence2, _rejected2 = map_site_scan_result(
        result, attempt_number=1, now=now
    )

    # Checksum equality
    assert attempt1.result_checksum == attempt2.result_checksum
    assert len(attempt1.result_checksum) == 64

    # Verification of URL privacy in DTOs
    assert attempt1.requested_url == "https://example.com/start"
    assert attempt1.redirect_history == [
        {
            "url": "http://example.com/start",
            "status_code": 301,
            "location": "https://example.com/start",
        }
    ]

    # Findings sorted deterministically by canonical_email
    assert [f.canonical_email for f in findings1] == ["john.doe@example.com", "sales@example.com"]

    # Rejected candidate masked
    assert rejected1[0].masked_candidate == "t***t@dummy.com"
    assert "test@dummy.com" not in repr(rejected1[0])


def test_no_html_body_in_mapped_dtos() -> None:
    """Verify HTML body is completely absent from mapped persistence DTOs."""
    now = datetime.now(UTC)
    result = SiteScanResult(
        starting_url="https://example.com",
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
            elapsed_seconds=1.0,
            stop_reason="COMPLETED",
        ),
        page_records=(
            PageScanRecord(
                requested_url="https://example.com",
                final_url="https://example.com",
                depth=0,
                outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
                status_code=200,
                robots_decision=RobotsDecision(
                    target_url="https://example.com",
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="OK",
                ),
                fetch_result=FetchResult(
                    final_url="https://example.com",
                    status_code=200,
                    content_type="text/html",
                    body_text="<html>secret html body payload</html>",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                emails_found_count=0,
                links_discovered_count=0,
            ),
        ),
        email_findings=(),
        rejected_email_candidates=(),
    )

    _attempt, pages, _, _, _ = map_site_scan_result(result, attempt_number=1, now=now)
    assert not hasattr(pages[0], "body_text")
    assert not hasattr(pages[0], "html")
    assert pages[0].content_sha256 is None


def test_result_mapper_deduplicates_page_records_and_retains_evidence() -> None:
    """Verify duplicate page URLs are deduplicated while preserving evidence."""
    now = datetime.now(UTC)
    duplicate_url_1 = "https://pypi.org/account/login/"
    duplicate_url_2 = "https://pypi.org/account/login/?next=/account/"

    result = SiteScanResult(
        starting_url="https://pypi.org/",
        outcome=SiteScanOutcome.COMPLETED,
        statistics=SiteScanStatistics(
            pages_queued=2,
            pages_attempted=2,
            pages_fetched=2,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=2,
            accepted_email_findings=1,
            rejected_email_candidates=0,
            elapsed_seconds=1.0,
            stop_reason="COMPLETED",
        ),
        page_records=(
            PageScanRecord(
                requested_url=duplicate_url_1,
                final_url=duplicate_url_1,
                depth=1,
                outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
                status_code=200,
                robots_decision=RobotsDecision(
                    target_url=duplicate_url_1,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="OK",
                ),
                fetch_result=FetchResult(
                    final_url=duplicate_url_1,
                    status_code=200,
                    content_type="text/html",
                    body_text="<html>login</html>",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                emails_found_count=1,
                links_discovered_count=0,
            ),
            PageScanRecord(
                requested_url=duplicate_url_2,
                final_url=duplicate_url_2,
                depth=2,
                outcome=PageScanOutcome.SKIPPED_BUDGET_REACHED,
                status_code=None,
                robots_decision=RobotsDecision(
                    target_url=duplicate_url_2,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="OK",
                ),
                fetch_result=None,
                emails_found_count=0,
                links_discovered_count=0,
            ),
        ),
        email_findings=(
            ScannerEmailFinding(
                source_url=duplicate_url_1,
                raw_candidate="admin@pypi.org",
                canonical_email="admin@pypi.org",
                local_part="admin",
                domain="pypi.org",
                source_kind=EmailSourceKind.VISIBLE_TEXT,
                category=EmailCategory.ROLE_BASED,
                domain_affinity=DomainAffinity.EXACT_HOST,
                evidence_snippet="Contact admin@pypi.org for help",
            ),
        ),
        rejected_email_candidates=(),
    )

    attempt1, pages1, findings1, evidence1, _ = map_site_scan_result(
        result, attempt_number=1, now=now
    )
    attempt2, pages2, findings2, _, _ = map_site_scan_result(result, attempt_number=1, now=now)

    # 1. Exactly one stored page row per unique normalized URL
    assert len(pages1) == 1
    assert pages1[0].normalized_url == "https://pypi.org/account/login/"

    # 2. Email finding and evidence reference the correct retained page and are NOT discarded
    assert len(findings1) == 1
    assert findings1[0].canonical_email == "admin@pypi.org"
    assert len(evidence1) == 1
    assert evidence1[0].normalized_page_url == "https://pypi.org/account/login/"
    assert evidence1[0].canonical_email == "admin@pypi.org"

    # 3. Deterministic checksum and replay behavior remain identical
    assert attempt1.result_checksum == attempt2.result_checksum
    assert len(pages1) == len(pages2)
    assert len(findings1) == len(findings2)


def test_mapper_enforces_none_failure_code_for_successful_outcomes() -> None:
    """Verify API mapper sets failure_code=None for COMPLETED and COMPLETED_NO_EMAILS."""
    from email_scanner.models import SiteScanDiagnostics

    now = datetime.now(UTC)
    diag_with_failure = SiteScanDiagnostics(
        total_duration_seconds=1.0,
        dns_resolution_duration_seconds=0.1,
        gate_wait_duration_seconds=0.0,
        robots_fetch_duration_seconds=0.1,
        robots_evaluation_duration_seconds=0.1,
        http_fetch_duration_seconds=0.5,
        page_processing_duration_seconds=0.2,
        retry_count=1,
        total_retry_delay_seconds=0.5,
        redirect_count=0,
        http_status=200,
        failure_code="UNEXPECTED_INTERNAL_ERROR",
        time_budget_exhausted=False,
        cancellation_occurred=False,
        retry_budget_exhausted=False,
    )

    res_completed_no_emails = SiteScanResult(
        starting_url="https://example.com",
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
            stop_reason="COMPLETED_NO_EMAILS",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=diag_with_failure,
    )

    attempt_no_emails, _, _, _, _ = map_site_scan_result(
        res_completed_no_emails, attempt_number=1, now=now
    )
    assert attempt_no_emails.failure_code is None

    res_completed = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.COMPLETED,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=1,
            rejected_email_candidates=0,
            elapsed_seconds=1.0,
            stop_reason="COMPLETED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=diag_with_failure,
    )

    attempt_completed, _, _, _, _ = map_site_scan_result(res_completed, attempt_number=1, now=now)
    assert attempt_completed.failure_code is None

    # PARTIAL outcome should retain failure code
    res_partial = SiteScanResult(
        starting_url="https://example.com",
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
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=diag_with_failure,
    )

    attempt_partial, _, _, _, _ = map_site_scan_result(res_partial, attempt_number=1, now=now)
    assert attempt_partial.failure_code == "UNEXPECTED_INTERNAL_ERROR"


def test_map_site_scan_result_preserves_and_sanitizes_redirect_target_url() -> None:
    """Verify mapper preserves redirect_target_url and strips query/fragment via sanitize_url."""
    now = datetime.now(UTC)
    fetch_res = FetchResult(
        final_url="https://carefreeair.com/start",
        status_code=301,
        content_type=None,
        body_text=None,
        redirect_history=(),
        outcome=FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT,
        error_message="Redirect rejected by scope policy",
        redirect_target_url="https://carefreeacandheating.com/landing?token=secret123&foo=bar#section1",
    )
    page_rec = PageScanRecord(
        requested_url="https://carefreeair.com/start",
        final_url="https://carefreeair.com/start",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=301,
        robots_decision=RobotsDecision(
            target_url="https://carefreeair.com/start",
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="OK",
        ),
        fetch_result=fetch_res,
        emails_found_count=0,
        links_discovered_count=0,
    )
    scan_res = SiteScanResult(
        starting_url="https://carefreeair.com/start",
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
    )

    attempt, _, _, _, _ = map_site_scan_result(scan_res, attempt_number=1, now=now)
    assert attempt.final_url == "https://carefreeair.com/start"
    assert attempt.redirect_target_url == "https://carefreeacandheating.com/landing"


def test_map_site_scan_result_none_redirect_target_url() -> None:
    """Verify mapper leaves redirect_target_url None when fetch_result has no target."""
    now = datetime.now(UTC)
    fetch_res = FetchResult(
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html",
        body_text="<html>content</html>",
        redirect_history=(),
        outcome=FetchOutcomeCode.SUCCESS,
        redirect_target_url=None,
    )
    page_rec = PageScanRecord(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        depth=0,
        outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
        status_code=200,
        robots_decision=RobotsDecision(
            target_url="https://example.com/",
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="OK",
        ),
        fetch_result=fetch_res,
        emails_found_count=0,
        links_discovered_count=0,
    )
    scan_res = SiteScanResult(
        starting_url="https://example.com/",
        outcome=SiteScanOutcome.COMPLETED,
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
    )

    attempt, _, _, _, _ = map_site_scan_result(scan_res, attempt_number=1, now=now)
    assert attempt.redirect_target_url is None


def test_map_site_scan_result_execution_interval_12_5_seconds() -> None:
    """Verify a 12.5-second result creates a 12.5-second timestamp interval."""
    now = datetime(2026, 9, 5, 12, 0, 15, tzinfo=UTC)
    scan_res = SiteScanResult(
        starting_url="https://example.com/",
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
            elapsed_seconds=12.5,
            stop_reason="COMPLETED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    attempt, _, _, _, _ = map_site_scan_result(scan_res, attempt_number=1, now=now)
    assert attempt.completed_at is not None
    assert attempt.completed_at == now
    assert attempt.started_at == now - timedelta(seconds=12.5)
    assert (attempt.completed_at - attempt.started_at).total_seconds() == 12.5
    assert attempt.elapsed_seconds == 12.5


def test_map_site_scan_result_execution_interval_zero_seconds() -> None:
    """Verify zero duration remains valid and creates identical start and completion timestamps."""
    now = datetime(2026, 9, 5, 12, 0, 15, tzinfo=UTC)
    scan_res = SiteScanResult(
        starting_url="https://example.com/",
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
            elapsed_seconds=0.0,
            stop_reason="COMPLETED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    attempt, _, _, _, _ = map_site_scan_result(scan_res, attempt_number=1, now=now)
    assert attempt.completed_at is not None
    assert attempt.completed_at == now
    assert attempt.started_at == now
    assert (attempt.completed_at - attempt.started_at).total_seconds() == 0.0
    assert attempt.elapsed_seconds == 0.0


def test_map_site_scan_result_preserves_timezone_awareness() -> None:
    """Verify timezone-aware datetime instances preserve their exact timezone awareness."""
    custom_tz = timezone(timedelta(hours=-5))
    now = datetime(2026, 9, 5, 8, 30, 0, tzinfo=custom_tz)
    scan_res = SiteScanResult(
        starting_url="https://example.com/",
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
            elapsed_seconds=7.25,
            stop_reason="COMPLETED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    attempt, _, _, _, _ = map_site_scan_result(scan_res, attempt_number=1, now=now)
    assert attempt.completed_at is not None
    assert attempt.completed_at.tzinfo == custom_tz
    assert attempt.started_at.tzinfo == custom_tz
    assert attempt.completed_at == now
    assert attempt.started_at == now - timedelta(seconds=7.25)
    assert (attempt.completed_at - attempt.started_at).total_seconds() == 7.25


@pytest.mark.parametrize(
    "invalid_elapsed",
    [
        -5.0,
        -0.001,
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        True,
        False,
        "invalid_str",
        1e308,
    ],
)
def test_map_site_scan_result_defensive_invalid_elapsed_values(invalid_elapsed: Any) -> None:
    """Verify invalid elapsed values do not produce future timestamps or unhandled errors."""
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)

    class FakeStats:
        elapsed_seconds = invalid_elapsed

    scan_res = SiteScanResult(
        starting_url="https://example.com/",
        outcome=SiteScanOutcome.FAILED,
        statistics=FakeStats(),  # type: ignore[arg-type]
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    attempt, _, _, _, _ = map_site_scan_result(scan_res, attempt_number=1, now=now)
    assert attempt.completed_at is not None
    assert attempt.completed_at == now
    assert attempt.started_at == now
    assert attempt.started_at == attempt.completed_at
    assert attempt.elapsed_seconds == 0.0


def test_map_site_scan_result_defensive_missing_statistics() -> None:
    """Verify missing statistics produces valid non-future timestamps."""
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
    scan_res = SiteScanResult(
        starting_url="https://example.com/",
        outcome=SiteScanOutcome.FAILED,
        statistics=None,  # type: ignore[arg-type]
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    attempt, _, _, _, _ = map_site_scan_result(scan_res, attempt_number=1, now=now)
    assert attempt.completed_at is not None
    assert attempt.completed_at == now
    assert attempt.started_at == now
    assert attempt.started_at == attempt.completed_at
    assert attempt.elapsed_seconds == 0.0
