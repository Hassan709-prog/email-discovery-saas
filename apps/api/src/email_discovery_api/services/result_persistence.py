"""Transactional service for persisting scan results and fenced cancellations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.mappers.crawl_results import (
    CrawlAttemptResult,
    MappedAttempt,
    compute_transient_attempt_checksum,
    map_site_scan_result,
    sanitize_text,
    sanitize_url,
)
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.job_event import JobEvent
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.repositories.crawl_results import (
    CrawlAttemptRepository,
    CrawledPageRepository,
    EmailEvidenceRepository,
    EmailFindingRepository,
    RejectedCandidateRepository,
)
from email_discovery_api.repositories.job_events import JobEventRepository
from email_discovery_api.repositories.scan_jobs import ScanJobRepository
from email_discovery_api.repositories.scan_urls import ScanURLRepository
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.result_policies import ResultPersistencePolicy
from email_discovery_api.services.retry_policy import RetryBackoffPolicy
from email_discovery_api.services.worker_contracts import LeaseLostError, URLClaim
from email_scanner.errors import SiteScanOutcome
from email_scanner.models import SiteScanResult


def map_outcome_to_url_status(
    outcome: SiteScanOutcome, email_findings_count: int
) -> tuple[ScanURLStatus, str | None]:
    """Pure mapping of scanner outcome to terminal ScanURLStatus and error_code."""
    if outcome in (SiteScanOutcome.COMPLETED, SiteScanOutcome.COMPLETED_NO_EMAILS):
        if email_findings_count > 0:
            return ScanURLStatus.COMPLETED, None
        return ScanURLStatus.NO_EMAIL, None
    if outcome == SiteScanOutcome.PARTIAL:
        return ScanURLStatus.COMPLETED, "PARTIAL_SCAN"
    if outcome == SiteScanOutcome.ROBOTS_BLOCKED:
        return ScanURLStatus.FAILED, "ROBOTS_BLOCKED"
    if outcome == SiteScanOutcome.CANCELLED:
        return ScanURLStatus.CANCELLED, "JOB_CANCELLED"
    return ScanURLStatus.FAILED, "SCAN_FAILED"


class ResultPersistenceService:
    """Service owning single transaction boundaries for persisting crawl scan results."""

    def __init__(
        self,
        session: AsyncSession,
        policy: ResultPersistencePolicy | None = None,
        retry_policy: RetryBackoffPolicy | None = None,
    ) -> None:
        self._session = session
        self._policy = policy or ResultPersistencePolicy()
        self._retry_policy = retry_policy or RetryBackoffPolicy()
        self._scan_job_repo = ScanJobRepository(session)
        self._scan_url_repo = ScanURLRepository(session)
        self._event_repo = JobEventRepository(session)
        self._attempt_repo = CrawlAttemptRepository(session)
        self._page_repo = CrawledPageRepository(session)
        self._finding_repo = EmailFindingRepository(session)
        self._evidence_repo = EmailEvidenceRepository(session)
        self._rejected_repo = RejectedCandidateRepository(session)

    async def persist_site_scan_result(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        scan_url_id: uuid.UUID,
        attempt_number: int,
        site_scan_result: SiteScanResult,
        now: datetime | None = None,
    ) -> CrawlAttemptResult:
        """Deprecated adapter for persisting scan results using a pre-existing active lease.

        Guarantees:
            1. Runs inside exactly ONE transaction.
            2. Never creates or repairs a lease; requires active SCANNING status with valid owner.
            3. Never extends lease expiry or alters attempt_count.
            4. Does not swallow exceptions.
        """
        async with self._session.begin():
            raw_url = await self._scan_url_repo.get_url_for_update(
                organization_id=organization_id, job_id=job_id, scan_url_id=scan_url_id
            )
            if raw_url is None:
                raise ServiceError(
                    ServiceErrorCode.INVALID_RESULT_STATE,
                    f"ScanURL {scan_url_id} not found.",
                )

            if raw_url.status != ScanURLStatus.SCANNING.value:
                raise ServiceError(
                    ServiceErrorCode.INVALID_RESULT_STATE,
                    f"ScanURL {scan_url_id} is in status {raw_url.status}, expected SCANNING.",
                )

            if not raw_url.lease_owner:
                raise ServiceError(
                    ServiceErrorCode.INVALID_RESULT_STATE,
                    f"ScanURL {scan_url_id} has no active lease owner.",
                )

            current_time = now or datetime.now(UTC)
            if raw_url.lease_expires_at is not None and raw_url.lease_expires_at <= current_time:
                raise ServiceError(
                    ServiceErrorCode.LEASE_LOST,
                    f"ScanURL {scan_url_id} lease expired at {raw_url.lease_expires_at}.",
                )

            claim = URLClaim(
                scan_url_id=scan_url_id,
                organization_id=organization_id,
                job_id=job_id,
                original_input=raw_url.original_input,
                normalized_url=raw_url.normalized_url,
                normalized_domain=raw_url.normalized_domain,
                lease_owner=raw_url.lease_owner,
                fence_token=raw_url.fence_token,
                attempt_count=raw_url.attempt_count,
                max_attempts=raw_url.max_attempts,
                lease_expires_at=raw_url.lease_expires_at
                or (current_time + timedelta(seconds=120)),
            )

            return await self._persist_fenced_result_in_transaction(
                claim=claim, site_scan_result=site_scan_result, now=now
            )

    async def persist_fenced_result(
        self,
        claim: URLClaim,
        site_scan_result: SiteScanResult,
        now: datetime | None = None,
    ) -> CrawlAttemptResult:
        """Persist scanner SiteScanResult using strict lease_owner + attempt_count fencing.

        Fencing predicate enforced in SQL:
            status = 'SCANNING'
            AND lease_owner = claim.lease_owner
            AND attempt_count = claim.attempt_count
            AND lease_expires_at > clock_timestamp()

        Raises LeaseLostError if fencing check fails.
        """
        async with self._session.begin():
            return await self._persist_fenced_result_in_transaction(
                claim=claim, site_scan_result=site_scan_result, now=now
            )

    async def _persist_fenced_result_in_transaction(
        self,
        claim: URLClaim,
        site_scan_result: SiteScanResult,
        now: datetime | None = None,
    ) -> CrawlAttemptResult:
        """Internal helper executing persistence within an active transaction."""
        current_time = now or datetime.now(UTC)
        self._policy.validate_site_scan_result(site_scan_result)

        attempt_number = claim.attempt_count
        assert attempt_number >= 1, (
            f"Authoritative attempt_number must be >= 1, got {attempt_number}"
        )

        (
            mapped_attempt,
            mapped_pages,
            mapped_findings,
            mapped_evidence,
            mapped_rejected,
        ) = map_site_scan_result(
            site_scan_result=site_scan_result,
            attempt_number=attempt_number,
            now=current_time,
            policy=self._policy,
        )

        # Check existing attempt for idempotent replay
        existing_attempt = await self._attempt_repo.get_by_scan_url_and_attempt(
            scan_url_id=claim.scan_url_id, attempt_number=claim.attempt_count
        )
        if existing_attempt:
            if existing_attempt.result_checksum == mapped_attempt.result_checksum:
                return CrawlAttemptResult(attempt=existing_attempt, is_replay=True)
            raise ServiceError(
                ServiceErrorCode.RESULT_CONFLICT,
                f"ScanURL attempt {claim.attempt_count} exists with different result checksum",
            )

        # 0. Acquire job lock first to prevent lock order deadlocks during FK checks
        job = await self._scan_job_repo.get_job_for_update(claim.organization_id, claim.job_id)

        if job and job.status in (ScanJobStatus.CANCELLING.value, ScanJobStatus.CANCELLED.value):
            # Parent job is cancelled: update ScanURL to CANCELLED without inserting findings
            stmt_cancel = (
                update(ScanURL)
                .where(
                    ScanURL.id == claim.scan_url_id,
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    ScanURL.lease_owner == claim.lease_owner,
                    ScanURL.fence_token == claim.fence_token,
                )
                .values(
                    status=ScanURLStatus.CANCELLED.value,
                    completed_at=current_time,
                    lease_owner=None,
                    lease_expires_at=None,
                    claimed_from_status=None,
                    claimed_from_next_retry_at=None,
                    attempt_started_at=None,
                    attempt_started_fence_token=None,
                    last_error_code="JOB_CANCELLED",
                )
            )
            await self._session.execute(stmt_cancel)
            raise LeaseLostError(claim.scan_url_id, claim.lease_owner, claim.fence_token)

        # Strict 5-predicate fencing check requiring lease_expires_at > clock_timestamp()
        stmt_check = (
            update(ScanURL)
            .where(
                ScanURL.id == claim.scan_url_id,
                ScanURL.status == ScanURLStatus.SCANNING.value,
                ScanURL.lease_owner == claim.lease_owner,
                ScanURL.fence_token == claim.fence_token,
                ScanURL.lease_expires_at > func.clock_timestamp(),
            )
            .values(updated_at=func.clock_timestamp())
        )
        fence_res = await self._session.execute(stmt_check)
        if int(getattr(fence_res, "rowcount", 0)) == 0:
            raise LeaseLostError(claim.scan_url_id, claim.lease_owner, claim.fence_token)

        target_status, err_code = map_outcome_to_url_status(
            site_scan_result.outcome, len(mapped_findings)
        )

        # 1. Save CrawlAttempt
        attempt_obj = await self._attempt_repo.create(claim.scan_url_id, mapped_attempt)

        # 2. Save CrawledPages
        page_id_map = await self._page_repo.create_many(
            crawl_attempt_id=attempt_obj.id,
            scan_url_id=claim.scan_url_id,
            mapped_pages=mapped_pages,
        )

        # 3. Upsert EmailFindings
        newly_inserted, existing_updated = await self._finding_repo.upsert_findings(
            job_id=claim.job_id,
            mapped_findings=mapped_findings,
            now=current_time,
            scan_url_id=claim.scan_url_id,
        )
        new_findings_count = len(newly_inserted)

        finding_id_map: dict[str, uuid.UUID] = {f.canonical_email: f.id for f in newly_inserted}
        finding_id_map.update({f.canonical_email: f.id for f in existing_updated})

        # 4. Insert EmailEvidence idempotently
        inserted_evidence = await self._evidence_repo.add_evidence(
            mapped_evidence=mapped_evidence,
            finding_id_map=finding_id_map,
            page_id_map=page_id_map,
        )

        evidence_counts: dict[uuid.UUID, int] = {}
        for ev in inserted_evidence:
            evidence_counts[ev.email_finding_id] = evidence_counts.get(ev.email_finding_id, 0) + 1

        for finding_id, added_count in evidence_counts.items():
            await self._finding_repo.increment_evidence_count(finding_id, added_count)

        # 5. Add bounded RejectedEmailCandidates
        await self._rejected_repo.add_rejected_candidates(
            job_id=claim.job_id,
            scan_url_id=claim.scan_url_id,
            page_id_map=page_id_map,
            mapped_rejected=mapped_rejected,
        )

        # 6. Update ScanURL to target terminal status and clear lease
        diag = getattr(site_scan_result, "diagnostics", None)
        stats = getattr(site_scan_result, "statistics", None)

        stmt_url_final = (
            update(ScanURL)
            .where(ScanURL.id == claim.scan_url_id)
            .values(
                status=target_status.value,
                completed_at=current_time,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=err_code,
                last_error_message=site_scan_result.error_message,
                total_duration_seconds=diag.total_duration_seconds if diag else None,
                pages_attempted=stats.pages_attempted if stats else None,
                pages_fetched=stats.pages_fetched if stats else None,
                retry_count=diag.retry_count if diag else None,
                last_failure_code=diag.failure_code if diag else None,
            )
        )
        await self._session.execute(stmt_url_final)

        # 7. Counter updates on ScanJob
        if job is not None and getattr(job, "running_count", None) is not None:
            if job.running_count > 0:
                job.running_count = job.running_count - 1

            if target_status in (ScanURLStatus.COMPLETED, ScanURLStatus.NO_EMAIL):
                job.completed_count = job.completed_count + 1
            elif target_status == ScanURLStatus.FAILED:
                job.failed_count = job.failed_count + 1

            if new_findings_count > 0:
                job.email_finding_count = job.email_finding_count + new_findings_count

            seq = await self._scan_job_repo.allocate_event_sequence(
                claim.organization_id, claim.job_id
            )
            event_type = (
                "SCAN_URL_COMPLETED" if target_status != ScanURLStatus.FAILED else "SCAN_URL_FAILED"
            )
            if seq is not None:
                job_event = JobEvent(
                    scan_job_id=claim.job_id,
                    scan_url_id=claim.scan_url_id,
                    sequence_number=seq,
                    event_type=event_type,
                    payload={
                        "attempt_number": claim.attempt_count,
                        "scan_url_id": str(claim.scan_url_id),
                        "target_status": target_status.value,
                        "total_findings": len(mapped_findings),
                        "new_findings": new_findings_count,
                    },
                )
                self._event_repo.append_event(job_event)

        return CrawlAttemptResult(attempt=attempt_obj, is_replay=False)

    async def persist_transient_failure(
        self,
        claim: URLClaim,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> CrawlAttemptResult:
        """Record transient failure CrawlAttempt and schedule RETRY_WAIT or mark FAILED.

        Enforces strict fencing requirement: lease_expires_at > clock_timestamp().
        Uses injectable RetryBackoffPolicy and claim.max_attempts.
        """
        current_time = now or datetime.now(UTC)
        sanitized_requested_url = sanitize_url(claim.original_input)
        sanitized_err_msg = sanitize_text(
            error_message, max_length=self._policy.max_error_message_length
        )

        retryable = claim.attempt_count < claim.max_attempts
        delay_seconds = self._retry_policy.compute_delay_seconds(claim.attempt_count)

        checksum = compute_transient_attempt_checksum(
            scan_url_id=claim.scan_url_id,
            attempt_number=claim.attempt_count,
            outcome="FAILED",
            error_code=error_code,
            retryable=retryable,
            requested_url=sanitized_requested_url,
        )

        async with self._session.begin():
            existing_attempt = await self._attempt_repo.get_by_scan_url_and_attempt(
                scan_url_id=claim.scan_url_id, attempt_number=claim.attempt_count
            )
            if existing_attempt:
                if existing_attempt.result_checksum == checksum:
                    return CrawlAttemptResult(attempt=existing_attempt, is_replay=True)
                raise ServiceError(
                    ServiceErrorCode.RESULT_CONFLICT,
                    f"ScanURL attempt {claim.attempt_count} exists with different checksum",
                )

            stmt_fence = (
                update(ScanURL)
                .where(
                    ScanURL.id == claim.scan_url_id,
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    ScanURL.lease_owner == claim.lease_owner,
                    ScanURL.attempt_count == claim.attempt_count,
                    ScanURL.lease_expires_at > func.clock_timestamp(),
                )
                .values(updated_at=func.clock_timestamp())
            )
            fence_res = await self._session.execute(stmt_fence)
            if int(getattr(fence_res, "rowcount", 0)) == 0:
                raise LeaseLostError(claim.scan_url_id, claim.lease_owner, claim.attempt_count)

            mapped_attempt = MappedAttempt(
                attempt_number=claim.attempt_count,
                outcome="FAILED",
                retryable=retryable,
                requested_url=sanitized_requested_url,
                final_url=sanitized_requested_url,
                status_code=None,
                error_code=error_code,
                error_message=sanitized_err_msg,
                redirect_history=None,
                connection_attempts=None,
                started_at=current_time,
                completed_at=current_time,
                elapsed_seconds=0.0,
                result_checksum=checksum,
            )
            attempt_obj = await self._attempt_repo.create(claim.scan_url_id, mapped_attempt)

            target_status = ScanURLStatus.RETRY_WAIT if retryable else ScanURLStatus.FAILED

            stmt_url_update = (
                update(ScanURL)
                .where(ScanURL.id == claim.scan_url_id)
                .values(
                    status=target_status.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error_code=error_code,
                    last_error_message=sanitized_err_msg,
                    completed_at=current_time if not retryable else None,
                    next_retry_at=(func.clock_timestamp() + timedelta(seconds=delay_seconds))
                    if retryable
                    else None,
                )
            )
            await self._session.execute(stmt_url_update)

            job = await self._scan_job_repo.get_job_for_update(claim.organization_id, claim.job_id)
            if job is not None and getattr(job, "running_count", None) is not None:
                if job.running_count > 0:
                    job.running_count = job.running_count - 1

                if retryable:
                    job.queued_count = job.queued_count + 1
                else:
                    job.failed_count = job.failed_count + 1

            return CrawlAttemptResult(attempt=attempt_obj, is_replay=False)

    async def persist_fenced_cancellation(self, claim: URLClaim) -> bool:
        """Persist fenced URL cancellation during active scan when cancellation requested.

        Guarantees:
            1. Verifies lease_expires_at > clock_timestamp().
            2. Transitions ScanURL to CANCELLED.
            3. Decrements running_count on ScanJob.
            4. DOES NOT increment failed_count.
        """
        async with self._session.begin():
            stmt_cancel = (
                update(ScanURL)
                .where(
                    ScanURL.id == claim.scan_url_id,
                    ScanURL.status == ScanURLStatus.SCANNING.value,
                    ScanURL.lease_owner == claim.lease_owner,
                    ScanURL.attempt_count == claim.attempt_count,
                    ScanURL.lease_expires_at > func.clock_timestamp(),
                )
                .values(
                    status=ScanURLStatus.CANCELLED.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=func.clock_timestamp(),
                    last_error_code="JOB_CANCELLED",
                    last_error_message="Scan URL cancelled by user job cancellation request.",
                )
            )
            res = await self._session.execute(stmt_cancel)
            if int(getattr(res, "rowcount", 0)) == 0:
                raise LeaseLostError(claim.scan_url_id, claim.lease_owner, claim.attempt_count)

            job = await self._scan_job_repo.get_job_for_update(claim.organization_id, claim.job_id)
            if (
                job is not None
                and getattr(job, "running_count", None) is not None
                and job.running_count > 0
            ):
                job.running_count = job.running_count - 1

            return True
