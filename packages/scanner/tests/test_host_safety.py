"""Tests for public-host and SSRF safety policy."""

import pytest

from email_scanner import (
    HostSafetyError,
    HostSafetyErrorCode,
    normalize_url,
    validate_public_host,
)


def test_accepts_public_domain_addresses_deterministically() -> None:
    url = normalize_url("https://example.com")

    result = validate_public_host(
        url,
        ["8.8.8.8", "1.1.1.1", "8.8.8.8"],
    )

    assert result == ("1.1.1.1", "8.8.8.8")


@pytest.mark.parametrize(
    "raw_url",
    [
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://192.168.1.10",
        "http://169.254.169.254",
        "http://0.0.0.0",
        "http://[::1]",
        "http://[fc00::1]",
        "http://[fe80::1]",
    ],
)
def test_blocks_non_public_ip_literal(raw_url: str) -> None:
    url = normalize_url(raw_url)

    with pytest.raises(HostSafetyError) as error:
        validate_public_host(url)

    assert error.value.code is HostSafetyErrorCode.NON_PUBLIC_IP_ADDRESS


@pytest.mark.parametrize(
    "raw_url",
    [
        "http://localhost",
        "http://api.localhost",
        "http://service.local",
        "http://database.internal",
        "http://router.lan",
        "http://metadata.google.internal",
    ],
)
def test_blocks_local_hostnames(raw_url: str) -> None:
    url = normalize_url(raw_url)

    with pytest.raises(HostSafetyError) as error:
        validate_public_host(url, ["8.8.8.8"])

    assert error.value.code is HostSafetyErrorCode.BLOCKED_HOSTNAME


def test_blocks_domain_when_any_resolved_address_is_private() -> None:
    url = normalize_url("https://example.com")

    with pytest.raises(HostSafetyError) as error:
        validate_public_host(url, ["8.8.8.8", "192.168.1.10"])

    assert error.value.code is HostSafetyErrorCode.NON_PUBLIC_IP_ADDRESS


def test_requires_resolved_addresses_for_domain() -> None:
    url = normalize_url("https://example.com")

    with pytest.raises(HostSafetyError) as error:
        validate_public_host(url)

    assert error.value.code is HostSafetyErrorCode.NO_RESOLVED_ADDRESSES


def test_rejects_invalid_dns_address() -> None:
    url = normalize_url("https://example.com")

    with pytest.raises(HostSafetyError) as error:
        validate_public_host(url, ["not-an-ip"])

    assert error.value.code is HostSafetyErrorCode.INVALID_IP_ADDRESS
