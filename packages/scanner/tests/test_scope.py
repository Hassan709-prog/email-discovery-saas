"""Tests for scanner-core crawl-scope filtering and asset detection policies."""

from email_scanner.models import CrawlScopeMode
from email_scanner.normalization import normalize_url
from email_scanner.scope import (
    is_asset_url,
    is_in_scope,
    is_same_origin,
    is_same_registrable_domain,
)


def test_ip_hosts_do_not_share_scope() -> None:
    ip1 = normalize_url("http://192.168.1.1/page")
    ip2 = normalize_url("http://192.168.1.2/page")
    ip1_same = normalize_url("http://192.168.1.1/other")

    assert not is_same_registrable_domain(ip1, ip2)
    assert not is_in_scope(ip2, ip1, CrawlScopeMode.SAME_REGISTRABLE_DOMAIN)

    assert is_same_registrable_domain(ip1, ip1_same)
    assert is_in_scope(ip1_same, ip1, CrawlScopeMode.SAME_REGISTRABLE_DOMAIN)


def test_same_origin_strict_mode() -> None:
    base = normalize_url("https://example.com/page")
    same_origin = normalize_url("https://example.com/other")
    diff_scheme = normalize_url("http://example.com/other")
    diff_subdomain = normalize_url("https://sub.example.com/other")
    diff_port = normalize_url("https://example.com:8443/other")

    assert is_same_origin(same_origin, base)
    assert is_in_scope(same_origin, base, CrawlScopeMode.SAME_ORIGIN)

    assert not is_same_origin(diff_scheme, base)
    assert not is_in_scope(diff_scheme, base, CrawlScopeMode.SAME_ORIGIN)

    assert not is_same_origin(diff_subdomain, base)
    assert not is_in_scope(diff_subdomain, base, CrawlScopeMode.SAME_ORIGIN)

    assert not is_same_origin(diff_port, base)
    assert not is_in_scope(diff_port, base, CrawlScopeMode.SAME_ORIGIN)


def test_same_registrable_domain_mode() -> None:
    base = normalize_url("https://example.com/page")
    subdomain = normalize_url("https://blog.example.com/posts")
    different_domain = normalize_url("https://example.org/page")

    assert is_same_registrable_domain(subdomain, base)
    assert is_in_scope(subdomain, base, CrawlScopeMode.SAME_REGISTRABLE_DOMAIN)

    assert not is_same_registrable_domain(different_domain, base)
    assert not is_in_scope(different_domain, base, CrawlScopeMode.SAME_REGISTRABLE_DOMAIN)


def test_asset_url_detection_with_uppercase_and_query_strings() -> None:
    ignored = (".png", ".pdf", ".zip", ".jpg")

    url1 = normalize_url("https://example.com/images/HERO.PNG?v=123#top")
    url2 = normalize_url("https://example.com/docs/report.Pdf?token=abc")
    url3 = normalize_url("https://example.com/download/archive.ZIP")
    html_page = normalize_url("https://example.com/page.html?ref=png")

    assert is_asset_url(url1, ignored)
    assert is_asset_url(url2, ignored)
    assert is_asset_url(url3, ignored)
    assert not is_asset_url(html_page, ignored)
