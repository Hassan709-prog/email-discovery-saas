"""Result persistence limits, bounds, and policy definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from email_discovery_api.services.errors import ServiceError, ServiceErrorCode

if TYPE_CHECKING:
    from email_scanner.models import SiteScanResult


@dataclass(frozen=True, slots=True)
class ResultPersistencePolicy:
    """Injectable policy bounds for scanning result persistence."""

    max_pages_per_result: int = 100
    max_findings_per_result: int = 500
    max_evidence_per_finding: int = 50
    max_rejected_candidates_per_result: int = 500
    max_snippet_length: int = 255
    max_error_message_length: int = 500
    max_redirect_hops: int = 20
    max_connection_attempts: int = 20

    def validate_site_scan_result(self, result: SiteScanResult) -> None:
        """Validate site scan result counts against configured policy limits."""
        if len(result.page_records) > self.max_pages_per_result:
            cnt, limit = len(result.page_records), self.max_pages_per_result
            raise ServiceError(
                ServiceErrorCode.RESULT_TOO_LARGE, f"Page count ({cnt}) exceeds limit ({limit})"
            )
        if len(result.email_findings) > self.max_findings_per_result:
            cnt, limit = len(result.email_findings), self.max_findings_per_result
            raise ServiceError(
                ServiceErrorCode.RESULT_TOO_LARGE, f"Finding count ({cnt}) exceeds limit ({limit})"
            )
        if len(result.rejected_email_candidates) > self.max_rejected_candidates_per_result:
            msg = (
                f"Rejected candidate count ({len(result.rejected_email_candidates)}) "
                f"exceeds limit ({self.max_rejected_candidates_per_result})"
            )
            raise ServiceError(ServiceErrorCode.RESULT_TOO_LARGE, msg)
