"""DNS-pinned async network transport for scanner-core.

Enforces connection pinning to validated IP addresses at the HTTPCore network backend
layer, preserving original Host headers, TLS SNI, and origin certificate verification.
"""

import contextvars
import ssl
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import httpcore
import httpx

from email_scanner.dns import AsyncDNSResolver, SystemDNSResolver
from email_scanner.errors import HostSafetyError, HostSafetyErrorCode
from email_scanner.models import IPConnectionAttempt, PinningConfig

_connection_attempts_ctx: contextvars.ContextVar[list[IPConnectionAttempt] | None] = (
    contextvars.ContextVar("_connection_attempts_ctx", default=None)
)


def record_ip_connection_attempt(attempt: IPConnectionAttempt) -> None:
    """Record a request-scoped IP connection attempt in current context if present."""
    attempts = _connection_attempts_ctx.get()
    if attempts is not None:
        attempts.append(attempt)


class PinnedAsyncNetworkStream(httpcore.AsyncNetworkStream):
    """Network stream wrapper preserving original target hostname for TLS SNI and cert checks."""

    def __init__(
        self,
        raw_stream: httpcore.AsyncNetworkStream,
        original_hostname: str,
    ) -> None:
        self._raw_stream = raw_stream
        self._original_hostname = original_hostname

    async def read(self, max_bytes: int = 4096, timeout: float | None = None) -> bytes:
        return await self._raw_stream.read(max_bytes=max_bytes, timeout=timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._raw_stream.write(buffer=buffer, timeout=timeout)

    async def aclose(self) -> None:
        await self._raw_stream.aclose()

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Initiate TLS handshake enforcing original hostname for SNI and cert validation."""
        clean_orig = self._original_hostname.lower().strip("[]")
        if server_hostname is not None:
            clean_supplied = server_hostname.lower().strip("[]")
            if clean_supplied != clean_orig:
                from ipaddress import ip_address

                is_ip = False
                try:
                    ip_address(clean_supplied)
                    is_ip = True
                except ValueError:
                    pass

                if is_ip or clean_supplied != clean_orig:
                    raise HostSafetyError(
                        code=HostSafetyErrorCode.BLOCKED_HOSTNAME,
                        message=(
                            f"Inconsistent or IP SNI server_hostname rejected: {server_hostname}"
                        ),
                    )

        tls_stream = await self._raw_stream.start_tls(
            ssl_context=ssl_context,
            server_hostname=self._original_hostname,
            timeout=timeout,
        )
        return PinnedAsyncNetworkStream(tls_stream, original_hostname=self._original_hostname)

    def get_extra_info(self, info: str, default: Any = None) -> Any:
        try:
            return self._raw_stream.get_extra_info(info)
        except TypeError:
            return default


class PinnedAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """HTTPCore async network backend enforcing DNS IP resolution and validation."""

    def __init__(
        self,
        dns_resolver: AsyncDNSResolver | None = None,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
        pinning_config: PinningConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._dns_resolver: AsyncDNSResolver = dns_resolver or SystemDNSResolver()
        self._real_backend: Any = network_backend or httpcore.AnyIOBackend()
        self._config = pinning_config or PinningConfig()
        self._clock = clock or time.monotonic

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        """Resolve host, validate all IP addresses, and connect TCP socket to validated IP."""
        if hasattr(self._dns_resolver, "resolve_host"):
            resolver_func = cast(Any, self._dns_resolver).resolve_host
            validated_ips = await resolver_func(host, port)
        else:
            from email_scanner.normalization import normalize_url

            scheme = "https" if port == 443 else "http"
            url_str = f"{scheme}://{host}" if port in (80, 443) else f"{scheme}://{host}:{port}"
            norm_url = normalize_url(url_str)
            validated_ips = await self._dns_resolver.resolve(norm_url)

        filtered_ips: list[str] = [
            ip for ip in validated_ips if not (":" in ip and not self._config.allow_ipv6)
        ]

        if not filtered_ips:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message=f"No usable IP addresses after filtering for {host}",
            )

        max_attempts = min(len(filtered_ips), self._config.max_ip_failover_attempts)
        last_exception: Exception | None = None

        for idx in range(max_attempts):
            ip = filtered_ips[idx]
            now = self._clock()

            try:
                raw_stream = await self._real_backend.connect_tcp(
                    host=ip,
                    port=port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
                record_ip_connection_attempt(
                    IPConnectionAttempt(
                        target_host=host,
                        target_port=port,
                        attempted_ip=ip,
                        success=True,
                        error_message=None,
                        timestamp=now,
                    )
                )
                return PinnedAsyncNetworkStream(raw_stream, original_hostname=host)
            except Exception as exc:
                last_exception = exc
                err_msg = str(exc) or type(exc).__name__
                record_ip_connection_attempt(
                    IPConnectionAttempt(
                        target_host=host,
                        target_port=port,
                        attempted_ip=ip,
                        success=False,
                        error_message=err_msg,
                        timestamp=now,
                    )
                )

        if last_exception is not None:
            raise last_exception

        raise HostSafetyError(
            code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
            message=f"Failed to connect to any validated IP address for {host}",
        )

    async def sleep(self, seconds: float) -> None:
        await self._real_backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Public AsyncBaseTransport adapter backed by HTTPCore connection pool and pinned backend."""

    def __init__(
        self,
        dns_resolver: AsyncDNSResolver | None = None,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
        pinning_config: PinningConfig | None = None,
        clock: Callable[[], float] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        ssl_ctx = ssl_context or ssl.create_default_context()
        ssl_ctx.check_hostname = True
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        self._ssl_context = ssl_ctx

        self._network_backend = PinnedAsyncNetworkBackend(
            dns_resolver=dns_resolver,
            network_backend=network_backend,
            pinning_config=pinning_config,
            clock=clock,
        )

        self._pool = httpcore.AsyncConnectionPool(
            network_backend=self._network_backend,
            ssl_context=self._ssl_context,
            http2=False,
        )

    @property
    def network_backend(self) -> PinnedAsyncNetworkBackend:
        return self._network_backend

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Handle request by delegating to httpcore.AsyncConnectionPool."""
        port = request.url.port
        if port is None:
            port = 443 if request.url.scheme == "https" else 80

        target_bytes = request.url.raw_path
        httpcore_url = httpcore.URL(
            scheme=request.url.scheme.encode("ascii"),
            host=request.url.raw_host,
            port=port,
            target=target_bytes,
        )

        content_obj: Any = request.stream

        httpcore_req = httpcore.Request(
            method=request.method.encode("ascii"),
            url=httpcore_url,
            headers=request.headers.raw,
            content=content_obj,
            extensions=request.extensions,
        )

        httpcore_resp = await self._pool.handle_async_request(httpcore_req)

        class _HTTPXByteStream(httpx.AsyncByteStream):
            def __init__(self, core_stream: Any) -> None:
                self._core_stream = core_stream

            async def __aiter__(self) -> AsyncIterator[bytes]:
                async for chunk in self._core_stream:
                    yield chunk

            async def aclose(self) -> None:
                if hasattr(self._core_stream, "aclose"):
                    await self._core_stream.aclose()

        return httpx.Response(
            status_code=httpcore_resp.status,
            headers=httpcore_resp.headers,
            stream=_HTTPXByteStream(httpcore_resp.stream),
            extensions=httpcore_resp.extensions,  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
            request=request,
        )

    async def aclose(self) -> None:
        """Close connection pool cleanly."""
        await self._pool.aclose()
