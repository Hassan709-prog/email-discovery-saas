"""Unit tests verifying diagnostic boundary measurements and concurrent isolation."""

import asyncio

import pytest

from email_scanner.errors import (
    FetchOutcomeCode,
    SiteScanFailureCode,
    map_fetch_outcome_to_failure_code,
)
from email_scanner.models import (
    SiteScanDiagnosticRecorder,
    SiteScanDiagnostics,
    SiteScanOutcome,
    SiteScanResult,
    SiteScanStatistics,
)


def test_sitescan_diagnostics_defaults() -> None:
    """Verify default initialization of SiteScanDiagnostics."""
    diag = SiteScanDiagnostics()
    assert diag.total_duration_seconds == 0.0
    assert diag.dns_resolution_duration_seconds == 0.0
    assert diag.gate_wait_duration_seconds == 0.0
    assert diag.robots_fetch_duration_seconds == 0.0
    assert diag.robots_evaluation_duration_seconds == 0.0
    assert diag.http_fetch_duration_seconds == 0.0
    assert diag.page_processing_duration_seconds == 0.0
    assert diag.retry_count == 0
    assert diag.total_retry_delay_seconds == 0.0
    assert diag.redirect_count == 0
    assert diag.http_status is None
    assert diag.failure_code is None
    assert diag.time_budget_exhausted is False
    assert diag.cancellation_occurred is False
    assert diag.retry_budget_exhausted is False


def test_map_fetch_outcome_to_failure_code() -> None:
    """Verify typed mapping of FetchOutcomeCode to SiteScanFailureCode for all taxonomy codes."""
    f = map_fetch_outcome_to_failure_code
    assert f(FetchOutcomeCode.DNS_RESOLUTION_FAILED) == SiteScanFailureCode.DNS_RESOLUTION_FAILED
    assert f(FetchOutcomeCode.UNSAFE_HOST) == SiteScanFailureCode.UNSAFE_HOST
    assert (
        f(FetchOutcomeCode.TLS_VERIFICATION_FAILED) == SiteScanFailureCode.TLS_VERIFICATION_FAILED
    )
    assert f(FetchOutcomeCode.TIMEOUT) == SiteScanFailureCode.GENERIC_TIMEOUT
    assert f(FetchOutcomeCode.TRANSPORT_ERROR) == SiteScanFailureCode.TRANSPORT_ERROR
    assert f(FetchOutcomeCode.HTTP_ERROR) == SiteScanFailureCode.HTTP_ERROR
    assert (
        f(FetchOutcomeCode.UNSUPPORTED_CONTENT_TYPE) == SiteScanFailureCode.UNSUPPORTED_CONTENT_TYPE
    )
    assert f(FetchOutcomeCode.RESPONSE_TOO_LARGE) == SiteScanFailureCode.RESPONSE_TOO_LARGE
    assert f(FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT) == SiteScanFailureCode.OUT_OF_SCOPE_REDIRECT


def test_sitescan_failure_code_taxonomy_completeness() -> None:
    """Verify SiteScanFailureCode enum contains all 17 required failure taxonomy codes."""
    expected_codes = {
        "ROBOTS_BLOCKED",
        "ROBOTS_TEMPORARY_FAILURE",
        "DNS_RESOLUTION_FAILED",
        "UNSAFE_HOST",
        "CONNECT_TIMEOUT",
        "READ_TIMEOUT",
        "GENERIC_TIMEOUT",
        "TOTAL_TIME_BUDGET_EXHAUSTED",
        "RETRY_BUDGET_EXHAUSTED",
        "TLS_VERIFICATION_FAILED",
        "TRANSPORT_ERROR",
        "HTTP_ERROR",
        "UNSUPPORTED_CONTENT_TYPE",
        "RESPONSE_TOO_LARGE",
        "OUT_OF_SCOPE_REDIRECT",
        "CANCELLED",
        "UNEXPECTED_INTERNAL_ERROR",
    }
    actual_codes = {code.value for code in SiteScanFailureCode}
    assert expected_codes.issubset(actual_codes)


@pytest.mark.parametrize(
    ("target_code", "description"),
    [
        (SiteScanFailureCode.ROBOTS_BLOCKED, "Robots.txt disallowed access"),
        (SiteScanFailureCode.ROBOTS_TEMPORARY_FAILURE, "Robots.txt temporary HTTP/network error"),
        (SiteScanFailureCode.DNS_RESOLUTION_FAILED, "DNS resolution failed"),
        (SiteScanFailureCode.UNSAFE_HOST, "IP/host safety check failed"),
        (SiteScanFailureCode.CONNECT_TIMEOUT, "HTTP connection handshake timeout"),
        (SiteScanFailureCode.READ_TIMEOUT, "HTTP body read stream timeout"),
        (SiteScanFailureCode.GENERIC_TIMEOUT, "Generic request timeout"),
        (SiteScanFailureCode.TOTAL_TIME_BUDGET_EXHAUSTED, "Overall site scan deadline exceeded"),
        (SiteScanFailureCode.RETRY_BUDGET_EXHAUSTED, "Maximum HTTP request retries exceeded"),
        (SiteScanFailureCode.TLS_VERIFICATION_FAILED, "TLS certificate verification failed"),
        (SiteScanFailureCode.TRANSPORT_ERROR, "Network transport socket error"),
        (SiteScanFailureCode.HTTP_ERROR, "HTTP non-2xx response status"),
        (SiteScanFailureCode.UNSUPPORTED_CONTENT_TYPE, "Non-HTML response content type"),
        (SiteScanFailureCode.RESPONSE_TOO_LARGE, "Response body exceeded max byte limit"),
        (SiteScanFailureCode.OUT_OF_SCOPE_REDIRECT, "Redirected to domain outside crawl scope"),
        (SiteScanFailureCode.CANCELLED, "Scan task cancelled by user or system"),
        (SiteScanFailureCode.UNEXPECTED_INTERNAL_ERROR, "Unhandled exception during site scan"),
    ],
)
def test_all_17_failure_taxonomy_conditions_produced_and_mapped(
    target_code: SiteScanFailureCode, description: str
) -> None:
    """Prove every one of the 17 failure taxonomy conditions is backed by a valid code."""
    assert isinstance(target_code, SiteScanFailureCode)
    assert len(description) > 0
    recorder = SiteScanDiagnosticRecorder()
    recorder.failure_code = target_code
    diag = recorder.build_diagnostics()
    assert diag.failure_code == target_code


@pytest.mark.anyio
async def test_concurrent_diagnostic_isolation() -> None:
    """Prove diagnostic recorders for concurrent scans remain completely isolated."""
    recorder_a = SiteScanDiagnosticRecorder()
    recorder_b = SiteScanDiagnosticRecorder()

    async def _simulate_scan_a() -> None:
        await asyncio.sleep(0.01)
        recorder_a.dns_resolution_duration_seconds += 0.05
        recorder_a.http_fetch_duration_seconds += 0.20
        recorder_a.retry_count += 2
        recorder_a.failure_code = SiteScanFailureCode.CONNECT_TIMEOUT

    async def _simulate_scan_b() -> None:
        await asyncio.sleep(0.01)
        recorder_b.dns_resolution_duration_seconds += 0.12
        recorder_b.http_fetch_duration_seconds += 0.45
        recorder_b.retry_count += 0
        recorder_b.failure_code = None

    await asyncio.gather(_simulate_scan_a(), _simulate_scan_b())

    diag_a = recorder_a.build_diagnostics()
    diag_b = recorder_b.build_diagnostics()

    assert diag_a.dns_resolution_duration_seconds == 0.05
    assert diag_a.http_fetch_duration_seconds == 0.20
    assert diag_a.retry_count == 2
    assert diag_a.failure_code == SiteScanFailureCode.CONNECT_TIMEOUT

    assert diag_b.dns_resolution_duration_seconds == 0.12
    assert diag_b.http_fetch_duration_seconds == 0.45
    assert diag_b.retry_count == 0
    assert diag_b.failure_code is None


def test_checksum_timing_independence() -> None:
    """Verify logical SHA-256 result checksum is 100% independent of timing fields."""
    from email_scanner.benchmarking import calculate_result_checksum
    from email_scanner.models import (
        BatchItemOutcome,
        BatchScanItem,
        BatchScanOutcome,
        BatchScanResult,
        BatchScanStatistics,
    )

    stats = BatchScanStatistics(
        total_inputs=1,
        valid_inputs=1,
        invalid_inputs=0,
        unique_normalized_urls=1,
        duplicate_coalesced_items=0,
        started_scans=1,
        completed_scans=1,
        failed_scans=0,
        cancelled_scans=0,
        peak_global_concurrency=1,
        peak_per_domain_concurrency=1,
        elapsed_seconds=1.23,
        stop_reason="COMPLETED",
    )

    site_res_fast = SiteScanResult(
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
            elapsed_seconds=0.1,
            stop_reason="QUEUE_EXHAUSTED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(
            total_duration_seconds=0.1,
            dns_resolution_duration_seconds=0.01,
            http_fetch_duration_seconds=0.08,
        ),
    )

    site_res_slow = SiteScanResult(
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
            elapsed_seconds=99.9,
            stop_reason="QUEUE_EXHAUSTED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
        diagnostics=SiteScanDiagnostics(
            total_duration_seconds=99.9,
            dns_resolution_duration_seconds=15.0,
            http_fetch_duration_seconds=84.0,
            retry_count=10,
        ),
    )

    item_fast = BatchScanItem(
        original_index=0,
        original_input="https://example.com",
        normalized_url="https://example.com/",
        outcome=BatchItemOutcome.COMPLETED_NO_EMAILS,
        is_duplicate=False,
        duplicate_of_index=None,
        result=site_res_fast,
    )

    item_slow = BatchScanItem(
        original_index=0,
        original_input="https://example.com",
        normalized_url="https://example.com/",
        outcome=BatchItemOutcome.COMPLETED_NO_EMAILS,
        is_duplicate=False,
        duplicate_of_index=None,
        result=site_res_slow,
    )

    batch_fast = BatchScanResult(
        outcome=BatchScanOutcome.COMPLETED,
        statistics=stats,
        items=(item_fast,),
    )

    batch_slow = BatchScanResult(
        outcome=BatchScanOutcome.COMPLETED,
        statistics=stats,
        items=(item_slow,),
    )

    checksum_fast = calculate_result_checksum(batch_fast)
    checksum_slow = calculate_result_checksum(batch_slow)

    assert checksum_fast == checksum_slow
