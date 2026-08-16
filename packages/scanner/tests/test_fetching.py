"""Tests for scanner-core AsyncHTTPFetcher."""

import asyncio

import httpx
import pytest

from email_scanner.errors import (
    FetchConfigError,
    FetchConfigErrorCode,
    FetchOutcomeCode,
    HostSafetyError,
    HostSafetyErrorCode,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.host_safety import validate_public_host
from email_scanner.models import FetchConfig, HostType, NormalizedURL


class FakeDNSResolver:
    def __init__(self, mapping: dict[str, tuple[str, ...]] | None = None) -> None:
        self.mapping = mapping or {
            "example.com": ("93.184.215.14",),
            "redirect.com": ("93.184.215.15",),
            "final.com": ("93.184.215.16",),
            "mixed.com": ("93.184.215.14", "10.0.0.1"),
        }

    async def resolve(self, url: NormalizedURL) -> tuple[str, ...]:
        if url.host_type in {HostType.IPV4, HostType.IPV6}:
            return validate_public_host(url, ())

        if url.hostname not in self.mapping:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message=f"DNS failed for {url.hostname}",
            )

        return validate_public_host(url, self.mapping[url.hostname])


def test_fetch_config_validation() -> None:
    with pytest.raises(FetchConfigError) as exc_info:
        FetchConfig(timeout_connect=-1.0)
    assert exc_info.value.code == FetchConfigErrorCode.INVALID_TIMEOUT

    with pytest.raises(FetchConfigError) as exc_info:
        FetchConfig(max_redirects=-1)
    assert exc_info.value.code == FetchConfigErrorCode.INVALID_MAX_REDIRECTS

    with pytest.raises(FetchConfigError) as exc_info:
        FetchConfig(user_agent="")
    assert exc_info.value.code == FetchConfigErrorCode.INVALID_USER_AGENT

    with pytest.raises(FetchConfigError) as exc_info:
        FetchConfig(max_response_bytes=0)
    assert exc_info.value.code == FetchConfigErrorCode.INVALID_MAX_RESPONSE_BYTES


def test_successful_html_fetch() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<html><body>Hello World</body></html>",
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://example.com/page")
        assert result.outcome == FetchOutcomeCode.SUCCESS
        assert result.status_code == 200
        assert result.content_type == "text/html; charset=utf-8"
        assert result.body_text == "<html><body>Hello World</body></html>"
        assert result.redirect_history == ()

    asyncio.run(_test())


def test_response_size_rejection() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                content=b"A" * 500,
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        config = FetchConfig(max_response_bytes=100)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client, config=config)

        result = await fetcher.fetch("https://example.com/large")
        assert result.outcome == FetchOutcomeCode.RESPONSE_TOO_LARGE
        assert result.status_code == 200
        assert result.body_text is None
        assert "exceeded maximum limit" in (result.error_message or "")

    asyncio.run(_test())


def test_binary_content_rejection() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/octet-stream"},
                content=b"\x00\x01\x02\x03",
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://example.com/file.bin")
        assert result.outcome == FetchOutcomeCode.UNSUPPORTED_CONTENT_TYPE
        assert result.status_code == 200
        assert result.body_text is None

    asyncio.run(_test())


def test_timeout_error_classification() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Read timed out")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://example.com/slow")
        assert result.outcome == FetchOutcomeCode.TIMEOUT
        assert result.status_code is None
        assert "timed out" in (result.error_message or "")

    asyncio.run(_test())


def test_transport_error_classification() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://example.com/refused")
        assert result.outcome == FetchOutcomeCode.TRANSPORT_ERROR
        assert result.status_code is None

    asyncio.run(_test())


def test_relative_and_absolute_redirect_success() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(302, headers={"Location": "/relative_step"})
            if request.url.path == "/relative_step":
                return httpx.Response(301, headers={"Location": "https://final.com/finish"})
            if request.url.host == "final.com" and request.url.path == "/finish":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    content=b"<html>Destination</body>",
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://example.com/start")
        assert result.outcome == FetchOutcomeCode.SUCCESS
        assert result.final_url == "https://final.com/finish"
        assert len(result.redirect_history) == 2
        assert result.redirect_history[0].url == "https://example.com/start"
        assert result.redirect_history[0].location == "/relative_step"
        assert result.redirect_history[1].url == "https://example.com/relative_step"
        assert result.redirect_history[1].location == "https://final.com/finish"

    asyncio.run(_test())


def test_redirect_limit_exceeded() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "/loop"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        config = FetchConfig(max_redirects=3)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client, config=config)

        result = await fetcher.fetch("https://example.com/loop")
        assert result.outcome == FetchOutcomeCode.MAX_REDIRECTS_EXCEEDED
        assert len(result.redirect_history) == 3

    asyncio.run(_test())


def test_redirect_to_private_ip_blocked() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://example.com/ssrf")
        assert result.outcome == FetchOutcomeCode.UNSAFE_HOST
        assert len(result.redirect_history) == 1

    asyncio.run(_test())


def test_mixed_public_private_dns_blocked() -> None:
    async def _test() -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200))
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://mixed.com/test")
        assert result.outcome == FetchOutcomeCode.UNSAFE_HOST

    asyncio.run(_test())


def test_dns_resolution_failed_outcome() -> None:
    async def _test() -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200))
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver({}), client=client)

        result = await fetcher.fetch("https://unknown.com/test")
        assert result.outcome == FetchOutcomeCode.DNS_RESOLUTION_FAILED

    asyncio.run(_test())


def test_non_2xx_http_error_preserves_status_code() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                headers={"Content-Type": "text/html"},
                content=b"<html>Not Found</html>",
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        result = await fetcher.fetch("https://example.com/missing")
        assert result.outcome == FetchOutcomeCode.HTTP_ERROR
        assert result.status_code == 404
        assert result.body_text == "<html>Not Found</html>"

    asyncio.run(_test())
