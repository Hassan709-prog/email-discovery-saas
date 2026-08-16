"""Independent scanner core for the Email Discovery SaaS."""

from email_scanner.errors import (
    HostSafetyError,
    HostSafetyErrorCode,
    URLNormalizationError,
    URLNormalizationErrorCode,
)
from email_scanner.host_safety import validate_public_host
from email_scanner.models import HostType, NormalizedURL
from email_scanner.normalization import normalize_url

__all__ = [
    "HostSafetyError",
    "HostSafetyErrorCode",
    "HostType",
    "NormalizedURL",
    "URLNormalizationError",
    "URLNormalizationErrorCode",
    "normalize_url",
    "validate_public_host",
]
