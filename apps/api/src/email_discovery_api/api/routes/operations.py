"""Private system operations endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from email_discovery_api.api.dependencies.identity import RequestPrincipal
from email_discovery_api.api.dependencies.operations import (
    require_operations_admin,
    require_operations_enabled,
)
from email_discovery_api.api.dependencies.services import get_operational_service
from email_discovery_api.schemas.operations import (
    OperationalDiagnosticsResponse,
    OperationalMetricsResponse,
    RecoveryRequest,
    RecoveryResponse,
)
from email_discovery_api.services.operations import OperationalService

router = APIRouter(
    prefix="/api/v1/operations",
    tags=["Operations"],
    dependencies=[Depends(require_operations_enabled)],
)


@router.get("/metrics", response_model=OperationalMetricsResponse)
async def operational_metrics(
    window_seconds: int = Query(default=300, ge=60, le=3600),
    _: RequestPrincipal = Depends(require_operations_admin),
    service: OperationalService = Depends(get_operational_service),
) -> OperationalMetricsResponse:
    """Return bounded privacy-safe system aggregates."""
    return await service.metrics(window_seconds)


@router.get("/diagnostics", response_model=OperationalDiagnosticsResponse)
async def operational_diagnostics(
    limit: int = Query(default=100, ge=1, le=100),
    _: RequestPrincipal = Depends(require_operations_admin),
    service: OperationalService = Depends(get_operational_service),
) -> OperationalDiagnosticsResponse:
    """Return a read-only bounded inconsistency report."""
    return await service.diagnostics(limit)


@router.post("/recovery/jobs/{job_id}", response_model=RecoveryResponse)
async def recover_job(
    job_id: UUID,
    body: RecoveryRequest,
    principal: RequestPrincipal = Depends(require_operations_admin),
    service: OperationalService = Depends(get_operational_service),
) -> RecoveryResponse:
    """Explicitly invoke existing idempotent tenant-safe job recovery."""
    del body
    return await service.recover_job(
        organization_id=principal.organization_id,
        job_id=job_id,
        actor_user_id=principal.user_id,
        request_id=principal.request_id,
    )
