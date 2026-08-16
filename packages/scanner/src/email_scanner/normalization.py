"""Deterministic URL normalization."""

import ipaddress
import re
from typing import Never
from urllib.parse import urlsplit, urlunsplit

import tldextract

from email_scanner.errors import (
    URLNormalizationError,
    URLNormalizationErrorCode,
)
from email_scanner.models import HostType, NormalizedURL

_MAX_URL_LENGTH = 8192
_EXPLICIT_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_DOMAIN_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())


def _raise(
    code: URLNormalizationErrorCode,
    message: str,
) -> Never:
    raise URLNormalizationError(code=code, message=message)


def _classify_host(hostname: str) -> tuple[HostType, str | None]:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        extracted = _DOMAIN_EXTRACTOR(hostname)
        registrable_domain = extracted.top_domain_under_public_suffix or None
        return HostType.DOMAIN, registrable_domain

    if isinstance(address, ipaddress.IPv4Address):
        return HostType.IPV4, None

    return HostType.IPV6, None


def _normalize_hostname(hostname: str) -> str:
    hostname = hostname.rstrip(".").lower()

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            _raise(
                URLNormalizationErrorCode.INVALID_URL,
                "The hostname contains invalid characters.",
            )

    if not hostname:
        _raise(
            URLNormalizationErrorCode.MISSING_HOST,
            "The URL must contain a hostname.",
        )

    return hostname


def normalize_url(raw_url: str) -> NormalizedURL:
    """Return a deterministic representation of an HTTP or HTTPS URL."""

    original_url = raw_url
    value = raw_url.strip()

    if not value:
        _raise(
            URLNormalizationErrorCode.EMPTY_INPUT,
            "A URL is required.",
        )

    if len(value) > _MAX_URL_LENGTH:
        _raise(
            URLNormalizationErrorCode.INPUT_TOO_LONG,
            "The URL is too long.",
        )

    if not _EXPLICIT_SCHEME.match(value):
        value = f"https://{value}"

    try:
        parsed = urlsplit(value)
    except ValueError:
        _raise(
            URLNormalizationErrorCode.INVALID_URL,
            "The URL could not be parsed.",
        )

    scheme = parsed.scheme.lower()

    if scheme not in {"http", "https"}:
        _raise(
            URLNormalizationErrorCode.UNSUPPORTED_SCHEME,
            "Only HTTP and HTTPS URLs are supported.",
        )

    if parsed.username is not None or parsed.password is not None:
        _raise(
            URLNormalizationErrorCode.CREDENTIALS_NOT_ALLOWED,
            "Credentials are not allowed in URLs.",
        )

    if parsed.hostname is None:
        _raise(
            URLNormalizationErrorCode.MISSING_HOST,
            "The URL must contain a hostname.",
        )

    hostname = _normalize_hostname(parsed.hostname)

    try:
        port = parsed.port
    except ValueError:
        _raise(
            URLNormalizationErrorCode.INVALID_PORT,
            "The URL contains an invalid port.",
        )

    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None

    host_type, registrable_domain = _classify_host(hostname)

    display_hostname = f"[{hostname}]" if host_type is HostType.IPV6 else hostname
    netloc = f"{display_hostname}:{port}" if port is not None else display_hostname

    path = parsed.path or "/"
    query = parsed.query

    normalized_url = urlunsplit((scheme, netloc, path, query, ""))

    return NormalizedURL(
        original_url=original_url,
        normalized_url=normalized_url,
        scheme=scheme,
        hostname=hostname,
        port=port,
        path=path,
        query=query,
        host_type=host_type,
        registrable_domain=registrable_domain,
    )
