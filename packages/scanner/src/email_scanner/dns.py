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
        """Resolve host for a NormalizedURL and return safe IP addresses.

        Returns a tuple of safe, deterministically sorted IP string addresses.
        Raises HostSafetyError if DNS resolution fails or the host is unsafe.
        """
        ...


class SystemDNSResolver:
    """Production DNS resolver using socket.getaddrinfo via asyncio.to_thread."""

    async def resolve(self, url: NormalizedURL) -> tuple[str, ...]:
        if url.host_type in {HostType.IPV4, HostType.IPV6}:
            return validate_public_host(url, ())

        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            results = await asyncio.to_thread(
                socket.getaddrinfo,
                url.hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as err:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message=f"DNS resolution failed for {url.hostname}: {err}",
            ) from err

        addresses = tuple(str(res[4][0]) for res in results if res[4])
        if not addresses:
            raise HostSafetyError(
                code=HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                message=f"No IP addresses resolved for {url.hostname}",
            )

        return validate_public_host(url, addresses)
