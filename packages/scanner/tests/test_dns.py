"""Tests for scanner-core DNS resolution and safety policy validation."""

import asyncio
import socket
from unittest.mock import patch

import pytest

from email_scanner.dns import SystemDNSResolver, is_permanent_dns_error
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


def test_is_permanent_dns_error_symbolic_constants() -> None:
    noname = getattr(socket, "EAI_NONAME", None)
    if isinstance(noname, int):
        err = socket.gaierror(noname, "Name or service not known")
        assert is_permanent_dns_error(err) is True

    again = getattr(socket, "EAI_AGAIN", None)
    if isinstance(again, int):
        err = socket.gaierror(again, "Temporary failure in name resolution")
        assert is_permanent_dns_error(err) is False

    unknown_err = socket.gaierror(-99999, "Arbitrary unknown error")
    assert is_permanent_dns_error(unknown_err) is False


def test_system_dns_resolver_eai_noname() -> None:
    async def _test() -> None:
        errno_val = getattr(socket, "EAI_NONAME", -2)
        with patch("socket.getaddrinfo", side_effect=socket.gaierror(errno_val, "mock noname")):
            resolver = SystemDNSResolver()
            with pytest.raises(HostSafetyError) as exc_info:
                await resolver.resolve_host("nonexistent.example")
            assert exc_info.value.code == HostSafetyErrorCode.DNS_NAME_NOT_FOUND

    asyncio.run(_test())


def test_system_dns_resolver_eai_nodata_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _test() -> None:
        # Test with synthetic/isolated errno to ensure portable test behavior across OSes
        mock_nodata_errno = -555
        monkeypatch.setattr(socket, "EAI_NODATA", mock_nodata_errno, raising=False)
        with patch(
            "socket.getaddrinfo", side_effect=socket.gaierror(mock_nodata_errno, "mock nodata")
        ):
            resolver = SystemDNSResolver()
            with pytest.raises(HostSafetyError) as exc_info:
                await resolver.resolve_host("nodata.example")
            assert exc_info.value.code == HostSafetyErrorCode.DNS_NAME_NOT_FOUND

    asyncio.run(_test())


def test_system_dns_resolver_aliasing_eai_nodata_and_noname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _test() -> None:
        # Simulate environments where EAI_NODATA and EAI_NONAME share numeric value
        shared_errno = 11001
        monkeypatch.setattr(socket, "EAI_NONAME", shared_errno, raising=False)
        monkeypatch.setattr(socket, "EAI_NODATA", shared_errno, raising=False)

        err = socket.gaierror(shared_errno, "mock aliased")
        assert is_permanent_dns_error(err) is True

        with patch("socket.getaddrinfo", side_effect=err):
            resolver = SystemDNSResolver()
            with pytest.raises(HostSafetyError) as exc_info:
                await resolver.resolve_host("aliased.example")
            assert exc_info.value.code == HostSafetyErrorCode.DNS_NAME_NOT_FOUND

    asyncio.run(_test())


def test_system_dns_resolver_eai_again() -> None:
    async def _test() -> None:
        errno_val = getattr(socket, "EAI_AGAIN", -3)
        with patch("socket.getaddrinfo", side_effect=socket.gaierror(errno_val, "mock again")):
            resolver = SystemDNSResolver()
            with pytest.raises(HostSafetyError) as exc_info:
                await resolver.resolve_host("transient.example")
            assert exc_info.value.code == HostSafetyErrorCode.NO_RESOLVED_ADDRESSES

    asyncio.run(_test())


def test_system_dns_resolver_unknown_gaierror() -> None:
    async def _test() -> None:
        with patch("socket.getaddrinfo", side_effect=socket.gaierror(-88888, "mock unknown error")):
            resolver = SystemDNSResolver()
            with pytest.raises(HostSafetyError) as exc_info:
                await resolver.resolve_host("unknown.example")
            assert exc_info.value.code == HostSafetyErrorCode.NO_RESOLVED_ADDRESSES

    asyncio.run(_test())


def test_system_dns_resolver_empty_address_results() -> None:
    async def _test() -> None:
        with patch("socket.getaddrinfo", return_value=[]):
            resolver = SystemDNSResolver()
            with pytest.raises(HostSafetyError) as exc_info:
                await resolver.resolve_host("empty.example")
            assert exc_info.value.code == HostSafetyErrorCode.DNS_NAME_NOT_FOUND

    asyncio.run(_test())


def test_system_dns_resolver_empty_and_invalid_hostname() -> None:
    async def _test() -> None:
        resolver = SystemDNSResolver()
        for invalid_host in ("", "   ", "[]"):
            with pytest.raises(HostSafetyError) as exc_info:
                await resolver.resolve_host(invalid_host)
            assert exc_info.value.code == HostSafetyErrorCode.NO_RESOLVED_ADDRESSES

    asyncio.run(_test())
