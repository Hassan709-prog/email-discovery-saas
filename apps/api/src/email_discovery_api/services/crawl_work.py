"""Transactional service for worker claim operations, heartbeats, and lease recovery."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.exc import DBAPIError
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
from email_discovery_api.services.retry_policy import RetryBackoffPolicy
from email_discovery_api.services.worker_contracts import (
    HeartbeatResult,
    HeartbeatStatus,
    LeaseLostError,
    URLClaim,
)

logger = logging.getLogger(__name__)


class CrawlWorkService:
    """Service owning short transactions for worker claims, lease renewals, and lease recovery."""

    _cycle_counter: int = 0

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.job_repo = ScanJobRepository(session)
        self.url_repo = ScanURLRepository(session)
        self.event_repo = JobEventRepository(session)
        self.retry_policy = RetryBackoffPolicy()

    def _transaction_context(self) -> Any:
        """Return nested or outer transaction context manager safely."""
        if self.session.in_transaction():
            return self.session.begin_nested()
        return self.session.begin()

    async def claim_next_url(
        self,
        lease_owner: str,
        lease_duration_seconds: float = 120.0,
    ) -> URLClaim | None:
        """Atomically claim the next eligible target URL for scanning.

        Guarantees:
            1. Stage A: Bounded job selection (max 10 jobs across orgs using
               last_claimed_at rotation).
            2. Stage B: Bounded URL candidate selection (ROW_NUMBER() <= 5 per job, K <= 50 total).
            3. 4:1 queued vs due retry opportunity cycle with instant fallback.
            4. Consistent lock order: Lock parent ScanJob first, then candidate ScanURL.
            5. Atomic CTE update setting SCANNING, lease_owner, fence_token+1,
               claimed_from_status while clearing attempt_started_* fields.
            6. Bounded deadlock retry logic for PostgreSQL serialization errors.
        """
        for attempt_idx in range(3):
            try:
                return await self._do_claim_next_url(lease_owner, lease_duration_seconds)
            except DBAPIError as exc:
                # Check for PostgreSQL deadlock (40P01) or serialization failure (40001)
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate in ("40P01", "40001") and attempt_idx < 2:
                    logger.warning(
                        "Deadlock encountered during claim_next_url. Retrying... (attempt %d)",
                        attempt_idx + 1,
                    )
                    await asyncio.sleep(0.05 * (2**attempt_idx))
                    continue
                raise

        return None

    async def _do_claim_next_url(
        self,
        lease_owner: str,
        lease_duration_seconds: float = 120.0,
    ) -> URLClaim | None:
        # Determine preferred claim class: 4:1 cycle ratio
        CrawlWorkService._cycle_counter += 1
        is_retry_opportunity = CrawlWorkService._cycle_counter % 5 == 0

        classes_to_try = [
            ScanURLStatus.RETRY_WAIT.value if is_retry_opportunity else ScanURLStatus.QUEUED.value,
            ScanURLStatus.QUEUED.value if is_retry_opportunity else ScanURLStatus.RETRY_WAIT.value,
        ]

        async with self._transaction_context():
            # Stage A: Select up to 10 active jobs across orgs with last_claimed_at rotation
            stage_a_stmt = text("""
                WITH org_jobs AS (
                  SELECT sj.id AS job_id, sj.organization_id, sj.created_at, sj.last_claimed_at,
                         ROW_NUMBER() OVER (
                           PARTITION BY sj.organization_id
                           ORDER BY sj.last_claimed_at ASC NULLS FIRST, sj.created_at ASC, sj.id ASC
                         ) AS org_rank
                  FROM scan_jobs sj
                  WHERE sj.status IN ('QUEUED', 'RUNNING')
                    AND (
                        COALESCE(sj.completed_count, 0)
                        + COALESCE(sj.failed_count, 0)
                    ) < sj.valid_input_count
                )
                SELECT job_id
                FROM org_jobs
                WHERE org_rank <= 2
                ORDER BY last_claimed_at ASC NULLS FIRST, created_at ASC, job_id ASC
                LIMIT 10
            """)
            stage_a_res = await self.session.execute(stage_a_stmt)
            selected_job_ids = [row[0] for row in stage_a_res.fetchall()]

            if not selected_job_ids:
                return None

            candidate_urls: list[dict[str, Any]] = []

            for preferred_class in classes_to_try:
                # Stage B: Bounded URL fetch (max 5 URLs per job, K <= 50)
                if preferred_class == ScanURLStatus.RETRY_WAIT.value:
                    class_filter = (
                        "su.status = 'RETRY_WAIT' AND su.next_retry_at <= clock_timestamp()"
                    )
                else:
                    class_filter = "su.status = 'QUEUED'"

                stage_b_stmt = text(f"""
                    WITH ranked_urls AS (
                        SELECT
                            su.id,
                            su.scan_job_id,
                            su.original_input,
                            su.normalized_url,
                            su.normalized_domain,
                            su.status,
                            su.next_retry_at,
                            su.attempt_count,
                            su.max_attempts,
                            su.fence_token,
                            su.created_at,
                            ROW_NUMBER() OVER (
                                PARTITION BY su.scan_job_id
                                ORDER BY su.created_at ASC, su.id ASC
                            ) AS job_url_rank
                        FROM scan_urls su
                        WHERE su.scan_job_id IN :selected_job_ids
                          AND ({class_filter})
                          AND su.attempt_count < su.max_attempts
                    )
                    SELECT
                        ru.id,
                        ru.scan_job_id,
                        ru.original_input,
                        ru.normalized_url,
                        ru.normalized_domain,
                        ru.status,
                        ru.next_retry_at,
                        ru.attempt_count,
                        ru.max_attempts,
                        ru.fence_token
                    FROM ranked_urls ru
                    JOIN scan_jobs sj ON ru.scan_job_id = sj.id
                    WHERE ru.job_url_rank <= 5
                    ORDER BY
                        sj.last_claimed_at ASC NULLS FIRST,
                        ru.job_url_rank ASC,
                        ru.created_at ASC,
                        ru.id ASC
                    LIMIT 50
                """).bindparams(bindparam("selected_job_ids", expanding=True))
                res = await self.session.execute(
                    stage_b_stmt, {"selected_job_ids": selected_job_ids}
                )
                rows = res.fetchall()

                if rows:
                    for r in rows:
                        candidate_urls.append(
                            {
                                "id": r[0],
                                "scan_job_id": r[1],
                                "original_input": r[2],
                                "normalized_url": r[3],
                                "normalized_domain": r[4],
                                "status": r[5],
                                "next_retry_at": r[6],
                                "attempt_count": r[7],
                                "max_attempts": r[8],
                                "fence_token": r[9],
                            }
                        )
                    break

            if not candidate_urls:
                return None

            # Stage C / D: Try candidates in order
            for cand in candidate_urls[:3]:
                cand_id = cand["id"]
                job_id = cand["scan_job_id"]

                # Consistent Lock Order Step 1: Lock parent job first
                job_stmt = (
                    select(ScanJob, ScanJob.organization_id)
                    .where(
                        ScanJob.id == job_id,
                        ScanJob.status.in_(
                            [ScanJobStatus.QUEUED.value, ScanJobStatus.RUNNING.value]
                        ),
                    )
                    .with_for_update()
                )
                job_res = await self.session.execute(job_stmt)
                job_row = job_res.one_or_none()

                if job_row is None:
                    continue

                job: ScanJob = job_row[0]
                org_id: uuid.UUID = job_row[1]

                # Step 2: Lock & update target ScanURL atomically
                lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_duration_seconds)
                cte_claim_stmt = text("""
                    WITH candidate AS (
                        SELECT su.id, su.status AS pre_status, su.next_retry_at AS pre_next_retry_at
                        FROM scan_urls su
                        WHERE su.id = :cand_id
                          AND su.status IN ('QUEUED', 'RETRY_WAIT')
                          AND (su.status = 'QUEUED' OR su.next_retry_at <= clock_timestamp())
                          AND su.attempt_count < su.max_attempts
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE scan_urls
                    SET status = 'SCANNING',
                        lease_owner = :lease_owner,
                        fence_token = scan_urls.fence_token + 1,
                        lease_expires_at = :lease_expires_at,
                        claimed_from_status = c.pre_status,
                        claimed_from_next_retry_at = c.pre_next_retry_at,
                        attempt_started_at = NULL,
                        attempt_started_fence_token = NULL
                    FROM candidate c
                    WHERE scan_urls.id = c.id
                    RETURNING scan_urls.id, scan_urls.fence_token, scan_urls.claimed_from_status,
                              scan_urls.claimed_from_next_retry_at;
                """)
                claim_res = await self.session.execute(
                    cte_claim_stmt,
                    {
                        "cand_id": cand_id,
                        "lease_owner": lease_owner,
                        "lease_expires_at": lease_expires_at,
                    },
                )
                claim_row = claim_res.one_or_none()

                if claim_row is None:
                    continue

                new_fence_token = claim_row[1]
                claimed_from_status = claim_row[2]
                claimed_from_next_retry_at = claim_row[3]

                # Step 3: Update parent ScanJob state
                job.last_claimed_at = datetime.now(UTC)

                if job.status == ScanJobStatus.QUEUED.value:
                    job.status = ScanJobStatus.RUNNING.value
                    job.started_at = datetime.now(UTC)
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

                if job.queued_count > 0:
                    job.queued_count = job.queued_count - 1
                job.running_count = job.running_count + 1

                # Recheck lease_expires_at
                recheck = await self.session.execute(
                    select(ScanURL.lease_expires_at).where(ScanURL.id == cand_id)
                )
                lease_expires_at = recheck.scalar_one()
                if lease_expires_at is None:
                    lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_duration_seconds)

                return URLClaim(
                    scan_url_id=cand_id,
                    organization_id=org_id,
                    job_id=job_id,
                    original_input=cand["original_input"],
                    normalized_url=cand["normalized_url"],
                    normalized_domain=cand["normalized_domain"],
                    lease_owner=lease_owner,
                    fence_token=new_fence_token,
                    attempt_count=cand["attempt_count"],
                    max_attempts=cand["max_attempts"],
                    lease_expires_at=lease_expires_at,
                    claimed_from_status=claimed_from_status,
                    claimed_from_next_retry_at=claimed_from_next_retry_at,
                )

        return None

    async def mark_attempt_started(
        self,
        scan_url_id: uuid.UUID,
        lease_owner: str,
        fence_token: int,
    ) -> int:
        """Atomically mark scan attempt as started immediately before HTTP execution.

        Guarantees:
            1. Enforces 5-predicate fence: id, SCANNING status, lease_owner, fence_token, unexpired.
            2. Checks attempt_count < max_attempts.
            3. If attempt_started_fence_token is NULL: increments attempt_count exactly once, sets
               attempt_started_at, and sets attempt_started_fence_token = fence_token.
            4. If attempt_started_fence_token == fence_token: returns attempt_count idempotently.
            5. If attempt_started_fence_token is non-null and != fence_token: fails closed.
        """
        async with self._transaction_context():
            stmt = (
                select(ScanURL)
                .where(
                    ScanURL.id == scan_url_id,
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    ScanURL.lease_owner == lease_owner,
                    ScanURL.fence_token == fence_token,
                    ScanURL.lease_expires_at > func.clock_timestamp(),
                )
                .with_for_update()
            )

            res = await self.session.execute(stmt)
            scan_url = res.scalar_one_or_none()

            if scan_url is None:
                raise LeaseLostError(scan_url_id, lease_owner, fence_token)

            if scan_url.attempt_started_fence_token == fence_token:
                # Idempotent return
                return scan_url.attempt_count

            if scan_url.attempt_started_fence_token is not None:
                # Invariant violation
                raise LeaseLostError(scan_url_id, lease_owner, fence_token)

            if scan_url.attempt_count >= scan_url.max_attempts:
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"ScanURL {scan_url_id} has reached maximum attempts.",
                )

            scan_url.attempt_count = scan_url.attempt_count + 1
            scan_url.attempt_started_at = datetime.now(UTC)
            scan_url.attempt_started_fence_token = fence_token

            return scan_url.attempt_count

    async def renew_lease(
        self,
        scan_url_id: uuid.UUID,
        lease_owner: str,
        fence_token: int,
        lease_duration_seconds: float = 120.0,
    ) -> HeartbeatResult:
        """Renew active scan lease using strict 5-predicate fence."""
        async with self._transaction_context():
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
                    ScanURL.fence_token == fence_token,
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

    async def release_fenced_claim(
        self,
        scan_url_id: uuid.UUID,
        lease_owner: str,
        fence_token: int,
    ) -> bool:
        """Transactionally release a claimed SCANNING URL.

        Pre-Attempt Release (attempt_started_fence_token IS NULL):
            Restores claimed_from_status (QUEUED or RETRY_WAIT) with 0 attempts consumed,
            0 delay added, and 0 events emitted.

        Post-Attempt Release (attempt_started_fence_token == fence_token):
            Preserves consumed attempt_count and applies RetryBackoffPolicy.
        """
        async with self._transaction_context():
            # Lock ScanURL and parent ScanJob
            url_stmt = (
                select(ScanURL, ScanJob)
                .join(ScanJob, ScanURL.scan_job_id == ScanJob.id)
                .where(
                    ScanURL.id == scan_url_id,
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    ScanURL.lease_owner == lease_owner,
                    ScanURL.fence_token == fence_token,
                )
                .with_for_update()
            )
            res = await self.session.execute(url_stmt)
            row = res.one_or_none()

            if row is None:
                return False

            scan_url: ScanURL = row[0]
            job: ScanJob = row[1]

            if job.running_count > 0:
                job.running_count = job.running_count - 1

            parent_cancelled = job.status in (
                ScanJobStatus.CANCELLING.value,
                ScanJobStatus.CANCELLED.value,
            )

            if parent_cancelled:
                scan_url.status = ScanURLStatus.CANCELLED.value
                scan_url.completed_at = datetime.now(UTC)
                scan_url.last_error_code = "JOB_CANCELLED"
            elif scan_url.attempt_started_fence_token is None:
                # RELEASED_BEFORE_ATTEMPT: 0 attempts consumed
                restored_status = scan_url.claimed_from_status or ScanURLStatus.QUEUED.value
                scan_url.status = restored_status
                scan_url.next_retry_at = scan_url.claimed_from_next_retry_at
                if restored_status == ScanURLStatus.QUEUED.value:
                    job.queued_count = job.queued_count + 1
            else:
                # RELEASED_AFTER_ATTEMPT: Consumed attempt preserved
                if scan_url.attempt_count < scan_url.max_attempts:
                    scan_url.status = ScanURLStatus.RETRY_WAIT.value
                    scan_url.next_retry_at = datetime.now(UTC) + timedelta(
                        seconds=self.retry_policy.compute_delay_seconds(scan_url.attempt_count)
                    )
                    scan_url.last_error_code = "WORKER_DRAINING"
                    job.queued_count = job.queued_count + 1
                else:
                    scan_url.status = ScanURLStatus.FAILED.value
                    scan_url.completed_at = datetime.now(UTC)
                    scan_url.last_error_code = "MAX_ATTEMPTS_EXCEEDED"
                    job.failed_count = job.failed_count + 1

            # Clear transient claim fields
            scan_url.lease_owner = None
            scan_url.lease_expires_at = None
            scan_url.claimed_from_status = None
            scan_url.claimed_from_next_retry_at = None
            scan_url.attempt_started_at = None
            scan_url.attempt_started_fence_token = None

            return True

    async def recover_expired_leases(self, batch_size: int = 50) -> int:
        """Recover expired SCANNING leases safely with strict parent-cancellation checks."""
        recovered_count = 0
        async with self._transaction_context():
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

                parent_cancelled = job.status in (
                    ScanJobStatus.CANCELLING.value,
                    ScanJobStatus.CANCELLED.value,
                )

                if parent_cancelled:
                    scan_url.status = ScanURLStatus.CANCELLED.value
                    scan_url.completed_at = datetime.now(UTC)
                    scan_url.last_error_code = "JOB_CANCELLED"
                elif scan_url.attempt_started_fence_token is None:
                    # Pre-attempt recovery: 0 attempts consumed
                    restored_status = scan_url.claimed_from_status or ScanURLStatus.QUEUED.value
                    scan_url.status = restored_status
                    scan_url.next_retry_at = scan_url.claimed_from_next_retry_at
                    if restored_status == ScanURLStatus.QUEUED.value:
                        job.queued_count = job.queued_count + 1
                elif scan_url.attempt_count < scan_url.max_attempts:
                    scan_url.status = ScanURLStatus.RETRY_WAIT.value
                    scan_url.next_retry_at = datetime.now(UTC) + timedelta(
                        seconds=self.retry_policy.compute_delay_seconds(scan_url.attempt_count)
                    )
                    scan_url.last_error_code = "LEASE_EXPIRED"
                    job.queued_count = job.queued_count + 1
                else:
                    scan_url.status = ScanURLStatus.FAILED.value
                    scan_url.completed_at = datetime.now(UTC)
                    scan_url.last_error_code = "LEASE_EXPIRED_MAX_ATTEMPTS"
                    job.failed_count = job.failed_count + 1

                scan_url.lease_owner = None
                scan_url.lease_expires_at = None
                scan_url.claimed_from_status = None
                scan_url.claimed_from_next_retry_at = None
                scan_url.attempt_started_at = None
                scan_url.attempt_started_fence_token = None

                recovered_count += 1

        return recovered_count
