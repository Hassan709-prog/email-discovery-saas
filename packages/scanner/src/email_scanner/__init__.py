"""Independent scanner core for the Email Discovery SaaS."""

from email_scanner.dns import AsyncDNSResolver, SystemDNSResolver
from email_scanner.errors import (
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
    FetchConfig,
    FetchResult,
    HostType,
    NormalizedURL,
    RedirectHop,
    RobotsDecision,
)
from email_scanner.normalization import normalize_url
from email_scanner.robots import RobotsPolicyEvaluator

__all__ = [
    "AsyncDNSResolver",
    "AsyncHTTPFetcher",
    "FetchConfig",
    "FetchConfigError",
    "FetchConfigErrorCode",
    "FetchOutcomeCode",
    "FetchResult",
    "HostSafetyError",
    "HostSafetyErrorCode",
    "HostType",
    "NormalizedURL",
    "RedirectHop",
    "RobotsDecision",
    "RobotsDecisionCode",
    "RobotsPolicyEvaluator",
    "SystemDNSResolver",
    "URLNormalizationError",
    "URLNormalizationErrorCode",
    "normalize_url",
    "validate_public_host",
]
