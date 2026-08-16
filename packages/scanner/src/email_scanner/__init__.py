"""Independent scanner core for the Email Discovery SaaS."""

from email_scanner.errors import (
    URLNormalizationError,
    URLNormalizationErrorCode,
)
from email_scanner.models import HostType, NormalizedURL

__all__ = [
    "HostType",
    "NormalizedURL",
    "URLNormalizationError",
    "URLNormalizationErrorCode",
]
