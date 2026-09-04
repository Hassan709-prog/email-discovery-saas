"""Asynchronous HTTP fetcher for scanner-core.

Provides safe, typed HTTP fetching with DNS pre-validation, DNS-pinned transport,
bounded retries, redirect tracking, content-type checking, response size streaming limits,
and deterministic attempt history recording.
"""

import ssl
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

import httpcore
import httpx

from email_scanner.dns import AsyncDNSResolver, SystemDNSResolver
from email_scanner.errors import (
    DelaySource,
    FetchOutcomeCode,
    HostSafetyError,
    HostSafetyErrorCode,
    SiteScanFailureCode,
    URLNormalizationError,
)
from email_scanner.models import (
    FetchAttempt,
    FetchConfig,
    FetchResult,
    IPConnectionAttempt,
    NormalizedURL,
    RedirectHop,
)
from email_scanner.normalization import normalize_url
from email_scanner.pinned_transport import (
    PinnedAsyncHTTPTransport,
    _connection_attempts_ctx,  # pyright: ignore[reportPrivateUsage]
)
from email_scanner.request_gate import (
    DomainRequestGate,
    RequestGateProtocol,
)
from email_scanner.retry import (
    calculate_backoff_delay,
    parse_retry_after_header,
    should_retry_fetch,
)


def _contains_tls_error(error: BaseException) -> bool:
    """Return whether an exception chain contains a typed TLS/SSL failure."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()

    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        if isinstance(current, ssl.SSLError):
            return True

        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))

    return False


class AsyncHTTPFetcher:
    """Asynchronous HTTP fetcher enforcing scanner-core safety policies and DNS pinning."""

    def __init__(
        self,
        dns_resolver: AsyncDNSResolver | None = None,
        client: httpx.AsyncClient | None = None,
        config: FetchConfig | None = None,
        redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
        request_gate: RequestGateProtocol | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        async_sleeper: Callable[[float], Awaitable[None]] | None = None,
        jitter_source: Callable[[float], float] | None = None,
        cancellation_checker: Callable[[], bool] | None = None,
        pinned: bool = True,
    ) -> None:
        self._dns_resolver = dns_resolver or SystemDNSResolver()
        self._config = config or FetchConfig()
        self._redirect_validator = redirect_validator
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._sleeper = async_sleeper
        self._jitter_source = jitter_source
        self._cancellation_checker = cancellation_checker

        self._request_gate: RequestGateProtocol = request_gate or DomainRequestGate(
            default_minimum_interval_seconds=1.0,
            clock=self._clock,
            async_sleeper=self._sleeper,
        )

        self._client = client
        self._transport = transport

        if self._client is None and self._transport is None and pinned:
            self._transport = PinnedAsyncHTTPTransport(
                dns_resolver=self._dns_resolver,
                pinning_config=self._config.pinning_config,
                clock=self._clock,
            )

    @property
    def config(self) -> FetchConfig:
        return self._config

    @property
    def request_gate(self) -> RequestGateProtocol:
        return self._request_gate

    async def fetch(
        self,
        url: str | NormalizedURL,
        allowed_content_types: tuple[str, ...] | None = None,
        redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
        recorder: Any | None = None,
    ) -> FetchResult:
        """Fetch content for a URL safely, asynchronously, with DNS pinning and bounded retries."""
        effective_redirect_validator = (
            redirect_validator if redirect_validator is not None else self._redirect_validator
        )

        config = self._config
        retry_policy = config.retry_policy
        effective_allowed_types = (
            allowed_content_types
            if allowed_content_types is not None
            else config.allowed_content_types
        )

        if isinstance(url, str):
            try:
                normalized_target = normalize_url(url)
            except URLNormalizationError as err:
                return FetchResult(
                    final_url=url,
                    status_code=None,
                    content_type=None,
                    body_text=None,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.INVALID_URL,
                    error_message=str(err),
                    attempts=(),
                )
        else:
            normalized_target = url

        current_url = normalized_target
        redirect_history: list[RedirectHop] = []
        attempts: list[FetchAttempt] = []

        global_start_time = self._clock()
        global_attempt_counter = 0
        retries_occurred = 0
        hop_index = 0
        status_code: int | None = None

        should_close_client = False
        client = self._client

        if client is None:
            if self._transport is not None:
                client = httpx.AsyncClient(
                    transport=self._transport,
                    follow_redirects=False,
                    trust_env=False,
                )
            else:
                client = httpx.AsyncClient(
                    follow_redirects=False,
                    trust_env=False,
                )
            should_close_client = True

        try:
            while True:
                # Check cancellation or global fetch timeout before starting a hop
                if self._cancellation_checker is not None and self._cancellation_checker():
                    return FetchResult(
                        final_url=current_url.normalized_url,
                        status_code=None,
                        content_type=None,
                        body_text=None,
                        redirect_history=tuple(redirect_history),
                        outcome=FetchOutcomeCode.TIMEOUT,
                        error_message="Fetch operation cancelled",
                        attempts=tuple(attempts),
                    )

                if (self._clock() - global_start_time) > config.timeout_total:
                    return FetchResult(
                        final_url=current_url.normalized_url,
                        status_code=None,
                        content_type=None,
                        body_text=None,
                        redirect_history=tuple(redirect_history),
                        outcome=FetchOutcomeCode.TIMEOUT,
                        error_message="Global fetch timeout budget exceeded",
                        attempts=tuple(attempts),
                    )

                if current_url.scheme not in {"http", "https"}:
                    return FetchResult(
                        final_url=current_url.normalized_url,
                        status_code=None,
                        content_type=None,
                        body_text=None,
                        redirect_history=tuple(redirect_history),
                        outcome=FetchOutcomeCode.INVALID_URL,
                        error_message=f"Unsupported scheme: {current_url.scheme}",
                        attempts=tuple(attempts),
                    )

                # Pre-validate host safety / DNS for current hop
                try:
                    try:
                        await self._dns_resolver.resolve(
                            current_url, recorder=recorder, clock=self._clock
                        )
                    except TypeError:
                        await self._dns_resolver.resolve(current_url)
                except HostSafetyError as err:
                    outcome = (
                        FetchOutcomeCode.DNS_RESOLUTION_FAILED
                        if err.code == HostSafetyErrorCode.NO_RESOLVED_ADDRESSES
                        else FetchOutcomeCode.UNSAFE_HOST
                    )
                    if recorder is not None:
                        recorder.failure_code = (
                            SiteScanFailureCode.DNS_RESOLUTION_FAILED
                            if outcome == FetchOutcomeCode.DNS_RESOLUTION_FAILED
                            else SiteScanFailureCode.UNSAFE_HOST
                        )
                    attempts.append(
                        FetchAttempt(
                            hop_index=hop_index,
                            hop_attempt_number=1,
                            global_attempt_number=global_attempt_counter + 1,
                            request_url=current_url.normalized_url,
                            status_code=None,
                            outcome=outcome,
                            pinned_ip=None,
                            delay_before_attempt_seconds=0.0,
                            delay_source=None,
                            connection_attempts=(),
                            error_message=str(err),
                        )
                    )
                    return FetchResult(
                        final_url=current_url.normalized_url,
                        status_code=None,
                        content_type=None,
                        body_text=None,
                        redirect_history=tuple(redirect_history),
                        outcome=outcome,
                        error_message=str(err),
                        attempts=tuple(attempts),
                    )

                # Hop retry attempt loop
                hop_attempt = 0
                next_delay_sec = 0.0
                next_delay_source: DelaySource | None = None

                while True:
                    # Check global retry attempt limit and elapsed time budget
                    if global_attempt_counter >= retry_policy.max_total_fetch_attempts:
                        if recorder is not None:
                            recorder.retry_budget_exhausted = True
                            recorder.failure_code = SiteScanFailureCode.RETRY_BUDGET_EXHAUSTED
                        return FetchResult(
                            final_url=current_url.normalized_url,
                            status_code=None,
                            content_type=None,
                            body_text=None,
                            redirect_history=tuple(redirect_history),
                            outcome=FetchOutcomeCode.TIMEOUT,
                            error_message="Global total fetch attempt limit reached",
                            attempts=tuple(attempts),
                        )

                    if (self._clock() - global_start_time) > retry_policy.max_elapsed_retry_seconds:
                        if recorder is not None:
                            recorder.time_budget_exhausted = True
                            recorder.failure_code = SiteScanFailureCode.TOTAL_TIME_BUDGET_EXHAUSTED
                        return FetchResult(
                            final_url=current_url.normalized_url,
                            status_code=None,
                            content_type=None,
                            body_text=None,
                            redirect_history=tuple(redirect_history),
                            outcome=FetchOutcomeCode.TIMEOUT,
                            error_message="Global elapsed retry budget exceeded",
                            attempts=tuple(attempts),
                        )

                    hop_attempt += 1
                    global_attempt_counter += 1

                    # Re-acquire domain rate-limiting gate permission before every request attempt
                    try:
                        await self._request_gate.acquire(current_url, recorder=recorder)
                    except TypeError:
                        await self._request_gate.acquire(current_url)

                    # Prepare request-scoped connection evidence collector
                    conn_list: list[IPConnectionAttempt] = []
                    token = _connection_attempts_ctx.set(conn_list)

                    timeout = httpx.Timeout(
                        config.timeout_total,
                        connect=config.timeout_connect,
                        read=config.timeout_read,
                        write=config.timeout_write,
                        pool=config.timeout_pool,
                    )

                    attempt_outcome = FetchOutcomeCode.SUCCESS
                    status_code: int | None = None
                    content_type: str | None = None
                    body_text: str | None = None
                    error_msg: str | None = None
                    pinned_ip: str | None = None
                    response_obj: httpx.Response | None = None

                    try:
                        async with client.stream(
                            "GET",
                            current_url.normalized_url,
                            headers={"User-Agent": config.user_agent},
                            timeout=timeout,
                            follow_redirects=False,
                        ) as response:
                            response_obj = response
                            status_code = response.status_code
                            content_type = response.headers.get("content-type")
                            media_type = (content_type or "").split(";")[0].strip().lower()

                            # Check redirect (301, 302, 303, 307, 308)
                            if status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location:
                                    attempt_outcome = FetchOutcomeCode.HTTP_ERROR
                                    error_msg = (
                                        "Redirect response missing Location header "
                                        f"(status {status_code})"
                                    )
                                else:
                                    target_str = urllib.parse.urljoin(
                                        current_url.normalized_url, location
                                    )
                                    try:
                                        target_url = normalize_url(target_str)
                                        is_approved_redirect = False
                                        if config.allow_cross_domain_redirects:
                                            is_approved_redirect = True
                                        elif (
                                            target_url.registrable_domain
                                            and target_url.registrable_domain.lower()
                                            in [d.lower() for d in config.approved_redirect_domains]
                                        ):
                                            is_approved_redirect = True

                                        if (
                                            effective_redirect_validator is not None
                                            and not is_approved_redirect
                                            and not effective_redirect_validator(
                                                current_url, target_url
                                            )
                                        ):
                                            attempt_outcome = FetchOutcomeCode.OUT_OF_SCOPE_REDIRECT
                                            error_msg = (
                                                f"Redirect to {target_str} rejected by scope policy"
                                            )
                                        else:
                                            if len(redirect_history) >= config.max_redirects:
                                                attempt_outcome = (
                                                    FetchOutcomeCode.MAX_REDIRECTS_EXCEEDED
                                                )
                                                error_msg = (
                                                    "Maximum redirect limit "
                                                    f"({config.max_redirects}) exceeded"
                                                )
                                            else:
                                                redirect_history.append(
                                                    RedirectHop(
                                                        url=current_url.normalized_url,
                                                        status_code=status_code,
                                                        location=location,
                                                    )
                                                )
                                                # Record attempt history for current hop
                                                attempt_outcome = FetchOutcomeCode.SUCCESS
                                                current_url = target_url
                                                hop_index += 1
                                                if conn_list:
                                                    pinned_ip = conn_list[-1].attempted_ip
                                                attempts.append(
                                                    FetchAttempt(
                                                        hop_index=hop_index - 1,
                                                        hop_attempt_number=hop_attempt,
                                                        global_attempt_number=global_attempt_counter,
                                                        request_url=str(response.url),
                                                        status_code=status_code,
                                                        outcome=attempt_outcome,
                                                        pinned_ip=pinned_ip,
                                                        delay_before_attempt_seconds=next_delay_sec,
                                                        delay_source=next_delay_source,
                                                        connection_attempts=tuple(conn_list),
                                                        error_message=error_msg,
                                                    )
                                                )
                                                break  # Break inner attempt loop
                                    except URLNormalizationError as norm_err:
                                        attempt_outcome = FetchOutcomeCode.INVALID_URL
                                        error_msg = f"Invalid redirect Location URL: {norm_err}"

                            else:
                                # Validate content-type for non-redirects
                                if (
                                    effective_allowed_types
                                    and media_type not in effective_allowed_types
                                ):
                                    attempt_outcome = FetchOutcomeCode.UNSUPPORTED_CONTENT_TYPE
                                    error_msg = f"Unsupported content-type: {media_type}"
                                else:
                                    # Stream body up to max_response_bytes
                                    chunks: list[bytes] = []
                                    bytes_read = 0
                                    too_large = False

                                    async for chunk in response.aiter_bytes():
                                        bytes_read += len(chunk)
                                        if bytes_read > config.max_response_bytes:
                                            too_large = True
                                            break
                                        chunks.append(chunk)

                                    if too_large:
                                        attempt_outcome = FetchOutcomeCode.RESPONSE_TOO_LARGE
                                        error_msg = (
                                            "Response body exceeded maximum limit of "
                                            f"{config.max_response_bytes} bytes"
                                        )
                                    else:
                                        encoding = response.encoding or "utf-8"
                                        try:
                                            body_text = b"".join(chunks).decode(
                                                encoding, errors="replace"
                                            )
                                            if status_code >= 400:
                                                attempt_outcome = FetchOutcomeCode.HTTP_ERROR
                                                error_msg = f"HTTP status error: {status_code}"
                                            else:
                                                attempt_outcome = FetchOutcomeCode.SUCCESS
                                        except Exception as dec_err:
                                            attempt_outcome = FetchOutcomeCode.TRANSPORT_ERROR
                                            error_msg = f"Failed to decode response body: {dec_err}"

                    except httpx.ConnectTimeout:
                        attempt_outcome = FetchOutcomeCode.TIMEOUT
                        error_msg = "Connection timed out"
                        if recorder is not None:
                            recorder.failure_code = SiteScanFailureCode.CONNECT_TIMEOUT
                    except httpx.ReadTimeout:
                        attempt_outcome = FetchOutcomeCode.TIMEOUT
                        error_msg = "Read timed out"
                        if recorder is not None:
                            recorder.failure_code = SiteScanFailureCode.READ_TIMEOUT
                    except (
                        httpx.WriteTimeout,
                        httpx.PoolTimeout,
                        httpcore.TimeoutException,
                    ):
                        attempt_outcome = FetchOutcomeCode.TIMEOUT
                        error_msg = "Request timed out"
                        if recorder is not None:
                            recorder.failure_code = SiteScanFailureCode.GENERIC_TIMEOUT
                    except ssl.SSLCertVerificationError, ssl.SSLError:
                        attempt_outcome = FetchOutcomeCode.TLS_VERIFICATION_FAILED
                        error_msg = "TLS certificate verification failed"
                        if recorder is not None:
                            recorder.failure_code = SiteScanFailureCode.TLS_VERIFICATION_FAILED
                    except (
                        httpx.ConnectError,
                        httpx.NetworkError,
                        httpcore.ConnectError,
                        httpcore.NetworkError,
                    ) as net_err:
                        if _contains_tls_error(net_err):
                            attempt_outcome = FetchOutcomeCode.TLS_VERIFICATION_FAILED
                            error_msg = "TLS certificate verification failed"
                            if recorder is not None:
                                recorder.failure_code = SiteScanFailureCode.TLS_VERIFICATION_FAILED
                        else:
                            attempt_outcome = FetchOutcomeCode.TRANSPORT_ERROR
                            error_msg = f"Transport network error: {net_err}"
                            if recorder is not None:
                                recorder.failure_code = SiteScanFailureCode.TRANSPORT_ERROR
                    except Exception as gen_err:
                        attempt_outcome = FetchOutcomeCode.TRANSPORT_ERROR
                        error_msg = f"Unexpected network error: {gen_err}"
                        if recorder is not None:
                            recorder.failure_code = SiteScanFailureCode.UNEXPECTED_INTERNAL_ERROR
                    finally:
                        _connection_attempts_ctx.reset(token)

                    if conn_list:
                        pinned_ip = conn_list[-1].attempted_ip

                    attempt_record = FetchAttempt(
                        hop_index=hop_index,
                        hop_attempt_number=hop_attempt,
                        global_attempt_number=global_attempt_counter,
                        request_url=current_url.normalized_url,
                        status_code=status_code,
                        outcome=attempt_outcome,
                        pinned_ip=pinned_ip,
                        delay_before_attempt_seconds=next_delay_sec,
                        delay_source=next_delay_source,
                        connection_attempts=tuple(conn_list),
                        error_message=error_msg,
                    )
                    attempts.append(attempt_record)

                    if attempt_outcome == FetchOutcomeCode.SUCCESS and status_code == 200:
                        return FetchResult(
                            final_url=current_url.normalized_url,
                            status_code=status_code,
                            content_type=content_type,
                            body_text=body_text,
                            redirect_history=tuple(redirect_history),
                            outcome=FetchOutcomeCode.SUCCESS,
                            attempts=tuple(attempts),
                        )

                    # Determine if fetch attempt can be retried
                    can_retry, retry_reason = should_retry_fetch(
                        "GET", attempt_outcome, status_code
                    )

                    if not can_retry or hop_attempt >= retry_policy.max_attempts_per_hop:
                        return FetchResult(
                            final_url=current_url.normalized_url,
                            status_code=status_code,
                            content_type=content_type,
                            body_text=body_text,
                            redirect_history=tuple(redirect_history),
                            outcome=attempt_outcome,
                            error_message=error_msg,
                            attempts=tuple(attempts),
                        )

                    retries_occurred += 1

                    # Calculate retry backoff delay
                    if retry_reason == DelaySource.RETRY_AFTER_HEADER and response_obj is not None:
                        retry_after_hdr = response_obj.headers.get("retry-after", "")
                        parsed_delay = parse_retry_after_header(
                            retry_after_hdr,
                            max_allowed_seconds=retry_policy.max_retry_after_seconds,
                            wall_clock=self._wall_clock,
                        )
                        if parsed_delay is not None:
                            next_delay_sec, next_delay_source = parsed_delay
                        else:
                            next_delay_sec, next_delay_source = calculate_backoff_delay(
                                hop_attempt, retry_policy, self._jitter_source
                            )
                    else:
                        next_delay_sec, next_delay_source = calculate_backoff_delay(
                            hop_attempt, retry_policy, self._jitter_source
                        )

                    if recorder is not None:
                        recorder.total_retry_delay_seconds += next_delay_sec

                    # Safely close response stream before backoff sleep
                    if response_obj is not None:
                        await response_obj.aclose()

                    # Check cancellation before sleep
                    if self._cancellation_checker is not None and self._cancellation_checker():
                        return FetchResult(
                            final_url=current_url.normalized_url,
                            status_code=status_code,
                            content_type=content_type,
                            body_text=body_text,
                            redirect_history=tuple(redirect_history),
                            outcome=FetchOutcomeCode.TIMEOUT,
                            error_message="Fetch operation cancelled prior to retry sleep",
                            attempts=tuple(attempts),
                        )

                    if next_delay_sec > 0.0 and self._sleeper is not None:
                        await self._sleeper(next_delay_sec)

                    # Check cancellation after sleep
                    if self._cancellation_checker is not None and self._cancellation_checker():
                        return FetchResult(
                            final_url=current_url.normalized_url,
                            status_code=status_code,
                            content_type=content_type,
                            body_text=body_text,
                            redirect_history=tuple(redirect_history),
                            outcome=FetchOutcomeCode.TIMEOUT,
                            error_message="Fetch operation cancelled after retry sleep",
                            attempts=tuple(attempts),
                        )

        finally:
            if recorder is not None:
                fetch_elapsed = max(0.0, self._clock() - global_start_time)
                recorder.http_fetch_duration_seconds += fetch_elapsed
                recorder.retry_count += retries_occurred
                recorder.redirect_count += len(redirect_history)
                if status_code is not None:
                    recorder.http_status = status_code
            if should_close_client and client:
                await client.aclose()
