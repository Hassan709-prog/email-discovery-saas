"""Pydantic schemas for batch scan job commands, previews, and progress reads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from email_discovery_api.models.enums import ScanJobSourceType, ScanJobStatus


class CreateScanJobCommand(BaseModel):
    """Command model for creating a new batch scan job."""

    model_config = ConfigDict(frozen=True)

    organization_id: UUID
    created_by_user_id: UUID
    name: str | None = Field(default=None, max_length=255)
    source_type: ScanJobSourceType = ScanJobSourceType.MANUAL
    inputs: list[str] = Field(..., min_length=1)
    idempotency_key: str | None = Field(default=None, max_length=255)
    overrides: dict[int, bool] | None = Field(default=None)
    configuration_snapshot: dict[str, Any] = Field(default_factory=dict)
    scanner_version: str = Field(default="1.0.0", max_length=50)
    normalization_version: str = Field(default="1.0.0", max_length=50)
    cleaning_policy_version: str = Field(default="1.0.0", max_length=50)
    ranking_version: str = Field(default="1.0.0", max_length=50)


class ScanInputPreview(BaseModel):
    """Processed preview item of a single URL input line."""

    model_config = ConfigDict(frozen=True)

    original_index: int
    original_input: str
    normalized_url: str | None = None
    normalized_domain: str | None = None
    classification: Literal["VALID", "INVALID", "DUPLICATE"]
    duplicate_of_index: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class ScanJobProgress(BaseModel):
    """Read progress response for a batch scan job calculated from database counters."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    status: ScanJobStatus
    total_input_count: int
    valid_input_count: int
    duplicate_input_count: int
    invalid_input_count: int
    queued_count: int
    running_count: int
    completed_count: int
    failed_count: int
    email_finding_count: int
    progress_percentage: float
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_counts(
        cls,
        *,
        job_id: UUID,
        status: ScanJobStatus,
        total_input_count: int,
        valid_input_count: int,
        duplicate_input_count: int,
        queued_count: int,
        running_count: int,
        completed_count: int,
        failed_count: int,
        email_finding_count: int,
        created_at: datetime,
        started_at: datetime | None,
        completed_at: datetime | None,
    ) -> ScanJobProgress:
        """Construct progress instance deriving invalid count and progress percentage safely."""
        invalid_count = max(0, total_input_count - valid_input_count - duplicate_input_count)

        if valid_input_count <= 0:
            percentage = (
                100.0
                if status in (ScanJobStatus.COMPLETED, ScanJobStatus.COMPLETED_WITH_ERRORS)
                else 0.0
            )
        else:
            processed = completed_count + failed_count
            percentage = min(100.0, max(0.0, (processed / valid_input_count) * 100.0))

        return cls(
            job_id=job_id,
            status=status,
            total_input_count=total_input_count,
            valid_input_count=valid_input_count,
            duplicate_input_count=duplicate_input_count,
            invalid_input_count=invalid_count,
            queued_count=queued_count,
            running_count=running_count,
            completed_count=completed_count,
            failed_count=failed_count,
            email_finding_count=email_finding_count,
            progress_percentage=round(percentage, 2),
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
        )
