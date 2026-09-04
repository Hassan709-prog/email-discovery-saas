"""Pure outcome classification engine for worker scan execution results."""

from __future__ import annotations

from enum import StrEnum

from email_discovery_api.services.worker_contracts import LeaseLostError
from email_scanner import FetchOutcomeCode, PageScanOutcome, SiteScanOutcome
from email_scanner.models import SiteScanResult

RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class WorkerExecutionOutcome(StrEnum):
    """Classified outcomes for worker scan execution."""

    TERMINAL_SUCCESS = "TERMINAL_SUCCESS"
    TERMINAL_NO_EMAIL = "TERMINAL_NO_EMAIL"
    TERMINAL_PARTIAL = "TERMINAL_PARTIAL"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    CANCELLED = "CANCELLED"
    LEASE_LOST = "LEASE_LOST"


def classify_error_code_and_retryability(
    site_scan_result: SiteScanResult,
) -> tuple[str, bool]:
    """Derive stable error code and retryability from page records and fetch outcomes."""
    for page in site_scan_result.page_records:
        if page.outcome == PageScanOutcome.ROBOTS_DISALLOWED:
            return "ROBOTS_BLOCKED", False
        if page.outcome == PageScanOutcome.ROBOTS_TEMPORARY_FAILURE:
            diagnostics = site_scan_result.diagnostics
            if diagnostics is not None and diagnostics.failure_code == "TLS_VERIFICATION_FAILED":
                return "TLS_VERIFICATION_FAILED", False
            if diagnostics is not None and (
                diagnostics.retry_count > 0 or diagnostics.retry_budget_exhausted
            ):
                return "ROBOTS_FETCH_ERROR", False
            return "ROBOTS_FETCH_ERROR", True
        if page.outcome == PageScanOutcome.UNSAFE_HOST:
            return "UNSAFE_HOST", False
        if page.outcome == PageScanOutcome.RESPONSE_TOO_LARGE:
            return "RESPONSE_TOO_LARGE", False
        if page.outcome == PageScanOutcome.UNSUPPORTED_CONTENT_TYPE:
            return "UNSUPPORTED_CONTENT_TYPE", False

        if page.fetch_result:
            fetch_code = page.fetch_result.outcome
            has_multiple_attempts = len(page.fetch_result.attempts) > 1

            if fetch_code == FetchOutcomeCode.TIMEOUT:
                return "TIMEOUT", not has_multiple_attempts
            if fetch_code == FetchOutcomeCode.TRANSPORT_ERROR:
                return "TRANSPORT_ERROR", not has_multiple_attempts
            if fetch_code == FetchOutcomeCode.DNS_RESOLUTION_FAILED:
                return "DNS_RESOLUTION_FAILED", True
            if fetch_code == FetchOutcomeCode.UNSAFE_HOST:
                return "UNSAFE_HOST", False
            if fetch_code == FetchOutcomeCode.INVALID_URL:
                return "INVALID_URL", False
            if fetch_code == FetchOutcomeCode.TLS_VERIFICATION_FAILED:
                return "TLS_VERIFICATION_FAILED", False
            if fetch_code == FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT:
                return "OUT_OF_SCOPE_REDIRECT", False
            if fetch_code == FetchOutcomeCode.RESPONSE_TOO_LARGE:
                return "RESPONSE_TOO_LARGE", False
            if fetch_code == FetchOutcomeCode.UNSUPPORTED_CONTENT_TYPE:
                return "UNSUPPORTED_CONTENT_TYPE", False
            if fetch_code == FetchOutcomeCode.MAX_REDIRECTS_EXCEEDED:
                return "MAX_REDIRECTS_EXCEEDED", False
            if fetch_code == FetchOutcomeCode.HTTP_ERROR:
                status_code = page.fetch_result.status_code
                if status_code in RETRYABLE_HTTP_STATUSES:
                    return f"HTTP_{status_code}", not has_multiple_attempts
                if status_code is not None:
                    return f"HTTP_{status_code}", False
                return "HTTP_ERROR", False

    if site_scan_result.outcome == SiteScanOutcome.ROBOTS_BLOCKED:
        return "ROBOTS_BLOCKED", False

    return "SCAN_FAILED", True


def classify_worker_outcome(
    site_scan_result: SiteScanResult | None,
    execution_exception: Exception | None,
    attempt_count: int,
    max_attempts: int = 3,
) -> WorkerExecutionOutcome:
    """Pure outcome classifier mapping scanner results and exceptions into worker outcomes."""
    if execution_exception is not None:
        if isinstance(execution_exception, LeaseLostError):
            return WorkerExecutionOutcome.LEASE_LOST
        if attempt_count < max_attempts:
            return WorkerExecutionOutcome.RETRYABLE_FAILURE
        return WorkerExecutionOutcome.TERMINAL_FAILURE

    if site_scan_result is None:
        if attempt_count < max_attempts:
            return WorkerExecutionOutcome.RETRYABLE_FAILURE
        return WorkerExecutionOutcome.TERMINAL_FAILURE

    outcome = site_scan_result.outcome

    if outcome == SiteScanOutcome.COMPLETED:
        if len(site_scan_result.email_findings) > 0:
            return WorkerExecutionOutcome.TERMINAL_SUCCESS
        return WorkerExecutionOutcome.TERMINAL_NO_EMAIL

    if outcome == SiteScanOutcome.COMPLETED_NO_EMAILS:
        return WorkerExecutionOutcome.TERMINAL_NO_EMAIL

    if outcome in (SiteScanOutcome.ROBOTS_BLOCKED, SiteScanOutcome.FAILED):
        _, is_retryable = classify_error_code_and_retryability(site_scan_result)
        if is_retryable and attempt_count < max_attempts:
            return WorkerExecutionOutcome.RETRYABLE_FAILURE
        return WorkerExecutionOutcome.TERMINAL_FAILURE

    if outcome == SiteScanOutcome.PARTIAL:
        stats = site_scan_result.statistics
        pages_fetched = stats.pages_fetched if stats else 0
        if pages_fetched > 0:
            return WorkerExecutionOutcome.TERMINAL_PARTIAL
        if attempt_count < max_attempts:
            return WorkerExecutionOutcome.RETRYABLE_FAILURE
        return WorkerExecutionOutcome.TERMINAL_FAILURE

    if outcome == SiteScanOutcome.CANCELLED:
        return WorkerExecutionOutcome.CANCELLED

    return WorkerExecutionOutcome.TERMINAL_FAILURE
