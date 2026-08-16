"""Golden tests for deterministic URL normalization."""

import pytest

from email_scanner import (
    HostType,
    URLNormalizationError,
    URLNormalizationErrorCode,
    normalize_url,
)


@pytest.mark.parametrize(
    ("raw_url", "expected_url"),
    [
        ("example.com", "https://example.com/"),
        ("Example.COM", "https://example.com/"),
        ("  example.com  ", "https://example.com/"),
        ("http://example.com", "http://example.com/"),
        ("https://example.com:443", "https://example.com/"),
        ("http://example.com:80", "http://example.com/"),
        ("https://example.com:8443", "https://example.com:8443/"),
        (
            "https://example.com/contact?team=sales#people",
            "https://example.com/contact?team=sales",
        ),
        ("https://example.com.", "https://example.com/"),
        ("https://BÜCHER.de", "https://xn--bcher-kva.de/"),
    ],
)
def test_golden_normalized_urls(
    raw_url: str,
    expected_url: str,
) -> None:
    result = normalize_url(raw_url)

    assert result.normalized_url == expected_url


def test_returns_structured_domain_information() -> None:
    result = normalize_url("https://www.shop.example.co.uk/contact")

    assert result.scheme == "https"
    assert result.hostname == "www.shop.example.co.uk"
    assert result.path == "/contact"
    assert result.query == ""
    assert result.port is None
    assert result.host_type is HostType.DOMAIN
    assert result.registrable_domain == "example.co.uk"


def test_classifies_ipv4_address() -> None:
    result = normalize_url("http://192.0.2.1:80")

    assert result.normalized_url == "http://192.0.2.1/"
    assert result.hostname == "192.0.2.1"
    assert result.host_type is HostType.IPV4
    assert result.registrable_domain is None


def test_classifies_ipv6_address() -> None:
    result = normalize_url("https://[2001:db8::1]:443/contact")

    assert result.normalized_url == "https://[2001:db8::1]/contact"
    assert result.hostname == "2001:db8::1"
    assert result.host_type is HostType.IPV6
    assert result.registrable_domain is None


def test_normalization_is_idempotent() -> None:
    first = normalize_url("Example.COM:443/contact#team")
    second = normalize_url(first.normalized_url)

    assert second.normalized_url == first.normalized_url


@pytest.mark.parametrize(
    ("raw_url", "expected_code"),
    [
        ("", URLNormalizationErrorCode.EMPTY_INPUT),
        ("   ", URLNormalizationErrorCode.EMPTY_INPUT),
        ("ftp://example.com", URLNormalizationErrorCode.UNSUPPORTED_SCHEME),
        (
            "https://user:password@example.com",
            URLNormalizationErrorCode.CREDENTIALS_NOT_ALLOWED,
        ),
        (
            "https://example.com:not-a-port",
            URLNormalizationErrorCode.INVALID_PORT,
        ),
        ("https:///contact", URLNormalizationErrorCode.MISSING_HOST),
    ],
)
def test_invalid_inputs_return_stable_error_codes(
    raw_url: str,
    expected_code: URLNormalizationErrorCode,
) -> None:
    with pytest.raises(URLNormalizationError) as error:
        normalize_url(raw_url)

    assert error.value.code is expected_code
