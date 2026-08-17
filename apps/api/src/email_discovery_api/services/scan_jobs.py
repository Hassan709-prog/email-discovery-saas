"""Transactional service for job creation, input ingestion, status transitions, and progress."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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

ALLOWED_ROLES: set[str] = {
    MembershipRole.OWNER.value,
    MembershipRole.ADMIN.value,
    MembershipRole.MEMBER.value,
}


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


def compute_request_fingerprint(command: CreateScanJobCommand) -> str:
    """Compute deterministic 64-character SHA-256 request fingerprint.

    Includes tenant identity, creator, source type, ordered inputs, name, config, and versions.
    Excludes timestamps and generated record IDs.
    """
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
    """Transactional service orchestrating job creation, idempotency, and status transitions."""

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

    async def create_job(self, command: CreateScanJobCommand) -> ScanJob:
        """Create scan job, ingest inputs, allocate sequence 1, and append JOB_CREATED event.

        Pre-ingestion Order:
            1. Validate quota/limits (ScanCreationPolicy)
            2. Lock Organization row (FOR UPDATE)
            3. Verify active Membership and role authorization
            4. Enforce active job count limit
            5. Check idempotency key and fingerprint
            6. Parse & normalize inputs into ScanURL ORM objects
            7. Create ScanJob, ScanURL, allocate sequence 1, and append JOB_CREATED event
        """
        # 1. Pre-ingestion limit policy validation before DB locks or row construction
        self.policy.validate_pre_ingestion(command.inputs, command.configuration_snapshot)

        fingerprint = compute_request_fingerprint(command)

        try:
            async with self.session.begin():
                return await self._create_job_in_transaction(command, fingerprint)
        except IntegrityError as err:
            # PostgreSQL aborts transaction on uniqueness conflict.
            # Recover by starting a fresh transaction to tenant-scoped re-read the existing job.
            if command.idempotency_key:
                async with self.session.begin():
                    existing = await self.job_repo.find_by_idempotency_key(
                        command.organization_id, command.idempotency_key
                    )
                    if existing is not None:
                        if existing.request_fingerprint == fingerprint:
                            return existing
                        raise ServiceError(
                            ServiceErrorCode.IDEMPOTENCY_CONFLICT,
                            f"Key {command.idempotency_key!r} used with different fingerprint.",
                        ) from err
            raise

    async def _create_job_in_transaction(
        self, command: CreateScanJobCommand, fingerprint: str
    ) -> ScanJob:
        """Internal job creation steps executed within an active transaction."""
        # 2. Lock Organization FOR UPDATE to serialize creation per tenant
        org = await self.org_repo.get_active_organization_for_update(command.organization_id)
        if org is None:
            raise ServiceError(
                ServiceErrorCode.ORGANIZATION_NOT_FOUND,
                f"Organization {command.organization_id} was not found or is not active.",
            )

        # 3. Check active membership and role
        membership = await self.org_repo.get_active_membership(
            command.organization_id, command.created_by_user_id
        )
        if membership is None or membership.role not in ALLOWED_ROLES:
            raise ServiceError(
                ServiceErrorCode.USER_NOT_AUTHORIZED,
                f"User {command.created_by_user_id} unauthorized for organization.",
            )

        # 4. Enforce active job quota
        active_count = await self.job_repo.count_active_jobs(command.organization_id)
        if active_count >= self.policy.max_active_jobs_per_organization:
            raise ServiceError(
                ServiceErrorCode.ACTIVE_JOB_LIMIT_EXCEEDED,
                f"Active count ({active_count}) reached limit "
                f"{self.policy.max_active_jobs_per_organization}.",
            )

        # 5. Check idempotency key if supplied
        if command.idempotency_key:
            existing = await self.job_repo.find_by_idempotency_key(
                command.organization_id, command.idempotency_key
            )
            if existing is not None:
                if existing.request_fingerprint == fingerprint:
                    return existing
                raise ServiceError(
                    ServiceErrorCode.IDEMPOTENCY_CONFLICT,
                    f"Idempotency key {command.idempotency_key!r} used with different fingerprint.",
                )

        # 6. Parse and normalize URL inputs
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

        # 7. Create ScanJob entity
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

        # 8. Allocate event sequence 1 atomically
        seq = await self.job_repo.allocate_event_sequence(command.organization_id, job_id)
        assert seq == 1

        # 9. Append JOB_CREATED event
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

        return job

    async def transition_job_status(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        new_status: ScanJobStatus,
    ) -> ScanJob:
        """Perform a validated status transition and append a JOB_STATUS_CHANGED event."""
        async with self.session.begin():
            job = await self.job_repo.get_job(organization_id, job_id)
            if job is None:
                raise ServiceError(
                    ServiceErrorCode.JOB_NOT_FOUND,
                    f"Scan job {job_id} was not found for organization {organization_id}.",
                )

            current_status = ScanJobStatus(job.status)
            allowed = ALLOWED_STATE_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise ServiceError(
                    ServiceErrorCode.INVALID_STATE_TRANSITION,
                    f"Transition from {current_status.value} to {new_status.value} not allowed.",
                    details={
                        "current_status": current_status.value,
                        "requested_status": new_status.value,
                    },
                )

            now = utc_now()
            started_at = now if new_status == ScanJobStatus.RUNNING else None
            completed_at = (
                now
                if new_status
                in (
                    ScanJobStatus.COMPLETED,
                    ScanJobStatus.COMPLETED_WITH_ERRORS,
                    ScanJobStatus.FAILED,
                    ScanJobStatus.CANCELLED,
                )
                else None
            )
            cancellation_requested_at = now if new_status == ScanJobStatus.CANCELLING else None

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
                # Disambiguate staveness vs missing job
                recheck = await self.job_repo.get_job(organization_id, job_id)
                if recheck is None:
                    raise ServiceError(
                        ServiceErrorCode.JOB_NOT_FOUND,
                        f"Scan job {job_id} was not found.",
                    )
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

            # Re-read updated job within transaction
            updated_job = await self.job_repo.get_job(organization_id, job_id)
            assert updated_job is not None
            return updated_job

    async def get_job_progress(
        self, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> ScanJobProgress:
        """Fetch derived progress response for job using persisted database counters."""
        job = await self.job_repo.get_job(organization_id, job_id)
        if job is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                f"Scan job {job_id} was not found for organization {organization_id}.",
            )

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
