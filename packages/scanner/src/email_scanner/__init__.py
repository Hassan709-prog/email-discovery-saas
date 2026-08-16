"""Independent scanner core for the Email Discovery SaaS."""

from email_scanner.discovery import HTMLLinkExtractor, discover_and_rank_links
from email_scanner.dns import AsyncDNSResolver, SystemDNSResolver
from email_scanner.errors import (
    DiscoveryConfigError,
    DiscoveryConfigErrorCode,
    DiscoveryOutcomeCode,
    FetchConfigError,
    FetchConfigErrorCode,
    FetchOutcomeCode,
    HostSafetyError,
    HostSafetyErrorCode,
    RobotsDecisionCode,
    URLNormalizationError,
    URLNormalizationErrorCode,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.host_safety import validate_public_host
from email_scanner.models import (
    CrawlScopeMode,
    DiscoveredLink,
    DiscoveryConfig,
    DiscoveryResult,
    FetchConfig,
    FetchResult,
    HostType,
    NormalizedURL,
    RankedPage,
    RedirectHop,
    RobotsDecision,
)
from email_scanner.normalization import normalize_url
from email_scanner.ranking import RANKING_VERSION, calculate_page_score, rank_pages
from email_scanner.robots import RobotsPolicyEvaluator
from email_scanner.scope import (
    is_asset_url,
    is_in_scope,
    is_same_origin,
    is_same_registrable_domain,
)

__all__ = [
    "RANKING_VERSION",
    "AsyncDNSResolver",
    "AsyncHTTPFetcher",
    "CrawlScopeMode",
    "DiscoveredLink",
    "DiscoveryConfig",
    "DiscoveryConfigError",
    "DiscoveryConfigErrorCode",
    "DiscoveryOutcomeCode",
    "DiscoveryResult",
    "FetchConfig",
    "FetchConfigError",
    "FetchConfigErrorCode",
    "FetchOutcomeCode",
    "FetchResult",
    "HTMLLinkExtractor",
    "HostSafetyError",
    "HostSafetyErrorCode",
    "HostType",
    "NormalizedURL",
    "RankedPage",
    "RedirectHop",
    "RobotsDecision",
    "RobotsDecisionCode",
    "RobotsPolicyEvaluator",
    "SystemDNSResolver",
    "URLNormalizationError",
    "URLNormalizationErrorCode",
    "calculate_page_score",
    "discover_and_rank_links",
    "is_asset_url",
    "is_in_scope",
    "is_same_origin",
    "is_same_registrable_domain",
    "normalize_url",
    "rank_pages",
    "validate_public_host",
]
