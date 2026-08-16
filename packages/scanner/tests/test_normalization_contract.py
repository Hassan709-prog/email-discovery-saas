"""Tests for the public URL-normalization contract."""

from dataclasses import FrozenInstanceError

import pytest

from email_scanner import (
    HostType,
    NormalizedURL,
    URLNormalizationError,
    URLNormalizationErrorCode,
)


def test_normalized_url_contains_expected_fields() -> None:
    result = NormalizedURL(
        original_url="Example.COM",
        normalized_url="https://example.com/",
        scheme="https",
        hostname="example.com",
        port=None,
        path="/",
        query="",
        host_type=HostType.DOMAIN,
        registrable_domain="example.com",
    )

    assert result.original_url == "Example.COM"
    assert result.normalized_url == "https://example.com/"
    assert result.scheme == "https"
    assert result.hostname == "example.com"
    assert result.port is None
    assert result.path == "/"
    assert result.query == ""
    assert result.host_type is HostType.DOMAIN
    assert result.registrable_domain == "example.com"


def test_normalized_url_is_immutable() -> None:
    result = NormalizedURL(
        original_url="example.com",
        normalized_url="https://example.com/",
        scheme="https",
        hostname="example.com",
        port=None,
        path="/",
        query="",
        host_type=HostType.DOMAIN,
        registrable_domain="example.com",
    )

    attribute_name = "hostname"
    with pytest.raises(FrozenInstanceError):
        setattr(result, attribute_name, "changed.example")


def test_normalization_error_exposes_stable_code() -> None:
    error = URLNormalizationError(
        code=URLNormalizationErrorCode.EMPTY_INPUT,
        message="A URL is required.",
    )

    assert error.code is URLNormalizationErrorCode.EMPTY_INPUT
    assert str(error) == "A URL is required."


@pytest.mark.parametrize(
    "code",
    list(URLNormalizationErrorCode),
)
def test_error_codes_have_stable_uppercase_values(
    code: URLNormalizationErrorCode,
) -> None:
    assert code.value == code.name
