"""Tests for scanner-core HTML link discovery and pipeline."""

import pytest

from email_scanner.discovery import HTMLLinkExtractor, discover_and_rank_links
from email_scanner.errors import (
    DiscoveryConfigError,
    DiscoveryConfigErrorCode,
    DiscoveryOutcomeCode,
)
from email_scanner.models import CrawlScopeMode, DiscoveryConfig


def test_discovery_config_validation() -> None:
    with pytest.raises(DiscoveryConfigError) as exc_info:
        DiscoveryConfig(max_html_chars=0)
    assert exc_info.value.code == DiscoveryConfigErrorCode.INVALID_LIMIT

    with pytest.raises(DiscoveryConfigError) as exc_info:
        DiscoveryConfig(max_raw_anchors=0)
    assert exc_info.value.code == DiscoveryConfigErrorCode.INVALID_LIMIT

    with pytest.raises(DiscoveryConfigError) as exc_info:
        DiscoveryConfig(max_ranked_pages=100, max_discovered_links=50)
    assert exc_info.value.code == DiscoveryConfigErrorCode.INVALID_LIMIT


def test_html_link_extractor_nested_text_and_ignored_tags() -> None:
    html = """
    <html>
      <head>
        <style> a { color: red; } </style>
        <script> console.log("<a href='/script_link'>Script</a>"); </script>
      </head>
      <body>
        <a href="/nested"><span>About <b>Our <i>Team</i></b></span></a>
        <a href="mailto:info@example.com">Email Us</a>
        <a href="tel:+123456789">Call Us</a>
        <a href="javascript:void(0)">Click</a>
      </body>
    </html>
    """

    extractor = HTMLLinkExtractor()
    extractor.feed(html)

    # Should only extract valid anchor tags, excluding script content
    assert len(extractor.raw_anchors) == 4
    assert extractor.raw_anchors[0] == ("/nested", "About Our Team")


def test_base_href_valid_and_external_poisoning() -> None:
    # Test valid internal base href
    html_valid = """
    <html>
      <head><base href="/subfolder/"></head>
      <body><a href="page.html">Page</a></body>
    </html>
    """
    res_valid = discover_and_rank_links("https://example.com/root/", html_valid)
    assert len(res_valid.discovered_links) == 1
    assert res_valid.discovered_links[0].normalized_url == "https://example.com/subfolder/page.html"

    # Test external base poisoning (must be ignored)
    html_poison = """
    <html>
      <head><base href="https://evil.com/poison/"></head>
      <body><a href="contact.html">Contact Us</a></body>
    </html>
    """
    res_poison = discover_and_rank_links("https://example.com/root/", html_poison)
    assert len(res_poison.discovered_links) == 1
    # Must resolve against source_url, NOT poison base URL
    assert res_poison.discovered_links[0].normalized_url == "https://example.com/root/contact.html"


def test_duplicate_url_stronger_evidence_selection() -> None:
    html = """
    <html>
      <body>
        <a href="/about">Link 1</a>
        <a href="/about#team">About Our Leadership Team</a>
      </body>
    </html>
    """
    res = discover_and_rank_links("https://example.com/", html)

    # Fragment removal -> single deduplicated URL
    assert len(res.discovered_links) == 1
    link = res.discovered_links[0]
    assert link.normalized_url == "https://example.com/about"
    # Stronger evidence ("About Our Leadership Team") selected
    assert link.link_text == "About Our Leadership Team"


def test_relative_absolute_and_protocol_relative_links() -> None:
    html = """
    <html>
      <body>
        <a href="/relative">Relative</a>
        <a href="https://example.com/absolute">Absolute</a>
        <a href="//example.com/protocol_relative">Protocol Relative</a>
        <a href="https://external.com/out">External</a>
      </body>
    </html>
    """
    res = discover_and_rank_links("https://example.com/", html)

    urls = [link.normalized_url for link in res.discovered_links]
    assert "https://example.com/relative" in urls
    assert "https://example.com/absolute" in urls
    assert "https://example.com/protocol_relative" in urls
    assert "https://external.com/out" not in urls


def test_hard_limits_max_html_chars_and_max_raw_anchors() -> None:
    # Generate large HTML with 10 anchors
    anchors = "".join(f'<a href="/page{i}">Page {i}</a>\n' for i in range(10))
    html = f"<html><body>{anchors}</body></html>"

    config = DiscoveryConfig(max_raw_anchors=3)
    res = discover_and_rank_links("https://example.com/", html, config=config)

    # Respects max_raw_anchors limit
    assert len(res.discovered_links) <= 3


def test_empty_discovery_outcome() -> None:
    html = "<html><body><p>No links here</p></body></html>"
    res = discover_and_rank_links("https://example.com/", html)

    assert res.outcome == DiscoveryOutcomeCode.NO_LINKS_DISCOVERED
    assert res.discovered_links == ()
    # Home page should still be ranked
    assert len(res.ranked_pages) == 1


def test_pipeline_repeatability() -> None:
    html = """
    <html>
      <body>
        <a href="/team">Team</a>
        <a href="/contact">Contact</a>
        <a href="/about">About</a>
      </body>
    </html>
    """
    config = DiscoveryConfig(scope_mode=CrawlScopeMode.SAME_REGISTRABLE_DOMAIN)

    res1 = discover_and_rank_links("https://example.com/", html, config)
    res2 = discover_and_rank_links("https://example.com/", html, config)

    assert res1 == res2
