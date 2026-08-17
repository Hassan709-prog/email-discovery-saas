"""Domain service for tenant-scoped scan job findings, detail, and CSV export."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_discovery_api.api.dependencies.cursors import (
    encode_cursor,
    parse_evidence_cursor,
    parse_results_cursor,
)
from email_discovery_api.models.email_evidence import EmailEvidence
from email_discovery_api.models.email_finding import EmailFinding
from email_discovery_api.models.enums import ScanJobStatus
from email_discovery_api.repositories.crawl_results import (
    EmailEvidenceRepository,
    EmailFindingRepository,
)
from email_discovery_api.repositories.scan_jobs import ScanJobRepository
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode

MAX_SYNC_EXPORT_ROWS = 50000
TERMINAL_JOB_STATUSES = {
    ScanJobStatus.COMPLETED.value,
    ScanJobStatus.COMPLETED_WITH_ERRORS.value,
    ScanJobStatus.CANCELLED.value,
    ScanJobStatus.FAILED.value,
}

CONTROL_CHAR_REGEX = re.compile(r"[\r\n\x00-\x1f]")


def sanitize_csv_cell(value: Any) -> str:
    """Format, sanitize, and formula-protect a single cell value for CSV export."""
    if value is None:
        return ""
    val_str = str(value)
    # 1. Replace CR, LF and control characters with spaces
    sanitized = CONTROL_CHAR_REGEX.sub(" ", val_str)
    # 2. Inspect first non-whitespace character after lstrip()
    stripped = sanitized.lstrip()
    if stripped and stripped[0] in ("=", "+", "-", "@"):
        return f"'{sanitized}"
    return sanitized


class ScanJobResultsService:
    """Service owning tenant-scoped query business logic for results and CSV exports."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._job_repo = ScanJobRepository(session)
        self._finding_repo = EmailFindingRepository(session)
        self._evidence_repo = EmailEvidenceRepository(session)

    async def list_results(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        limit: int = 50,
        cursor: str | None = None,
        classification: str | None = None,
        validation_status: str | None = None,
        email_domain: str | None = None,
        search_prefix: str | None = None,
    ) -> tuple[list[EmailFinding], dict[uuid.UUID, list[EmailEvidence]], str | None]:
        """List tenant-scoped findings for a scan job with 1-query representative evidence."""
        # 1. Verify job exists for tenant
        job = await self._job_repo.get_job(organization_id, job_id)
        if job is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                "Scan job not found.",
            )

        cursor_email, cursor_id = parse_results_cursor(cursor)

        findings, has_more = await self._finding_repo.list_findings_keyset(
            organization_id=organization_id,
            job_id=job_id,
            limit=limit,
            cursor_email=cursor_email,
            cursor_id=cursor_id,
            classification=classification,
            validation_status=validation_status,
            email_domain=email_domain,
            search_prefix=search_prefix,
        )

        finding_ids = [f.id for f in findings]
        rep_evidence = await self._finding_repo.get_representative_evidence_for_findings(
            organization_id=organization_id,
            job_id=job_id,
            finding_ids=finding_ids,
            max_per_finding=3,
        )

        next_cursor = None
        if has_more and findings:
            last = findings[-1]
            next_cursor = encode_cursor("results", [last.canonical_email, str(last.id)])

        return findings, rep_evidence, next_cursor

    async def get_result_detail(
        self, organization_id: uuid.UUID, job_id: uuid.UUID, finding_id: uuid.UUID
    ) -> tuple[EmailFinding, list[EmailEvidence]]:
        """Fetch single finding detail with top representative evidence."""
        # 1. Verify job exists for tenant
        job = await self._job_repo.get_job(organization_id, job_id)
        if job is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                "Scan job not found.",
            )

        finding = await self._finding_repo.get_finding_detail(organization_id, job_id, finding_id)
        if finding is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                "Scan job not found.",
            )

        rep_map = await self._finding_repo.get_representative_evidence_for_findings(
            organization_id=organization_id,
            job_id=job_id,
            finding_ids=[finding_id],
            max_per_finding=3,
        )
        evidence_list = rep_map.get(finding_id, [])

        return finding, evidence_list

    async def list_result_evidence(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        finding_id: uuid.UUID,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[EmailEvidence], str | None]:
        """Fetch paginated evidence items for a specific finding."""
        job = await self._job_repo.get_job(organization_id, job_id)
        if job is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                "Scan job not found.",
            )

        finding = await self._finding_repo.get_finding_detail(organization_id, job_id, finding_id)
        if finding is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                "Scan job not found.",
            )

        cursor_created_at, cursor_id = parse_evidence_cursor(cursor)

        evidence_items, has_more = await self._evidence_repo.list_evidence_keyset(
            organization_id=organization_id,
            job_id=job_id,
            finding_id=finding_id,
            limit=limit,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )

        next_cursor = None
        if has_more and evidence_items:
            last = evidence_items[-1]
            next_cursor = encode_cursor("evidence", [last.created_at.isoformat(), str(last.id)])

        return evidence_items, next_cursor

    async def validate_export_eligibility(
        self, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> None:
        """Verify job is terminal and count does not exceed limit BEFORE starting stream."""
        job = await self._job_repo.get_job(organization_id, job_id)
        if job is None:
            raise ServiceError(
                ServiceErrorCode.JOB_NOT_FOUND,
                "Scan job not found.",
            )

        if job.status not in TERMINAL_JOB_STATUSES:
            raise ServiceError(
                ServiceErrorCode.INVALID_STATE_TRANSITION,
                "CSV export is forbidden while scan job is nonterminal.",
            )

        count = await self._finding_repo.count_findings_for_job(organization_id, job_id)
        if count > MAX_SYNC_EXPORT_ROWS:
            raise ServiceError(
                ServiceErrorCode.EXPORT_TOO_LARGE,
                "This result set exceeds the synchronous CSV export limit. "
                "Asynchronous exports will be supported in a later phase.",
            )

    async def stream_export_batches(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        batch_size: int = 1000,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> AsyncGenerator[list[EmailFinding]]:
        """Public service generator yielding bounded keyset batches for CSV streaming."""
        cursor_email: str | None = None
        cursor_id: uuid.UUID | None = None

        while True:
            if session_factory is not None:
                async with session_factory() as batch_session:
                    repo = EmailFindingRepository(batch_session)
                    items, has_more = await repo.list_findings_keyset(
                        organization_id=organization_id,
                        job_id=job_id,
                        limit=batch_size,
                        cursor_email=cursor_email,
                        cursor_id=cursor_id,
                    )
                    batch_session.expunge_all()
            else:
                items, has_more = await self._finding_repo.list_findings_keyset(
                    organization_id=organization_id,
                    job_id=job_id,
                    limit=batch_size,
                    cursor_email=cursor_email,
                    cursor_id=cursor_id,
                )
                self.session.expunge_all()
                await self.session.rollback()

            if not items:
                break
            yield items
            if not has_more:
                break
            last = items[-1]
            cursor_email = last.canonical_email
            cursor_id = last.id
