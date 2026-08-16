"""Asynchronous HTTP fetcher for scanner-core.

Provides safe, typed HTTP fetching with DNS pre-validation, redirect tracking,
content-type checking, response size streaming limits, and error mapping.
"""

import urllib.parse
from collections.abc import Callable

import httpx

from email_scanner.dns import AsyncDNSResolver, SystemDNSResolver
from email_scanner.errors import (
    FetchOutcomeCode,
    HostSafetyError,
    HostSafetyErrorCode,
    URLNormalizationError,
)
from email_scanner.models import (
    FetchConfig,
    FetchResult,
    NormalizedURL,
    RedirectHop,
)
from email_scanner.normalization import normalize_url
from email_scanner.request_gate import (
    DomainRequestGate,
    RequestGateProtocol,
)


class AsyncHTTPFetcher:
    """Asynchronous HTTP fetcher enforcing scanner-core safety policies."""

    def __init__(
        self,
        dns_resolver: AsyncDNSResolver | None = None,
        client: httpx.AsyncClient | None = None,
        config: FetchConfig | None = None,
        redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
        request_gate: RequestGateProtocol | None = None,
    ) -> None:
        self._dns_resolver = dns_resolver or SystemDNSResolver()
        self._client = client
        self._config = config or FetchConfig()
        self._redirect_validator = redirect_validator
        self._request_gate: RequestGateProtocol = request_gate or DomainRequestGate()

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
    ) -> FetchResult:
        """Fetch content for a URL safely and asynchronously."""
        effective_redirect_validator = (
            redirect_validator if redirect_validator is not None else self._redirect_validator
        )

        config = self._config
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
                )
        else:
            normalized_target = url

        redirect_history: list[RedirectHop] = []
        current_url = normalized_target

        # Resolve initial DNS & safety
        try:
            await self._dns_resolver.resolve(current_url)
        except HostSafetyError as err:
            outcome = (
                FetchOutcomeCode.DNS_RESOLUTION_FAILED
                if err.code == HostSafetyErrorCode.NO_RESOLVED_ADDRESSES
                else FetchOutcomeCode.UNSAFE_HOST
            )
            return FetchResult(
                final_url=current_url.normalized_url,
                status_code=None,
                content_type=None,
                body_text=None,
                redirect_history=(),
                outcome=outcome,
                error_message=str(err),
            )

        timeout = httpx.Timeout(
            connect=config.timeout_connect,
            read=config.timeout_read,
            write=config.timeout_write,
            pool=config.timeout_pool,
        )

        should_close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(follow_redirects=False)
            should_close_client = True

        try:
            while True:
                if current_url.scheme not in {"http", "https"}:
                    return FetchResult(
                        final_url=current_url.normalized_url,
                        status_code=None,
                        content_type=None,
                        body_text=None,
                        redirect_history=tuple(redirect_history),
                        outcome=FetchOutcomeCode.INVALID_URL,
                        error_message=f"Unsupported scheme: {current_url.scheme}",
                    )

                # Acquire permission from domain request gate before HTTP request attempt
                await self._request_gate.acquire(current_url)

                try:
                    async with client.stream(
                        "GET",
                        current_url.normalized_url,
                        headers={"User-Agent": config.user_agent},
                        timeout=timeout,
                        follow_redirects=False,
                    ) as response:
                        status_code = response.status_code
                        content_type_header = response.headers.get("content-type")

                        # Check for HTTP redirect
                        if (
                            status_code in {301, 302, 303, 307, 308}
                            and "location" in response.headers
                        ):
                            location = response.headers["location"]
                            if len(redirect_history) >= config.max_redirects:
                                return FetchResult(
                                    final_url=current_url.normalized_url,
                                    status_code=status_code,
                                    content_type=content_type_header,
                                    body_text=None,
                                    redirect_history=tuple(redirect_history),
                                    outcome=FetchOutcomeCode.MAX_REDIRECTS_EXCEEDED,
                                    error_message=(
                                        f"Exceeded maximum redirects ({config.max_redirects})"
                                    ),
                                )

                            # Resolve relative/absolute redirect URL
                            next_raw = urllib.parse.urljoin(current_url.normalized_url, location)
                            redirect_history.append(
                                RedirectHop(
                                    url=current_url.normalized_url,
                                    status_code=status_code,
                                    location=location,
                                )
                            )

                            try:
                                current_url = normalize_url(next_raw)
                            except URLNormalizationError as err:
                                return FetchResult(
                                    final_url=next_raw,
                                    status_code=None,
                                    content_type=None,
                                    body_text=None,
                                    redirect_history=tuple(redirect_history),
                                    outcome=FetchOutcomeCode.INVALID_URL,
                                    error_message=str(err),
                                )

                            if effective_redirect_validator is not None:
                                if not effective_redirect_validator(normalized_target, current_url):
                                    return FetchResult(
                                        final_url=current_url.normalized_url,
                                        status_code=status_code,
                                        content_type=content_type_header,
                                        body_text=None,
                                        redirect_history=tuple(redirect_history),
                                        outcome=FetchOutcomeCode.UNSAFE_HOST,
                                        error_message="Redirect target is out of crawl scope",
                                    )

                            try:
                                await self._dns_resolver.resolve(current_url)
                            except HostSafetyError as err:
                                outcome = (
                                    FetchOutcomeCode.DNS_RESOLUTION_FAILED
                                    if err.code == HostSafetyErrorCode.NO_RESOLVED_ADDRESSES
                                    else FetchOutcomeCode.UNSAFE_HOST
                                )
                                return FetchResult(
                                    final_url=current_url.normalized_url,
                                    status_code=None,
                                    content_type=None,
                                    body_text=None,
                                    redirect_history=tuple(redirect_history),
                                    outcome=outcome,
                                    error_message=str(err),
                                )
                            continue

                        # Content-Type validation
                        media_type = ""
                        if content_type_header:
                            media_type = content_type_header.split(";")[0].strip().lower()

                        if media_type not in effective_allowed_types:
                            return FetchResult(
                                final_url=current_url.normalized_url,
                                status_code=status_code,
                                content_type=content_type_header,
                                body_text=None,
                                redirect_history=tuple(redirect_history),
                                outcome=FetchOutcomeCode.UNSUPPORTED_CONTENT_TYPE,
                                error_message=f"Unsupported Content-Type: {content_type_header}",
                            )

                        # Stream body with response size limit
                        body_bytes = bytearray()
                        size_exceeded = False
                        async for chunk in response.aiter_bytes():
                            body_bytes.extend(chunk)
                            if len(body_bytes) > config.max_response_bytes:
                                size_exceeded = True
                                break

                        if size_exceeded:
                            return FetchResult(
                                final_url=current_url.normalized_url,
                                status_code=status_code,
                                content_type=content_type_header,
                                body_text=None,
                                redirect_history=tuple(redirect_history),
                                outcome=FetchOutcomeCode.RESPONSE_TOO_LARGE,
                                error_message=(
                                    f"Response body exceeded maximum limit of "
                                    f"{config.max_response_bytes} bytes"
                                ),
                            )

                        encoding = response.encoding or "utf-8"
                        try:
                            body_text = body_bytes.decode(encoding, errors="replace")
                        except Exception:
                            body_text = body_bytes.decode("utf-8", errors="replace")

                        outcome = (
                            FetchOutcomeCode.SUCCESS
                            if 200 <= status_code < 300
                            else FetchOutcomeCode.HTTP_ERROR
                        )

                        return FetchResult(
                            final_url=current_url.normalized_url,
                            status_code=status_code,
                            content_type=content_type_header,
                            body_text=body_text,
                            redirect_history=tuple(redirect_history),
                            outcome=outcome,
                        )
                except httpx.TimeoutException as err:
                    return FetchResult(
                        final_url=current_url.normalized_url,
                        status_code=None,
                        content_type=None,
                        body_text=None,
                        redirect_history=tuple(redirect_history),
                        outcome=FetchOutcomeCode.TIMEOUT,
                        error_message=f"Request timed out: {err}",
                    )
                except httpx.HTTPError as err:
                    return FetchResult(
                        final_url=current_url.normalized_url,
                        status_code=None,
                        content_type=None,
                        body_text=None,
                        redirect_history=tuple(redirect_history),
                        outcome=FetchOutcomeCode.TRANSPORT_ERROR,
                        error_message=f"HTTP error: {err}",
                    )
        finally:
            if should_close_client:
                await client.aclose()
