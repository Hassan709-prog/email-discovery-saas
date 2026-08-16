"""Async DNS resolver abstraction for scanner-core.

Pre-resolving DNS addresses allows checking against host safety policies
(e.g., blocking private IP ranges and local hostnames) prior to making HTTP requests.

Note on Security Boundaries (DNS-Rebinding / TOCTOU):
Pre-resolving hostnames alone does not fully eliminate DNS-rebinding or
Time-of-Check to Time-of-Use (TOCTOU) risks because standard HTTP transports
may re-resolve hostnames when establishing sockets. This resolver interface
and returned IP list are designed so that future transports can consume the
pre-resolved addresses directly for socket connection pinning.
"""

import asyncio
import socket
from typing import Protocol

from email_scanner.errors import HostSafetyError, HostSafetyErrorCode
from email_scanner.host_safety import validate_public_host
from email_scanner.models import HostType, NormalizedURL


class AsyncDNSResolver(Protocol):
    """Protocol for asynchronous DNS resolution and host safety checking."""

    async def resolve(self, url: NormalizedURL) -> tuple[str, ...]:
        """Resolve host for a NormalizedURL and return safe IP addresses."""
        ...


class SystemDNSResolver:
    """Production DNS resolver using socket.getaddrinfo via asyncio.to_thread."""

    async def resolve(self, url: NormalizedURL) -> tuple[str, ...]:
        if url.host_type in {HostType.IPV4, HostType.IPV6}:
            return validate_public_host(url, ())

        port = url.port or (443 if url.scheme == "https" else 80)
        return await self.resolve_host(url.hostname, port)

    async def resolve_host(self, hostname: str, port: int = 80) -> tuple[str, ...]:
        cleaned_host = hostname.strip().strip("[]")
        if not cleaned_host:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message="Hostname string is empty",
            )

        # Check if hostname is an IP literal directly
        try:
            from ipaddress import ip_address

            ip_obj = ip_address(cleaned_host)
            fake_url = NormalizedURL(
                original_url=f"http://{cleaned_host}",
                normalized_url=f"http://{cleaned_host}",
                scheme="https" if port == 443 else "http",
                hostname=cleaned_host,
                port=port if port not in (80, 443) else None,
                path="/",
                query="",
                host_type=HostType.IPV6 if ip_obj.version == 6 else HostType.IPV4,
                registrable_domain=None,
            )
            return validate_public_host(fake_url, ())
        except ValueError:
            pass

        try:
            results = await asyncio.to_thread(
                socket.getaddrinfo,
                cleaned_host,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as err:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message=f"DNS resolution failed for {cleaned_host}: {err}",
            ) from err

        addresses = tuple(str(res[4][0]) for res in results if res[4])
        if not addresses:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message=f"No IP addresses resolved for {cleaned_host}",
            )

        fake_url = NormalizedURL(
            original_url=f"http://{cleaned_host}",
            normalized_url=f"http://{cleaned_host}",
            scheme="https" if port == 443 else "http",
            hostname=cleaned_host,
            port=port if port not in (80, 443) else None,
            path="/",
            query="",
            host_type=HostType.DOMAIN,
            registrable_domain=None,
        )
        return validate_public_host(fake_url, addresses)
