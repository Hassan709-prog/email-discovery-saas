"""HTTP routes for scan job lifecycle under /api/v1/scan-jobs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from email_discovery_api.api.dependencies.cursors import (
    parse_event_cursor,
    parse_job_cursor,
    parse_url_cursor,
)
from email_discovery_api.api.dependencies.idempotency import validate_idempotency_key
from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_scan_job_service
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.schemas.api_scan_jobs import (
    CreateScanJobApiRequest,
    JobEventApiResponse,
    PaginatedResponse,
    PreviewScanInputsApiRequest,
    PreviewScanInputsApiResponse,
    ScanInputPreviewItemApiResponse,
    ScanJobApiResponse,
    ScanJobProgressApiResponse,
    ScanURLApiResponse,
)
from email_discovery_api.schemas.scan_jobs import CreateScanJobCommand
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.policies import ScanCreationPolicy
from email_discovery_api.services.scan_jobs import ScanJobService, preview_scan_inputs

router = APIRouter(prefix="/api/v1/scan-jobs", tags=["scan-jobs"])


@router.post(
    "/preview",
    response_model=PreviewScanInputsApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview and normalize URL inputs without persisting",
)
async def preview_scan_jobs(
    request: PreviewScanInputsApiRequest,
    principal: RequestPrincipal = Depends(get_current_principal),
    policy: ScanCreationPolicy = Depends(ScanCreationPolicy),
) -> PreviewScanInputsApiResponse:
    """Preview URL inputs for normalization, domain extraction, and syntax validation."""
    policy.validate_pre_ingestion(request.inputs, request.configuration_snapshot)

    previews = preview_scan_inputs(request.inputs)

    total_input_count = len(previews)
    valid_input_count = sum(1 for p in previews if p.classification == "VALID")
    duplicate_input_count = sum(1 for p in previews if p.classification == "DUPLICATE")
    invalid_input_count = sum(1 for p in previews if p.classification == "INVALID")

    items = [
        ScanInputPreviewItemApiResponse(
            original_index=p.original_index,
            original_input=p.original_input,
            normalized_url=p.normalized_url,
            normalized_domain=p.normalized_domain,
            classification=p.classification,
            duplicate_of_index=p.duplicate_of_index,
            error_code=p.error_code,
            error_message=p.error_message,
        )
        for p in previews
    ]

    return PreviewScanInputsApiResponse(
        previews=items,
        total_input_count=total_input_count,
        valid_input_count=valid_input_count,
        duplicate_input_count=duplicate_input_count,
        invalid_input_count=invalid_input_count,
    )


@router.post(
    "",
    response_model=ScanJobApiResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new scan job or replay an idempotent creation",
)
async def create_scan_job(
    request: CreateScanJobApiRequest,
    response: Response,
    principal: RequestPrincipal = Depends(get_current_principal),
    idempotency_key: str | None = Depends(validate_idempotency_key),
    service: ScanJobService = Depends(get_scan_job_service),
) -> ScanJobApiResponse:
    """Create scan job and ingest URL inputs into draft state."""
    command = CreateScanJobCommand(
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        inputs=request.inputs,
        name=request.name,
        source_type=request.source_type,
        configuration_snapshot=request.configuration_snapshot,
        scanner_version=request.scanner_version,
        normalization_version=request.normalization_version,
        ranking_version=request.ranking_version,
        idempotency_key=idempotency_key,
    )

    result = await service.create_job(command)

    response.headers["Location"] = f"/api/v1/scan-jobs/{result.job.id}"
    if not result.created:
        response.status_code = status.HTTP_200_OK

    return ScanJobApiResponse.from_orm_model(result.job)


@router.get(
    "",
    response_model=PaginatedResponse[ScanJobApiResponse],
    summary="List tenant scan jobs with keyset pagination",
)
async def list_scan_jobs(
    limit: int = Query(50, ge=1, le=100, description="Page size limit"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    status_filter: ScanJobStatus | None = Query(None, alias="status", description="Status filter"),
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
) -> PaginatedResponse[ScanJobApiResponse]:
    """List tenant-scoped scan jobs with keyset cursor pagination."""
    created_at, job_id = parse_job_cursor(cursor)
    status_str = status_filter.value if status_filter else None

    jobs, next_cursor = await service.list_jobs(
        principal.organization_id,
        limit=limit,
        cursor_created_at=created_at,
        cursor_id=job_id,
        status=status_str,
    )

    items = [ScanJobApiResponse.from_orm_model(j) for j in jobs]
    return PaginatedResponse[ScanJobApiResponse](items=items, next_cursor=next_cursor)


@router.get(
    "/{job_id}",
    response_model=ScanJobApiResponse,
    summary="Get tenant scan job detail",
)
async def get_scan_job(
    job_id: UUID,
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
) -> ScanJobApiResponse:
    """Fetch scan job detail for an authorized tenant."""
    job = await service.get_job(principal.organization_id, job_id)
    return ScanJobApiResponse.from_orm_model(job)


@router.get(
    "/{job_id}/progress",
    response_model=ScanJobProgressApiResponse,
    summary="Get scan job execution progress",
)
async def get_scan_job_progress(
    job_id: UUID,
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
) -> ScanJobProgressApiResponse:
    """Fetch execution progress derived strictly from persisted database counters."""
    prog = await service.get_job_progress(principal.organization_id, job_id)
    return ScanJobProgressApiResponse(
        job_id=prog.job_id,
        status=prog.status,
        progress_percentage=prog.progress_percentage,
        total_input_count=prog.total_input_count,
        valid_input_count=prog.valid_input_count,
        duplicate_input_count=prog.duplicate_input_count,
        invalid_input_count=prog.invalid_input_count,
        queued_count=prog.queued_count,
        running_count=prog.running_count,
        completed_count=prog.completed_count,
        failed_count=prog.failed_count,
        email_finding_count=prog.email_finding_count,
        created_at=prog.created_at,
        started_at=prog.started_at,
        completed_at=prog.completed_at,
    )


@router.get(
    "/{job_id}/urls",
    response_model=PaginatedResponse[ScanURLApiResponse],
    summary="List job URL input rows",
)
async def list_scan_job_urls(
    job_id: UUID,
    limit: int = Query(100, ge=1, le=100, description="Page size limit"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    status_filter: ScanURLStatus | None = Query(None, alias="status", description="Status filter"),
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
) -> PaginatedResponse[ScanURLApiResponse]:
    """List URL inputs associated with job using keyset cursor pagination."""
    idx, url_id = parse_url_cursor(cursor)
    status_str = status_filter.value if status_filter else None

    urls, next_cursor = await service.list_job_urls(
        principal.organization_id,
        job_id,
        limit=limit,
        cursor_index=idx,
        cursor_id=url_id,
        status=status_str,
    )

    items = [ScanURLApiResponse.from_orm_model(u) for u in urls]
    return PaginatedResponse[ScanURLApiResponse](items=items, next_cursor=next_cursor)


@router.get(
    "/{job_id}/events",
    response_model=PaginatedResponse[JobEventApiResponse],
    summary="List job event audit history",
)
async def list_scan_job_events(
    job_id: UUID,
    limit: int = Query(100, ge=1, le=100, description="Page size limit"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
) -> PaginatedResponse[JobEventApiResponse]:
    """List event audit log entries associated with job using sequence cursor pagination."""
    seq, event_id = parse_event_cursor(cursor)

    events, next_cursor = await service.list_job_events(
        principal.organization_id,
        job_id,
        limit=limit,
        cursor_seq=seq,
        cursor_id=event_id,
    )

    items = [JobEventApiResponse.from_orm_model(e) for e in events]
    return PaginatedResponse[JobEventApiResponse](items=items, next_cursor=next_cursor)


@router.post(
    "/{job_id}/queue",
    response_model=ScanJobApiResponse,
    summary="Transition job from DRAFT to QUEUED",
)
async def queue_scan_job(
    job_id: UUID,
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
) -> ScanJobApiResponse:
    """Transition draft job to queued state for execution."""
    job = await service.transition_job_status(
        principal.organization_id, job_id, ScanJobStatus.QUEUED
    )
    return ScanJobApiResponse.from_orm_model(job)


@router.post(
    "/{job_id}/cancel",
    response_model=ScanJobApiResponse,
    summary="Request job cancellation",
)
async def cancel_scan_job(
    job_id: UUID,
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
) -> ScanJobApiResponse:
    """Request job cancellation from QUEUED or RUNNING states."""
    job = await service.get_job(principal.organization_id, job_id)
    current_status = ScanJobStatus(job.status)

    if current_status == ScanJobStatus.DRAFT:
        raise ServiceError(
            ServiceErrorCode.INVALID_STATE_TRANSITION,
            f"Scan job {job_id} is in DRAFT state and cannot be cancelled.",
        )

    if current_status == ScanJobStatus.QUEUED:
        target_status = ScanJobStatus.CANCELLED
    elif current_status == ScanJobStatus.RUNNING:
        target_status = ScanJobStatus.CANCELLING
    elif current_status in (ScanJobStatus.CANCELLING, ScanJobStatus.CANCELLED):
        target_status = current_status
    else:
        raise ServiceError(
            ServiceErrorCode.INVALID_STATE_TRANSITION,
            f"Job {job_id} is in terminal state {current_status.value}.",
        )

    updated_job = await service.transition_job_status(
        principal.organization_id, job_id, target_status
    )
    return ScanJobApiResponse.from_orm_model(updated_job)
