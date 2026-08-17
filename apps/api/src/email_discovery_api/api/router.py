"""Main API router combining route modules."""

from fastapi import APIRouter

from email_discovery_api.api.routes import auth, health, results, scan_jobs

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(scan_jobs.router)
api_router.include_router(results.router)
api_router.include_router(auth.router)
