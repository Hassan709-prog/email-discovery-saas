"""Typed values returned by scanner-core."""

from dataclasses import dataclass
from enum import StrEnum

from email_scanner.errors import DiscoveryOutcomeCode, FetchOutcomeCode, RobotsDecisionCode


class HostType(StrEnum):
    """The normalized form of a URL host."""

    DOMAIN = "DOMAIN"
    IPV4 = "IPV4"
    IPV6 = "IPV6"


@dataclass(frozen=True, slots=True)
class NormalizedURL:
    """Deterministic representation of one accepted URL."""

    original_url: str
    normalized_url: str
    scheme: str
    hostname: str
    port: int | None
    path: str
    query: str
    host_type: HostType
    registrable_domain: str | None


@dataclass(frozen=True, slots=True)
class FetchConfig:
    """Configuration options for HTTP fetcher and robots.txt evaluation."""

    timeout_connect: float = 5.0
    timeout_read: float = 10.0
    timeout_write: float = 5.0
    timeout_pool: float = 5.0
    max_redirects: int = 5
    max_response_bytes: int = 2 * 1024 * 1024
    user_agent: str = "EmailDiscoveryBot/1.0 (+https://example.com/bot)"
    robots_user_agent_token: str = "EmailDiscoveryBot"
    allowed_content_types: tuple[str, ...] = (
        "text/html",
        "application/xhtml+xml",
    )

    def __post_init__(self) -> None:
        from email_scanner.errors import FetchConfigError, FetchConfigErrorCode

        if (
            self.timeout_connect < 0
            or self.timeout_read < 0
            or self.timeout_write < 0
            or self.timeout_pool < 0
        ):
            raise FetchConfigError(
                FetchConfigErrorCode.INVALID_TIMEOUT,
                "Timeouts must be non-negative.",
            )
        if self.max_redirects < 0:
            raise FetchConfigError(
                FetchConfigErrorCode.INVALID_MAX_REDIRECTS,
                "max_redirects must be non-negative.",
            )
        if not self.user_agent or not self.user_agent.strip():
            raise FetchConfigError(
                FetchConfigErrorCode.INVALID_USER_AGENT,
                "user_agent cannot be empty.",
            )
        if not self.robots_user_agent_token or not self.robots_user_agent_token.strip():
            raise FetchConfigError(
                FetchConfigErrorCode.INVALID_USER_AGENT,
                "robots_user_agent_token cannot be empty.",
            )
        if self.max_response_bytes < 1:
            raise FetchConfigError(
                FetchConfigErrorCode.INVALID_MAX_RESPONSE_BYTES,
                "max_response_bytes must be at least 1.",
            )


@dataclass(frozen=True, slots=True)
class RedirectHop:
    """Record of a single redirect step."""

    url: str
    status_code: int
    location: str


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Typed outcome of an async HTTP fetch request."""

    final_url: str
    status_code: int | None
    content_type: str | None
    body_text: str | None
    redirect_history: tuple[RedirectHop, ...]
    outcome: FetchOutcomeCode
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """Typed decision produced by robots.txt evaluation."""

    target_url: str
    decision: RobotsDecisionCode
    crawl_delay: float | None
    reason: str


class CrawlScopeMode(StrEnum):
    """Modes for crawl scope filtering."""

    SAME_REGISTRABLE_DOMAIN = "SAME_REGISTRABLE_DOMAIN"
    SAME_ORIGIN = "SAME_ORIGIN"


_DEFAULT_IGNORED_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".bmp",
    ".tiff",
    ".mp4",
    ".webm",
    ".avi",
    ".mov",
    ".mkv",
    ".flv",
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".bz2",
    ".xz",
    ".pdf",
    ".exe",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".apk",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".csv",
    ".json",
    ".xml",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
)


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Configuration for HTML link discovery, scope filtering, and page ranking."""

    scope_mode: CrawlScopeMode = CrawlScopeMode.SAME_REGISTRABLE_DOMAIN
    max_html_chars: int = 1_000_000
    max_raw_anchors: int = 2_000
    max_discovered_links: int = 500
    max_ranked_pages: int = 50
    ignored_extensions: tuple[str, ...] = _DEFAULT_IGNORED_EXTENSIONS

    def __post_init__(self) -> None:
        from email_scanner.errors import DiscoveryConfigError, DiscoveryConfigErrorCode

        if self.max_html_chars < 1:
            raise DiscoveryConfigError(
                DiscoveryConfigErrorCode.INVALID_LIMIT,
                "max_html_chars must be at least 1.",
            )
        if self.max_raw_anchors < 1:
            raise DiscoveryConfigError(
                DiscoveryConfigErrorCode.INVALID_LIMIT,
                "max_raw_anchors must be at least 1.",
            )
        if self.max_discovered_links < 1:
            raise DiscoveryConfigError(
                DiscoveryConfigErrorCode.INVALID_LIMIT,
                "max_discovered_links must be at least 1.",
            )
        if self.max_ranked_pages < 1:
            raise DiscoveryConfigError(
                DiscoveryConfigErrorCode.INVALID_LIMIT,
                "max_ranked_pages must be at least 1.",
            )
        if self.max_ranked_pages > self.max_discovered_links:
            raise DiscoveryConfigError(
                DiscoveryConfigErrorCode.INVALID_LIMIT,
                "max_ranked_pages cannot exceed max_discovered_links.",
            )


@dataclass(frozen=True, slots=True)
class DiscoveredLink:
    """A single HTML candidate link discovered on a source page."""

    source_url: str
    raw_href: str
    normalized_url: str
    link_text: str
    is_same_origin: bool
    is_same_registrable_domain: bool


@dataclass(frozen=True, slots=True)
class RankedPage:
    """An important candidate page prioritized for scanner operations."""

    url: str
    score: int
    signals: tuple[str, ...]
    ranking_version: str
    discovered_link: DiscoveredLink | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Typed result of HTML link discovery and ranking."""

    source_url: str
    discovered_links: tuple[DiscoveredLink, ...]
    ranked_pages: tuple[RankedPage, ...]
    outcome: DiscoveryOutcomeCode
    error_message: str | None = None
