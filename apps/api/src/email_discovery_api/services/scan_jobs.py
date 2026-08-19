"""Transactional service for job creation, input ingestion, status transitions, and progress."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.api.dependencies.cursors import encode_cursor
from email_discovery_api.models.crawl_attempt import CrawlAttempt
from email_discovery_api.models.enums import MembershipRole, ScanJobStatus, ScanURLStatus
from email_discovery_api.models.job_event import JobEvent
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.repositories.job_events import JobEventRepository
from email_discovery_api.repositories.organizations import OrganizationAccessRepository
from email_discovery_api.repositories.scan_jobs import ScanJobRepository
from email_discovery_api.repositories.scan_urls import ScanURLRepository
from email_discovery_api.schemas.scan_jobs import (
    CreateScanJobCommand,
    ScanInputPreview,
    ScanJobProgress,
)
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.policies import ScanCreationPolicy
from email_scanner import URLNormalizationError, normalize_url

ALLOWED_STATE_TRANSITIONS: dict[ScanJobStatus, set[ScanJobStatus]] = {
    ScanJobStatus.DRAFT: {ScanJobStatus.QUEUED},
    ScanJobStatus.QUEUED: {ScanJobStatus.RUNNING, ScanJobStatus.CANCELLED},
    ScanJobStatus.RUNNING: {
        ScanJobStatus.CANCELLING,
        ScanJobStatus.COMPLETED,
        ScanJobStatus.COMPLETED_WITH_ERRORS,
        ScanJobStatus.FAILED,
    },
    ScanJobStatus.CANCELLING: {ScanJobStatus.CANCELLED},
}

TERMINAL_STATUSES: set[ScanJobStatus] = {
    ScanJobStatus.CANCELLED,
    ScanJobStatus.COMPLETED,
    ScanJobStatus.COMPLETED_WITH_ERRORS,
    ScanJobStatus.FAILED,
}

ALLOWED_ROLES: set[str] = {
    MembershipRole.OWNER.value,
    MembershipRole.ADMIN.value,
    MembershipRole.MEMBER.value,
}


@dataclass(frozen=True)
class CreateJobResult:
    """Result of job creation distinguishing newly created jobs from idempotent replays."""

    job: ScanJob
    created: bool


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


def compute_request_fingerprint(command: CreateScanJobCommand) -> str:
    """Compute deterministic 64-character SHA-256 request fingerprint."""
    payload: dict[str, Any] = {
        "organization_id": str(command.organization_id),
        "created_by_user_id": str(command.created_by_user_id),
        "source_type": command.source_type.value,
        "inputs": command.inputs,
        "name": command.name,
        "configuration_snapshot": command.configuration_snapshot,
        "scanner_version": command.scanner_version,
        "normalization_version": command.normalization_version,
        "ranking_version": command.ranking_version,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def preview_scan_inputs(inputs: list[str]) -> list[ScanInputPreview]:
    """Parse inputs deterministically into a list of preview items with classification."""
    previews: list[ScanInputPreview] = []
    seen_urls: dict[str, int] = {}

    for idx, raw_input in enumerate(inputs):
        try:
            norm = normalize_url(raw_input)
            canonical_url = norm.normalized_url
            if canonical_url in seen_urls:
                previews.append(
                    ScanInputPreview(
                        original_index=idx,
                        original_input=raw_input,
                        normalized_url=norm.normalized_url,
                        normalized_domain=norm.hostname,
                        classification="DUPLICATE",
                        duplicate_of_index=seen_urls[canonical_url],
                    )
                )
            else:
                seen_urls[canonical_url] = idx
                previews.append(
                    ScanInputPreview(
                        original_index=idx,
                        original_input=raw_input,
                        normalized_url=norm.normalized_url,
                        normalized_domain=norm.hostname,
                        classification="VALID",
                    )
                )
        except URLNormalizationError as err:
            previews.append(
                ScanInputPreview(
                    original_index=idx,
                    original_input=raw_input,
                    classification="INVALID",
                    error_code=err.code.value if hasattr(err, "code") else "INVALID_URL",
                    error_message=str(err),
                )
            )

    return previews


class ScanJobService:
    """Transactional service for job creation, queueing, cancellation, and finalization."""

    def __init__(
        self,
        session: AsyncSession,
        policy: ScanCreationPolicy | None = None,
    ) -> None:
        self.session = session
        self.policy = policy or ScanCreationPolicy()
        self.org_repo = OrganizationAccessRepository(session)
        self.job_repo = ScanJobRepository(session)
        self.url_repo = ScanURLRepository(session)
        self.event_repo = JobEventRepository(session)

    async def create_job(self, command: CreateScanJobCommand) -> CreateJobResult:
        """Create scan job and ingest input URLs into draft state."""
        self.policy.validate_pre_ingestion(command.inputs, command.configuration_snapshot)
        fingerprint = compute_request_fingerprint(command)

        try:
            async with self.session.begin():
                return await self._create_job_in_transaction(command, fingerprint)
        except IntegrityError as err:
            if command.idempotency_key:
                async with self.session.begin():
                    existing = await self.job_repo.find_by_idempotency_key(
                        command.organization_id, command.idempotency_key
                    )
                    if existing is not None:
                        if existing.request_fingerprint == fingerprint:
                            return CreateJobResult(job=existing, created=False)
                        raise ServiceError(
                            ServiceErrorCode.IDEMPOTENCY_CONFLICT,
                            f"Key {command.idempotency_key!r} used with different fingerprint.",
                        ) from err
            raise

    async def _create_job_in_transaction(
        self, command: CreateScanJobCommand, fingerprint: str
    ) -> CreateJobResult:
        """Internal job creation steps executed within an active transaction."""
        org = await self.org_repo.get_active_organization_for_update(command.organization_id)
        if org is None:
            raise ServiceError(
                ServiceErrorCode.ORGANIZATION_NOT_FOUND,
                f"Organization {command.organization_id} was not found or is not active.",
            )

        membership = await self.org_repo.get_active_membership(
            command.organization_id, command.created_by_user_id
        )
        if membership is None or membership.role not in ALLOWED_ROLES:
            raise ServiceError(
                ServiceErrorCode.USER_NOT_AUTHORIZED,
                f"User {command.created_by_user_id} unauthorized for organization.",
            )

        active_count = await self.job_repo.count_active_jobs(command.organization_id)
        if active_count >= self.policy.max_active_jobs_per_organization:
            raise ServiceError(
                ServiceErrorCode.ACTIVE_JOB_LIMIT_EXCEEDED,
                f"Active count ({active_count}) reached limit "
                f"{self.policy.max_active_jobs_per_organization}.",
            )

        if command.idempotency_key:
            existing = await self.job_repo.find_by_idempotency_key(
                command.organization_id, command.idempotency_key
            )
            if existing is not None:
                if existing.request_fingerprint == fingerprint:
                    return CreateJobResult(job=existing, created=False)
                raise ServiceError(
                    ServiceErrorCode.IDEMPOTENCY_CONFLICT,
                    f"Key {command.idempotency_key!r} used with different fingerprint.",
                )

        job_id = uuid.uuid4()
        scan_urls: list[ScanURL] = []
        seen_urls: dict[str, tuple[uuid.UUID, int]] = {}

        total_input_count = len(command.inputs)
        valid_input_count = 0
        duplicate_input_count = 0

        for idx, raw_input in enumerate(command.inputs):
            url_id = uuid.uuid4()
            try:
                norm = normalize_url(raw_input)
                canonical_url = norm.normalized_url

                if canonical_url in seen_urls:
                    first_url_id, _first_idx = seen_urls[canonical_url]
                    duplicate_input_count += 1
                    scan_urls.append(
                        ScanURL(
                            id=url_id,
                            scan_job_id=job_id,
                            original_index=idx,
                            original_input=raw_input,
                            normalized_url=norm.normalized_url,
                            normalized_domain=norm.hostname,
                            status=ScanURLStatus.DUPLICATE.value,
                            duplicate_of_scan_url_id=first_url_id,
                        )
                    )
                else:
                    seen_urls[canonical_url] = (url_id, idx)
                    valid_input_count += 1
                    scan_urls.append(
                        ScanURL(
                            id=url_id,
                            scan_job_id=job_id,
                            original_index=idx,
                            original_input=raw_input,
                            normalized_url=norm.normalized_url,
                            normalized_domain=norm.hostname,
                            status=ScanURLStatus.PENDING.value,
                        )
                    )
            except URLNormalizationError as err:
                code_str = err.code.value if hasattr(err, "code") else "INVALID_URL"
                scan_urls.append(
                    ScanURL(
                        id=url_id,
                        scan_job_id=job_id,
                        original_index=idx,
                        original_input=raw_input,
                        status=ScanURLStatus.INVALID.value,
                        last_error_code=code_str,
                        last_error_message=str(err),
                    )
                )

        job = ScanJob(
            id=job_id,
            organization_id=command.organization_id,
            created_by_user_id=command.created_by_user_id,
            name=command.name,
            status=ScanJobStatus.DRAFT.value,
            source_type=command.source_type.value,
            scanner_version=command.scanner_version,
            normalization_version=command.normalization_version,
            ranking_version=command.ranking_version,
            configuration_snapshot=command.configuration_snapshot,
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            next_event_sequence=1,
            total_input_count=total_input_count,
            valid_input_count=valid_input_count,
            duplicate_input_count=duplicate_input_count,
            queued_count=0,
            running_count=0,
            completed_count=0,
            failed_count=0,
            email_finding_count=0,
        )

        self.job_repo.add_job(job)
        self.url_repo.add_scan_urls(scan_urls)

        seq = await self.job_repo.allocate_event_sequence(command.organization_id, job_id)
        assert seq == 1

        event = JobEvent(
            id=uuid.uuid4(),
            scan_job_id=job_id,
            event_type="JOB_CREATED",
            sequence_number=seq,
            payload={
                "total_input_count": total_input_count,
                "valid_input_count": valid_input_count,
                "duplicate_input_count": duplicate_input_count,
            },
        )
        self.event_repo.append_event(event)

        return CreateJobResult(job=job, created=True)

    async def queue_job(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> ScanJob:
        """Atomically transition job from DRAFT to QUEUED.

        Guard: Rejects queueing if valid_input_count == 0 with ServiceErrorCode.NO_VALID_INPUTS.
        """
        async with self.session.begin():
            job = await self.job_repo.get_job_for_update(organization_id, job_id)
            if job is None or getattr(job, "status", None) is None:
                job = await self.job_repo.get_job(organization_id, job_id)
            if job is None:
                raise ServiceError(
                    ServiceErrorCode.JOB_NOT_FOUND,
                    f"Scan job {job_id} was not found for organization {organization_id}.",
                )

            current_status = ScanJobStatus(job.status)
            if current_status == ScanJobStatus.QUEUED:
                return job

            if current_status != ScanJobStatus.DRAFT:
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"Job {job_id} is in status {current_status.value} and cannot be queued.",
                )

            if job.valid_input_count == 0:
                raise ServiceError(
                    ServiceErrorCode.NO_VALID_INPUTS,
                    "Cannot queue scan job with zero valid target URLs.",
                )

            # Bulk-update eligible PENDING URLs -> QUEUED
            stmt_bulk = (
                update(ScanURL)
                .where(
                    ScanURL.scan_job_id == job_id,
                    ScanURL.status == ScanURLStatus.PENDING.value,
                )
                .values(status=ScanURLStatus.QUEUED.value)
            )
            res_bulk = await self.session.execute(stmt_bulk)
            affected_count = int(getattr(res_bulk, "rowcount", 0))

            job.status = ScanJobStatus.QUEUED.value
            job.queued_count = affected_count

            seq = await self.job_repo.allocate_event_sequence(organization_id, job_id)
            if seq is not None:
                event = JobEvent(
                    scan_job_id=job_id,
                    event_type="JOB_STATUS_CHANGED",
                    sequence_number=seq,
                    payload={
                        "previous_status": ScanJobStatus.DRAFT.value,
                        "new_status": ScanJobStatus.QUEUED.value,
                        "queued_count": affected_count,
                    },
                )
                self.event_repo.append_event(event)

            return job

    async def cancel_job(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> ScanJob:
        """Cancel a scan job atomically from QUEUED or RUNNING state.

        Guarantees:
            1. Unclaimed URLs (PENDING, QUEUED, RETRY_WAIT) transition to CANCELLED.
            2. queued_count becomes 0.
            3. DOES NOT increment failed_count.
            4. If running_count == 0, job transitions directly to CANCELLED.
            5. If running_count > 0, job transitions to CANCELLING.
        """
        async with self.session.begin():
            job = await self.job_repo.get_job_for_update(organization_id, job_id)
            if job is None or getattr(job, "status", None) is None:
                job = await self.job_repo.get_job(organization_id, job_id)
            if job is None:
                raise ServiceError(
                    ServiceErrorCode.JOB_NOT_FOUND,
                    f"Scan job {job_id} was not found for organization {organization_id}.",
                )

            current_status = ScanJobStatus(job.status)
            if current_status in (ScanJobStatus.CANCELLED, ScanJobStatus.CANCELLING):
                return job

            if current_status not in (ScanJobStatus.QUEUED, ScanJobStatus.RUNNING):
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"Job {job_id} is in status {current_status.value} and cannot be cancelled.",
                )

            # Cancel all unclaimed or expired SCANNING URLs
            stmt_unclaimed = (
                update(ScanURL)
                .where(
                    ScanURL.scan_job_id == job_id,
                    (
                        ScanURL.status.in_(
                            [
                                ScanURLStatus.PENDING.value,
                                ScanURLStatus.QUEUED.value,
                                ScanURLStatus.RETRY_WAIT.value,
                            ]
                        )
                        | (
                            (ScanURL.status == ScanURLStatus.SCANNING.value)
                            & (ScanURL.lease_expires_at <= func.clock_timestamp())
                        )
                    ),
                )
                .values(
                    status=ScanURLStatus.CANCELLED.value,
                    completed_at=func.clock_timestamp(),
                    last_error_code="JOB_CANCELLED",
                    last_error_message="Cancelled by job cancellation request.",
                )
            )
            await self.session.execute(stmt_unclaimed)

            job.queued_count = 0
            job.cancellation_requested_at = utc_now()

            # Determine new job status based on remaining active UNEXPIRED SCANNING leases
            count_active_stmt = select(func.count(ScanURL.id)).where(
                ScanURL.scan_job_id == job_id,
                ScanURL.status == ScanURLStatus.SCANNING.value,
                ScanURL.lease_expires_at > func.clock_timestamp(),
            )
            active_res = await self.session.execute(count_active_stmt)
            active_scanning_count = active_res.scalar_one() or 0
            job.running_count = active_scanning_count

            target_job_status = (
                ScanJobStatus.CANCELLED if active_scanning_count == 0 else ScanJobStatus.CANCELLING
            )

            job.status = target_job_status.value
            if target_job_status == ScanJobStatus.CANCELLED and job.completed_at is None:
                job.completed_at = utc_now()

            seq = await self.job_repo.allocate_event_sequence(organization_id, job_id)
            if seq is not None:
                event = JobEvent(
                    scan_job_id=job_id,
                    event_type="JOB_STATUS_CHANGED",
                    sequence_number=seq,
                    payload={
                        "previous_status": current_status.value,
                        "new_status": target_job_status.value,
                    },
                )
                self.event_repo.append_event(event)

            return job

    async def try_finalize_job(
        self, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> ScanJob | None:
        """Authoritatively check and finalize job inside a separate short transaction (T5).

        Checks:
            1. Lock ScanJob row FOR UPDATE.
            2. Return existing job if already terminal.
            3. Count nonterminal URLs: PENDING, QUEUED, SCANNING, RETRY_WAIT.
            4. If nonterminal_count == 0:
               - Check PARTIAL crawl attempts count.
               - If CANCELLING -> CANCELLED.
               - Else if failed_count == 0 AND partial_count == 0 -> COMPLETED.
               - Else if completed_count == 0 AND failed_count == valid_input_count -> FAILED.
               - Else -> COMPLETED_WITH_ERRORS.
               - Append single JOB_STATUS_CHANGED event.
        """
        async with self.session.begin():
            job = await self.job_repo.get_job_for_update(organization_id, job_id)
            if job is None:
                return None

            current_status = ScanJobStatus(job.status)
            if current_status in TERMINAL_STATUSES:
                return job

            # Count nonterminal URLs in database
            nonterminal_stmt = select(func.count(ScanURL.id)).where(
                ScanURL.scan_job_id == job_id,
                ScanURL.status.in_(
                    [
                        ScanURLStatus.PENDING.value,
                        ScanURLStatus.QUEUED.value,
                        ScanURLStatus.SCANNING.value,
                        ScanURLStatus.RETRY_WAIT.value,
                    ]
                ),
            )
            res_nonterm = await self.session.execute(nonterminal_stmt)
            nonterminal_count = res_nonterm.scalar_one() or 0

            if nonterminal_count > 0:
                return None  # Work still in progress

            # Reconcile actual ScanURL status counts
            st_counts_stmt = (
                select(ScanURL.status, func.count(ScanURL.id))
                .where(ScanURL.scan_job_id == job_id)
                .group_by(ScanURL.status)
            )
            res_st = await self.session.execute(st_counts_stmt)
            counts = dict(res_st.tuples().all())

            completed_actual = counts.get(ScanURLStatus.COMPLETED.value, 0) + counts.get(
                ScanURLStatus.NO_EMAIL.value, 0
            )
            failed_actual = counts.get(ScanURLStatus.FAILED.value, 0) + counts.get(
                ScanURLStatus.INVALID.value, 0
            )
            queued_actual = counts.get(ScanURLStatus.QUEUED.value, 0) + counts.get(
                ScanURLStatus.RETRY_WAIT.value, 0
            )
            running_actual = counts.get(ScanURLStatus.SCANNING.value, 0)

            job.queued_count = queued_actual
            job.running_count = running_actual
            job.completed_count = completed_actual
            job.failed_count = failed_actual

            # Query partial crawl attempt count
            partial_stmt = (
                select(func.count(CrawlAttempt.id))
                .join(ScanURL, CrawlAttempt.scan_url_id == ScanURL.id)
                .where(
                    ScanURL.scan_job_id == job_id,
                    CrawlAttempt.outcome == "PARTIAL",
                )
            )
            res_partial = await self.session.execute(partial_stmt)
            partial_count = res_partial.scalar_one() or 0

            if current_status == ScanJobStatus.CANCELLING:
                target_status = ScanJobStatus.CANCELLED
            elif failed_actual == 0 and partial_count == 0:
                target_status = ScanJobStatus.COMPLETED
            elif completed_actual == 0 and failed_actual == job.valid_input_count:
                target_status = ScanJobStatus.FAILED
            else:
                target_status = ScanJobStatus.COMPLETED_WITH_ERRORS

            job.status = target_status.value
            if job.completed_at is None:
                job.completed_at = utc_now()

            seq = await self.job_repo.allocate_event_sequence(organization_id, job_id)
            if seq is not None:
                event = JobEvent(
                    scan_job_id=job_id,
                    event_type="JOB_STATUS_CHANGED",
                    sequence_number=seq,
                    payload={
                        "previous_status": current_status.value,
                        "new_status": target_status.value,
                        "partial_attempts_count": partial_count,
                    },
                )
                self.event_repo.append_event(event)

            return job

    async def finalize_eligible_stuck_jobs(self, limit: int = 50) -> int:
        """Find and finalize RUNNING or CANCELLING jobs that have 0 nonterminal ScanURL child rows.

        Runs inside a bounded transaction, locking jobs FOR UPDATE SKIP LOCKED.
        Returns the number of jobs finalized.
        """
        finalized_count = 0
        async with self.session.begin():
            subq = select(ScanURL.id).where(
                ScanURL.scan_job_id == ScanJob.id,
                ScanURL.status.in_(
                    [
                        ScanURLStatus.PENDING.value,
                        ScanURLStatus.QUEUED.value,
                        ScanURLStatus.SCANNING.value,
                        ScanURLStatus.RETRY_WAIT.value,
                    ]
                ),
            )
            stmt = (
                select(ScanJob)
                .where(
                    ScanJob.status.in_(
                        [ScanJobStatus.RUNNING.value, ScanJobStatus.CANCELLING.value]
                    ),
                    ~subq.exists(),
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
            res = await self.session.execute(stmt)
            eligible_jobs = list(res.scalars().all())

            for job in eligible_jobs:
                st_counts_stmt = (
                    select(ScanURL.status, func.count(ScanURL.id))
                    .where(ScanURL.scan_job_id == job.id)
                    .group_by(ScanURL.status)
                )
                res_st = await self.session.execute(st_counts_stmt)
                counts = dict(res_st.tuples().all())

                completed_actual = counts.get(ScanURLStatus.COMPLETED.value, 0) + counts.get(
                    ScanURLStatus.NO_EMAIL.value, 0
                )
                failed_actual = counts.get(ScanURLStatus.FAILED.value, 0) + counts.get(
                    ScanURLStatus.INVALID.value, 0
                )

                job.queued_count = 0
                job.running_count = 0
                job.completed_count = completed_actual
                job.failed_count = failed_actual

                partial_stmt = (
                    select(func.count(CrawlAttempt.id))
                    .join(ScanURL, CrawlAttempt.scan_url_id == ScanURL.id)
                    .where(
                        ScanURL.scan_job_id == job.id,
                        CrawlAttempt.outcome == "PARTIAL",
                    )
                )
                res_partial = await self.session.execute(partial_stmt)
                partial_count = res_partial.scalar_one() or 0

                cur_st = ScanJobStatus(job.status)
                if cur_st == ScanJobStatus.CANCELLING:
                    target_st = ScanJobStatus.CANCELLED
                elif failed_actual == 0 and partial_count == 0:
                    target_st = ScanJobStatus.COMPLETED
                elif completed_actual == 0 and failed_actual == job.valid_input_count:
                    target_st = ScanJobStatus.FAILED
                else:
                    target_st = ScanJobStatus.COMPLETED_WITH_ERRORS

                job.status = target_st.value
                if job.completed_at is None:
                    job.completed_at = utc_now()

                seq = await self.job_repo.allocate_event_sequence(job.organization_id, job.id)
                if seq is not None:
                    event = JobEvent(
                        scan_job_id=job.id,
                        event_type="JOB_STATUS_CHANGED",
                        sequence_number=seq,
                        payload={
                            "previous_status": cur_st.value,
                            "new_status": target_st.value,
                            "partial_attempts_count": partial_count,
                        },
                    )
                    self.event_repo.append_event(event)

                finalized_count += 1

        return finalized_count

    async def reconcile_and_recover_stuck_job(
        self, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> ScanJob | None:
        """Tenant-safe, idempotent maintenance method to reconcile counters and finalize stuck jobs.

        Guarantees:
            1. Recovers expired leases using CrawlWorkService.
            2. Reconciles persisted ScanJob counters against actual ScanURL rows.
            3. Cancels leftover nonterminal URLs if job is in CANCELLING state.
            4. Authoritatively attempts job finalization.
        """
        from email_discovery_api.services.crawl_work import CrawlWorkService

        # 1. Recover expired leases
        crawl_work = CrawlWorkService(self.session)
        await crawl_work.recover_expired_leases()

        async with self.session.begin():
            job = await self.job_repo.get_job_for_update(organization_id, job_id)
            if job is None:
                return None

            # 2. Count actual ScanURL rows grouped by status
            st_counts_stmt = (
                select(ScanURL.status, func.count(ScanURL.id))
                .where(ScanURL.scan_job_id == job_id)
                .group_by(ScanURL.status)
            )
            res = await self.session.execute(st_counts_stmt)
            counts = dict(res.tuples().all())

            queued_actual = counts.get(ScanURLStatus.QUEUED.value, 0) + counts.get(
                ScanURLStatus.RETRY_WAIT.value, 0
            )
            running_actual = counts.get(ScanURLStatus.SCANNING.value, 0)
            completed_actual = counts.get(ScanURLStatus.COMPLETED.value, 0) + counts.get(
                ScanURLStatus.NO_EMAIL.value, 0
            )
            failed_actual = counts.get(ScanURLStatus.FAILED.value, 0) + counts.get(
                ScanURLStatus.INVALID.value, 0
            )

            # If CANCELLING or CANCELLED, clean up leftover nonterminal rows
            if job.status in (ScanJobStatus.CANCELLING.value, ScanJobStatus.CANCELLED.value):
                stmt_cancel_leftovers = (
                    update(ScanURL)
                    .where(
                        ScanURL.scan_job_id == job_id,
                        ScanURL.status.in_(
                            [
                                ScanURLStatus.PENDING.value,
                                ScanURLStatus.QUEUED.value,
                                ScanURLStatus.RETRY_WAIT.value,
                            ]
                        ),
                    )
                    .values(
                        status=ScanURLStatus.CANCELLED.value,
                        completed_at=func.clock_timestamp(),
                        last_error_code="JOB_CANCELLED",
                        last_error_message="Cancelled by job cancellation recovery.",
                    )
                )
                await self.session.execute(stmt_cancel_leftovers)
                queued_actual = 0

            # Reconcile counters
            job.queued_count = queued_actual
            job.running_count = running_actual
            job.completed_count = completed_actual
            job.failed_count = failed_actual

        # 3. Attempt finalization
        await self.try_finalize_job(organization_id, job_id)
        return await self.job_repo.get_job(organization_id, job_id)

    async def get_job(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> ScanJob:
        """Fetch a job strictly scoped to tenant or raise JOB_NOT_FOUND."""
        job = await self.job_repo.get_job(organization_id, job_id)
        if job is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                f"Scan job {job_id} was not found for organization {organization_id}.",
            )
        return job

    async def list_jobs(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[ScanJob], str | None]:
        """List jobs tenant-scoped with keyset pagination returning items and next_cursor."""
        fetch_limit = limit + 1
        jobs = await self.job_repo.list_jobs(
            organization_id,
            limit=fetch_limit,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            status=status,
        )

        next_cursor: str | None = None
        if len(jobs) > limit:
            has_more = jobs[:limit]
            last_item = has_more[-1]
            next_cursor = encode_cursor(
                "jobs", [last_item.created_at.isoformat(), str(last_item.id)]
            )
            return has_more, next_cursor

        return jobs, None

    async def list_job_urls(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        limit: int = 100,
        cursor_index: int | None = None,
        cursor_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[ScanURL], str | None]:
        """List job URLs tenant-scoped with keyset pagination returning items and next_cursor."""
        await self.get_job(organization_id, job_id)

        fetch_limit = limit + 1
        urls = await self.url_repo.list_job_urls(
            organization_id,
            job_id,
            limit=fetch_limit,
            cursor_index=cursor_index,
            cursor_id=cursor_id,
            status=status,
        )

        next_cursor: str | None = None
        if len(urls) > limit:
            has_more = urls[:limit]
            last_item = has_more[-1]
            next_cursor = encode_cursor("urls", [last_item.original_index, str(last_item.id)])
            return has_more, next_cursor

        return urls, None

    async def list_job_events(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        limit: int = 100,
        cursor_seq: int | None = None,
        cursor_id: uuid.UUID | None = None,
    ) -> tuple[list[JobEvent], str | None]:
        """List job events tenant-scoped with keyset pagination returning items and next_cursor."""
        await self.get_job(organization_id, job_id)

        fetch_limit = limit + 1
        events = await self.event_repo.list_job_events(
            organization_id,
            job_id,
            limit=fetch_limit,
            cursor_seq=cursor_seq,
            cursor_id=cursor_id,
        )

        next_cursor: str | None = None
        if len(events) > limit:
            has_more = events[:limit]
            last_item = has_more[-1]
            next_cursor = encode_cursor("events", [last_item.sequence_number, str(last_item.id)])
            return has_more, next_cursor

        return events, None

    async def transition_job_status(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        new_status: ScanJobStatus,
    ) -> ScanJob:
        """Perform a validated status transition with strict idempotency and event logging."""
        if new_status == ScanJobStatus.QUEUED:
            return await self.queue_job(organization_id, job_id)
        elif new_status in (ScanJobStatus.CANCELLING, ScanJobStatus.CANCELLED):
            return await self.cancel_job(organization_id, job_id)

        async with self.session.begin():
            job = await self.job_repo.get_job(organization_id, job_id)
            if job is None:
                raise ServiceError(
                    ServiceErrorCode.JOB_NOT_FOUND,
                    f"Scan job {job_id} was not found for organization {organization_id}.",
                )

            current_status = ScanJobStatus(job.status)
            if current_status == new_status:
                return job

            if current_status in TERMINAL_STATUSES:
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"Job {job_id} is in terminal state {current_status.value}.",
                )

            allowed = ALLOWED_STATE_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"Transition from {current_status.value} to {new_status.value} not allowed.",
                )

            now = utc_now()
            started_at = now if new_status == ScanJobStatus.RUNNING else None
            completed_at = now if new_status in TERMINAL_STATUSES else None
            cancellation_requested_at = None

            updated = await self.job_repo.update_job_status_conditional(
                organization_id,
                job_id,
                expected_status=current_status.value,
                new_status=new_status.value,
                started_at=started_at,
                completed_at=completed_at,
                cancellation_requested_at=cancellation_requested_at,
            )

            if not updated:
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"Concurrent state update detected for job {job_id}.",
                )

            seq = await self.job_repo.allocate_event_sequence(organization_id, job_id)
            assert seq is not None

            event = JobEvent(
                id=uuid.uuid4(),
                scan_job_id=job_id,
                event_type="JOB_STATUS_CHANGED",
                sequence_number=seq,
                payload={
                    "previous_status": current_status.value,
                    "new_status": new_status.value,
                },
            )
            self.event_repo.append_event(event)

            updated_job = await self.job_repo.get_job(organization_id, job_id)
            assert updated_job is not None
            return updated_job

    async def get_job_progress(
        self, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> ScanJobProgress:
        """Fetch derived progress response for job using persisted database counters."""
        job = await self.get_job(organization_id, job_id)
        return ScanJobProgress.from_counts(
            job_id=job.id,
            status=ScanJobStatus(job.status),
            total_input_count=job.total_input_count,
            valid_input_count=job.valid_input_count,
            duplicate_input_count=job.duplicate_input_count,
            queued_count=job.queued_count,
            running_count=job.running_count,
            completed_count=job.completed_count,
            failed_count=job.failed_count,
            email_finding_count=job.email_finding_count,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
