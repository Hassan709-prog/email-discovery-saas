"""Public-host safety policy used to prevent SSRF."""

import ipaddress
from collections.abc import Iterable
from typing import Never

from email_scanner.errors import HostSafetyError, HostSafetyErrorCode
from email_scanner.models import HostType, NormalizedURL

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)

_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
)


def _raise(
    code: HostSafetyErrorCode,
    message: str,
) -> Never:
    raise HostSafetyError(code=code, message=message)


def _validate_hostname_policy(hostname: str) -> None:
    hostname = hostname.lower().rstrip(".")

    if hostname in _BLOCKED_HOSTNAMES:
        _raise(
            HostSafetyErrorCode.BLOCKED_HOSTNAME,
            f"Hostname is blocked by crawler policy: {hostname}",
        )

    if hostname.endswith(_BLOCKED_SUFFIXES):
        _raise(
            HostSafetyErrorCode.BLOCKED_HOSTNAME,
            f"Hostname uses a private or local suffix: {hostname}",
        )


def _parse_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(address)
    except ValueError:
        _raise(
            HostSafetyErrorCode.INVALID_IP_ADDRESS,
            f"DNS returned an invalid IP address: {address}",
        )


def _validate_public_ip(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> None:
    if not address.is_global:
        _raise(
            HostSafetyErrorCode.NON_PUBLIC_IP_ADDRESS,
            f"Host resolves to a non-public IP address: {address}",
        )


def validate_public_host(
    url: NormalizedURL,
    resolved_addresses: Iterable[str] = (),
) -> tuple[str, ...]:
    """Validate that every destination address is publicly routable.

    Domain names require pre-resolved addresses. IP-literal URLs are validated
    directly. Every resolved address must be safe; one unsafe address blocks
    the entire host.
    """

    _validate_hostname_policy(url.hostname)

    if url.host_type in {HostType.IPV4, HostType.IPV6}:
        candidates = (url.hostname,)
    else:
        candidates = tuple(resolved_addresses)

        if not candidates:
            _raise(
                HostSafetyErrorCode.NO_RESOLVED_ADDRESSES,
                f"No IP addresses were resolved for: {url.hostname}",
            )

    parsed_addresses = {_parse_ip(address) for address in candidates}

    for address in parsed_addresses:
        _validate_public_ip(address)

    ordered_addresses = sorted(
        parsed_addresses,
        key=lambda address: (address.version, int(address)),
    )

    return tuple(str(address) for address in ordered_addresses)
