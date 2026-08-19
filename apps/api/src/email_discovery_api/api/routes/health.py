"""Liveness and readiness health check endpoints."""

from typing import Any

from fastapi import APIRouter, Request, Response, status

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live", status_code=status.HTTP_200_OK)
async def health_live(request: Request) -> dict[str, str]:
    """Liveness probe returning HTTP 200 process status without DB or Redis access."""
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.app_name,
    }


@router.get("/ready")
async def health_ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness probe checking database and optional Redis coordination connectivity."""
    settings = request.app.state.settings
    db_manager = request.app.state.db_manager
    redis_manager = getattr(request.app.state, "redis_manager", None)

    db_ok = await db_manager.check_health(settings.db_health_timeout_seconds)

    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unavailable",
            "service": settings.app_name,
            "dependencies": {
                "database": "unavailable",
                "redis": "unknown",
            },
        }

    redis_status = "ok"
    redis_degraded = False

    if redis_manager is not None:
        redis_ok = await redis_manager.check_health()
        if not redis_ok:
            if settings.redis_required:
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                return {
                    "status": "unavailable",
                    "service": settings.app_name,
                    "dependencies": {
                        "database": "ok",
                        "redis": "unavailable",
                    },
                }
            redis_status = "degraded"
            redis_degraded = True

    response.status_code = status.HTTP_200_OK
    return {
        "status": "ok",
        "service": settings.app_name,
        "dependencies": {
            "database": "ok",
            "redis": redis_status,
        },
        "redis_degraded": redis_degraded,
    }
