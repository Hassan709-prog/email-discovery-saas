"""Transactional service for worker claim operations, heartbeats, and lease recovery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.job_event import JobEvent
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.repositories.job_events import JobEventRepository
from email_discovery_api.repositories.scan_jobs import ScanJobRepository
from email_discovery_api.repositories.scan_urls import ScanURLRepository
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.worker_contracts import (
    HeartbeatResult,
    HeartbeatStatus,
    URLClaim,
)


class CrawlWorkService:
    """Service owning short transactions for worker claims, lease renewals, and lease recovery."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.job_repo = ScanJobRepository(session)
        self.url_repo = ScanURLRepository(session)
        self.event_repo = JobEventRepository(session)

    async def claim_next_url(
        self,
        lease_owner: str,
        lease_duration_seconds: float = 120.0,
    ) -> URLClaim | None:
        """Atomically claim the next eligible target URL for scanning.

        Fairness Ordering:
            1. Global active SCANNING count for domain ASC (cross-job domain fairness).
            2. Current job running_count ASC.
            3. Processed fraction (completed + failed) / valid_input_count ASC.
            4. Job created_at ASC.
            5. Job ID ASC.
            6. Original URL index ASC.
            7. URL ID ASC.

        Guarantees:
            - short transaction boundary (< 5ms)
            - SELECT ... FOR UPDATE SKIP LOCKED
            - transitions ScanURL status QUEUED / RETRY_WAIT -> SCANNING
            - transitions parent ScanJob QUEUED -> RUNNING on first claim
            - increments attempt_count exactly once
            - sets lease_owner and lease_expires_at using PostgreSQL clock_timestamp()
        """
        async with self.session.begin():
            # Domain key subquery for active scanning count across ALL jobs
            domain_key = func.coalesce(
                ScanURL.normalized_domain, ScanURL.normalized_url, ScanURL.original_input
            )

            active_domain_subquery = (
                select(func.count(ScanURL.id))
                .where(
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    func.coalesce(
                        ScanURL.normalized_domain, ScanURL.normalized_url, ScanURL.original_input
                    )
                    == domain_key,
                )
                .scalar_subquery()
            )

            processed_fraction = (ScanJob.completed_count + ScanJob.failed_count) / func.nullif(
                ScanJob.valid_input_count, 0
            )

            # Subquery to pick single best ScanURL ID
            claimable_stmt = (
                select(ScanURL.id)
                .join(ScanJob, ScanURL.scan_job_id == ScanJob.id)
                .where(
                    ScanJob.status.in_([ScanJobStatus.QUEUED.value, ScanJobStatus.RUNNING.value]),
                    (
                        (ScanURL.status == ScanURLStatus.QUEUED.value)
                        | (
                            (ScanURL.status == ScanURLStatus.RETRY_WAIT.value)
                            & (ScanURL.next_retry_at <= func.clock_timestamp())
                        )
                    ),
                )
                .order_by(
                    active_domain_subquery.asc(),
                    ScanJob.running_count.asc(),
                    processed_fraction.asc(),
                    ScanJob.created_at.asc(),
                    ScanJob.id.asc(),
                    ScanURL.original_index.asc(),
                    ScanURL.id.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )

            res = await self.session.execute(claimable_stmt)
            target_url_id = res.scalar_one_or_none()

            if target_url_id is None:
                return None

            # Lock selected URL and parent job
            stmt_url = (
                select(ScanURL, ScanJob.organization_id)
                .join(ScanJob, ScanURL.scan_job_id == ScanJob.id)
                .where(ScanURL.id == target_url_id)
                .with_for_update()
            )
            url_res = await self.session.execute(stmt_url)
            row = url_res.one_or_none()
            if row is None:
                return None

            scan_url: ScanURL = row[0]
            org_id: uuid.UUID = row[1]

            new_attempt = scan_url.attempt_count + 1

            # Update ScanURL row
            stmt_update_url = (
                update(ScanURL)
                .where(ScanURL.id == target_url_id)
                .values(
                    status=ScanURLStatus.SCANNING.value,
                    lease_owner=lease_owner,
                    attempt_count=new_attempt,
                    lease_expires_at=func.clock_timestamp()
                    + timedelta(seconds=lease_duration_seconds),
                    started_at=func.clock_timestamp()
                    if scan_url.started_at is None
                    else scan_url.started_at,
                )
                .execution_options(synchronize_session=False)
            )
            await self.session.execute(stmt_update_url)

            # Update parent ScanJob counters
            job = await self.job_repo.get_job_for_update(org_id, scan_url.scan_job_id)
            if job is None:
                raise ServiceError(
                    ServiceErrorCode.JOB_NOT_FOUND,
                    f"Job {scan_url.scan_job_id} not found during claim.",
                )

            # Transition parent job QUEUED -> RUNNING if needed
            if job.status == ScanJobStatus.QUEUED.value:
                job.status = ScanJobStatus.RUNNING.value
                job.started_at = datetime.now()
                seq = await self.job_repo.allocate_event_sequence(org_id, job.id)
                if seq is not None:
                    event = JobEvent(
                        scan_job_id=job.id,
                        event_type="JOB_STATUS_CHANGED",
                        sequence_number=seq,
                        payload={
                            "previous_status": ScanJobStatus.QUEUED.value,
                            "new_status": ScanJobStatus.RUNNING.value,
                        },
                    )
                    self.event_repo.append_event(event)

            # Counter adjustment: queued_count -= 1, running_count += 1
            if job.queued_count > 0:
                job.queued_count = job.queued_count - 1
            job.running_count = job.running_count + 1

            # Fetch fresh lease_expires_at timestamp
            recheck = await self.session.execute(
                select(ScanURL.lease_expires_at).where(ScanURL.id == target_url_id)
            )
            lease_expires_at = recheck.scalar_one()
            assert lease_expires_at is not None

            return URLClaim(
                scan_url_id=scan_url.id,
                organization_id=org_id,
                job_id=scan_url.scan_job_id,
                original_input=scan_url.original_input,
                normalized_url=scan_url.normalized_url,
                normalized_domain=scan_url.normalized_domain,
                lease_owner=lease_owner,
                attempt_count=new_attempt,
                max_attempts=scan_url.max_attempts,
                lease_expires_at=lease_expires_at,
            )

    async def renew_lease(
        self,
        scan_url_id: uuid.UUID,
        lease_owner: str,
        attempt_count: int,
        lease_duration_seconds: float = 120.0,
    ) -> HeartbeatResult:
        """Renew active scan lease using a fresh, short-lived session.

        Guarantees:
            1. Verifies parent job status first. If CANCELLING or CANCELLED,
               returns CANCEL_REQUESTED.
            2. Requires status='SCANNING', lease_owner, attempt_count, AND
               lease_expires_at > clock_timestamp().
            3. Never resurrects an already expired lease.
        """
        async with self.session.begin():
            # Check parent job status
            job_status_stmt = (
                select(ScanJob.status)
                .join(ScanURL, ScanURL.scan_job_id == ScanJob.id)
                .where(ScanURL.id == scan_url_id)
            )
            job_status_res = await self.session.execute(job_status_stmt)
            job_status = job_status_res.scalar_one_or_none()

            if job_status in (ScanJobStatus.CANCELLING.value, ScanJobStatus.CANCELLED.value):
                return HeartbeatResult(status=HeartbeatStatus.CANCEL_REQUESTED)

            stmt = (
                update(ScanURL)
                .where(
                    ScanURL.id == scan_url_id,
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    ScanURL.lease_owner == lease_owner,
                    ScanURL.attempt_count == attempt_count,
                    ScanURL.lease_expires_at > func.clock_timestamp(),
                )
                .values(
                    lease_expires_at=func.clock_timestamp()
                    + timedelta(seconds=lease_duration_seconds),
                )
                .returning(ScanURL.lease_expires_at)
            )
            res = await self.session.execute(stmt)
            new_expires = res.scalar_one_or_none()

            if new_expires is None:
                return HeartbeatResult(status=HeartbeatStatus.LEASE_LOST)

            return HeartbeatResult(
                status=HeartbeatStatus.RENEWED,
                lease_expires_at=new_expires,
            )

    async def recover_expired_leases(self, batch_size: int = 50) -> int:
        """Recover expired SCANNING leases safely with strict locking.

        Transitions:
            - Expired SCANNING with attempt < max_attempts -> RETRY_WAIT, queued_count += 1
            - Expired SCANNING with attempt >= max_attempts -> FAILED, failed_count += 1
        """
        recovered_count = 0
        async with self.session.begin():
            # Lock expired SCANNING rows
            stmt = (
                select(ScanURL)
                .options(selectinload(ScanURL.scan_job))
                .where(
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    ScanURL.lease_expires_at <= func.clock_timestamp(),
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            res = await self.session.execute(stmt)
            expired_urls = list(res.scalars().all())

            for scan_url in expired_urls:
                job = await self.job_repo.get_job_for_update(
                    scan_url.scan_job.organization_id, scan_url.scan_job_id
                )
                if job is None:
                    continue

                if job.running_count > 0:
                    job.running_count = job.running_count - 1

                if job.status in (ScanJobStatus.CANCELLING.value, ScanJobStatus.CANCELLED.value):
                    scan_url.status = ScanURLStatus.CANCELLED.value
                    scan_url.completed_at = datetime.now(UTC)
                    scan_url.lease_owner = None
                    scan_url.lease_expires_at = None
                    scan_url.last_error_code = "JOB_CANCELLED"
                    scan_url.last_error_message = (
                        "Cancelled by job cancellation request during lease recovery."
                    )
                elif scan_url.attempt_count < scan_url.max_attempts:
                    scan_url.status = ScanURLStatus.RETRY_WAIT.value
                    scan_url.next_retry_at = datetime.now(UTC)
                    scan_url.lease_owner = None
                    scan_url.lease_expires_at = None
                    scan_url.last_error_code = "LEASE_EXPIRED"
                    scan_url.last_error_message = "Scan lease expired and was reclaimed."
                    job.queued_count = job.queued_count + 1
                else:
                    scan_url.status = ScanURLStatus.FAILED.value
                    scan_url.completed_at = datetime.now(UTC)
                    scan_url.lease_owner = None
                    scan_url.lease_expires_at = None
                    scan_url.last_error_code = "LEASE_EXPIRED_MAX_ATTEMPTS"
                    scan_url.last_error_message = "Scan lease expired after maximum attempts."
                    job.failed_count = job.failed_count + 1

                recovered_count += 1

        return recovered_count
