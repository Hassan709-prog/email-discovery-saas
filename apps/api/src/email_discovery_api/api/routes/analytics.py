"""HTTP API routes for tenant-scoped analytics overview under /api/v1/analytics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_analytics_service
from email_discovery_api.schemas.analytics import AnalyticsOverviewResponse, AnalyticsPeriodEnum
from email_discovery_api.services.analytics import AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get(
    "/overview",
    response_model=AnalyticsOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get tenant-scoped analytics overview",
)
async def get_analytics_overview(
    period: AnalyticsPeriodEnum = Query(
        AnalyticsPeriodEnum.THIRTY_DAYS,
        description="Time period filter: 7d, 30d, 90d, or all",
    ),
    principal: RequestPrincipal = Depends(get_current_principal),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalyticsOverviewResponse:
    """Fetch tenant aggregate metrics, status distributions, timeline, and recent scans.

    Security & Authorization:
        Tenant identity is derived strictly from RequestPrincipal.organization_id.
        Client cannot pass or override organization parameters.
    """
    return await service.get_overview(
        organization_id=principal.organization_id,
        period=period,
    )
