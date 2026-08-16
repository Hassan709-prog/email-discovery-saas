"""Main API router combining route modules."""

from fastapi import APIRouter

from email_discovery_api.api.routes import health

api_router = APIRouter()
api_router.include_router(health.router)
