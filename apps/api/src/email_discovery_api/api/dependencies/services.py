"""Service dependency injection factories for FastAPI routes."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.database import get_db_session
from email_discovery_api.services.scan_jobs import ScanJobService


async def get_scan_job_service(
    session: AsyncSession = Depends(get_db_session),
) -> ScanJobService:
    """Dependency injecting a request-scoped ScanJobService."""
    return ScanJobService(session)
