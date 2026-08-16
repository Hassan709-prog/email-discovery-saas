"""Crawl-scope filtering policies for scanner-core."""

from collections.abc import Iterable

from email_scanner.models import CrawlScopeMode, NormalizedURL


def is_same_origin(target: NormalizedURL, source: NormalizedURL) -> bool:
    """Check if target and source URLs share the exact same origin."""
    if target.scheme != source.scheme:
        return False
    if target.hostname != source.hostname:
        return False
    target_port = target.port or (443 if target.scheme == "https" else 80)
    source_port = source.port or (443 if source.scheme == "https" else 80)
    return target_port == source_port


def is_same_registrable_domain(target: NormalizedURL, source: NormalizedURL) -> bool:
    """Check if target and source URLs share the same registrable domain."""
    if target.registrable_domain is not None and source.registrable_domain is not None:
        return target.registrable_domain == source.registrable_domain

    # For IP literals or hostnames without a registrable domain, match exact hostname.
    return target.hostname == source.hostname


def is_in_scope(
    target_url: NormalizedURL,
    source_url: NormalizedURL,
    scope_mode: CrawlScopeMode,
) -> bool:
    """Check if target URL is within the allowed crawl scope of source URL."""
    if scope_mode == CrawlScopeMode.SAME_ORIGIN:
        return is_same_origin(target_url, source_url)

    if scope_mode == CrawlScopeMode.SAME_REGISTRABLE_DOMAIN:
        return is_same_registrable_domain(target_url, source_url)

    return False


def is_asset_url(url: NormalizedURL, ignored_extensions: Iterable[str]) -> bool:
    """Check if URL path ends with an excluded asset extension.

    Only inspects the path component (case-insensitively), ignoring query string and fragment.
    """
    path_lower = url.path.lower()
    for ext in ignored_extensions:
        ext_lower = ext.lower()
        if not ext_lower.startswith("."):
            ext_lower = f".{ext_lower}"
        if path_lower.endswith(ext_lower):
            return True
    return False
