"""Typed values returned by scanner-core."""

import math
from dataclasses import dataclass, field
from enum import StrEnum

from email_scanner.errors import (
    DiscoveryOutcomeCode,
    EmailRejectionCode,
    ExtractionOutcomeCode,
    FetchOutcomeCode,
    PageScanOutcome,
    RobotsDecisionCode,
    SiteScanOutcome,
)


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


class EmailSourceKind(StrEnum):
    """Source origin of an extracted email candidate."""

    VISIBLE_TEXT = "VISIBLE_TEXT"
    MAILTO = "MAILTO"
    OBFUSCATED_TEXT = "OBFUSCATED_TEXT"


class EmailDisposition(StrEnum):
    """Disposition of an extracted email candidate."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class EmailCategory(StrEnum):
    """Classification of an accepted email address."""

    ROLE_BASED = "ROLE_BASED"
    PERSONAL_OR_NAMED = "PERSONAL_OR_NAMED"
    NO_REPLY = "NO_REPLY"
    UNKNOWN = "UNKNOWN"


class DomainAffinity(StrEnum):
    """Domain affinity of an email address relative to the source page URL."""

    EXACT_HOST = "EXACT_HOST"
    SAME_REGISTRABLE_DOMAIN = "SAME_REGISTRABLE_DOMAIN"
    EXTERNAL = "EXTERNAL"


@dataclass(frozen=True, slots=True)
class EmailExtractionConfig:
    """Configuration options for HTML email extraction, cleaning, and filtering."""

    max_html_chars: int = 1_000_000
    max_raw_candidates: int = 2_000
    max_accepted_findings: int = 200
    max_rejected_candidates: int = 200
    max_evidence_length: int = 120
    allow_obfuscated: bool = True
    allow_external_domains: bool = True
    reject_no_reply: bool = True
    reject_dummy_test: bool = True

    def __post_init__(self) -> None:
        from email_scanner.errors import ExtractionConfigError, ExtractionConfigErrorCode

        if self.max_html_chars < 1:
            raise ExtractionConfigError(
                ExtractionConfigErrorCode.INVALID_LIMIT,
                "max_html_chars must be at least 1.",
            )
        if self.max_raw_candidates < 1:
            raise ExtractionConfigError(
                ExtractionConfigErrorCode.INVALID_LIMIT,
                "max_raw_candidates must be at least 1.",
            )
        if self.max_accepted_findings < 1:
            raise ExtractionConfigError(
                ExtractionConfigErrorCode.INVALID_LIMIT,
                "max_accepted_findings must be at least 1.",
            )
        if self.max_rejected_candidates < 1:
            raise ExtractionConfigError(
                ExtractionConfigErrorCode.INVALID_LIMIT,
                "max_rejected_candidates must be at least 1.",
            )
        if self.max_evidence_length < 10:
            raise ExtractionConfigError(
                ExtractionConfigErrorCode.INVALID_LIMIT,
                "max_evidence_length must be at least 10.",
            )


@dataclass(frozen=True, slots=True)
class EmailFinding:
    """An accepted email address discovered on a source page."""

    source_url: str
    raw_candidate: str
    canonical_email: str
    local_part: str
    domain: str
    source_kind: EmailSourceKind
    category: EmailCategory
    domain_affinity: DomainAffinity
    evidence_snippet: str
    disposition: EmailDisposition = EmailDisposition.ACCEPTED


@dataclass(frozen=True, slots=True)
class RejectedEmailCandidate:
    """An email candidate rejected during extraction/validation with audit reason."""

    source_url: str
    raw_candidate: str
    rejection_code: EmailRejectionCode
    reason: str
    source_kind: EmailSourceKind
    evidence_snippet: str
    disposition: EmailDisposition = EmailDisposition.REJECTED


@dataclass(frozen=True, slots=True)
class EmailExtractionResult:
    """Typed result of deterministic HTML email extraction."""

    source_url: str
    findings: tuple[EmailFinding, ...]
    rejected_candidates: tuple[RejectedEmailCandidate, ...]
    outcome: ExtractionOutcomeCode
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SiteScanConfig:
    """Configuration options for deterministic single-site scan orchestration."""

    max_pages: int = 10
    max_depth: int = 2
    max_total_discovered_urls: int = 100
    max_email_findings: int = 50
    max_rejected_candidates: int = 500
    minimum_request_interval_seconds: float = 1.0
    max_elapsed_seconds: float | None = 60.0
    fetch_config: FetchConfig = field(default_factory=FetchConfig)
    discovery_config: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    email_config: EmailExtractionConfig = field(default_factory=EmailExtractionConfig)

    def __post_init__(self) -> None:
        from email_scanner.errors import SiteScanConfigError, SiteScanConfigErrorCode

        def _check_finite(val: float, name: str) -> None:
            if math.isnan(val) or math.isinf(val):
                raise SiteScanConfigError(
                    SiteScanConfigErrorCode.NON_FINITE_VALUE,
                    f"{name} must be a finite number",
                )

        _check_finite(self.minimum_request_interval_seconds, "minimum_request_interval_seconds")
        if self.max_elapsed_seconds is not None:
            _check_finite(self.max_elapsed_seconds, "max_elapsed_seconds")
            if self.max_elapsed_seconds <= 0.0:
                raise SiteScanConfigError(
                    SiteScanConfigErrorCode.INVALID_INTERVAL,
                    "max_elapsed_seconds must be positive",
                )

        if self.max_pages < 1:
            raise SiteScanConfigError(
                SiteScanConfigErrorCode.INVALID_LIMIT,
                "max_pages must be at least 1",
            )
        if self.max_depth < 0:
            raise SiteScanConfigError(
                SiteScanConfigErrorCode.INVALID_LIMIT,
                "max_depth must be non-negative",
            )
        if self.max_total_discovered_urls < 1:
            raise SiteScanConfigError(
                SiteScanConfigErrorCode.INVALID_LIMIT,
                "max_total_discovered_urls must be at least 1",
            )
        if self.max_email_findings < 1:
            raise SiteScanConfigError(
                SiteScanConfigErrorCode.INVALID_LIMIT,
                "max_email_findings must be at least 1",
            )
        if self.max_rejected_candidates < 1:
            raise SiteScanConfigError(
                SiteScanConfigErrorCode.INVALID_LIMIT,
                "max_rejected_candidates must be at least 1",
            )
        if self.minimum_request_interval_seconds < 0.0:
            raise SiteScanConfigError(
                SiteScanConfigErrorCode.INVALID_INTERVAL,
                "minimum_request_interval_seconds must be non-negative",
            )


@dataclass(frozen=True, slots=True)
class PageScanRecord:
    """Record of an individual page scan step within a site scan."""

    requested_url: str
    final_url: str | None
    depth: int
    outcome: PageScanOutcome
    status_code: int | None
    robots_decision: RobotsDecision
    fetch_result: FetchResult | None
    emails_found_count: int
    links_discovered_count: int
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SiteScanStatistics:
    """Deterministic counters and metrics for a site scan."""

    pages_queued: int
    pages_attempted: int
    pages_fetched: int
    pages_blocked_by_robots: int
    pages_failed: int
    urls_discovered: int
    accepted_email_findings: int
    rejected_email_candidates: int
    elapsed_seconds: float
    stop_reason: str


@dataclass(frozen=True, slots=True)
class SiteScanResult:
    """Typed result of a single-site orchestration scan."""

    starting_url: str
    outcome: SiteScanOutcome
    statistics: SiteScanStatistics
    page_records: tuple[PageScanRecord, ...]
    email_findings: tuple[EmailFinding, ...]
    rejected_email_candidates: tuple[RejectedEmailCandidate, ...]
    error_message: str | None = None
