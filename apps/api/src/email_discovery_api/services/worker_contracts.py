"""Shared data contracts and enums for worker execution and claim management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from email_discovery_api.services.errors import ServiceError, ServiceErrorCode


@dataclass(frozen=True, slots=True)
class URLClaim:
    """Typed immutable claim token returned when a worker claims a ScanURL for scanning."""

    scan_url_id: uuid.UUID
    organization_id: uuid.UUID
    job_id: uuid.UUID
    original_input: str
    normalized_url: str | None
    normalized_domain: str | None
    lease_owner: str
    attempt_count: int
    max_attempts: int
    lease_expires_at: datetime


class HeartbeatStatus(StrEnum):
    """Status returned by lease heartbeat renewal."""

    RENEWED = "RENEWED"
    LEASE_LOST = "LEASE_LOST"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    """Typed result of a heartbeat renewal check."""

    status: HeartbeatStatus
    lease_expires_at: datetime | None = None


class LeaseLostError(ServiceError):
    """Raised when a worker operation fails conditional fencing or lease expired."""

    def __init__(self, scan_url_id: uuid.UUID, lease_owner: str, attempt_count: int) -> None:
        super().__init__(
            code=ServiceErrorCode.LEASE_LOST,
            message=(
                f"Lease lost or expired for ScanURL {scan_url_id} "
                f"(owner={lease_owner!r}, attempt={attempt_count})"
            ),
            details={
                "scan_url_id": str(scan_url_id),
                "lease_owner": lease_owner,
                "attempt_count": attempt_count,
            },
        )
