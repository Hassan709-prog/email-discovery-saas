"""Transactional service for persisting scan results and fenced cancellations."""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

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
from email_discovery_api.models.scan_job import ScanJob
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
from email_discovery_api.services.worker_contracts import (
    FencedCancellationResult,
    LeaseLostError,
    URLClaim,
)
from email_scanner.errors import PageScanOutcome, SiteScanOutcome
from email_scanner.models import SiteScanResult


def _sanitize_diagnostic_message(text: str | None, max_length: int = 500) -> str | None:
    """Sanitize and bound diagnostic error message.

    Truncates stack traces, strips query strings and credentials from URLs,
    redacts sensitive key-value/header patterns, and normalizes whitespace.
    """
    if not text:
        return None

    # 1. Truncate at first stack trace marker if present
    for marker in ("Traceback (most recent call last):", 'File "', "File '"):
        if marker in text:
            text = text.split(marker)[0]

    # 2. Sanitize any embedded HTTP/HTTPS URLs to remove query params, fragments & userinfo
    def _clean_url_match(match: re.Match[str]) -> str:
        return sanitize_url(match.group(0))

    text = re.sub(r"https?://[^\s'\"]+", _clean_url_match, text)

    # 3. Redact secret / credential / authorization header patterns
    text = re.sub(
        r"(?i)\b(authorization|cookie|token|api[_-]?key|password|secret|pwd|connect_string|connection_string)\b\s*[:=]\s*(?:bearer\s+)?[^\s;,]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[^\s;,]+", "Bearer [REDACTED]", text)

    # 4. Standard text sanitization (control characters, whitespace, max length)
    return sanitize_text(text, max_length=max_length)


def _derive_diagnostic_message(
    site_scan_result: SiteScanResult,
    max_length: int = 500,
) -> str | None:
    """Derive a safe, bounded diagnostic message for ScanURL.last_error_message.

    Top-level site_scan_result.error_message takes highest priority if present.
    Otherwise, derives from the first relevant failed page using:
      - For ROBOTS_DISALLOWED / ROBOTS_TEMPORARY_FAILURE:
          1. robots_decision.reason
          2. page.error_message
      - For ordinary fetch/other failures:
          1. fetch_result.error_message
          2. fetch_result.status_code / page.status_code (as "HTTP <status>")
          3. page.error_message
    """
    if site_scan_result.outcome in (
        SiteScanOutcome.COMPLETED,
        SiteScanOutcome.COMPLETED_NO_EMAILS,
    ):
        return None

    if site_scan_result.error_message and site_scan_result.error_message.strip():
        return _sanitize_diagnostic_message(site_scan_result.error_message, max_length=max_length)

    for page in site_scan_result.page_records:
        candidate: str | None = None

        if page.outcome in (
            PageScanOutcome.ROBOTS_DISALLOWED,
            PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        ):
            if (
                page.robots_decision
                and page.robots_decision.reason
                and page.robots_decision.reason.strip()
            ):
                candidate = page.robots_decision.reason
            elif page.error_message and page.error_message.strip():
                candidate = page.error_message
        else:
            if (
                page.fetch_result
                and page.fetch_result.error_message
                and page.fetch_result.error_message.strip()
            ):
                candidate = page.fetch_result.error_message
            elif page.fetch_result and page.fetch_result.status_code is not None:
                candidate = f"HTTP {page.fetch_result.status_code}"
            elif page.status_code is not None:
                candidate = f"HTTP {page.status_code}"
            elif page.error_message and page.error_message.strip():
                candidate = page.error_message

        if candidate:
            sanitized = _sanitize_diagnostic_message(candidate, max_length=max_length)
            if sanitized:
                return sanitized

    return None


def map_outcome_to_url_status(
    outcome: SiteScanOutcome,
    email_findings_count: int,
    failure_code: str | None = None,
) -> tuple[ScanURLStatus, str | None]:
    """Pure mapping of scanner outcome to terminal ScanURLStatus and error_code."""
    if outcome in (SiteScanOutcome.COMPLETED, SiteScanOutcome.COMPLETED_NO_EMAILS):
        if email_findings_count > 0:
            return ScanURLStatus.COMPLETED, None
        return ScanURLStatus.NO_EMAIL, None
    if outcome == SiteScanOutcome.PARTIAL:
        return ScanURLStatus.COMPLETED, "PARTIAL_SCAN"
    if outcome == SiteScanOutcome.ROBOTS_BLOCKED:
        if failure_code in (
            "ROBOTS_TEMPORARY_FAILURE",
            "ROBOTS_FETCH_ERROR",
            "TRANSPORT_ERROR",
            "DNS_RESOLUTION_FAILED",
            "CONNECT_TIMEOUT",
            "READ_TIMEOUT",
            "GENERIC_TIMEOUT",
        ):
            return ScanURLStatus.FAILED, "ROBOTS_FETCH_ERROR"
        return ScanURLStatus.FAILED, "ROBOTS_BLOCKED"
    if outcome == SiteScanOutcome.CANCELLED:
        return ScanURLStatus.CANCELLED, "JOB_CANCELLED"
    return ScanURLStatus.FAILED, failure_code or "SCAN_FAILED"


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
            2. Locks parent ScanJob FOR UPDATE first before ScanURL.
            3. Never creates or repairs a lease; requires active SCANNING status with valid owner.
            4. Never extends lease expiry or alters attempt_count.
            5. Does not swallow exceptions.
        """
        async with self._session.begin():
            # Acquire parent job lock first to maintain canonical lock order ScanJob -> ScanURL
            await self._scan_job_repo.get_job_for_update(organization_id, job_id)

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

    async def _execute_fenced_cancellation_in_transaction(
        self,
        claim: URLClaim,
        job: ScanJob | None,
        current_time: datetime,
    ) -> bool:
        """Execute strict fenced URL cancellation and job counter adjustment inside transaction.

        Enforces strict 6-predicate fencing check in SQL:
            - ScanURL.id == claim.scan_url_id
            - ScanURL.status == 'SCANNING'
            - ScanURL.lease_owner == claim.lease_owner
            - ScanURL.fence_token == claim.fence_token
            - ScanURL.attempt_count == claim.attempt_count
            - ScanURL.lease_expires_at > clock_timestamp()

        If fencing check affects 0 rows, raises LeaseLostError (rolling back T1).
        If fencing check affects 1 row:
            - sets status = 'CANCELLED'
            - sets completed_at = current_time
            - clears lease and claim-origin fields
            - clears attempt-start fields
            - sets last_error_code="JOB_CANCELLED", last_error_message="..."
            - decrements job.running_count exactly once (if job is present and running_count > 0)
            - does not increment failed_count
            - returns True
        """
        stmt_cancel = (
            update(ScanURL)
            .where(
                ScanURL.id == claim.scan_url_id,
                ScanURL.status == ScanURLStatus.SCANNING.value,
                ScanURL.lease_owner == claim.lease_owner,
                ScanURL.fence_token == claim.fence_token,
                ScanURL.attempt_count == claim.attempt_count,
                ScanURL.lease_expires_at > func.clock_timestamp(),
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
                last_error_message="Scan URL cancelled by user job cancellation request.",
            )
        )
        res = await self._session.execute(stmt_cancel)
        if int(getattr(res, "rowcount", 0)) == 0:
            raise LeaseLostError(claim.scan_url_id, claim.lease_owner, claim.attempt_count)

        if (
            job is not None
            and getattr(job, "running_count", None) is not None
            and job.running_count > 0
        ):
            job.running_count = job.running_count - 1

        return True

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
            # Parent job cancelled: execute fenced URL cancellation without inserting artifacts
            await self._execute_fenced_cancellation_in_transaction(
                claim=claim, job=job, current_time=current_time
            )
            return CrawlAttemptResult(attempt=None, is_replay=False, is_cancelled=True)

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
            site_scan_result.outcome,
            len(mapped_findings),
            mapped_attempt.failure_code,
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

        redirect_target_domain = None
        redirect_target_url = None
        if (
            err_code in ("OUT_OF_SCOPE_REDIRECT", "BUSINESS_DOMAIN_REDIRECT_REVIEW")
            or mapped_attempt.failure_code == "OUT_OF_SCOPE_REDIRECT"
        ):
            if mapped_attempt.final_url:
                redirect_target_url = mapped_attempt.final_url
                parsed = urlsplit(mapped_attempt.final_url)
                redirect_target_domain = parsed.hostname

        if site_scan_result.outcome in (
            SiteScanOutcome.COMPLETED,
            SiteScanOutcome.COMPLETED_NO_EMAILS,
        ):
            last_error_code_val = None
            last_error_message_val = None
            last_failure_code_val = None
        else:
            last_error_code_val = err_code
            last_error_message_val = _derive_diagnostic_message(
                site_scan_result, max_length=self._policy.max_error_message_length
            )
            last_failure_code_val = diag.failure_code if diag else None

        stmt_url_final = (
            update(ScanURL)
            .where(ScanURL.id == claim.scan_url_id)
            .values(
                status=target_status.value,
                completed_at=current_time,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=last_error_code_val,
                last_error_message=last_error_message_val,
                total_duration_seconds=diag.total_duration_seconds if diag else None,
                pages_attempted=stats.pages_attempted if stats else None,
                pages_fetched=stats.pages_fetched if stats else None,
                retry_count=diag.retry_count if diag else None,
                last_failure_code=last_failure_code_val,
                redirect_target_domain=redirect_target_domain,
                redirect_target_url=redirect_target_url,
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
        site_scan_result: SiteScanResult | None = None,
    ) -> CrawlAttemptResult:
        """Record transient failure CrawlAttempt and schedule RETRY_WAIT or mark FAILED.

        Enforces strict fencing requirement: lease_expires_at > clock_timestamp().
        Uses injectable RetryBackoffPolicy and claim.max_attempts.
        """
        current_time = now or datetime.now(UTC)
        sanitized_requested_url = sanitize_url(claim.original_input)
        sanitized_err_msg = _sanitize_diagnostic_message(
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
            # 0. Acquire parent job lock first to maintain canonical lock order ScanJob -> ScanURL
            job = await self._scan_job_repo.get_job_for_update(claim.organization_id, claim.job_id)

            cancelling_statuses = (ScanJobStatus.CANCELLING.value, ScanJobStatus.CANCELLED.value)
            if job and job.status in cancelling_statuses:
                # Parent job cancelled: execute fenced URL cancellation without recording attempt
                await self._execute_fenced_cancellation_in_transaction(
                    claim=claim, job=job, current_time=current_time
                )
                return CrawlAttemptResult(attempt=None, is_replay=False, is_cancelled=True)

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
                    ScanURL.fence_token == claim.fence_token,
                    ScanURL.attempt_count == claim.attempt_count,
                    ScanURL.lease_expires_at > func.clock_timestamp(),
                )
                .values(updated_at=func.clock_timestamp())
            )
            fence_res = await self._session.execute(stmt_fence)
            if int(getattr(fence_res, "rowcount", 0)) == 0:
                raise LeaseLostError(claim.scan_url_id, claim.lease_owner, claim.attempt_count)

            if site_scan_result is not None:
                mapped_attempt, _, _, _, _ = map_site_scan_result(
                    site_scan_result=site_scan_result,
                    attempt_number=claim.attempt_count,
                    now=current_time,
                    policy=self._policy,
                )
                mapped_attempt = replace(
                    mapped_attempt,
                    outcome="FAILED",
                    retryable=retryable,
                    error_code=error_code,
                    error_message=sanitized_err_msg,
                    result_checksum=checksum,
                )
            else:
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
                    attempt_started_at=None,
                    attempt_started_fence_token=None,
                    last_error_code=error_code,
                    last_error_message=sanitized_err_msg,
                    total_duration_seconds=mapped_attempt.elapsed_seconds,
                    pages_attempted=(
                        site_scan_result.statistics.pages_attempted
                        if site_scan_result is not None
                        else None
                    ),
                    pages_fetched=(
                        site_scan_result.statistics.pages_fetched
                        if site_scan_result is not None
                        else None
                    ),
                    retry_count=(
                        site_scan_result.diagnostics.retry_count
                        if site_scan_result is not None
                        and site_scan_result.diagnostics is not None
                        else None
                    ),
                    last_failure_code=mapped_attempt.failure_code,
                    completed_at=current_time if not retryable else None,
                    next_retry_at=(func.clock_timestamp() + timedelta(seconds=delay_seconds))
                    if retryable
                    else None,
                )
            )
            await self._session.execute(stmt_url_update)

            if job is not None and getattr(job, "running_count", None) is not None:
                if job.running_count > 0:
                    job.running_count = job.running_count - 1

                if retryable:
                    job.queued_count = job.queued_count + 1
                else:
                    job.failed_count = job.failed_count + 1

            return CrawlAttemptResult(attempt=attempt_obj, is_replay=False)

    async def persist_fenced_cancellation(
        self, claim: URLClaim, now: datetime | None = None
    ) -> FencedCancellationResult:
        """Persist fenced URL cancellation during active scan when cancellation requested.

        Guarantees:
            1. Locks parent ScanJob FOR UPDATE first.
            2. Verifies parent ScanJob exists and is in CANCELLING or CANCELLED status.
            3. Verifies strict 6-predicate fencing check in SQL (including owner and fence).
            4. Transitions ScanURL to CANCELLED and clears lease/claim fields.
            5. Decrements running_count on ScanJob exactly once without incrementing failed_count.
            6. Returns explicit typed FencedCancellationResult(cancelled=True).
        """
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            job = await self._scan_job_repo.get_job_for_update(claim.organization_id, claim.job_id)
            if job is None:
                raise ServiceError(
                    ServiceErrorCode.JOB_NOT_FOUND,
                    f"ScanJob {claim.job_id} not found.",
                )
            if job.status not in (ScanJobStatus.CANCELLING.value, ScanJobStatus.CANCELLED.value):
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"ScanJob {claim.job_id} status {job.status} is not CANCELLING/CANCELLED.",
                )
            await self._execute_fenced_cancellation_in_transaction(
                claim=claim, job=job, current_time=current_time
            )
            return FencedCancellationResult(cancelled=True, scan_url_id=claim.scan_url_id)
