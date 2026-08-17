"""Tenant-scoped ScanJob repository."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.enums import ScanJobStatus
from email_discovery_api.models.scan_job import ScanJob

ACTIVE_JOB_STATUSES = (
    ScanJobStatus.DRAFT.value,
    ScanJobStatus.QUEUED.value,
    ScanJobStatus.RUNNING.value,
    ScanJobStatus.CANCELLING.value,
)


class ScanJobRepository:
    """Tenant-scoped database repository for ScanJob operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_job(self, organization_id: UUID, job_id: UUID) -> ScanJob | None:
        """Fetch a scan job strictly scoped to the tenant organization."""
        stmt = select(ScanJob).where(
            ScanJob.organization_id == organization_id,
            ScanJob.id == job_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_jobs(
        self,
        organization_id: UUID,
        *,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: UUID | None = None,
        status: str | None = None,
    ) -> list[ScanJob]:
        """List scan jobs for tenant with deterministic ordering (created_at DESC, id DESC)."""
        stmt = select(ScanJob).where(ScanJob.organization_id == organization_id)

        if status:
            stmt = stmt.where(ScanJob.status == status)

        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                (ScanJob.created_at < cursor_created_at)
                | ((ScanJob.created_at == cursor_created_at) & (ScanJob.id < cursor_id))
            )

        stmt = stmt.order_by(ScanJob.created_at.desc(), ScanJob.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_active_jobs(self, organization_id: UUID) -> int:
        """Count currently active scan jobs for tenant quota enforcement."""
        stmt = select(func.count(ScanJob.id)).where(
            ScanJob.organization_id == organization_id,
            ScanJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def find_by_idempotency_key(
        self, organization_id: UUID, idempotency_key: str
    ) -> ScanJob | None:
        """Look up existing scan job by tenant-scoped idempotency key."""
        stmt = select(ScanJob).where(
            ScanJob.organization_id == organization_id,
            ScanJob.idempotency_key == idempotency_key,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def add_job(self, job: ScanJob) -> None:
        """Stage a new ScanJob row into session without committing."""
        self.session.add(job)

    async def allocate_event_sequence(self, organization_id: UUID, job_id: UUID) -> int | None:
        """Atomically increment next_event_sequence and return allocated sequence value.

        SQL:
            UPDATE scan_jobs
            SET next_event_sequence = next_event_sequence + 1
            WHERE organization_id = :org_id AND id = :job_id
            RETURNING next_event_sequence - 1
        """
        stmt = (
            update(ScanJob)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
            .values(next_event_sequence=ScanJob.next_event_sequence + 1)
            .returning(ScanJob.next_event_sequence - 1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_job_status_conditional(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        expected_status: str,
        new_status: str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        cancellation_requested_at: datetime | None = None,
    ) -> bool:
        """Conditionally update job status if tenant ID and current status match."""
        values: dict[str, object] = {"status": new_status}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if cancellation_requested_at is not None:
            values["cancellation_requested_at"] = cancellation_requested_at

        stmt = (
            update(ScanJob)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
                ScanJob.status == expected_status,
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0)) == 1

    async def get_job_for_update(self, organization_id: UUID, job_id: UUID) -> ScanJob | None:
        """Lock and fetch scan job for tenant using SELECT ... FOR UPDATE."""
        stmt = (
            select(ScanJob)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
            .with_for_update()
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def increment_completed_urls(
        self, organization_id: UUID, job_id: UUID, delta: int = 1
    ) -> None:
        """Atomically increment completed_count for job."""
        stmt = (
            update(ScanJob)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
            .values(completed_count=ScanJob.completed_count + delta)
        )
        await self.session.execute(stmt)

    async def increment_failed_urls(
        self, organization_id: UUID, job_id: UUID, delta: int = 1
    ) -> None:
        """Atomically increment failed_count for job."""
        stmt = (
            update(ScanJob)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
            .values(failed_count=ScanJob.failed_count + delta)
        )
        await self.session.execute(stmt)

    async def increment_email_findings(
        self, organization_id: UUID, job_id: UUID, delta: int
    ) -> None:
        """Atomically increment email_finding_count by delta for job."""
        if delta <= 0:
            return
        stmt = (
            update(ScanJob)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
            .values(email_finding_count=ScanJob.email_finding_count + delta)
        )
        await self.session.execute(stmt)
