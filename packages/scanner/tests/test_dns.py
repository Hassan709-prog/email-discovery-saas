"""Tests for scanner-core DNS resolution and safety policy validation."""

import asyncio

import pytest

from email_scanner.dns import SystemDNSResolver
from email_scanner.errors import HostSafetyError, HostSafetyErrorCode
from email_scanner.models import HostType, NormalizedURL
from email_scanner.normalization import normalize_url


class FakeDNSResolver:
    """Deterministic fake DNS resolver for unit testing."""

    def __init__(self, mapping: dict[str, tuple[str, ...]] | None = None) -> None:
        self.mapping = mapping or {}

    async def resolve(self, url: NormalizedURL) -> tuple[str, ...]:
        from email_scanner.host_safety import validate_public_host

        if url.host_type in {HostType.IPV4, HostType.IPV6}:
            return validate_public_host(url, ())

        if url.hostname not in self.mapping:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message=f"DNS lookup failed for {url.hostname}",
            )

        addresses = self.mapping[url.hostname]
        return validate_public_host(url, addresses)


def test_fake_dns_resolver_success() -> None:
    async def _test() -> None:
        resolver = FakeDNSResolver(
            {"example.com": ("93.184.215.14", "2606:2800:220:1:248:1893:25c8:1946")}
        )
        url = normalize_url("https://example.com/page")
        resolved = await resolver.resolve(url)
        assert len(resolved) == 2
        assert resolved[0] == "93.184.215.14"
        assert resolved[1] == "2606:2800:220:1:248:1893:25c8:1946"

    asyncio.run(_test())


def test_fake_dns_resolver_mixed_ips_blocked() -> None:
    async def _test() -> None:
        resolver = FakeDNSResolver({"mixed.com": ("93.184.215.14", "192.168.1.1")})
        url = normalize_url("https://mixed.com")
        with pytest.raises(HostSafetyError) as exc_info:
            await resolver.resolve(url)
        assert exc_info.value.code == HostSafetyErrorCode.NON_PUBLIC_IP_ADDRESS

    asyncio.run(_test())


def test_fake_dns_resolver_unresolved_host() -> None:
    async def _test() -> None:
        resolver = FakeDNSResolver({})
        url = normalize_url("https://nonexistent.example")
        with pytest.raises(HostSafetyError) as exc_info:
            await resolver.resolve(url)
        assert exc_info.value.code == HostSafetyErrorCode.NO_RESOLVED_ADDRESSES

    asyncio.run(_test())


def test_system_dns_resolver_ip_literal() -> None:
    async def _test() -> None:
        resolver = SystemDNSResolver()
        url = normalize_url("http://93.184.215.14/test")
        resolved = await resolver.resolve(url)
        assert resolved == ("93.184.215.14",)

    asyncio.run(_test())
