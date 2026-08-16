"""Security invariant and network pinning unit tests for scanner-core transport."""

import asyncio
import ssl
from typing import Any

import httpcore
import httpx
import pytest

from email_scanner.dns import AsyncDNSResolver
from email_scanner.errors import HostSafetyError, HostSafetyErrorCode
from email_scanner.models import NormalizedURL, PinningConfig
from email_scanner.pinned_transport import (
    PinnedAsyncHTTPTransport,
    PinnedAsyncNetworkBackend,
    PinnedAsyncNetworkStream,
    _connection_attempts_ctx,  # pyright: ignore[reportPrivateUsage]
)


class MockNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, target: str) -> None:
        self.target = target
        self.read_calls = 0
        self.write_calls = 0
        self.aclose_calls = 0
        self.start_tls_calls: list[tuple[Any, str | None]] = []

    async def read(self, max_bytes: int = 4096, timeout: float | None = None) -> bytes:
        self.read_calls += 1
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.write_calls += 1

    async def aclose(self) -> None:
        self.aclose_calls += 1

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.start_tls_calls.append((ssl_context, server_hostname))
        return self

    def get_extra_info(self, info: str, default: Any = None) -> Any:
        return default


class MockNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, fail_ips: set[str] | None = None) -> None:
        self.connect_tcp_calls: list[tuple[str, int]] = []
        self.fail_ips = fail_ips or set()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connect_tcp_calls.append((host, port))
        if host in self.fail_ips:
            raise httpcore.ConnectError(f"Simulated connection failure to {host}")
        return MockNetworkStream(target=host)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0.0)


class MockDNSResolver(AsyncDNSResolver):
    def __init__(self, map_data: dict[str, list[str]] | None = None) -> None:
        self.map_data = map_data or {}

    async def resolve(self, url: NormalizedURL) -> tuple[str, ...]:
        return await self.resolve_host(url.hostname, url.port or 80)

    async def resolve_host(self, hostname: str, port: int = 80) -> tuple[str, ...]:
        clean_host = hostname.strip().strip("[]")
        if clean_host in self.map_data:
            ips = self.map_data[clean_host]
            # Verify no private IP in mixed results
            if any(ip.startswith("127.") or ip == "::1" for ip in ips) and len(ips) > 1:
                raise HostSafetyError(
                    code=HostSafetyErrorCode.NON_PUBLIC_IP_ADDRESS,
                    message=f"Mixed public/private DNS result rejected for {clean_host}",
                )
            return tuple(ips)
        if clean_host == "127.0.0.1" or clean_host == "::1":
            raise HostSafetyError(
                code=HostSafetyErrorCode.NON_PUBLIC_IP_ADDRESS,
                message=f"Private IP rejected: {clean_host}",
            )
        return ("93.184.216.34",)


def test_pinned_stream_wrapper_delegation_and_sni_enforcement() -> None:
    async def _test() -> None:
        raw_stream = MockNetworkStream(target="93.184.216.34")
        wrapped = PinnedAsyncNetworkStream(raw_stream, original_hostname="example.com")

        await wrapped.write(b"data")
        assert raw_stream.write_calls == 1

        await wrapped.read(1024)
        assert raw_stream.read_calls == 1

        # Test start_tls with None server_hostname forces original hostname
        ssl_ctx = ssl.create_default_context()
        tls_stream = await wrapped.start_tls(ssl_ctx)
        assert isinstance(tls_stream, PinnedAsyncNetworkStream)
        assert raw_stream.start_tls_calls[0][1] == "example.com"

        # Test start_tls with inconsistent IP SNI raises HostSafetyError (fails closed)
        with pytest.raises(HostSafetyError) as exc_info:
            await wrapped.start_tls(ssl_ctx, server_hostname="93.184.216.34")
        assert exc_info.value.code == HostSafetyErrorCode.BLOCKED_HOSTNAME

        await wrapped.aclose()
        assert raw_stream.aclose_calls == 1

    asyncio.run(_test())


def test_connect_tcp_receives_validated_ip_never_hostname() -> None:
    async def _test() -> None:
        dns_resolver = MockDNSResolver({"acme.com": ["93.184.216.34"]})
        mock_backend = MockNetworkBackend()
        pinned_backend = PinnedAsyncNetworkBackend(
            dns_resolver=dns_resolver,
            network_backend=mock_backend,
        )

        stream = await pinned_backend.connect_tcp("acme.com", 443)
        assert isinstance(stream, PinnedAsyncNetworkStream)

        # Underlying backend MUST receive validated IP, NEVER original hostname
        assert mock_backend.connect_tcp_calls == [("93.184.216.34", 443)]

    asyncio.run(_test())


def test_mixed_public_private_dns_causes_zero_connection_attempts() -> None:
    async def _test() -> None:
        dns_resolver = MockDNSResolver({"rebound.com": ["93.184.216.34", "127.0.0.1"]})
        mock_backend = MockNetworkBackend()
        pinned_backend = PinnedAsyncNetworkBackend(
            dns_resolver=dns_resolver,
            network_backend=mock_backend,
        )

        with pytest.raises(HostSafetyError) as exc_info:
            await pinned_backend.connect_tcp("rebound.com", 80)

        assert exc_info.value.code == HostSafetyErrorCode.NON_PUBLIC_IP_ADDRESS
        # ZERO TCP connection attempts allowed when mixed result contains private IP
        assert len(mock_backend.connect_tcp_calls) == 0

    asyncio.run(_test())


def test_deterministic_ip_failover() -> None:
    async def _test() -> None:
        dns_resolver = MockDNSResolver({"multi.com": ["198.51.100.1", "198.51.100.2"]})
        mock_backend = MockNetworkBackend(fail_ips={"198.51.100.1"})
        pinned_backend = PinnedAsyncNetworkBackend(
            dns_resolver=dns_resolver,
            network_backend=mock_backend,
            pinning_config=PinningConfig(max_ip_failover_attempts=2),
        )

        stream = await pinned_backend.connect_tcp("multi.com", 80)
        assert isinstance(stream, PinnedAsyncNetworkStream)

        # Failed on 1st IP 198.51.100.1, succeeded on 2nd IP 198.51.100.2
        assert mock_backend.connect_tcp_calls == [
            ("198.51.100.1", 80),
            ("198.51.100.2", 80),
        ]

    asyncio.run(_test())


def test_request_scoped_connection_evidence_isolation() -> None:
    async def _test() -> None:
        dns_resolver = MockDNSResolver({"site.com": ["93.184.216.34"]})
        mock_backend = MockNetworkBackend()
        pinned_backend = PinnedAsyncNetworkBackend(
            dns_resolver=dns_resolver,
            network_backend=mock_backend,
        )

        token = _connection_attempts_ctx.set([])  # pyright: ignore[reportPrivateUsage]
        try:
            await pinned_backend.connect_tcp("site.com", 80)
            recorded = _connection_attempts_ctx.get()  # pyright: ignore[reportPrivateUsage]
            assert recorded is not None
            assert len(recorded) == 1
            assert recorded[0].target_host == "site.com"
            assert recorded[0].attempted_ip == "93.184.216.34"
            assert recorded[0].success is True
        finally:
            _connection_attempts_ctx.reset(token)  # pyright: ignore[reportPrivateUsage]

        # Outside context, default is None and does not leak
        assert _connection_attempts_ctx.get() is None  # pyright: ignore[reportPrivateUsage]

    asyncio.run(_test())


def test_ssl_context_verification_enabled_default() -> None:
    transport = PinnedAsyncHTTPTransport()
    assert (
        transport._ssl_context.check_hostname is True  # pyright: ignore[reportPrivateUsage]
    )
    assert (
        transport._ssl_context.verify_mode == ssl.CERT_REQUIRED  # pyright: ignore[reportPrivateUsage]
    )


class FakeHTTPNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, target_ip: str) -> None:
        self.target_ip = target_ip
        self.written_bytes = b""
        self.closed = False
        self.read_buffer = b""

    async def read(self, max_bytes: int = 4096, timeout: float | None = None) -> bytes:
        if not self.read_buffer:
            return b""
        chunk = self.read_buffer[:max_bytes]
        self.read_buffer = self.read_buffer[max_bytes:]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written_bytes += buffer
        if not self.read_buffer:
            self.read_buffer = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 12\r\n"
                b"X-Custom-Header: adapter-test\r\n"
                b"\r\n"
                b"Hello World!"
            )

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return self

    def get_extra_info(self, info: str, default: Any = None) -> Any:
        return default


class FakeHTTPNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connected_hosts: list[tuple[str, int]] = []
        self.last_stream: FakeHTTPNetworkStream | None = None

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected_hosts.append((host, port))
        stream = FakeHTTPNetworkStream(target_ip=host)
        self.last_stream = stream
        return stream

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0.0)


def test_pinned_transport_httpx_request_adapter_integration() -> None:
    async def _test() -> None:
        dns_resolver = MockDNSResolver({"example.com": ["93.184.216.34"]})
        backend = FakeHTTPNetworkBackend()
        transport = PinnedAsyncHTTPTransport(
            dns_resolver=dns_resolver,
            network_backend=backend,
        )

        async with httpx.AsyncClient(transport=transport) as client:
            # Test POST with request body stream adaptation, headers, and extensions
            response = await client.post(
                "https://example.com/api/test",
                content=b"request body payload",
                headers={"X-Test-Header": "httpx-adapter"},
            )

            assert response.status_code == 200
            assert response.text == "Hello World!"
            assert response.headers["x-custom-header"] == "adapter-test"

            # Check original host header & original URL origin
            assert response.request.url.scheme == "https"
            assert response.request.url.host == "example.com"
            assert response.request.headers["host"] == "example.com"

            # Check backend connected to resolved IP
            assert backend.connected_hosts == [("93.184.216.34", 443)]

            # Check request payload written to fake network stream
            assert backend.last_stream is not None
            assert b"POST /api/test HTTP/1.1" in backend.last_stream.written_bytes
            assert (
                b"Host: example.com" in backend.last_stream.written_bytes
                or b"host: example.com" in backend.last_stream.written_bytes
            )
            assert (
                b"X-Test-Header: httpx-adapter" in backend.last_stream.written_bytes
                or b"x-test-header: httpx-adapter" in backend.last_stream.written_bytes
            )
            assert b"request body payload" in backend.last_stream.written_bytes

            # Test streaming response close propagation
            async with client.stream("GET", "https://example.com/stream") as stream_resp:
                chunks = [chunk async for chunk in stream_resp.aiter_bytes()]
                assert b"".join(chunks) == b"Hello World!"

        # Verify closing client/transport closes underlying connection pool stream
        assert backend.last_stream.closed is True

    asyncio.run(_test())
