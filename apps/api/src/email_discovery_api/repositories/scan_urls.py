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
        from sqlalchemy.orm import selectinload

        stmt = (
            select(ScanURL)
            .options(selectinload(ScanURL.email_finding))
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

    async def get_url_for_update(
        self, organization_id: UUID, job_id: UUID, scan_url_id: UUID
    ) -> ScanURL | None:
        """Lock and retrieve tenant-scoped ScanURL row using SELECT ... FOR UPDATE."""
        stmt = (
            select(ScanURL)
            .join(ScanJob, ScanURL.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanURL.scan_job_id == job_id,
                ScanURL.id == scan_url_id,
            )
            .with_for_update()
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def update_status_conditional(
        self,
        organization_id: UUID,
        job_id: UUID,
        scan_url_id: UUID,
        expected_status: str,
        new_status: str,
    ) -> bool:
        """Conditionally update status of ScanURL if current status matches expected_status."""
        from sqlalchemy import update

        stmt = (
            update(ScanURL)
            .where(
                ScanURL.id == scan_url_id,
                ScanURL.scan_job_id == job_id,
                ScanURL.status == expected_status,
            )
            .values(status=new_status)
        )
        res = await self.session.execute(stmt)
        return int(getattr(res, "rowcount", 0)) > 0
