"""Tests for retry policy, backoff calculation, Retry-After parsing, and fetch attempt history."""

from collections.abc import Callable

from email_scanner.errors import (
    DelaySource,
    FetchOutcomeCode,
    RetryReason,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    FetchConfig,
    FetchResult,
    NormalizedURL,
    RetryPolicy,
)
from email_scanner.request_gate import DomainRequestGate
from email_scanner.retry import (
    calculate_backoff_delay,
    parse_retry_after_header,
    should_retry_fetch,
)


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.current_time = start

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


class MockHTTPFetcherWithResponses(AsyncHTTPFetcher):
    def __init__(
        self,
        responses: list[FetchResult],
        config: FetchConfig | None = None,
        clock: FakeClock | None = None,
        sleeper: None = None,
    ) -> None:
        cfg = config or FetchConfig()
        super().__init__(config=cfg, pinned=False)
        self.responses = responses
        self.call_count = 0
        self.gate_acquire_count = 0
        self._fake_clock = clock or FakeClock()
        self._request_gate = DomainRequestGate(clock=self._fake_clock)

    async def fetch(
        self,
        url: str | NormalizedURL,
        allowed_content_types: tuple[str, ...] | None = None,
        redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
    ) -> FetchResult:
        # Override to simulate responses without real network calls
        # while retaining fetch wrapper behavior
        return await super().fetch(url, allowed_content_types, redirect_validator)


def test_parse_retry_after_header_delta_seconds_and_http_date() -> None:
    # 1. Delta seconds
    res1 = parse_retry_after_header("12", max_allowed_seconds=60.0)
    assert res1 is not None
    assert res1[0] == 12.0
    assert res1[1] == DelaySource.RETRY_AFTER_HEADER

    # Clamping excessive delta seconds to max_allowed_seconds
    res2 = parse_retry_after_header("120", max_allowed_seconds=60.0)
    assert res2 is not None
    assert res2[0] == 60.0

    # Negative delta seconds clamped to 0.0
    res3 = parse_retry_after_header("-5", max_allowed_seconds=60.0)
    assert res3 is not None
    assert res3[0] == 0.0

    # 2. HTTP date format
    def fake_wall_clock() -> float:
        return 1700000000.0  # Sun Nov 14 2023 22:13:20 GMT

    date_str = "Sun, 14 Nov 2023 22:13:30 GMT"  # +10 seconds
    res4 = parse_retry_after_header(date_str, max_allowed_seconds=60.0, wall_clock=fake_wall_clock)
    assert res4 is not None
    assert res4[0] == 10.0
    assert res4[1] == DelaySource.RETRY_AFTER_HEADER

    # Invalid header returns None
    assert parse_retry_after_header("invalid-header-val") is None


def test_calculate_backoff_delay_exponential_with_jitter() -> None:
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=10.0)

    # Attempt 1 -> 1.0s base
    d1, src1 = calculate_backoff_delay(1, policy, jitter_source=lambda d: 0.0)
    assert d1 == 1.0
    assert src1 == DelaySource.EXPONENTIAL_BACKOFF

    # Attempt 2 -> 2.0s base
    d2, _ = calculate_backoff_delay(2, policy, jitter_source=lambda d: 0.0)
    assert d2 == 2.0

    # Attempt 3 -> 4.0s base
    d3, _ = calculate_backoff_delay(3, policy, jitter_source=lambda d: 0.0)
    assert d3 == 4.0


def test_should_retry_fetch_classification() -> None:
    # Only GET / HEAD are retryable
    assert should_retry_fetch("POST", FetchOutcomeCode.TIMEOUT, None) == (False, None)

    # Security/Validation failures are NEVER retryable
    assert should_retry_fetch("GET", FetchOutcomeCode.UNSAFE_HOST, None) == (False, None)
    assert should_retry_fetch("GET", FetchOutcomeCode.TLS_VERIFICATION_FAILED, None) == (
        False,
        None,
    )
    assert should_retry_fetch("GET", FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT, None) == (False, None)
    assert should_retry_fetch("GET", FetchOutcomeCode.INVALID_URL, None) == (False, None)

    # Timeouts and Transport errors are retryable
    assert should_retry_fetch("GET", FetchOutcomeCode.TIMEOUT, None) == (True, RetryReason.TIMEOUT)
    assert should_retry_fetch("GET", FetchOutcomeCode.TRANSPORT_ERROR, None) == (
        True,
        RetryReason.TRANSPORT_ERROR,
    )

    # HTTP Statuses: 429/503 retryable, 400 non-retryable
    assert should_retry_fetch("GET", FetchOutcomeCode.HTTP_ERROR, 429) == (
        True,
        RetryReason.RETRY_AFTER_HEADER,
    )
    assert should_retry_fetch("GET", FetchOutcomeCode.HTTP_ERROR, 503) == (
        True,
        RetryReason.HTTP_STATUS,
    )
    assert should_retry_fetch("GET", FetchOutcomeCode.HTTP_ERROR, 400) == (False, None)
