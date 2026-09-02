"""Typed values returned by scanner-core."""

import math
from dataclasses import dataclass, field
from enum import StrEnum

from email_scanner.errors import (
    BatchItemOutcome,
    BatchScanOutcome,
    DelaySource,
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
class PinningConfig:
    """Configuration options for DNS IP pinning and failover."""

    max_ip_failover_attempts: int = 3
    allow_ipv6: bool = True

    def __post_init__(self) -> None:
        if self.max_ip_failover_attempts < 1:
            raise ValueError("max_ip_failover_attempts must be at least 1")


@dataclass(frozen=True, slots=True)
class IPConnectionAttempt:
    """Diagnostic evidence of an individual TCP connection attempt to a pinned IP."""

    target_host: str
    target_port: int
    attempted_ip: str
    success: bool
    error_message: str | None = None
    timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Configuration for deterministic bounded retry logic."""

    max_attempts_per_hop: int = 3
    max_total_fetch_attempts: int = 10
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 10.0
    max_elapsed_retry_seconds: float = 30.0
    max_retry_after_seconds: float = 60.0

    def __post_init__(self) -> None:
        from email_scanner.errors import RetryPolicyError, RetryPolicyErrorCode

        def _check_finite(val: float, name: str) -> None:
            if math.isnan(val) or math.isinf(val):
                raise RetryPolicyError(
                    RetryPolicyErrorCode.NON_FINITE_VALUE,
                    f"{name} must be a finite number",
                )

        _check_finite(self.base_delay_seconds, "base_delay_seconds")
        _check_finite(self.max_delay_seconds, "max_delay_seconds")
        _check_finite(self.max_elapsed_retry_seconds, "max_elapsed_retry_seconds")
        _check_finite(self.max_retry_after_seconds, "max_retry_after_seconds")

        if self.max_attempts_per_hop < 1:
            raise RetryPolicyError(
                RetryPolicyErrorCode.INVALID_LIMIT,
                "max_attempts_per_hop must be at least 1",
            )
        if self.max_total_fetch_attempts < 1:
            raise RetryPolicyError(
                RetryPolicyErrorCode.INVALID_LIMIT,
                "max_total_fetch_attempts must be at least 1",
            )
        if self.base_delay_seconds < 0.0:
            raise RetryPolicyError(
                RetryPolicyErrorCode.INVALID_INTERVAL,
                "base_delay_seconds must be non-negative",
            )
        if self.max_delay_seconds < 0.0:
            raise RetryPolicyError(
                RetryPolicyErrorCode.INVALID_INTERVAL,
                "max_delay_seconds must be non-negative",
            )
        if self.max_elapsed_retry_seconds < 0.0:
            raise RetryPolicyError(
                RetryPolicyErrorCode.INVALID_INTERVAL,
                "max_elapsed_retry_seconds must be non-negative",
            )
        if self.max_retry_after_seconds < 0.0:
            raise RetryPolicyError(
                RetryPolicyErrorCode.INVALID_INTERVAL,
                "max_retry_after_seconds must be non-negative",
            )


@dataclass(frozen=True, slots=True)
class FetchAttempt:
    """Record of an individual HTTP request attempt during a fetch execution."""

    hop_index: int
    hop_attempt_number: int
    global_attempt_number: int
    request_url: str
    status_code: int | None
    outcome: FetchOutcomeCode
    pinned_ip: str | None
    delay_before_attempt_seconds: float
    delay_source: DelaySource | None
    connection_attempts: tuple[IPConnectionAttempt, ...]
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class FetchConfig:
    """Configuration parameters governing network fetches."""

    timeout_total: float = 15.0
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
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    pinning_config: PinningConfig = field(default_factory=PinningConfig)
    allow_cross_domain_redirects: bool = False
    approved_redirect_domains: tuple[str, ...] = ()

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
    attempts: tuple[FetchAttempt, ...] = ()


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
class EmailEvidenceRecord:
    """Individual page evidence record for a discovered email candidate."""

    source_url: str
    source_kind: EmailSourceKind
    raw_candidate: str
    evidence_snippet: str
    page_score: int = 0


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
    evidence_records: tuple[EmailEvidenceRecord, ...] = ()


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
class SiteScanDiagnostics:
    """Immutable diagnostic breakdown of a single site scan execution."""

    total_duration_seconds: float = 0.0
    dns_resolution_duration_seconds: float = 0.0
    gate_wait_duration_seconds: float = 0.0
    robots_fetch_duration_seconds: float = 0.0
    robots_evaluation_duration_seconds: float = 0.0
    http_fetch_duration_seconds: float = 0.0
    page_processing_duration_seconds: float = 0.0
    retry_count: int = 0
    total_retry_delay_seconds: float = 0.0
    redirect_count: int = 0
    http_status: int | None = None
    failure_code: str | None = None
    time_budget_exhausted: bool = False
    cancellation_occurred: bool = False
    retry_budget_exhausted: bool = False


class SiteScanDiagnosticRecorder:
    """Request-scoped mutable recorder passed through boundary executions."""

    __slots__ = (
        "total_duration_seconds",
        "dns_resolution_duration_seconds",
        "gate_wait_duration_seconds",
        "robots_fetch_duration_seconds",
        "robots_evaluation_duration_seconds",
        "http_fetch_duration_seconds",
        "page_processing_duration_seconds",
        "retry_count",
        "total_retry_delay_seconds",
        "redirect_count",
        "http_status",
        "failure_code",
        "time_budget_exhausted",
        "cancellation_occurred",
        "retry_budget_exhausted",
    )

    def __init__(self) -> None:
        self.total_duration_seconds: float = 0.0
        self.dns_resolution_duration_seconds: float = 0.0
        self.gate_wait_duration_seconds: float = 0.0
        self.robots_fetch_duration_seconds: float = 0.0
        self.robots_evaluation_duration_seconds: float = 0.0
        self.http_fetch_duration_seconds: float = 0.0
        self.page_processing_duration_seconds: float = 0.0
        self.retry_count: int = 0
        self.total_retry_delay_seconds: float = 0.0
        self.redirect_count: int = 0
        self.http_status: int | None = None
        self.failure_code: str | None = None
        self.time_budget_exhausted: bool = False
        self.cancellation_occurred: bool = False
        self.retry_budget_exhausted: bool = False

    def build_diagnostics(self) -> SiteScanDiagnostics:
        """Create an immutable snapshot of current measurements."""
        return SiteScanDiagnostics(
            total_duration_seconds=round(self.total_duration_seconds, 4),
            dns_resolution_duration_seconds=round(self.dns_resolution_duration_seconds, 4),
            gate_wait_duration_seconds=round(self.gate_wait_duration_seconds, 4),
            robots_fetch_duration_seconds=round(self.robots_fetch_duration_seconds, 4),
            robots_evaluation_duration_seconds=round(self.robots_evaluation_duration_seconds, 4),
            http_fetch_duration_seconds=round(self.http_fetch_duration_seconds, 4),
            page_processing_duration_seconds=round(self.page_processing_duration_seconds, 4),
            retry_count=self.retry_count,
            total_retry_delay_seconds=round(self.total_retry_delay_seconds, 4),
            redirect_count=self.redirect_count,
            http_status=self.http_status,
            failure_code=self.failure_code,
            time_budget_exhausted=self.time_budget_exhausted,
            cancellation_occurred=self.cancellation_occurred,
            retry_budget_exhausted=self.retry_budget_exhausted,
        )


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
    diagnostics: SiteScanDiagnostics | None = None


@dataclass(frozen=True, slots=True)
class BatchScanConfig:
    """Configuration options for multi-URL batch scan orchestration."""

    max_inputs: int = 100
    global_concurrency: int = 5
    per_domain_concurrency: int = 1
    default_minimum_domain_interval_seconds: float = 1.0
    coalesce_duplicate_urls: bool = True
    max_elapsed_batch_seconds: float | None = 300.0
    site_scan_config: SiteScanConfig = field(default_factory=SiteScanConfig)

    def __post_init__(self) -> None:
        from email_scanner.errors import BatchScanConfigError, BatchScanConfigErrorCode

        def _check_finite(val: float, name: str) -> None:
            if math.isnan(val) or math.isinf(val):
                raise BatchScanConfigError(
                    BatchScanConfigErrorCode.NON_FINITE_VALUE,
                    f"{name} must be a finite number",
                )

        _check_finite(
            self.default_minimum_domain_interval_seconds,
            "default_minimum_domain_interval_seconds",
        )
        if self.max_elapsed_batch_seconds is not None:
            _check_finite(self.max_elapsed_batch_seconds, "max_elapsed_batch_seconds")
            if self.max_elapsed_batch_seconds <= 0.0:
                raise BatchScanConfigError(
                    BatchScanConfigErrorCode.INVALID_INTERVAL,
                    "max_elapsed_batch_seconds must be positive",
                )

        if self.max_inputs < 1:
            raise BatchScanConfigError(
                BatchScanConfigErrorCode.INVALID_LIMIT,
                "max_inputs must be at least 1",
            )
        if self.global_concurrency < 1:
            raise BatchScanConfigError(
                BatchScanConfigErrorCode.INVALID_LIMIT,
                "global_concurrency must be at least 1",
            )
        if self.per_domain_concurrency < 1:
            raise BatchScanConfigError(
                BatchScanConfigErrorCode.INVALID_LIMIT,
                "per_domain_concurrency must be at least 1",
            )
        if self.default_minimum_domain_interval_seconds < 0.0:
            raise BatchScanConfigError(
                BatchScanConfigErrorCode.INVALID_INTERVAL,
                "default_minimum_domain_interval_seconds must be non-negative",
            )


@dataclass(frozen=True, slots=True)
class BatchScanItem:
    """Result record for an individual input item within a batch scan."""

    original_index: int
    original_input: str
    normalized_url: str | None
    outcome: BatchItemOutcome
    is_duplicate: bool
    duplicate_of_index: int | None
    result: SiteScanResult | None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class BatchScanStatistics:
    """Deterministic counters and metrics for a multi-URL batch scan."""

    total_inputs: int
    valid_inputs: int
    invalid_inputs: int
    unique_normalized_urls: int
    duplicate_coalesced_items: int
    started_scans: int
    completed_scans: int
    failed_scans: int
    cancelled_scans: int
    peak_global_concurrency: int
    peak_per_domain_concurrency: int
    elapsed_seconds: float
    stop_reason: str


@dataclass(frozen=True, slots=True)
class BatchScanResult:
    """Typed result of a multi-URL batch scan execution."""

    outcome: BatchScanOutcome
    statistics: BatchScanStatistics
    items: tuple[BatchScanItem, ...]
    error_message: str | None = None
