"""Typed scanner errors.

Human-readable messages may change. Error codes are stable contracts used by
tests, logs, the API, workers, and persisted scan outcomes.
"""

from enum import StrEnum


class URLNormalizationErrorCode(StrEnum):
    """Stable reasons why URL normalization can fail."""

    EMPTY_INPUT = "EMPTY_INPUT"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    INVALID_URL = "INVALID_URL"
    UNSUPPORTED_SCHEME = "UNSUPPORTED_SCHEME"
    MISSING_HOST = "MISSING_HOST"
    CREDENTIALS_NOT_ALLOWED = "CREDENTIALS_NOT_ALLOWED"
    INVALID_PORT = "INVALID_PORT"
    NON_PUBLIC_HOST = "NON_PUBLIC_HOST"


class URLNormalizationError(ValueError):
    """Raised when a raw URL cannot be normalized safely."""

    def __init__(
        self,
        code: URLNormalizationErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


class HostSafetyErrorCode(StrEnum):
    """Stable reasons why a host is unsafe to crawl."""

    BLOCKED_HOSTNAME = "BLOCKED_HOSTNAME"
    NO_RESOLVED_ADDRESSES = "NO_RESOLVED_ADDRESSES"
    INVALID_IP_ADDRESS = "INVALID_IP_ADDRESS"
    NON_PUBLIC_IP_ADDRESS = "NON_PUBLIC_IP_ADDRESS"


class HostSafetyError(ValueError):
    """Raised when a URL points to an unsafe destination."""

    def __init__(
        self,
        code: HostSafetyErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


class FetchOutcomeCode(StrEnum):
    """Stable status codes representing the outcome of an HTTP fetch operation."""

    SUCCESS = "SUCCESS"
    HTTP_ERROR = "HTTP_ERROR"
    MAX_REDIRECTS_EXCEEDED = "MAX_REDIRECTS_EXCEEDED"
    UNSAFE_HOST = "UNSAFE_HOST"
    DNS_RESOLUTION_FAILED = "DNS_RESOLUTION_FAILED"
    TLS_VERIFICATION_FAILED = "TLS_VERIFICATION_FAILED"
    OUT_OF_SCOPE_REDIRECT = "OUT_OF_SCOPE_REDIRECT"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    TIMEOUT = "TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    INVALID_URL = "INVALID_URL"


class RobotsDecisionCode(StrEnum):
    """Stable decisions produced by the robots.txt policy evaluator."""

    ALLOWED = "ALLOWED"
    DISALLOWED = "DISALLOWED"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"


class FetchConfigErrorCode(StrEnum):
    """Stable error codes for invalid fetch configuration."""

    INVALID_TIMEOUT = "INVALID_TIMEOUT"
    INVALID_MAX_REDIRECTS = "INVALID_MAX_REDIRECTS"
    INVALID_USER_AGENT = "INVALID_USER_AGENT"
    INVALID_MAX_RESPONSE_BYTES = "INVALID_MAX_RESPONSE_BYTES"


class FetchConfigError(ValueError):
    """Raised when a FetchConfig instance contains invalid parameters."""

    def __init__(
        self,
        code: FetchConfigErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


class DiscoveryOutcomeCode(StrEnum):
    """Stable status codes representing the outcome of HTML link discovery."""

    SUCCESS = "SUCCESS"
    NO_LINKS_DISCOVERED = "NO_LINKS_DISCOVERED"
    INVALID_SOURCE_URL = "INVALID_SOURCE_URL"
    HTML_TOO_LARGE = "HTML_TOO_LARGE"
    PARSING_ERROR = "PARSING_ERROR"


class DiscoveryConfigErrorCode(StrEnum):
    """Stable error codes for invalid discovery configuration."""

    INVALID_LIMIT = "INVALID_LIMIT"
    INVALID_SCOPE_MODE = "INVALID_SCOPE_MODE"


class DiscoveryConfigError(ValueError):
    """Raised when a DiscoveryConfig instance contains invalid parameters."""

    def __init__(
        self,
        code: DiscoveryConfigErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


class ExtractionOutcomeCode(StrEnum):
    """Stable status codes representing the outcome of email extraction."""

    SUCCESS = "SUCCESS"
    NO_EMAILS_FOUND = "NO_EMAILS_FOUND"
    INVALID_SOURCE_URL = "INVALID_SOURCE_URL"
    HTML_TOO_LARGE = "HTML_TOO_LARGE"
    PARSING_ERROR = "PARSING_ERROR"


class EmailRejectionCode(StrEnum):
    """Stable reason codes for rejected email candidates."""

    INVALID_SYNTAX = "INVALID_SYNTAX"
    LOCAL_PART_TOO_LONG = "LOCAL_PART_TOO_LONG"
    TOTAL_LENGTH_TOO_LONG = "TOTAL_LENGTH_TOO_LONG"
    INVALID_DOMAIN_LABEL = "INVALID_DOMAIN_LABEL"
    NO_PUBLIC_SUFFIX = "NO_PUBLIC_SUFFIX"
    PLACEHOLDER_DOMAIN = "PLACEHOLDER_DOMAIN"
    FILE_EXTENSION_LIKE = "FILE_EXTENSION_LIKE"
    NO_REPLY_ADDRESS = "NO_REPLY_ADDRESS"
    DUMMY_TEST_ADDRESS = "DUMMY_TEST_ADDRESS"
    EXTERNAL_DOMAIN_REJECTED = "EXTERNAL_DOMAIN_REJECTED"


class ExtractionConfigErrorCode(StrEnum):
    """Stable error codes for invalid email extraction configuration."""

    INVALID_LIMIT = "INVALID_LIMIT"


class ExtractionConfigError(ValueError):
    """Raised when an EmailExtractionConfig instance contains invalid parameters."""

    def __init__(
        self,
        code: ExtractionConfigErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


class SiteScanOutcome(StrEnum):
    """Stable status codes representing the overall outcome of a single-site scan."""

    COMPLETED = "COMPLETED"
    COMPLETED_NO_EMAILS = "COMPLETED_NO_EMAILS"
    PARTIAL = "PARTIAL"
    ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SiteScanFailureCode(StrEnum):
    """Stable failure classification codes for website scanning outcomes."""

    ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
    ROBOTS_TEMPORARY_FAILURE = "ROBOTS_TEMPORARY_FAILURE"
    DNS_RESOLUTION_FAILED = "DNS_RESOLUTION_FAILED"
    UNSAFE_HOST = "UNSAFE_HOST"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    READ_TIMEOUT = "READ_TIMEOUT"
    GENERIC_TIMEOUT = "GENERIC_TIMEOUT"
    TOTAL_TIME_BUDGET_EXHAUSTED = "TOTAL_TIME_BUDGET_EXHAUSTED"
    TLS_VERIFICATION_FAILED = "TLS_VERIFICATION_FAILED"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"
    HTTP_ERROR = "HTTP_ERROR"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    OUT_OF_SCOPE_REDIRECT = "OUT_OF_SCOPE_REDIRECT"
    CANCELLED = "CANCELLED"
    UNEXPECTED_INTERNAL_ERROR = "UNEXPECTED_INTERNAL_ERROR"


def map_fetch_outcome_to_failure_code(outcome: FetchOutcomeCode) -> SiteScanFailureCode | None:
    """Map FetchOutcomeCode to stable SiteScanFailureCode strictly via typed enum matching."""
    mapping = {
        FetchOutcomeCode.DNS_RESOLUTION_FAILED: SiteScanFailureCode.DNS_RESOLUTION_FAILED,
        FetchOutcomeCode.UNSAFE_HOST: SiteScanFailureCode.UNSAFE_HOST,
        FetchOutcomeCode.TLS_VERIFICATION_FAILED: SiteScanFailureCode.TLS_VERIFICATION_FAILED,
        FetchOutcomeCode.TIMEOUT: SiteScanFailureCode.GENERIC_TIMEOUT,
        FetchOutcomeCode.TRANSPORT_ERROR: SiteScanFailureCode.TRANSPORT_ERROR,
        FetchOutcomeCode.HTTP_ERROR: SiteScanFailureCode.HTTP_ERROR,
        FetchOutcomeCode.UNSUPPORTED_CONTENT_TYPE: SiteScanFailureCode.UNSUPPORTED_CONTENT_TYPE,
        FetchOutcomeCode.RESPONSE_TOO_LARGE: SiteScanFailureCode.RESPONSE_TOO_LARGE,
        FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT: SiteScanFailureCode.OUT_OF_SCOPE_REDIRECT,
    }
    return mapping.get(outcome)


class PageScanOutcome(StrEnum):
    """Stable status codes representing the scan outcome for an individual page."""

    FETCHED_AND_PROCESSED = "FETCHED_AND_PROCESSED"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    ROBOTS_TEMPORARY_FAILURE = "ROBOTS_TEMPORARY_FAILURE"
    FETCH_FAILED = "FETCH_FAILED"
    UNSAFE_HOST = "UNSAFE_HOST"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    SKIPPED_BUDGET_REACHED = "SKIPPED_BUDGET_REACHED"


class SiteScanConfigErrorCode(StrEnum):
    """Stable error codes for invalid site scan configuration."""

    INVALID_LIMIT = "INVALID_LIMIT"
    INVALID_INTERVAL = "INVALID_INTERVAL"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"


class SiteScanConfigError(ValueError):
    """Raised when a SiteScanConfig instance contains invalid parameters."""

    def __init__(
        self,
        code: SiteScanConfigErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


class BatchScanOutcome(StrEnum):
    """Stable status codes representing the overall outcome of a multi-URL batch scan."""

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BatchItemOutcome(StrEnum):
    """Stable status codes representing the outcome of an individual item in a batch scan."""

    COMPLETED = "COMPLETED"
    COMPLETED_NO_EMAILS = "COMPLETED_NO_EMAILS"
    PARTIAL = "PARTIAL"
    ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DUPLICATE_COALESCED = "DUPLICATE_COALESCED"
    INVALID_INPUT = "INVALID_INPUT"
    SKIPPED_BUDGET_REACHED = "SKIPPED_BUDGET_REACHED"


class BatchScanConfigErrorCode(StrEnum):
    """Stable error codes for invalid batch scan configuration."""

    INVALID_LIMIT = "INVALID_LIMIT"
    INVALID_INTERVAL = "INVALID_INTERVAL"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"


class BatchScanConfigError(ValueError):
    """Raised when a BatchScanConfig instance contains invalid parameters."""

    def __init__(
        self,
        code: BatchScanConfigErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


class RetryReason(StrEnum):
    """Stable reason codes for HTTP request retries."""

    TIMEOUT = "TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    HTTP_STATUS = "HTTP_STATUS"
    RETRY_AFTER_HEADER = "RETRY_AFTER_HEADER"


class DelaySource(StrEnum):
    """Source of backoff delay calculation."""

    EXPONENTIAL_BACKOFF = "EXPONENTIAL_BACKOFF"
    RETRY_AFTER_HEADER = "RETRY_AFTER_HEADER"


class RetryPolicyErrorCode(StrEnum):
    """Stable error codes for invalid retry policy configuration."""

    INVALID_LIMIT = "INVALID_LIMIT"
    INVALID_INTERVAL = "INVALID_INTERVAL"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"


class RetryPolicyError(ValueError):
    """Raised when a RetryPolicy instance contains invalid parameters."""

    def __init__(
        self,
        code: RetryPolicyErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)
