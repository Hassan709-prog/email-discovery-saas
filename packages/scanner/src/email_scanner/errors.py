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
