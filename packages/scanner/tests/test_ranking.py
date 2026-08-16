"""Tests for scanner-core page ranking module."""

from email_scanner.models import DiscoveredLink
from email_scanner.ranking import RANKING_VERSION, calculate_page_score, rank_pages


def test_ranking_positive_and_negative_signals() -> None:
    source_url = "https://example.com/"

    links = (
        DiscoveredLink(
            source_url=source_url,
            raw_href="/contact-us",
            normalized_url="https://example.com/contact-us",
            link_text="Contact Us",
            is_same_origin=True,
            is_same_registrable_domain=True,
        ),
        DiscoveredLink(
            source_url=source_url,
            raw_href="/team",
            normalized_url="https://example.com/team",
            link_text="Our Leadership Team",
            is_same_origin=True,
            is_same_registrable_domain=True,
        ),
        DiscoveredLink(
            source_url=source_url,
            raw_href="/login",
            normalized_url="https://example.com/login",
            link_text="Sign In / Login",
            is_same_origin=True,
            is_same_registrable_domain=True,
        ),
        DiscoveredLink(
            source_url=source_url,
            raw_href="/cart",
            normalized_url="https://example.com/cart",
            link_text="Shopping Cart",
            is_same_origin=True,
            is_same_registrable_domain=True,
        ),
    )

    ranked = rank_pages(source_url, links, max_ranked_pages=10)

    # Verify ranking version present on all entries
    assert all(r.ranking_version == RANKING_VERSION for r in ranked)

    # Top ranked pages should be Contact and Team
    urls = [r.url for r in ranked]
    assert "https://example.com/contact-us" in urls[:2]
    assert "https://example.com/team" in urls[:2]

    # Negative signal pages should be at the bottom
    assert urls.index("https://example.com/login") > urls.index("https://example.com/contact-us")
    assert urls.index("https://example.com/cart") > urls.index("https://example.com/team")


def test_explainable_signals_no_double_counting() -> None:
    score, signals = calculate_page_score(
        "https://example.com/contact-us/contact-form", "Contact Contact Us"
    )
    assert "KEYWORD_CONTACT" in signals
    # Signals are returned in sorted order and without duplicate keys
    assert signals == tuple(sorted(signals))
    assert len(signals) == len(set(signals))
    assert score == 100


def test_stable_lexical_tie_breaking() -> None:
    source_url = "https://example.com/"

    # Two links with identical positive score
    link_a = DiscoveredLink(
        source_url=source_url,
        raw_href="/a-contact",
        normalized_url="https://example.com/a-contact",
        link_text="Contact",
        is_same_origin=True,
        is_same_registrable_domain=True,
    )
    link_b = DiscoveredLink(
        source_url=source_url,
        raw_href="/b-contact",
        normalized_url="https://example.com/b-contact",
        link_text="Contact",
        is_same_origin=True,
        is_same_registrable_domain=True,
    )

    ranked = rank_pages(source_url, (link_b, link_a), max_ranked_pages=10)

    # Find the positions of a-contact and b-contact
    urls = [r.url for r in ranked]
    idx_a = urls.index("https://example.com/a-contact")
    idx_b = urls.index("https://example.com/b-contact")

    # Lexical order tie breaking: a-contact before b-contact
    assert idx_a < idx_b


def test_home_page_inclusion() -> None:
    source_url = "https://example.com/"
    ranked = rank_pages(source_url, (), max_ranked_pages=5)
    assert len(ranked) == 1
    assert ranked[0].url == "https://example.com/"
    assert "HOME_PAGE" in ranked[0].signals
