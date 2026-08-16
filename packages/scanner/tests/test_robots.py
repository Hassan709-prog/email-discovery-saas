"""Tests for scanner-core RobotsPolicyEvaluator."""

import asyncio

import httpx

from email_scanner.errors import HostSafetyError, HostSafetyErrorCode
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.host_safety import validate_public_host
from email_scanner.models import (
    FetchConfig,
    HostType,
    NormalizedURL,
    RobotsDecisionCode,
)
from email_scanner.robots import RobotsPolicyEvaluator


class FakeDNSResolver:
    def __init__(self, mapping: dict[str, tuple[str, ...]] | None = None) -> None:
        self.mapping = mapping or {
            "example.com": ("93.184.215.14",),
            "denied.com": ("93.184.215.15",),
            "missing.com": ("93.184.215.16",),
            "error.com": ("93.184.215.17",),
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


def test_robots_allow_disallow_and_crawl_delay() -> None:
    async def _test() -> None:
        robots_txt = (
            "User-agent: EmailDiscoveryBot\n"
            "Disallow: /private/\n"
            "Allow: /public/\n"
            "Crawl-delay: 2.5\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("User-Agent") == "CustomHTTPUserAgent/1.0"
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/plain"},
                    content=robots_txt.encode("utf-8"),
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        config = FetchConfig(
            user_agent="CustomHTTPUserAgent/1.0",
            robots_user_agent_token="EmailDiscoveryBot",
        )
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client, config=config)
        evaluator = RobotsPolicyEvaluator(fetcher=fetcher, config=config)

        # Test allowed URL
        decision1 = await evaluator.evaluate("https://example.com/public/page")
        assert decision1.decision == RobotsDecisionCode.ALLOWED
        assert decision1.crawl_delay == 2.5

        # Test disallowed URL
        decision2 = await evaluator.evaluate("https://example.com/private/secret")
        assert decision2.decision == RobotsDecisionCode.DISALLOWED
        assert decision2.crawl_delay == 2.5

    asyncio.run(_test())


def test_robots_401_403_deny() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, headers={"Content-Type": "text/plain"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)
        evaluator = RobotsPolicyEvaluator(fetcher=fetcher)

        decision = await evaluator.evaluate("https://denied.com/any/page")
        assert decision.decision == RobotsDecisionCode.DISALLOWED
        assert "403" in decision.reason

    asyncio.run(_test())


def test_robots_ordinary_404_allow() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, headers={"Content-Type": "text/plain"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)
        evaluator = RobotsPolicyEvaluator(fetcher=fetcher)

        decision = await evaluator.evaluate("https://missing.com/any/page")
        assert decision.decision == RobotsDecisionCode.ALLOWED
        assert "unavailable" in decision.reason

    asyncio.run(_test())


def test_robots_429_and_5xx_temporary_deny() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "error.com":
                return httpx.Response(503, headers={"Content-Type": "text/plain"})
            return httpx.Response(429, headers={"Content-Type": "text/plain"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)
        evaluator = RobotsPolicyEvaluator(fetcher=fetcher)

        d1 = await evaluator.evaluate("https://error.com/page")
        assert d1.decision == RobotsDecisionCode.TEMPORARY_FAILURE
        assert "503" in d1.reason

        d2 = await evaluator.evaluate("https://example.com/page")
        assert d2.decision == RobotsDecisionCode.TEMPORARY_FAILURE
        assert "429" in d2.reason

    asyncio.run(_test())


def test_robots_cache_hit_and_expiry() -> None:
    async def _test() -> None:
        fetch_count = 0
        current_time = 1000.0

        def fake_clock() -> float:
            return current_time

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal fetch_count
            fetch_count += 1
            return httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=b"User-agent: *\nDisallow: /admin/",
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)
        evaluator = RobotsPolicyEvaluator(fetcher=fetcher, cache_ttl=100.0, clock=fake_clock)

        # First fetch (cache miss)
        res1 = await evaluator.evaluate("https://example.com/admin/page")
        assert res1.decision == RobotsDecisionCode.DISALLOWED
        assert fetch_count == 1

        # Second fetch at t=1050 (cache hit)
        current_time = 1050.0
        res2 = await evaluator.evaluate("https://example.com/public/page")
        assert res2.decision == RobotsDecisionCode.ALLOWED
        assert fetch_count == 1  # No additional network call!

        # Third fetch at t=1101 (cache expired, t > 1000 + 100)
        current_time = 1101.0
        res3 = await evaluator.evaluate("https://example.com/admin/page")
        assert res3.decision == RobotsDecisionCode.DISALLOWED
        assert fetch_count == 2  # Fetched again after expiry!

    asyncio.run(_test())


def test_stable_deterministic_results() -> None:
    async def _test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=b"User-agent: *\nDisallow: /bad/",
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = AsyncHTTPFetcher(dns_resolver=FakeDNSResolver(), client=client)

        evaluator1 = RobotsPolicyEvaluator(fetcher=fetcher, clock=lambda: 100.0)
        evaluator2 = RobotsPolicyEvaluator(fetcher=fetcher, clock=lambda: 100.0)

        res1 = await evaluator1.evaluate("https://example.com/bad/thing")
        res2 = await evaluator2.evaluate("https://example.com/bad/thing")

        assert res1 == res2

    asyncio.run(_test())
