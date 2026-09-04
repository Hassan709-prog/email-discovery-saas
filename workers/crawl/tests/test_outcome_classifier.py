"""Unit tests for outcome classification matrix."""

import uuid

from email_discovery_api.services.worker_contracts import LeaseLostError
from email_discovery_crawl_worker.outcome_classifier import (
    WorkerExecutionOutcome,
    classify_error_code_and_retryability,
    classify_worker_outcome,
)
from email_scanner import FetchOutcomeCode, PageScanOutcome, RobotsDecisionCode
from email_scanner.errors import SiteScanOutcome
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailFinding,
    EmailSourceKind,
    FetchAttempt,
    FetchResult,
    PageScanRecord,
    RobotsDecision,
    SiteScanDiagnostics,
    SiteScanResult,
    SiteScanStatistics,
)


def make_dummy_stats(pages_fetched: int = 1) -> SiteScanStatistics:
    """Helper to create dummy SiteScanStatistics."""
    return SiteScanStatistics(
        pages_queued=1,
        pages_attempted=1,
        pages_fetched=pages_fetched,
        pages_blocked_by_robots=0,
        pages_failed=0,
        urls_discovered=1,
        accepted_email_findings=0,
        rejected_email_candidates=0,
        elapsed_seconds=0.1,
        stop_reason="QUEUE_EXHAUSTED",
    )


def test_classify_completed_with_findings() -> None:
    """Verify COMPLETED with findings maps to TERMINAL_SUCCESS."""
    finding = EmailFinding(
        raw_candidate="test@example.com",
        canonical_email="test@example.com",
        local_part="test",
        domain="example.com",
        category=EmailCategory.PERSONAL_OR_NAMED,
        domain_affinity=DomainAffinity.EXACT_HOST,
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        source_url="https://example.com",
        evidence_snippet="Contact: test@example.com",
    )
    res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.COMPLETED,
        statistics=make_dummy_stats(1),
        page_records=(),
        email_findings=(finding,),
        rejected_email_candidates=(),
    )
    outcome = classify_worker_outcome(res, None, attempt_count=1)
    assert outcome == WorkerExecutionOutcome.TERMINAL_SUCCESS


def test_classify_completed_no_findings() -> None:
    """Verify COMPLETED without findings maps to TERMINAL_NO_EMAIL."""
    res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.COMPLETED,
        statistics=make_dummy_stats(1),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )
    outcome = classify_worker_outcome(res, None, attempt_count=1)
    assert outcome == WorkerExecutionOutcome.TERMINAL_NO_EMAIL


def test_classify_robots_blocked() -> None:
    """Verify ROBOTS_BLOCKED maps to TERMINAL_FAILURE."""
    res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )
    outcome = classify_worker_outcome(res, None, attempt_count=1)
    assert outcome == WorkerExecutionOutcome.TERMINAL_FAILURE


def test_classify_partial_with_pages_fetched() -> None:
    """Verify PARTIAL with pages_fetched > 0 maps to TERMINAL_PARTIAL."""
    stats = make_dummy_stats(2)
    res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.PARTIAL,
        statistics=stats,
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )
    outcome = classify_worker_outcome(res, None, attempt_count=1)
    assert outcome == WorkerExecutionOutcome.TERMINAL_PARTIAL


def test_classify_lease_lost_exception() -> None:
    """Verify LeaseLostError exception maps to LEASE_LOST outcome."""
    exc = LeaseLostError(scan_url_id=uuid.uuid4(), lease_owner="w1", attempt_count=1)
    outcome = classify_worker_outcome(None, exc, attempt_count=1)
    assert outcome == WorkerExecutionOutcome.LEASE_LOST


def test_classify_transient_exception_retry_wait() -> None:
    """Verify transient exception with attempt < max_attempts maps to RETRYABLE_FAILURE."""
    exc = RuntimeError("HTTP timeout")
    outcome = classify_worker_outcome(None, exc, attempt_count=1, max_attempts=3)
    assert outcome == WorkerExecutionOutcome.RETRYABLE_FAILURE


def test_classify_transient_exception_max_attempts_reached() -> None:
    """Verify transient exception with attempt >= max_attempts maps to TERMINAL_FAILURE."""
    exc = RuntimeError("HTTP timeout")
    outcome = classify_worker_outcome(None, exc, attempt_count=3, max_attempts=3)
    assert outcome == WorkerExecutionOutcome.TERMINAL_FAILURE


def test_error_code_classification_matrix() -> None:
    """Verify error code and retryability derivation for retryable vs terminal fetch outcomes."""
    robots_ok = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.ALLOWED,
        crawl_delay=None,
        reason="OK",
    )

    # 1. Retryable: TIMEOUT
    page_timeout = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=None,
        robots_decision=robots_ok,
        fetch_result=FetchResult(
            final_url="https://example.com",
            status_code=None,
            content_type=None,
            body_text="",
            redirect_history=(),
            outcome=FetchOutcomeCode.TIMEOUT,
            error_message="Read timeout",
        ),
        emails_found_count=0,
        links_discovered_count=0,
    )
    res_timeout = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page_timeout,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(res_timeout)
    assert err_code == "TIMEOUT"
    assert retryable is True

    # 2. Non-retryable: UNSAFE_HOST
    page_unsafe = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.UNSAFE_HOST,
        status_code=None,
        robots_decision=robots_ok,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    res_unsafe = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page_unsafe,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(res_unsafe)
    assert err_code == "UNSAFE_HOST"
    assert retryable is False

    # 3. Retryable HTTP 429
    page_429 = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=429,
        robots_decision=robots_ok,
        fetch_result=FetchResult(
            final_url="https://example.com",
            status_code=429,
            content_type="text/html",
            body_text="Too Many Requests",
            redirect_history=(),
            outcome=FetchOutcomeCode.HTTP_ERROR,
            error_message="Too Many Requests",
        ),
        emails_found_count=0,
        links_discovered_count=0,
    )
    res_429 = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page_429,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(res_429)
    assert err_code == "HTTP_429"
    assert retryable is True

    # 4. Terminal HTTP 404
    page_404 = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=404,
        robots_decision=robots_ok,
        fetch_result=FetchResult(
            final_url="https://example.com",
            status_code=404,
            content_type="text/html",
            body_text="Not Found",
            redirect_history=(),
            outcome=FetchOutcomeCode.HTTP_ERROR,
            error_message="Not Found",
        ),
        emails_found_count=0,
        links_discovered_count=0,
    )
    res_404 = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page_404,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(res_404)
    assert err_code == "HTTP_404"
    assert retryable is False


def test_classify_robots_temporary_failure_error_code() -> None:
    """Verify ROBOTS_TEMPORARY_FAILURE page outcome yields ROBOTS_FETCH_ERROR and is retryable."""
    robots_temp_fail = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.TEMPORARY_FAILURE,
        crawl_delay=None,
        reason="robots.txt fetch error: Connection refused",
    )
    page_temp = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        status_code=None,
        robots_decision=robots_temp_fail,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    res_temp = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(page_temp,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(res_temp)
    assert err_code == "ROBOTS_FETCH_ERROR"
    assert retryable is True


def test_explicit_robots_denial_is_terminal_immediately() -> None:
    """Verify PageScanOutcome.ROBOTS_DISALLOWED is terminal on the first attempt."""
    robots_denied = RobotsDecision(
        target_url="https://example.com/admin",
        decision=RobotsDecisionCode.DISALLOWED,
        crawl_delay=None,
        reason="Disallowed by robots.txt rule",
    )
    page_denied = PageScanRecord(
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
    res = SiteScanResult(
        starting_url="https://example.com/admin",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(page_denied,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    outcome = classify_worker_outcome(res, None, attempt_count=1, max_attempts=3)
    assert outcome == WorkerExecutionOutcome.TERMINAL_FAILURE


def test_temporary_robots_failure_is_retryable_when_attempts_remain() -> None:
    """Verify ROBOTS_TEMPORARY_FAILURE is retryable when attempt_count < max_attempts."""
    robots_temp = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.TEMPORARY_FAILURE,
        crawl_delay=None,
        reason="robots.txt fetch timeout",
    )
    page_temp = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        status_code=None,
        robots_decision=robots_temp,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(page_temp,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    outcome = classify_worker_outcome(res, None, attempt_count=1, max_attempts=3)
    assert outcome == WorkerExecutionOutcome.RETRYABLE_FAILURE


def test_temporary_robots_failure_is_terminal_at_max_attempts() -> None:
    """Verify ROBOTS_TEMPORARY_FAILURE is terminal when attempt_count >= max_attempts."""
    robots_temp = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.TEMPORARY_FAILURE,
        crawl_delay=None,
        reason="robots.txt DNS resolution failed",
    )
    page_temp = PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        status_code=None,
        robots_decision=robots_temp,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    res = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(page_temp,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    outcome = classify_worker_outcome(res, None, attempt_count=3, max_attempts=3)
    assert outcome == WorkerExecutionOutcome.TERMINAL_FAILURE


def test_robots_certificate_failure_is_terminal_on_first_attempt() -> None:
    """A certificate failure fetching robots.txt must not trigger full-site retries."""
    robots_temp = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.TEMPORARY_FAILURE,
        crawl_delay=None,
        reason="robots.txt fetch error: TLS certificate verification failed",
    )
    page = PageScanRecord(
        requested_url="https://example.com",
        final_url=None,
        depth=0,
        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        status_code=None,
        robots_decision=robots_temp,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(failure_code="TLS_VERIFICATION_FAILED"),
    )

    error_code, retryable = classify_error_code_and_retryability(result)
    assert error_code == "TLS_VERIFICATION_FAILED"
    assert retryable is False
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.TERMINAL_FAILURE
    )


def make_dummy_attempt(
    hop_attempt: int = 1,
    outcome: FetchOutcomeCode = FetchOutcomeCode.TIMEOUT,
    hop_index: int = 0,
    status_code: int | None = None,
) -> FetchAttempt:
    """Helper to create dummy FetchAttempt with recorded attempt number."""
    return FetchAttempt(
        hop_index=hop_index,
        hop_attempt_number=hop_attempt,
        global_attempt_number=hop_attempt,
        request_url="https://example.com",
        status_code=status_code,
        outcome=outcome,
        pinned_ip="93.184.215.14",
        delay_before_attempt_seconds=0.0,
        delay_source=None,
        connection_attempts=(),
        error_message=None,
    )


def _make_dummy_page_with_fetch(
    outcome: FetchOutcomeCode,
    attempts: tuple[FetchAttempt, ...] = (),
    status_code: int | None = None,
) -> PageScanRecord:
    robots_ok = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.ALLOWED,
        crawl_delay=None,
        reason="OK",
    )
    fetch_res = FetchResult(
        final_url="https://example.com",
        status_code=status_code,
        content_type="text/html",
        body_text="",
        redirect_history=(),
        outcome=outcome,
        error_message=outcome.value,
        attempts=attempts,
    )
    return PageScanRecord(
        requested_url="https://example.com",
        final_url="https://example.com",
        depth=0,
        outcome=PageScanOutcome.FETCH_FAILED,
        status_code=status_code,
        robots_decision=robots_ok,
        fetch_result=fetch_res,
        emails_found_count=0,
        links_discovered_count=0,
    )


def test_timeout_with_one_request_attempt_is_retryable() -> None:
    """Timeout with exactly one recorded attempt remains eligible for worker retry."""
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TIMEOUT,
        attempts=(make_dummy_attempt(1, FetchOutcomeCode.TIMEOUT),),
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TIMEOUT"
    assert retryable is True
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.RETRYABLE_FAILURE
    )


def test_timeout_with_three_request_attempts_is_terminal() -> None:
    """Timeout with multiple recorded attempts is terminal after fetcher exhaustion."""
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TIMEOUT,
        attempts=(
            make_dummy_attempt(1, FetchOutcomeCode.TIMEOUT),
            make_dummy_attempt(2, FetchOutcomeCode.TIMEOUT),
            make_dummy_attempt(3, FetchOutcomeCode.TIMEOUT),
        ),
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TIMEOUT"
    assert retryable is False
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.TERMINAL_FAILURE
    )


def test_transport_error_with_one_request_attempt_is_retryable() -> None:
    """Transport error with one recorded attempt remains eligible for worker retry."""
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TRANSPORT_ERROR,
        attempts=(make_dummy_attempt(1, FetchOutcomeCode.TRANSPORT_ERROR),),
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TRANSPORT_ERROR"
    assert retryable is True
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.RETRYABLE_FAILURE
    )


def test_transport_error_with_three_request_attempts_is_terminal() -> None:
    """Transport error with multiple recorded attempts is terminal after fetcher exhaustion."""
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TRANSPORT_ERROR,
        attempts=(
            make_dummy_attempt(1, FetchOutcomeCode.TRANSPORT_ERROR),
            make_dummy_attempt(2, FetchOutcomeCode.TRANSPORT_ERROR),
            make_dummy_attempt(3, FetchOutcomeCode.TRANSPORT_ERROR),
        ),
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TRANSPORT_ERROR"
    assert retryable is False
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.TERMINAL_FAILURE
    )


def test_dns_failure_is_retryable() -> None:
    """DNS failure remains worker-retryable because the fetcher performs no DNS retry."""
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.DNS_RESOLUTION_FAILED,
        attempts=(make_dummy_attempt(1, FetchOutcomeCode.DNS_RESOLUTION_FAILED),),
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "DNS_RESOLUTION_FAILED"
    assert retryable is True
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.RETRYABLE_FAILURE
    )


def test_unexpected_worker_exception_is_retryable() -> None:
    """Unexpected worker/process exceptions remain worker-retryable when attempts remain."""
    exc = RuntimeError("Worker worker task failed unexpectedly")
    outcome = classify_worker_outcome(None, exc, attempt_count=1, max_attempts=3)
    assert outcome == WorkerExecutionOutcome.RETRYABLE_FAILURE


def test_robots_temporary_failure_with_no_proof_of_internal_retries_is_retryable() -> None:
    """Robots temporary failure without proof of internal retries is worker-retryable."""
    robots_temp = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.TEMPORARY_FAILURE,
        crawl_delay=None,
        reason="robots.txt DNS resolution failed",
    )
    page = PageScanRecord(
        requested_url="https://example.com",
        final_url=None,
        depth=0,
        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        status_code=None,
        robots_decision=robots_temp,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(retry_count=0),
    )
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "ROBOTS_FETCH_ERROR"
    assert retryable is True
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.RETRYABLE_FAILURE
    )


def test_robots_temporary_failure_with_typed_proof_of_internal_retries_is_terminal() -> None:
    """Robots temporary failure with typed proof of internal retries is terminal."""
    robots_temp = RobotsDecision(
        target_url="https://example.com",
        decision=RobotsDecisionCode.TEMPORARY_FAILURE,
        crawl_delay=None,
        reason="robots.txt fetch timed out",
    )
    page = PageScanRecord(
        requested_url="https://example.com",
        final_url=None,
        depth=0,
        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        status_code=None,
        robots_decision=robots_temp,
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(retry_count=2, failure_code="GENERIC_TIMEOUT"),
    )
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "ROBOTS_FETCH_ERROR"
    assert retryable is False
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.TERMINAL_FAILURE
    )


def test_redirect_followed_by_first_attempt_timeout_is_retryable() -> None:
    """Redirect at hop 0 followed by first-attempt timeout at hop 1 is retryable."""
    attempts = (
        make_dummy_attempt(1, FetchOutcomeCode.SUCCESS, hop_index=0, status_code=301),
        make_dummy_attempt(1, FetchOutcomeCode.TIMEOUT, hop_index=1),
    )
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TIMEOUT,
        attempts=attempts,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    assert len(attempts) == 2
    assert attempts[-1].hop_attempt_number == 1
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TIMEOUT"
    assert retryable is True
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.RETRYABLE_FAILURE
    )


def test_redirect_followed_by_first_attempt_transport_error_is_retryable() -> None:
    """Redirect at hop 0 followed by first-attempt transport error at hop 1 is retryable."""
    attempts = (
        make_dummy_attempt(1, FetchOutcomeCode.SUCCESS, hop_index=0, status_code=301),
        make_dummy_attempt(1, FetchOutcomeCode.TRANSPORT_ERROR, hop_index=1),
    )
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TRANSPORT_ERROR,
        attempts=attempts,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    assert len(attempts) == 2
    assert attempts[-1].hop_attempt_number == 1
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TRANSPORT_ERROR"
    assert retryable is True
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.RETRYABLE_FAILURE
    )


def test_redirect_followed_by_first_attempt_http_503_is_retryable() -> None:
    """Redirect at hop 0 followed by first-attempt HTTP 503 at hop 1 is retryable."""
    attempts = (
        make_dummy_attempt(1, FetchOutcomeCode.SUCCESS, hop_index=0, status_code=301),
        make_dummy_attempt(1, FetchOutcomeCode.HTTP_ERROR, hop_index=1, status_code=503),
    )
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.HTTP_ERROR,
        attempts=attempts,
        status_code=503,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    assert len(attempts) == 2
    assert attempts[-1].hop_attempt_number == 1
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "HTTP_503"
    assert retryable is True
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.RETRYABLE_FAILURE
    )


def test_final_timeout_with_hop_attempt_two_is_terminal() -> None:
    """A final timeout with hop_attempt_number == 2 is terminal."""
    attempts = (
        make_dummy_attempt(1, FetchOutcomeCode.TIMEOUT, hop_index=0),
        make_dummy_attempt(2, FetchOutcomeCode.TIMEOUT, hop_index=0),
    )
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TIMEOUT,
        attempts=attempts,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    assert attempts[-1].hop_attempt_number == 2
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TIMEOUT"
    assert retryable is False
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.TERMINAL_FAILURE
    )


def test_final_transport_error_with_hop_attempt_two_is_terminal() -> None:
    """A final transport error with hop_attempt_number == 2 is terminal."""
    attempts = (
        make_dummy_attempt(1, FetchOutcomeCode.TRANSPORT_ERROR, hop_index=0),
        make_dummy_attempt(2, FetchOutcomeCode.TRANSPORT_ERROR, hop_index=0),
    )
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.TRANSPORT_ERROR,
        attempts=attempts,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    assert attempts[-1].hop_attempt_number == 2
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "TRANSPORT_ERROR"
    assert retryable is False
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.TERMINAL_FAILURE
    )


def test_final_http_503_with_hop_attempt_two_is_terminal() -> None:
    """A final HTTP 503 with hop_attempt_number == 2 is terminal."""
    attempts = (
        make_dummy_attempt(1, FetchOutcomeCode.HTTP_ERROR, hop_index=0, status_code=503),
        make_dummy_attempt(2, FetchOutcomeCode.HTTP_ERROR, hop_index=0, status_code=503),
    )
    page = _make_dummy_page_with_fetch(
        FetchOutcomeCode.HTTP_ERROR,
        attempts=attempts,
        status_code=503,
    )
    result = SiteScanResult(
        starting_url="https://example.com",
        outcome=SiteScanOutcome.FAILED,
        statistics=make_dummy_stats(0),
        page_records=(page,),
        email_findings=(),
        rejected_email_candidates=(),
    )
    assert attempts[-1].hop_attempt_number == 2
    err_code, retryable = classify_error_code_and_retryability(result)
    assert err_code == "HTTP_503"
    assert retryable is False
    assert (
        classify_worker_outcome(result, None, attempt_count=1, max_attempts=3)
        == WorkerExecutionOutcome.TERMINAL_FAILURE
    )
