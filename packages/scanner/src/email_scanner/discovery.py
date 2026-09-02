"""HTML link discovery and pipeline for scanner-core."""

import html.parser
import urllib.parse

from email_scanner.errors import (
    DiscoveryOutcomeCode,
    URLNormalizationError,
)
from email_scanner.models import (
    DiscoveredLink,
    DiscoveryConfig,
    DiscoveryResult,
    NormalizedURL,
)
from email_scanner.normalization import normalize_url
from email_scanner.ranking import calculate_page_score, rank_pages
from email_scanner.scope import (
    is_asset_url,
    is_in_scope,
    is_same_origin,
    is_same_registrable_domain,
)

_REJECTED_SCHEME_PREFIXES = (
    "mailto:",
    "tel:",
    "javascript:",
    "data:",
    "file:",
    "ftp:",
    "about:",
    "urn:",
    "sms:",
)


def is_directory_index_or_placeholder(html_content: str) -> bool:
    """Check if HTML content is a bare directory listing, parking page, or empty placeholder."""
    if not html_content or len(html_content.strip()) < 50:
        return True

    text_lower = html_content.lower()

    if "index of /" in text_lower or "<title>index of" in text_lower:
        return True
    if "apache server at" in text_lower and "[to parent directory]" in text_lower:
        return True
    if "parent directory</a>" in text_lower and "last modified" in text_lower:
        return True

    if "domain is for sale" in text_lower or "this domain has expired" in text_lower:
        return True

    return False


class HTMLLinkExtractor(html.parser.HTMLParser):
    """Resilient HTML parser extracting base href and anchor tags."""

    def __init__(self, max_raw_anchors: int = 2_000) -> None:
        super().__init__()
        self.max_raw_anchors = max_raw_anchors
        self.base_href: str | None = None
        self.raw_anchors: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text_parts: list[str] = []
        self._ignored_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in {"script", "style"}:
            self._ignored_stack.append(tag_lower)
            return

        if self._ignored_stack:
            return

        attr_dict = {k.lower(): v for k, v in attrs if v is not None}

        if tag_lower == "base" and self.base_href is None:
            if "href" in attr_dict and attr_dict["href"]:
                self.base_href = attr_dict["href"]
            return

        if tag_lower == "a" and len(self.raw_anchors) < self.max_raw_anchors:
            if "href" in attr_dict and attr_dict["href"]:
                self._current_href = attr_dict["href"]
                self._current_text_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._ignored_stack and self._ignored_stack[-1] == tag_lower:
            self._ignored_stack.pop()
            return

        if self._ignored_stack:
            return

        if tag_lower == "a" and self._current_href is not None:
            raw_text = " ".join("".join(self._current_text_parts).split())
            self.raw_anchors.append((self._current_href, raw_text))
            self._current_href = None
            self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_stack:
            return
        if self._current_href is not None:
            self._current_text_parts.append(data)


def _get_effective_base_url(raw_base_href: str | None, source_url: NormalizedURL) -> str:
    """Validate base href to prevent external base-poisoning attacks."""
    if not raw_base_href or not raw_base_href.strip():
        return source_url.normalized_url

    resolved_base = urllib.parse.urljoin(source_url.normalized_url, raw_base_href.strip())
    try:
        norm_base = normalize_url(resolved_base)
    except URLNormalizationError:
        return source_url.normalized_url

    if norm_base.scheme not in {"http", "https"}:
        return source_url.normalized_url

    if not is_same_registrable_domain(norm_base, source_url):
        return source_url.normalized_url

    return norm_base.normalized_url


def discover_and_rank_links(
    source_url: str | NormalizedURL,
    html_content: str,
    config: DiscoveryConfig | None = None,
) -> DiscoveryResult:
    """Parse HTML content, discover in-scope links, and rank important pages."""
    cfg = config or DiscoveryConfig()

    if isinstance(source_url, str):
        try:
            norm_source = normalize_url(source_url)
        except URLNormalizationError as err:
            return DiscoveryResult(
                source_url=source_url,
                discovered_links=(),
                ranked_pages=(),
                outcome=DiscoveryOutcomeCode.INVALID_SOURCE_URL,
                error_message=str(err),
            )
    else:
        norm_source = source_url

    # Truncate HTML content to max_html_chars limit
    safe_html = html_content[: cfg.max_html_chars]

    parser = HTMLLinkExtractor(max_raw_anchors=cfg.max_raw_anchors)
    try:
        parser.feed(safe_html)
    except Exception as err:
        return DiscoveryResult(
            source_url=norm_source.normalized_url,
            discovered_links=(),
            ranked_pages=(),
            outcome=DiscoveryOutcomeCode.PARSING_ERROR,
            error_message=f"HTML parsing failed: {err}",
        )

    effective_base_url = _get_effective_base_url(parser.base_href, norm_source)

    # Process and deduplicate candidate links keeping the strongest ranking evidence
    best_candidates: dict[str, tuple[int, DiscoveredLink]] = {}

    for raw_href, link_text in parser.raw_anchors:
        href_lower = raw_href.strip().lower()
        if any(href_lower.startswith(prefix) for prefix in _REJECTED_SCHEME_PREFIXES):
            continue

        resolved_target = urllib.parse.urljoin(effective_base_url, raw_href.strip())
        try:
            target_norm = normalize_url(resolved_target)
        except URLNormalizationError:
            continue

        if not is_in_scope(target_norm, norm_source, cfg.scope_mode):
            continue

        if is_asset_url(target_norm, cfg.ignored_extensions):
            continue

        link_obj = DiscoveredLink(
            source_url=norm_source.normalized_url,
            raw_href=raw_href,
            normalized_url=target_norm.normalized_url,
            link_text=link_text,
            is_same_origin=is_same_origin(target_norm, norm_source),
            is_same_registrable_domain=is_same_registrable_domain(target_norm, norm_source),
        )

        score, _ = calculate_page_score(target_norm.normalized_url, link_text)

        if target_norm.normalized_url not in best_candidates:
            best_candidates[target_norm.normalized_url] = (score, link_obj)
        else:
            existing_score, existing_link = best_candidates[target_norm.normalized_url]
            if score > existing_score:
                best_candidates[target_norm.normalized_url] = (score, link_obj)
            elif score == existing_score:
                if len(link_text) > len(existing_link.link_text):
                    best_candidates[target_norm.normalized_url] = (score, link_obj)

    # Sort deduplicated candidate links by score descending, then normalized_url ascending
    sorted_candidate_links = sorted(
        best_candidates.values(),
        key=lambda item: (-item[0], item[1].normalized_url),
    )

    discovered_links = tuple(item[1] for item in sorted_candidate_links[: cfg.max_discovered_links])

    # Rank important pages from discovered links + home page
    ranked_pages = rank_pages(
        source_url=norm_source.normalized_url,
        links=discovered_links,
        max_ranked_pages=cfg.max_ranked_pages,
    )

    outcome = (
        DiscoveryOutcomeCode.SUCCESS
        if discovered_links
        else DiscoveryOutcomeCode.NO_LINKS_DISCOVERED
    )

    return DiscoveryResult(
        source_url=norm_source.normalized_url,
        discovered_links=discovered_links,
        ranked_pages=ranked_pages,
        outcome=outcome,
    )
