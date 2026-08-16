"""Deterministic retry policy and Retry-After header parsing for scanner-core."""

import email.utils
import time
from collections.abc import Callable

from email_scanner.errors import DelaySource, FetchOutcomeCode, RetryReason
from email_scanner.models import RetryPolicy


def parse_retry_after_header(
    header_val: str,
    max_allowed_seconds: float = 60.0,
    wall_clock: Callable[[], float] | None = None,
) -> tuple[float, DelaySource] | None:
    """Parse Retry-After header as delta-seconds or HTTP-date string with clamping."""
    cleaned = header_val.strip()
    if not cleaned:
        return None

    # Try delta-seconds integer/float first
    try:
        delta_sec = float(cleaned)
        clamped_sec = max(0.0, min(delta_sec, max_allowed_seconds))
        return (clamped_sec, DelaySource.RETRY_AFTER_HEADER)
    except ValueError:
        pass

    # Try RFC-2822 / RFC-7231 HTTP date string
    try:
        parsed_dt = email.utils.parsedate_to_datetime(cleaned)
        if parsed_dt is not None:  # pyright: ignore[reportUnnecessaryIsInstance,reportUnnecessaryComparison]
            now_epoch = (wall_clock or time.time)()
            target_epoch = parsed_dt.timestamp()
            diff_sec = target_epoch - now_epoch
            clamped_sec = max(0.0, min(diff_sec, max_allowed_seconds))
            return (clamped_sec, DelaySource.RETRY_AFTER_HEADER)
    except Exception:
        pass

    return None


def calculate_backoff_delay(
    attempt: int,
    policy: RetryPolicy,
    jitter_source: Callable[[float], float] | None = None,
) -> tuple[float, DelaySource]:
    """Calculate exponential backoff delay with deterministic jitter."""
    attempt_index = max(0, attempt - 1)
    raw_delay = min(
        policy.max_delay_seconds,
        policy.base_delay_seconds * (2.0**attempt_index),
    )

    if jitter_source is not None:
        jitter = jitter_source(raw_delay)
    else:
        jitter = max(0.0, min(raw_delay, raw_delay * 0.1))

    final_delay = min(policy.max_delay_seconds, raw_delay + jitter)
    return (final_delay, DelaySource.EXPONENTIAL_BACKOFF)


def should_retry_fetch(
    method: str,
    outcome: FetchOutcomeCode,
    status_code: int | None,
) -> tuple[bool, RetryReason | None]:
    """Classify fetch outcome to determine if request is eligible for bounded retry."""
    if method.upper() not in {"GET", "HEAD"}:
        return (False, None)

    # Non-retryable security, validation, or permanent failures
    if outcome in {
        FetchOutcomeCode.UNSAFE_HOST,
        FetchOutcomeCode.TLS_VERIFICATION_FAILED,
        FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT,
        FetchOutcomeCode.INVALID_URL,
        FetchOutcomeCode.RESPONSE_TOO_LARGE,
        FetchOutcomeCode.UNSUPPORTED_CONTENT_TYPE,
        FetchOutcomeCode.MAX_REDIRECTS_EXCEEDED,
    }:
        return (False, None)

    if outcome == FetchOutcomeCode.TIMEOUT:
        return (True, RetryReason.TIMEOUT)

    if outcome in {FetchOutcomeCode.TRANSPORT_ERROR, FetchOutcomeCode.DNS_RESOLUTION_FAILED}:
        return (True, RetryReason.TRANSPORT_ERROR)

    if outcome == FetchOutcomeCode.HTTP_ERROR and status_code is not None:
        if status_code in {408, 425, 429, 500, 502, 503, 504}:
            if status_code == 429:
                return (True, RetryReason.RETRY_AFTER_HEADER)
            return (True, RetryReason.HTTP_STATUS)

    return (False, None)
