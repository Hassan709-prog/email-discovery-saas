"""Analytics service executing tenant-scoped PostgreSQL aggregations for dashboard overview."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.email_finding import EmailFinding
from email_discovery_api.models.enums import (
    EmailClassification,
    EmailValidationStatus,
    ScanJobStatus,
)
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.schemas.analytics import (
    AnalyticsOverviewResponse,
    AnalyticsPeriodEnum,
    AnalyticsTimelinePoint,
    RecentScanJobSummary,
)

ALL_STATUS_KEYS = [status.value for status in ScanJobStatus]
ALL_CLASSIFICATION_KEYS = [c.value for c in EmailClassification]
ALL_VALIDATION_KEYS = [v.value for v in EmailValidationStatus]


class AnalyticsService:
    """Application service for tenant-isolated aggregate metric calculation.

    Design & Architecture Notes:
        - Tenant Isolation: Every SQL query explicitly filters by organization_id = :org_id.
        - AsyncSession Safety: Queries are executed sequentially on the provided AsyncSession.
        - Formulas:
            - total_scans: Count of scan jobs created inside the UTC period boundary.
            - active_scans: Count of jobs currently in QUEUED, RUNNING, or CANCELLING.
            - websites_submitted: Sum of valid_input_count (accepted deduplicated inputs).
            - websites_completed: Sum of completed_count (includes NO_EMAIL outcomes).
            - websites_failed: Sum of failed_count.
            - websites_processed: websites_completed + websites_failed.
            - emails_discovered: Sum of email_finding_count.
            - successful_processing_rate: completed_count / (completed_count + failed_count) * 100,
              returning 0.0 when denominator is zero.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_overview(
        self,
        organization_id: UUID,
        period: AnalyticsPeriodEnum = AnalyticsPeriodEnum.THIRTY_DAYS,
        now: datetime | None = None,
    ) -> AnalyticsOverviewResponse:
        """Fetch complete tenant analytics overview with exact UTC period boundaries."""
        end_at = now if now is not None else datetime.now(UTC)
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=UTC)

        start_at: datetime | None = None
        if period == AnalyticsPeriodEnum.SEVEN_DAYS:
            start_at = (end_at - timedelta(days=6)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif period == AnalyticsPeriodEnum.THIRTY_DAYS:
            start_at = (end_at - timedelta(days=29)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif period == AnalyticsPeriodEnum.NINETY_DAYS:
            start_at = (end_at - timedelta(days=89)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif period == AnalyticsPeriodEnum.ALL_TIME:
            start_at = None

        # 1. Job Level Aggregates (total_scans, websites_submitted, completed,
        #    failed, emails_discovered)
        job_aggregates_stmt = select(
            func.count(ScanJob.id).label("total_scans"),
            func.coalesce(func.sum(ScanJob.valid_input_count), 0).label("websites_submitted"),
            func.coalesce(func.sum(ScanJob.completed_count), 0).label("websites_completed"),
            func.coalesce(func.sum(ScanJob.failed_count), 0).label("websites_failed"),
            func.coalesce(func.sum(ScanJob.email_finding_count), 0).label("emails_discovered"),
        ).where(ScanJob.organization_id == organization_id)

        if start_at is not None:
            job_aggregates_stmt = job_aggregates_stmt.where(
                ScanJob.created_at >= start_at, ScanJob.created_at < end_at
            )

        job_agg_res = await self._session.execute(job_aggregates_stmt)
        agg_row = job_agg_res.one()

        total_scans = int(agg_row.total_scans or 0)
        websites_submitted = int(agg_row.websites_submitted or 0)
        websites_completed = int(agg_row.websites_completed or 0)
        websites_failed = int(agg_row.websites_failed or 0)
        emails_discovered = int(agg_row.emails_discovered or 0)

        websites_processed = websites_completed + websites_failed
        if websites_processed > 0:
            successful_processing_rate = round((websites_completed / websites_processed) * 100.0, 2)
        else:
            successful_processing_rate = 0.0

        # 2. Active Scans (QUEUED, RUNNING, CANCELLING currently for tenant)
        active_stmt = select(func.count(ScanJob.id)).where(
            ScanJob.organization_id == organization_id,
            ScanJob.status.in_(
                [
                    ScanJobStatus.QUEUED.value,
                    ScanJobStatus.RUNNING.value,
                    ScanJobStatus.CANCELLING.value,
                ]
            ),
        )
        if start_at is not None:
            active_stmt = active_stmt.where(
                ScanJob.created_at >= start_at, ScanJob.created_at < end_at
            )

        active_res = await self._session.execute(active_stmt)
        active_scans = int(active_res.scalar_one() or 0)

        # 3. Status Distribution (Zero-filled for all status keys)
        status_dist: dict[str, int] = {k: 0 for k in ALL_STATUS_KEYS}
        status_stmt = (
            select(ScanJob.status, func.count(ScanJob.id))
            .where(ScanJob.organization_id == organization_id)
            .group_by(ScanJob.status)
        )
        if start_at is not None:
            status_stmt = status_stmt.where(
                ScanJob.created_at >= start_at, ScanJob.created_at < end_at
            )

        status_res = await self._session.execute(status_stmt)
        for st_val, count in status_res.all():
            if st_val in status_dist:
                status_dist[st_val] = count

        # 4. Findings Classification Distribution
        class_dist: dict[str, int] = {k: 0 for k in ALL_CLASSIFICATION_KEYS}
        class_stmt = (
            select(EmailFinding.classification, func.count(EmailFinding.id))
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(ScanJob.organization_id == organization_id)
            .group_by(EmailFinding.classification)
        )
        if start_at is not None:
            class_stmt = class_stmt.where(
                ScanJob.created_at >= start_at, ScanJob.created_at < end_at
            )

        class_res = await self._session.execute(class_stmt)
        for cls_val, count in class_res.all():
            if cls_val in class_dist:
                class_dist[cls_val] = count

        # 5. Findings Validation Status Distribution
        val_dist: dict[str, int] = {k: 0 for k in ALL_VALIDATION_KEYS}
        val_stmt = (
            select(EmailFinding.validation_status, func.count(EmailFinding.id))
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(ScanJob.organization_id == organization_id)
            .group_by(EmailFinding.validation_status)
        )
        if start_at is not None:
            val_stmt = val_stmt.where(ScanJob.created_at >= start_at, ScanJob.created_at < end_at)

        val_res = await self._session.execute(val_stmt)
        for v_val, count in val_res.all():
            if v_val in val_dist:
                val_dist[v_val] = count

        # 6. Activity Timeline (Deterministic daily buckets)
        timeline_points = await self._build_timeline(
            organization_id=organization_id,
            start_at=start_at,
            end_at=end_at,
        )

        # 7. Recent Completed Scans (Max 5, COMPLETED or COMPLETED_WITH_ERRORS)
        recent_stmt = (
            select(ScanJob)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.status.in_(
                    [ScanJobStatus.COMPLETED.value, ScanJobStatus.COMPLETED_WITH_ERRORS.value]
                ),
            )
            .order_by(ScanJob.completed_at.desc().nulls_last(), ScanJob.id.desc())
            .limit(5)
        )
        if start_at is not None:
            recent_stmt = recent_stmt.where(
                ScanJob.created_at >= start_at, ScanJob.created_at < end_at
            )

        recent_res = await self._session.execute(recent_stmt)
        recent_jobs = recent_res.scalars().all()
        recent_summaries = [
            RecentScanJobSummary(
                id=job.id,
                name=job.name,
                status=job.status,
                completed_at=job.completed_at or job.created_at,
                valid_input_count=job.valid_input_count,
                completed_count=job.completed_count,
                failed_count=job.failed_count,
                email_finding_count=job.email_finding_count,
            )
            for job in recent_jobs
        ]

        return AnalyticsOverviewResponse(
            period=period,
            start_at=start_at,
            end_at=end_at,
            total_scans=total_scans,
            active_scans=active_scans,
            websites_submitted=websites_submitted,
            websites_processed=websites_processed,
            websites_completed=websites_completed,
            websites_failed=websites_failed,
            emails_discovered=emails_discovered,
            successful_processing_rate=successful_processing_rate,
            status_distribution=status_dist,
            findings_by_classification=class_dist,
            findings_by_validation_status=val_dist,
            scan_activity_timeline=timeline_points,
            recent_completed_scans=recent_summaries,
        )

    async def _build_timeline(
        self,
        organization_id: UUID,
        start_at: datetime | None,
        end_at: datetime,
    ) -> list[AnalyticsTimelinePoint]:
        """Generate zero-filled daily timeline points across date boundaries."""
        # 1. Gather scan creation counts grouped by UTC date
        scan_date_col = func.to_char(ScanJob.created_at, "YYYY-MM-DD")
        scans_timeline_stmt = (
            select(scan_date_col.label("day_str"), func.count(ScanJob.id).label("count"))
            .where(ScanJob.organization_id == organization_id)
            .group_by("day_str")
        )
        if start_at is not None:
            scans_timeline_stmt = scans_timeline_stmt.where(
                ScanJob.created_at >= start_at, ScanJob.created_at < end_at
            )

        scans_res = await self._session.execute(scans_timeline_stmt)
        scans_by_date = {str(d_str): int(cnt) for d_str, cnt in scans_res.all()}

        # 2. Gather email discovery counts grouped by first_found_at UTC date
        email_date_col = func.to_char(EmailFinding.first_found_at, "YYYY-MM-DD")
        emails_timeline_stmt = (
            select(email_date_col.label("day_str"), func.count(EmailFinding.id).label("count"))
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(ScanJob.organization_id == organization_id)
            .group_by("day_str")
        )
        if start_at is not None:
            emails_timeline_stmt = emails_timeline_stmt.where(
                ScanJob.created_at >= start_at, ScanJob.created_at < end_at
            )

        emails_res = await self._session.execute(emails_timeline_stmt)
        emails_by_date = {str(d_str): int(cnt) for d_str, cnt in emails_res.all()}

        # 3. Determine calendar range
        if start_at is not None:
            current_day = start_at.date()
            end_day = end_at.date()
        else:
            # For 'all', find minimum scan job date or default to today
            min_stmt = select(func.min(ScanJob.created_at)).where(
                ScanJob.organization_id == organization_id
            )
            min_res = await self._session.execute(min_stmt)
            min_date = min_res.scalar_one_or_none()
            if min_date is None:
                return []
            current_day = min_date.date()
            end_day = end_at.date()

        timeline: list[AnalyticsTimelinePoint] = []
        max_days = 365
        step_count = 0

        while current_day <= end_day and step_count < max_days:
            d_str = current_day.strftime("%Y-%m-%d")
            timeline.append(
                AnalyticsTimelinePoint(
                    date=d_str,
                    scans_created=scans_by_date.get(d_str, 0),
                    emails_found=emails_by_date.get(d_str, 0),
                )
            )
            current_day += timedelta(days=1)
            step_count += 1

        return timeline
