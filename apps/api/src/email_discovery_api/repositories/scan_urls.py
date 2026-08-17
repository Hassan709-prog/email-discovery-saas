"""Tenant-scoped ScanURL repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL


class ScanURLRepository:
    """Tenant-scoped database repository for ScanURL operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add_scan_urls(self, urls: list[ScanURL]) -> None:
        """Stage new ScanURL rows into session without committing."""
        self.session.add_all(urls)

    async def list_job_urls(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        limit: int = 100,
        cursor_index: int | None = None,
        cursor_id: UUID | None = None,
        status: str | None = None,
    ) -> list[ScanURL]:
        """List scan URLs with tenant JOIN and deterministic ordering."""
        stmt = (
            select(ScanURL)
            .join(ScanJob, ScanURL.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanURL.scan_job_id == job_id,
            )
        )

        if status:
            stmt = stmt.where(ScanURL.status == status)

        if cursor_index is not None and cursor_id is not None:
            stmt = stmt.where(
                (ScanURL.original_index > cursor_index)
                | ((ScanURL.original_index == cursor_index) & (ScanURL.id > cursor_id))
            )

        stmt = stmt.order_by(ScanURL.original_index.asc(), ScanURL.id.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
