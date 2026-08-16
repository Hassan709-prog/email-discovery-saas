"""Liveness and readiness health check endpoints."""

from typing import Any

from fastapi import APIRouter, Request, Response, status

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live", status_code=status.HTTP_200_OK)
async def health_live(request: Request) -> dict[str, str]:
    """Liveness probe returning HTTP 200 process status without DB access."""
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.app_name,
    }


@router.get("/ready")
async def health_ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness probe performing a bounded SELECT 1 query to confirm database connectivity."""
    settings = request.app.state.settings
    db_manager = request.app.state.db_manager

    is_healthy = await db_manager.check_health(settings.db_health_timeout_seconds)

    if is_healthy:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "ok",
            "service": settings.app_name,
            "dependencies": {
                "database": "ok",
            },
        }

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "unavailable",
        "service": settings.app_name,
        "dependencies": {
            "database": "unavailable",
        },
    }
