"""External HTTP API Pydantic request and response schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from email_discovery_api.models.enums import ScanJobSourceType, ScanJobStatus, ScanURLStatus

T = TypeVar("T")


class CreateScanJobApiRequest(BaseModel):
    """Client request model for scan job creation with strict extra field prohibition."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255, description="Optional job name")
    source_type: ScanJobSourceType = Field(
        default=ScanJobSourceType.MANUAL, description="Job origin source type"
    )
    inputs: list[str] = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="URL input strings to crawl and scan",
    )
    configuration_snapshot: dict[str, Any] = Field(
        default_factory=dict, description="Scan job configuration settings"
    )
    scanner_version: str = Field(
        default="1.0.0", max_length=50, description="Scanner engine version string"
    )
    normalization_version: str = Field(
        default="1.0.0", max_length=50, description="URL normalization version string"
    )
    ranking_version: str = Field(
        default="1.0.0", max_length=50, description="Email ranking algorithm version string"
    )


class PreviewScanInputsApiRequest(BaseModel):
    """Client request model for URL input previewing."""

    model_config = ConfigDict(extra="forbid")

    inputs: list[str] = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="URL input strings to preview and normalize",
    )
    configuration_snapshot: dict[str, Any] = Field(
        default_factory=dict, description="Optional scan configuration for policy validation"
    )


class ScanJobApiResponse(BaseModel):
    """Public scan job detail response model."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    organization_id: UUID
    created_by_user_id: UUID
    name: str | None = None
    status: ScanJobStatus
    source_type: ScanJobSourceType
    scanner_version: str
    normalization_version: str
    ranking_version: str
    configuration_snapshot: dict[str, Any]
    total_input_count: int
    valid_input_count: int
    duplicate_input_count: int
    invalid_input_count: int
    queued_count: int
    running_count: int
    completed_count: int
    failed_count: int
    email_finding_count: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancellation_requested_at: datetime | None = None

    @classmethod
    def from_orm_model(cls, job: Any) -> ScanJobApiResponse:
        """Map ScanJob ORM model to API response schema without lazy relationship issues."""
        invalid_count = job.total_input_count - job.valid_input_count - job.duplicate_input_count
        return cls(
            id=job.id,
            organization_id=job.organization_id,
            created_by_user_id=job.created_by_user_id,
            name=job.name,
            status=ScanJobStatus(job.status),
            source_type=ScanJobSourceType(job.source_type),
            scanner_version=job.scanner_version,
            normalization_version=job.normalization_version,
            ranking_version=job.ranking_version,
            configuration_snapshot=job.configuration_snapshot,
            total_input_count=job.total_input_count,
            valid_input_count=job.valid_input_count,
            duplicate_input_count=job.duplicate_input_count,
            invalid_input_count=max(0, invalid_count),
            queued_count=job.queued_count,
            running_count=job.running_count,
            completed_count=job.completed_count,
            failed_count=job.failed_count,
            email_finding_count=job.email_finding_count,
            created_at=job.created_at or datetime.now(UTC),
            started_at=job.started_at,
            completed_at=job.completed_at,
            cancellation_requested_at=job.cancellation_requested_at,
        )


class ScanInputPreviewItemApiResponse(BaseModel):
    """Preview item classification result."""

    model_config = ConfigDict(frozen=True)

    original_index: int
    original_input: str
    normalized_url: str | None = None
    normalized_domain: str | None = None
    classification: str
    duplicate_of_index: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class PreviewScanInputsApiResponse(BaseModel):
    """Response model for scan input preview endpoint."""

    model_config = ConfigDict(frozen=True)

    previews: list[ScanInputPreviewItemApiResponse]
    total_input_count: int
    valid_input_count: int
    duplicate_input_count: int
    invalid_input_count: int


class ScanJobProgressApiResponse(BaseModel):
    """Public progress response model."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    status: ScanJobStatus
    progress_percentage: float
    total_input_count: int
    valid_input_count: int
    duplicate_input_count: int
    invalid_input_count: int
    queued_count: int
    running_count: int
    completed_count: int
    failed_count: int
    email_finding_count: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ScanURLApiResponse(BaseModel):
    """Public scan URL detail response model."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    scan_job_id: UUID
    original_index: int
    original_input: str
    normalized_url: str | None = None
    normalized_domain: str | None = None
    status: ScanURLStatus
    duplicate_of_scan_url_id: UUID | None = None
    last_error_code: str | None = None
    created_at: datetime

    @classmethod
    def from_orm_model(cls, url: Any) -> ScanURLApiResponse:
        return cls(
            id=url.id,
            scan_job_id=url.scan_job_id,
            original_index=url.original_index,
            original_input=url.original_input,
            normalized_url=url.normalized_url,
            normalized_domain=url.normalized_domain,
            status=ScanURLStatus(url.status),
            duplicate_of_scan_url_id=url.duplicate_of_scan_url_id,
            last_error_code=url.last_error_code,
            created_at=url.created_at or datetime.now(UTC),
        )


class JobEventApiResponse(BaseModel):
    """Public job event response model."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    scan_job_id: UUID
    event_type: str
    sequence_number: int
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_orm_model(cls, event: Any) -> JobEventApiResponse:
        return cls(
            id=event.id,
            scan_job_id=event.scan_job_id,
            event_type=event.event_type,
            sequence_number=event.sequence_number,
            payload=event.payload or {},
            created_at=event.created_at or datetime.now(UTC),
        )


class PaginatedResponse[T](BaseModel):
    """Generic paginated response envelope with next_cursor."""

    model_config = ConfigDict(frozen=True)

    items: list[T]
    next_cursor: str | None = None
