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
