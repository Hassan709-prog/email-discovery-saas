"""HTTP routes for tenant-scoped findings, detail, evidence, and CSV export."""

from __future__ import annotations

import csv
import io
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import (
    get_scan_job_results_service,
    get_session_factory,
)
from email_discovery_api.mappers.crawl_results import sanitize_url
from email_discovery_api.models.enums import EmailValidationStatus
from email_discovery_api.schemas.api_results import (
    FindingEvidenceItemApiResponse,
    RepresentativeEvidenceApiResponse,
    ScanJobResultDetailApiResponse,
    ScanJobResultItemApiResponse,
)
from email_discovery_api.schemas.api_scan_jobs import PaginatedResponse
from email_discovery_api.services.results import ScanJobResultsService, sanitize_csv_cell
from email_scanner.models import EmailCategory

router = APIRouter(prefix="/api/v1/scan-jobs", tags=["scan-job-results"])

DOMAIN_REGEX = re.compile(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PREFIX_REGEX = re.compile(r"^[a-z0-9.+-]{2,50}$")
CONTROL_CHAR_REGEX = re.compile(r"[\r\n\x00-\x1f]")


@router.get(
    "/{job_id}/results",
    response_model=PaginatedResponse[ScanJobResultItemApiResponse],
    summary="List tenant-scoped email findings for a scan job",
)
async def list_scan_job_results(
    job_id: UUID,
    limit: int = Query(50, ge=1, le=100, description="Page size limit"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    classification: EmailCategory | None = Query(None, description="Classification filter"),
    validation_status: EmailValidationStatus | None = Query(
        None, description="Validation status filter"
    ),
    email_domain: str | None = Query(None, description="Email domain filter"),
    search_prefix: str | None = Query(None, description="Canonical email search prefix"),
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobResultsService = Depends(get_scan_job_results_service),
) -> PaginatedResponse[ScanJobResultItemApiResponse]:
    """List tenant-scoped canonical email findings for a scan job with keyset pagination."""
    domain_clean: str | None = None
    if email_domain is not None:
        domain_clean = email_domain.strip().lower()
        if not domain_clean or not DOMAIN_REGEX.match(domain_clean):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email_domain parameter format.",
            )

    prefix_clean: str | None = None
    if search_prefix is not None:
        if CONTROL_CHAR_REGEX.search(search_prefix):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid search_prefix parameter format.",
            )
        trimmed = search_prefix.strip().lower()
        if len(trimmed) < 2 or len(trimmed) > 50 or not PREFIX_REGEX.match(trimmed):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid search_prefix parameter format.",
            )
        prefix_clean = trimmed

    classification_str = classification.value if classification else None
    validation_status_str = validation_status.value if validation_status else None

    findings, rep_evidence_map, next_cursor = await service.list_results(
        organization_id=principal.organization_id,
        job_id=job_id,
        limit=limit,
        cursor=cursor,
        classification=classification_str,
        validation_status=validation_status_str,
        email_domain=domain_clean,
        search_prefix=prefix_clean,
    )

    items: list[ScanJobResultItemApiResponse] = []
    for f in findings:
        evidence_list = rep_evidence_map.get(f.id, [])
        rep_items = [
            RepresentativeEvidenceApiResponse(
                evidence_id=ev.id,
                source_type=ev.source_type,
                sanitized_page_url=sanitize_url(ev.page_url),
                snippet=ev.snippet,
                created_at=ev.created_at,
            )
            for ev in evidence_list
        ]
        items.append(
            ScanJobResultItemApiResponse(
                finding_id=f.id,
                canonical_email=f.canonical_email,
                email_domain=f.email_domain,
                classification=f.classification,
                is_role_based=f.is_role_based,
                validation_status=f.validation_status,
                evidence_count=f.evidence_count,
                first_found_at=f.first_found_at,
                last_found_at=f.last_found_at,
                representative_evidence=rep_items,
            )
        )

    return PaginatedResponse[ScanJobResultItemApiResponse](items=items, next_cursor=next_cursor)


@router.get(
    "/{job_id}/results/{finding_id}",
    response_model=ScanJobResultDetailApiResponse,
    summary="Get tenant-scoped finding detail",
)
async def get_scan_job_result_detail(
    job_id: UUID,
    finding_id: UUID,
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobResultsService = Depends(get_scan_job_results_service),
) -> ScanJobResultDetailApiResponse:
    """Fetch finding detail and top representative evidence for an authorized tenant."""
    finding, evidence_list = await service.get_result_detail(
        organization_id=principal.organization_id,
        job_id=job_id,
        finding_id=finding_id,
    )

    rep_items = [
        RepresentativeEvidenceApiResponse(
            evidence_id=ev.id,
            source_type=ev.source_type,
            sanitized_page_url=sanitize_url(ev.page_url),
            snippet=ev.snippet,
            created_at=ev.created_at,
        )
        for ev in evidence_list
    ]

    return ScanJobResultDetailApiResponse(
        finding_id=finding.id,
        job_id=finding.scan_job_id,
        canonical_email=finding.canonical_email,
        email_domain=finding.email_domain,
        classification=finding.classification,
        is_role_based=finding.is_role_based,
        validation_status=finding.validation_status,
        evidence_count=finding.evidence_count,
        first_found_at=finding.first_found_at,
        last_found_at=finding.last_found_at,
        created_at=finding.created_at,
        updated_at=finding.updated_at,
        representative_evidence=rep_items,
    )


@router.get(
    "/{job_id}/results/{finding_id}/evidence",
    response_model=PaginatedResponse[FindingEvidenceItemApiResponse],
    summary="List paginated evidence items for a finding",
)
async def list_finding_evidence(
    job_id: UUID,
    finding_id: UUID,
    limit: int = Query(50, ge=1, le=100, description="Page size limit"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobResultsService = Depends(get_scan_job_results_service),
) -> PaginatedResponse[FindingEvidenceItemApiResponse]:
    """List paginated evidence items associated with a specific finding."""
    evidence_items, next_cursor = await service.list_result_evidence(
        organization_id=principal.organization_id,
        job_id=job_id,
        finding_id=finding_id,
        limit=limit,
        cursor=cursor,
    )

    items = [
        FindingEvidenceItemApiResponse(
            evidence_id=ev.id,
            source_type=ev.source_type,
            sanitized_page_url=sanitize_url(ev.page_url),
            snippet=ev.snippet,
            confidence=ev.confidence,
            crawled_page_status_code=ev.crawled_page.status_code if ev.crawled_page else None,
            crawled_page_depth=ev.crawled_page.depth if ev.crawled_page else None,
            created_at=ev.created_at,
        )
        for ev in evidence_items
    ]

    return PaginatedResponse[FindingEvidenceItemApiResponse](items=items, next_cursor=next_cursor)


@router.get(
    "/{job_id}/export.csv",
    summary="Stream tenant-scoped findings as CSV file",
)
async def export_scan_job_results_csv(
    job_id: UUID,
    principal: RequestPrincipal = Depends(get_current_principal),
    service: ScanJobResultsService = Depends(get_scan_job_results_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
    """Stream tenant-scoped scan job findings as a CSV download for terminal jobs."""
    # 1. Validate job state and count BEFORE creating StreamingResponse
    await service.validate_export_eligibility(principal.organization_id, job_id)

    org_id = principal.organization_id

    async def csv_generator():
        header_buffer = io.StringIO()
        writer = csv.writer(header_buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow(
            [
                "canonical_email",
                "email_domain",
                "classification",
                "is_role_based",
                "validation_status",
                "evidence_count",
                "first_found_at",
                "last_found_at",
            ]
        )
        yield header_buffer.getvalue()

        async for batch in service.stream_export_batches(
            org_id, job_id, batch_size=1000, session_factory=session_factory
        ):
            buffer = io.StringIO()
            writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
            for f in batch:
                writer.writerow(
                    [
                        sanitize_csv_cell(f.canonical_email),
                        sanitize_csv_cell(f.email_domain),
                        sanitize_csv_cell(f.classification),
                        sanitize_csv_cell(f.is_role_based),
                        sanitize_csv_cell(f.validation_status),
                        sanitize_csv_cell(f.evidence_count),
                        sanitize_csv_cell(f.first_found_at.isoformat() if f.first_found_at else ""),
                        sanitize_csv_cell(f.last_found_at.isoformat() if f.last_found_at else ""),
                    ]
                )
            yield buffer.getvalue()

    filename = f"scan-job-{job_id}-results.csv"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    return StreamingResponse(
        csv_generator(),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )
