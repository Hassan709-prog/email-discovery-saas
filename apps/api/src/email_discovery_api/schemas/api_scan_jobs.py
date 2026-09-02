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
    overrides: dict[int, bool] | None = Field(
        default=None, description="Optional index-based selection overrides"
    )
    approved_redirect_domains: dict[str, str] | None = Field(
        default=None, description="Optional per-URL redirect domain approvals"
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
    cleaning_policy_version: str = Field(
        default="1.0.0", max_length=50, description="Cleaning policy version string"
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
    overrides: dict[int, bool] | None = Field(
        default=None, description="Optional index-based selection overrides"
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
    canonical_target: str | None = None
    classification: str
    decision_code: str
    explanation: str
    duplicate_of_index: int | None = None
    is_selected: bool
    user_override_permitted: bool
    ui_label: str
    error_code: str | None = None
    error_message: str | None = None


class PreviewScanInputsApiResponse(BaseModel):
    """Response model for scan input preview endpoint."""

    model_config = ConfigDict(frozen=True)

    previews: list[ScanInputPreviewItemApiResponse]
    total_input_count: int
    ready_to_check_count: int
    needs_review_count: int
    unrelated_platform_count: int
    duplicate_input_count: int
    invalid_input_count: int
    final_target_count: int
    valid_input_count: int
    accepted_canonical_targets: list[str]


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


class ScanURLDiagnosticsApiResponse(BaseModel):
    """Bounded, typed diagnostic breakdown for an individual target URL."""

    model_config = ConfigDict(frozen=True)

    total_duration_seconds: float | None = None
    pages_attempted: int | None = None
    pages_fetched: int | None = None
    retry_count: int | None = None
    last_failure_code: str | None = None
    selected_primary_email: str | None = None
    primary_email_selection_version: str | None = None
    plain_language_outcome: str | None = None
    failure_reason: str | None = None


_FAILURE_REASON_DESCRIPTIONS: dict[str, str] = {
    "ROBOTS_BLOCKED": "Blocked by an explicit robots.txt rule",
    "ROBOTS_FETCH_ERROR": "Skipped because robots.txt could not be verified",
    "ROBOTS_TEMPORARY_FAILURE": "Skipped because robots.txt could not be verified",
    "HTTP_403": "Website denied automated access",
    "AUTOMATED_ACCESS_DENIED": "Website denied automated access",
    "OUT_OF_SCOPE_REDIRECT": "Redirected to another business website—approval required",
    "BUSINESS_DOMAIN_REDIRECT_REVIEW": "Redirected to another business website—approval required",
    "NO_EMAIL": "No public email detected",
    "REJECTED_UNRELATED": "Public email found but rejected as unrelated",
    "DIRECTORY_INDEX_ONLY": "No meaningful website content found across safe origin variants",
    "TRANSPORT_ERROR": "Network or TLS connection failed",
    "DNS_RESOLUTION_FAILED": "Network or TLS connection failed",
    "TLS_VERIFICATION_FAILED": "Network or TLS connection failed",
    "CONNECT_TIMEOUT": "Network or TLS connection failed",
    "READ_TIMEOUT": "Network or TLS connection failed",
    "GENERIC_TIMEOUT": "Network or TLS connection failed",
}


def format_failure_reason(fail_code: str | None, error_message: str | None) -> str:
    """Format failure_reason into clean human-readable descriptions."""
    if fail_code and fail_code in _FAILURE_REASON_DESCRIPTIONS:
        return _FAILURE_REASON_DESCRIPTIONS[fail_code]
    return fail_code or error_message or "Scan execution failed"


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
    # Optional Phase 4B diagnostic fields
    processing_duration_seconds: float | None = None
    retry_count: int | None = None
    pages_checked: int | None = None
    selected_primary_email: str | None = None
    primary_email_selection_version: str | None = None
    plain_language_outcome: str | None = None
    failure_reason: str | None = None
    approved_redirect_domain: str | None = None
    redirect_target_domain: str | None = None
    redirect_target_url: str | None = None
    requires_redirect_approval: bool = False
    can_approve_redirect: bool = False
    diagnostics: ScanURLDiagnosticsApiResponse | None = None

    @classmethod
    def from_orm_model(cls, url: Any) -> ScanURLApiResponse:
        finding = getattr(url, "email_finding", None)
        selected_email = finding.canonical_email if finding else None
        selection_version = "primary-email-selection-v1" if selected_email else None

        st = ScanURLStatus(url.status)
        if st == ScanURLStatus.COMPLETED:
            plain_outcome = "Completed"
            reason = None
        elif st == ScanURLStatus.NO_EMAIL:
            plain_outcome = "No Email Found"
            reason = "No suitable public email found"
        elif st == ScanURLStatus.CANCELLED:
            plain_outcome = "Cancelled"
            reason = "Scan job was cancelled"
        elif st == ScanURLStatus.DUPLICATE:
            plain_outcome = "Duplicate"
            reason = "Duplicate input coalesced"
        elif st == ScanURLStatus.INVALID:
            plain_outcome = "Invalid URL"
            reason = url.last_error_message or "URL normalization failed"
        else:
            plain_outcome = "Failed"
            fail_code = getattr(url, "last_failure_code", None) or url.last_error_code
            reason = format_failure_reason(fail_code, url.last_error_message)

        tot_dur = getattr(url, "total_duration_seconds", None)
        p_attempted = getattr(url, "pages_attempted", None)
        r_count = getattr(url, "retry_count", None)
        p_fetched = getattr(url, "pages_fetched", None)
        last_fail = getattr(url, "last_failure_code", None)

        diag_obj = None
        if any(v is not None for v in (tot_dur, p_attempted, r_count, last_fail, selected_email)):
            diag_obj = ScanURLDiagnosticsApiResponse(
                total_duration_seconds=tot_dur,
                pages_attempted=p_attempted,
                pages_fetched=p_fetched,
                retry_count=r_count,
                last_failure_code=last_fail,
                selected_primary_email=selected_email,
                primary_email_selection_version=selection_version,
                plain_language_outcome=plain_outcome,
                failure_reason=reason,
            )

        app_redirect = getattr(url, "approved_redirect_domain", None)
        target_redirect = getattr(url, "redirect_target_domain", None)
        target_redirect_url = getattr(url, "redirect_target_url", None)
        last_fail_code = getattr(url, "last_failure_code", None) or url.last_error_code

        requires_redirect = (
            st == ScanURLStatus.FAILED
            and last_fail_code in ("OUT_OF_SCOPE_REDIRECT", "BUSINESS_DOMAIN_REDIRECT_REVIEW")
            and target_redirect is not None
            and app_redirect is None
        )
        job_obj = getattr(url, "scan_job", None)
        can_approve = requires_redirect and (
            job_obj is None or getattr(job_obj, "status", None) not in ("CANCELLED", "CANCELLING")
        )

        return cls(
            id=url.id,
            scan_job_id=url.scan_job_id,
            original_index=url.original_index,
            original_input=url.original_input,
            normalized_url=url.normalized_url,
            normalized_domain=url.normalized_domain,
            status=st,
            duplicate_of_scan_url_id=url.duplicate_of_scan_url_id,
            last_error_code=url.last_error_code,
            created_at=url.created_at or datetime.now(UTC),
            processing_duration_seconds=tot_dur,
            retry_count=r_count,
            pages_checked=p_attempted,
            selected_primary_email=selected_email,
            primary_email_selection_version=selection_version,
            plain_language_outcome=plain_outcome,
            failure_reason=reason,
            approved_redirect_domain=app_redirect,
            redirect_target_domain=target_redirect,
            redirect_target_url=target_redirect_url,
            requires_redirect_approval=requires_redirect,
            can_approve_redirect=can_approve,
            diagnostics=diag_obj,
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
