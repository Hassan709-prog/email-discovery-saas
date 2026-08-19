"""Service dependency injection factories for FastAPI routes."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_discovery_api.database import get_db_session
from email_discovery_api.services.analytics import AnalyticsService
from email_discovery_api.services.auth import AuthService
from email_discovery_api.services.results import ScanJobResultsService
from email_discovery_api.services.scan_jobs import ScanJobService


async def get_scan_job_service(
    session: AsyncSession = Depends(get_db_session),
) -> ScanJobService:
    """Dependency injecting a request-scoped ScanJobService."""
    return ScanJobService(session)


async def get_scan_job_results_service(
    session: AsyncSession = Depends(get_db_session),
) -> ScanJobResultsService:
    """Dependency injecting a request-scoped ScanJobResultsService."""
    return ScanJobResultsService(session)


async def get_auth_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuthService:
    """Dependency injecting a request-scoped AuthService."""
    return AuthService(session)


async def get_analytics_service(
    session: AsyncSession = Depends(get_db_session),
) -> AnalyticsService:
    """Dependency injecting a request-scoped AnalyticsService."""
    return AnalyticsService(session)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Dependency injecting the application-owned shared async_sessionmaker."""
    return request.app.state.db_manager.session_factory


def get_redis_publisher(request: Request) -> Any:
    """Dependency injecting the application-owned APIRedisClient publisher."""
    return request.app.state.redis_manager
