"""HTTP routes for scan job lifecycle under /api/v1/scan-jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from email_discovery_api.api.dependencies.cursors import (
    parse_event_cursor,
    parse_job_cursor,
    parse_url_cursor,
)
from email_discovery_api.api.dependencies.idempotency import validate_idempotency_key
from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import (
    get_redis_publisher,
    get_scan_job_service,
)
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
from email_discovery_api.services.policies import ScanCreationPolicy
from email_discovery_api.services.scan_jobs import ScanJobService, preview_scan_inputs

logger = logging.getLogger(__name__)

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

    batch_result = preview_scan_inputs(request.inputs, overrides=request.overrides)

    items = [
        ScanInputPreviewItemApiResponse(
            original_index=p.original_index,
            original_input=p.original_input,
            normalized_url=p.normalized_url,
            normalized_domain=urlsplit(p.normalized_url).hostname if p.normalized_url else None,
            canonical_target=p.canonical_target,
            classification=(
                "VALID"
                if (p.is_selected and p.canonical_target)
                else ("DUPLICATE" if str(p.decision_code).startswith("DUPLICATE") else "INVALID")
            ),
            decision_code=p.decision_code.value,
            explanation=p.explanation,
            duplicate_of_index=p.duplicate_of_index,
            is_selected=p.is_selected,
            user_override_permitted=p.user_override_permitted,
            ui_label=p.ui_label,
            error_code=p.decision_code.value if not p.is_selected else None,
            error_message=p.explanation if not p.is_selected else None,
        )
        for p in batch_result.items
    ]

    return PreviewScanInputsApiResponse(
        previews=items,
        total_input_count=batch_result.total_input_count,
        ready_to_check_count=batch_result.ready_to_check_count,
        needs_review_count=batch_result.needs_review_count,
        unrelated_platform_count=batch_result.unrelated_platform_count,
        duplicate_input_count=batch_result.duplicate_input_count,
        invalid_input_count=batch_result.invalid_input_count,
        final_target_count=batch_result.final_target_count,
        valid_input_count=batch_result.final_target_count,
        accepted_canonical_targets=batch_result.accepted_canonical_targets,
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
        overrides=request.overrides,
        name=request.name,
        source_type=request.source_type,
        configuration_snapshot=request.configuration_snapshot,
        scanner_version=request.scanner_version,
        normalization_version=request.normalization_version,
        cleaning_policy_version=request.cleaning_policy_version,
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
    publisher: Any = Depends(get_redis_publisher),
) -> ScanJobApiResponse:
    """Transition draft job to queued state for execution."""
    result = await service.queue_job(principal.organization_id, job_id)

    if result.transitioned_to_queued and publisher is not None:
        try:
            await asyncio.wait_for(
                publisher.publish_work_available(),
                timeout=0.25,
            )
        except Exception as exc:
            logger.warning(
                "Redis wake-up publish failed [code=REDIS_PUBLISH_FAILED, error_type=%s]",
                type(exc).__name__,
            )

    return ScanJobApiResponse.from_orm_model(result.job)


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
    job = await service.cancel_job(principal.organization_id, job_id)
    return ScanJobApiResponse.from_orm_model(job)


@router.post(
    "/{job_id}/urls/{url_id}/approve-redirect",
    response_model=ScanURLApiResponse,
    summary="Approve cross-domain redirect and retry ScanURL",
)
async def approve_url_redirect(
    job_id: UUID,
    url_id: UUID,
    approved_target_domain: str | None = Query(default=None),
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobService = Depends(get_scan_job_service),
    publisher: Any = Depends(get_redis_publisher),
) -> ScanURLApiResponse:
    """Approve a pending cross-domain redirect for a specific target URL and re-queue it."""
    url = await service.approve_url_redirect(
        organization_id=principal.organization_id,
        job_id=job_id,
        url_id=url_id,
        approved_target_domain=approved_target_domain,
    )

    if publisher is not None:
        try:
            await asyncio.wait_for(
                publisher.publish_work_available(),
                timeout=0.25,
            )
        except Exception as exc:
            logger.warning(
                "Redis wake-up publish failed [code=REDIS_PUBLISH_FAILED, error_type=%s]",
                type(exc).__name__,
            )

    return ScanURLApiResponse.from_orm_model(url)
