"""Deterministic page ranking module for scanner-core.

Prioritizes important discovery target pages (contact, team, about, locations)
using integer weight signals and deterministic tie-breaking.
"""

from collections.abc import Iterable
from urllib.parse import urlparse

from email_scanner.models import DiscoveredLink, RankedPage

RANKING_VERSION = "page-ranking-v1"

_SIGNAL_WEIGHTS: dict[str, int] = {
    "KEYWORD_CONTACT": 100,
    "KEYWORD_TEAM": 90,
    "KEYWORD_ABOUT": 80,
    "KEYWORD_LOCATIONS": 60,
    "HOME_PAGE": 50,
    "KEYWORD_SUPPORT": 40,
    "KEYWORD_LEGAL": 30,
    "NEGATIVE_ARCHIVE": -40,
    "NEGATIVE_SEARCH": -50,
    "NEGATIVE_ACCOUNT": -80,
    "NEGATIVE_LOGIN": -100,
    "NEGATIVE_ECOMMERCE": -100,
}

_KEYWORD_PATTERNS: dict[str, tuple[str, ...]] = {
    "KEYWORD_CONTACT": ("contact", "contacts", "contact-us", "get-in-touch", "reach-us"),
    "KEYWORD_TEAM": (
        "team",
        "staff",
        "leadership",
        "people",
        "management",
        "executives",
        "founders",
        "board",
        "our-team",
    ),
    "KEYWORD_ABOUT": ("about", "about-us", "who-we-are", "company", "our-story"),
    "KEYWORD_LOCATIONS": ("locations", "offices", "branches", "find-us"),
    "KEYWORD_SUPPORT": ("support", "help", "help-center", "faq"),
    "KEYWORD_LEGAL": ("legal", "imprint", "privacy", "terms"),
    "NEGATIVE_LOGIN": ("login", "signin", "signup", "register", "auth", "sso", "password"),
    "NEGATIVE_ECOMMERCE": ("cart", "checkout", "basket", "bag", "store", "buy"),
    "NEGATIVE_ACCOUNT": ("account", "profile", "my-account", "settings", "dashboard"),
    "NEGATIVE_SEARCH": ("search", "query"),
    "NEGATIVE_ARCHIVE": ("/page/", "/archive/", "/tag/", "/category/"),
}


def calculate_page_score(url_str: str, link_text: str = "") -> tuple[int, tuple[str, ...]]:
    """Compute deterministic page score and sorted active signals for a URL and link text."""
    parsed = urlparse(url_str)
    path = parsed.path.lower()
    combined_text = f"{path} {link_text.lower()}"

    active_signals: set[str] = set()

    # Check home page
    if path in {"", "/", "/index.html", "/index.htm", "/home"}:
        active_signals.add("HOME_PAGE")

    # Match keyword categories without double-counting
    for signal_name, patterns in _KEYWORD_PATTERNS.items():
        for pattern in patterns:
            if pattern in combined_text:
                active_signals.add(signal_name)
                break

    sorted_signals = tuple(sorted(active_signals))
    score = sum(_SIGNAL_WEIGHTS[sig] for sig in sorted_signals)
    return score, sorted_signals


def rank_pages(
    source_url: str,
    links: Iterable[DiscoveredLink],
    max_ranked_pages: int = 50,
) -> tuple[RankedPage, ...]:
    """Rank discovered links and source page deterministically."""
    # Deduplicate candidate URLs keeping the best link evidence for each URL
    candidate_map: dict[str, tuple[int, tuple[str, ...], DiscoveredLink | None]] = {}

    # Source page candidate (if eligible)
    source_score, source_signals = calculate_page_score(source_url, "Home")
    candidate_map[source_url] = (source_score, source_signals, None)

    for link in links:
        score, signals = calculate_page_score(link.normalized_url, link.link_text)
        if link.normalized_url not in candidate_map:
            candidate_map[link.normalized_url] = (score, signals, link)
        else:
            existing_score, _, existing_link = candidate_map[link.normalized_url]
            # Replace if score is higher, or if score equal and new link text is longer
            if score > existing_score:
                candidate_map[link.normalized_url] = (score, signals, link)
            elif score == existing_score and existing_link is not None:
                if len(link.link_text) > len(existing_link.link_text):
                    candidate_map[link.normalized_url] = (score, signals, link)

    # Sort deterministically: score descending, url ascending
    sorted_candidates = sorted(
        candidate_map.items(),
        key=lambda item: (-item[1][0], item[0]),
    )

    ranked_pages: list[RankedPage] = []
    for url, (score, signals, link) in sorted_candidates[:max_ranked_pages]:
        ranked_pages.append(
            RankedPage(
                url=url,
                score=score,
                signals=signals,
                ranking_version=RANKING_VERSION,
                discovered_link=link,
            )
        )

    return tuple(ranked_pages)
