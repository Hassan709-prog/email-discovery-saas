"""Transactional result persistence service for scanner SiteScanResult ingestion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.mappers.crawl_results import map_site_scan_result
from email_discovery_api.models.crawl_attempt import CrawlAttempt
from email_discovery_api.models.enums import ScanURLStatus
from email_discovery_api.models.job_event import JobEvent
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
from email_scanner.models import SiteScanResult


@dataclass(frozen=True, slots=True)
class CrawlAttemptResult:
    """Result container returned by result persistence service."""

    attempt: CrawlAttempt
    is_replay: bool


def map_outcome_to_url_status(outcome: str, findings_count: int) -> ScanURLStatus:
    """Map pure scanner outcome string and findings count to target ScanURLStatus."""
    outcome_upper = outcome.strip().upper()
    if outcome_upper == "COMPLETED":
        return ScanURLStatus.COMPLETED if findings_count > 0 else ScanURLStatus.NO_EMAIL
    elif outcome_upper == "COMPLETED_NO_EMAILS":
        return ScanURLStatus.NO_EMAIL
    elif outcome_upper == "PARTIAL":
        return ScanURLStatus.COMPLETED if findings_count > 0 else ScanURLStatus.FAILED
    elif outcome_upper == "ROBOTS_BLOCKED":
        return ScanURLStatus.NO_EMAIL
    elif outcome_upper == "CANCELLED":
        return ScanURLStatus.CANCELLED
    else:
        return ScanURLStatus.FAILED


class ResultPersistenceService:
    """Service owning single transaction boundaries for persisting crawl scan results."""

    def __init__(
        self,
        session: AsyncSession,
        policy: ResultPersistencePolicy | None = None,
    ) -> None:
        self._session = session
        self._policy = policy or ResultPersistencePolicy()
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
        """Persist scanner SiteScanResult within a single database transaction.

        Guarantees:
            1. Validates tenant scope and locks ScanURL via SELECT ... FOR UPDATE.
            2. Idempotent replay returns existing attempt without duplicate rows or counter changes.
            3. Conflicting attempt replay raises RESULT_CONFLICT.
            4. EmailFinding & evidence counts increment precisely without duplicates.
            5. ScanJob counters & events trigger ONLY on initial SCANNING -> terminal transition.
        """
        current_time = now or datetime.now(UTC)

        # Validate result size limits against policy prior to database interaction
        self._policy.validate_site_scan_result(site_scan_result)

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

        async with self._session.begin():
            # 1. Lock ScanURL tenant-scoped using SELECT ... FOR UPDATE
            scan_url = await self._scan_url_repo.get_url_for_update(
                organization_id=organization_id,
                job_id=job_id,
                scan_url_id=scan_url_id,
            )
            if not scan_url:
                raise ServiceError(
                    ServiceErrorCode.SCAN_URL_NOT_FOUND,
                    f"ScanURL {scan_url_id} not found for job {job_id}",
                )

            # Check existing attempt for replay check
            existing_attempt = await self._attempt_repo.get_by_scan_url_and_attempt(
                scan_url_id=scan_url_id, attempt_number=attempt_number
            )
            if existing_attempt:
                if existing_attempt.result_checksum == mapped_attempt.result_checksum:
                    return CrawlAttemptResult(attempt=existing_attempt, is_replay=True)
                else:
                    raise ServiceError(
                        ServiceErrorCode.RESULT_CONFLICT,
                        f"ScanURL attempt {attempt_number} exists with different result checksum",
                    )

            # Enforce requirement 1: ScanURL must be in SCANNING state for first result submission
            if scan_url.status != ScanURLStatus.SCANNING.value:
                if scan_url.status == ScanURLStatus.CANCELLED.value:
                    raise ServiceError(
                        ServiceErrorCode.INVALID_RESULT_STATE,
                        f"ScanURL {scan_url_id} is CANCELLED and cannot accept results",
                    )
                raise ServiceError(
                    ServiceErrorCode.INVALID_RESULT_STATE,
                    f"ScanURL {scan_url_id} is in status {scan_url.status}, expected SCANNING",
                )

            # 2. Save CrawlAttempt
            attempt_obj = await self._attempt_repo.create(scan_url_id, mapped_attempt)

            # 3. Save CrawledPages
            page_id_map = await self._page_repo.create_many(
                crawl_attempt_id=attempt_obj.id,
                scan_url_id=scan_url_id,
                mapped_pages=mapped_pages,
            )

            # 4. Upsert EmailFindings
            newly_inserted, existing_updated = await self._finding_repo.upsert_findings(
                job_id=job_id,
                mapped_findings=mapped_findings,
                now=current_time,
            )
            new_findings_count = len(newly_inserted)

            # Build map of canonical_email -> finding_id
            finding_id_map: dict[str, uuid.UUID] = {f.canonical_email: f.id for f in newly_inserted}
            finding_id_map.update({f.canonical_email: f.id for f in existing_updated})

            # 5. Insert EmailEvidence idempotently
            inserted_evidence = await self._evidence_repo.add_evidence(
                mapped_evidence=mapped_evidence,
                finding_id_map=finding_id_map,
                page_id_map=page_id_map,
            )

            # Increment evidence_count on EmailFinding ONLY by count of actually inserted evidence
            evidence_counts: dict[uuid.UUID, int] = {}
            for ev in inserted_evidence:
                evidence_counts[ev.email_finding_id] = (
                    evidence_counts.get(ev.email_finding_id, 0) + 1
                )

            for finding_id, added_count in evidence_counts.items():
                await self._finding_repo.increment_evidence_count(finding_id, added_count)

            # 6. Add bounded RejectedEmailCandidates
            await self._rejected_repo.add_rejected_candidates(
                job_id=job_id,
                scan_url_id=scan_url_id,
                page_id_map=page_id_map,
                mapped_rejected=mapped_rejected,
            )

            # 7. Transition ScanURL SCANNING -> terminal
            target_status = map_outcome_to_url_status(
                site_scan_result.outcome, len(mapped_findings)
            )
            transitioned = await self._scan_url_repo.update_status_conditional(
                organization_id=organization_id,
                job_id=job_id,
                scan_url_id=scan_url_id,
                expected_status=ScanURLStatus.SCANNING.value,
                new_status=target_status.value,
            )

            if transitioned:
                # 8. Increment counters & dispatch event ONLY on first terminal transition
                if target_status in (ScanURLStatus.COMPLETED, ScanURLStatus.NO_EMAIL):
                    await self._scan_job_repo.increment_completed_urls(
                        organization_id, job_id, delta=1
                    )
                elif target_status == ScanURLStatus.FAILED:
                    await self._scan_job_repo.increment_failed_urls(
                        organization_id, job_id, delta=1
                    )

                if new_findings_count > 0:
                    await self._scan_job_repo.increment_email_findings(
                        organization_id, job_id, delta=new_findings_count
                    )

                seq = await self._scan_job_repo.allocate_event_sequence(organization_id, job_id)
                event_type = (
                    "SCAN_URL_COMPLETED"
                    if target_status != ScanURLStatus.FAILED
                    else "SCAN_URL_FAILED"
                )
                if seq is not None:
                    job_event = JobEvent(
                        scan_job_id=job_id,
                        scan_url_id=scan_url_id,
                        sequence_number=seq,
                        event_type=event_type,
                        payload={
                            "attempt_number": attempt_number,
                            "scan_url_id": str(scan_url_id),
                            "target_status": target_status.value,
                            "total_findings": len(mapped_findings),
                            "new_findings": new_findings_count,
                        },
                    )
                    self._event_repo.append_event(job_event)

            return CrawlAttemptResult(attempt=attempt_obj, is_replay=False)
