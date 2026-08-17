"""Tenant-scoped JobEvent repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.job_event import JobEvent
from email_discovery_api.models.scan_job import ScanJob


class JobEventRepository:
    """Tenant-scoped database repository for JobEvent operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def append_event(self, event: JobEvent) -> None:
        """Stage an append-only JobEvent row into session without committing."""
        self.session.add(event)

    async def list_job_events(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        limit: int = 100,
        cursor_seq: int | None = None,
        cursor_id: UUID | None = None,
    ) -> list[JobEvent]:
        """List job events with tenant JOIN and deterministic ordering."""
        stmt = (
            select(JobEvent)
            .join(ScanJob, JobEvent.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                JobEvent.scan_job_id == job_id,
            )
        )

        if cursor_seq is not None and cursor_id is not None:
            stmt = stmt.where(
                (JobEvent.sequence_number > cursor_seq)
                | ((JobEvent.sequence_number == cursor_seq) & (JobEvent.id > cursor_id))
            )

        stmt = stmt.order_by(JobEvent.sequence_number.asc(), JobEvent.id.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
