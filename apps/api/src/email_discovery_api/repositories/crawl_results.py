"""Tenant-scoped repositories for crawl attempts, pages, findings, and evidence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from email_discovery_api.mappers.crawl_results import (
    MappedAttempt,
    MappedEvidence,
    MappedFinding,
    MappedPage,
    MappedRejectedCandidate,
)
from email_discovery_api.models.crawl_attempt import CrawlAttempt
from email_discovery_api.models.crawled_page import CrawledPage
from email_discovery_api.models.email_evidence import EmailEvidence
from email_discovery_api.models.email_finding import EmailFinding
from email_discovery_api.models.rejected_email_candidate import RejectedEmailCandidate
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL


class CrawlAttemptRepository:
    """Repository for CrawlAttempt entity persistence and reads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, scan_url_id: uuid.UUID, mapped: MappedAttempt) -> CrawlAttempt:
        """Create a new CrawlAttempt row without committing."""
        attempt = CrawlAttempt(
            scan_url_id=scan_url_id,
            attempt_number=mapped.attempt_number,
            outcome=mapped.outcome,
            retryable=mapped.retryable,
            requested_url=mapped.requested_url,
            final_url=mapped.final_url,
            status_code=mapped.status_code,
            error_code=mapped.error_code,
            error_message=mapped.error_message,
            failure_code=mapped.failure_code,
            redirect_history=mapped.redirect_history,
            connection_attempts=mapped.connection_attempts,
            started_at=mapped.started_at,
            completed_at=mapped.completed_at,
            elapsed_seconds=mapped.elapsed_seconds,
            dns_duration_seconds=mapped.dns_duration_seconds,
            gate_wait_seconds=mapped.gate_wait_seconds,
            robots_duration_seconds=mapped.robots_duration_seconds,
            http_duration_seconds=mapped.http_duration_seconds,
            parse_duration_seconds=mapped.parse_duration_seconds,
            result_checksum=mapped.result_checksum,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def get_by_scan_url_and_attempt(
        self, scan_url_id: uuid.UUID, attempt_number: int
    ) -> CrawlAttempt | None:
        """Retrieve CrawlAttempt by scan_url_id and attempt_number."""
        stmt = select(CrawlAttempt).where(
            CrawlAttempt.scan_url_id == scan_url_id,
            CrawlAttempt.attempt_number == attempt_number,
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_attempt_detail(
        self, organization_id: uuid.UUID, job_id: uuid.UUID, attempt_id: uuid.UUID
    ) -> CrawlAttempt | None:
        """Retrieve tenant-scoped CrawlAttempt detail with pages."""
        stmt = (
            select(CrawlAttempt)
            .join(ScanURL, CrawlAttempt.scan_url_id == ScanURL.id)
            .join(ScanJob, ScanURL.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
                CrawlAttempt.id == attempt_id,
            )
            .options(selectinload(CrawlAttempt.crawled_pages))
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()


class CrawledPageRepository:
    """Repository for CrawledPage entity persistence and reads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_many(
        self,
        crawl_attempt_id: uuid.UUID,
        scan_url_id: uuid.UUID,
        mapped_pages: list[MappedPage],
    ) -> dict[str, uuid.UUID]:
        """Bulk insert CrawledPages and return dict mapping normalized_url -> crawled_page_id."""
        page_map: dict[str, uuid.UUID] = {}
        for p in mapped_pages:
            page = CrawledPage(
                crawl_attempt_id=crawl_attempt_id,
                scan_url_id=scan_url_id,
                normalized_url=p.normalized_url,
                final_url=p.final_url,
                depth=p.depth,
                outcome=p.outcome,
                status_code=p.status_code,
                content_type=p.content_type,
                content_sha256=p.content_sha256,
                page_score=p.page_score,
                ranking_version=p.ranking_version,
                robots_decision=p.robots_decision,
                links_discovered_count=p.links_discovered_count,
                emails_found_count=p.emails_found_count,
                fetched_at=p.fetched_at,
            )
            self._session.add(page)
            await self._session.flush()
            page_map[p.normalized_url] = page.id
        return page_map

    async def list_pages_for_job(
        self, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> list[CrawledPage]:
        """List tenant-scoped CrawledPages for a ScanJob."""
        stmt = (
            select(CrawledPage)
            .join(ScanURL, CrawledPage.scan_url_id == ScanURL.id)
            .join(ScanJob, ScanURL.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
            .order_by(CrawledPage.depth.asc(), CrawledPage.normalized_url.asc())
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())


class EmailFindingRepository:
    """Repository for EmailFinding entity persistence, upserts, and reads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_findings(
        self,
        job_id: uuid.UUID,
        mapped_findings: list[MappedFinding],
        now: datetime,
        scan_url_id: uuid.UUID | None = None,
    ) -> tuple[list[EmailFinding], list[EmailFinding]]:
        """Upsert EmailFindings for job_id and scan_url_id without overwriting first_found_at.

        Returns:
            (newly_inserted_findings, existing_updated_findings)
        """
        if not mapped_findings:
            return [], []

        newly_inserted: list[EmailFinding] = []
        existing_updated: list[EmailFinding] = []

        for f in mapped_findings:
            if scan_url_id is not None:
                stmt_select = select(EmailFinding).where(
                    EmailFinding.scan_url_id == scan_url_id,
                )
            else:
                stmt_select = select(EmailFinding).where(
                    EmailFinding.scan_job_id == job_id,
                    EmailFinding.canonical_email == f.canonical_email,
                )
            res = await self._session.execute(stmt_select)
            existing = res.scalar_one_or_none()

            if existing is None or getattr(existing, "last_found_at", None) is None:
                new_finding = EmailFinding(
                    scan_job_id=job_id,
                    scan_url_id=scan_url_id,
                    canonical_email=f.canonical_email,
                    email_domain=f.email_domain,
                    classification=f.classification,
                    is_role_based=f.is_role_based,
                    validation_status=f.validation_status,
                    first_found_at=now,
                    last_found_at=now,
                    evidence_count=0,
                )
                self._session.add(new_finding)
                await self._session.flush()
                newly_inserted.append(new_finding)
            else:
                existing.last_found_at = now
                existing.updated_at = now
                await self._session.flush()
                existing_updated.append(existing)

        return newly_inserted, existing_updated

    async def increment_evidence_count(self, finding_id: uuid.UUID, added_count: int) -> None:
        """Increment evidence_count on EmailFinding."""
        if added_count <= 0:
            return
        stmt = (
            update(EmailFinding)
            .where(EmailFinding.id == finding_id)
            .values(
                evidence_count=EmailFinding.evidence_count + added_count,
                updated_at=datetime.now(),
            )
        )
        await self._session.execute(stmt)

    async def count_findings_for_job(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> int:
        """Count total tenant-scoped EmailFindings for a ScanJob."""
        stmt = (
            select(func.count(EmailFinding.id))
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
        )
        res = await self._session.execute(stmt)
        return res.scalar_one() or 0

    async def list_findings_keyset(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        limit: int = 50,
        cursor_email: str | None = None,
        cursor_id: uuid.UUID | None = None,
        classification: str | None = None,
        validation_status: str | None = None,
        email_domain: str | None = None,
        search_prefix: str | None = None,
    ) -> tuple[list[EmailFinding], bool]:
        """Fetch limit + 1 tenant-scoped EmailFindings using keyset pagination."""
        stmt = (
            select(EmailFinding)
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
            )
            .options(selectinload(EmailFinding.scan_url))
        )

        if classification:
            stmt = stmt.where(EmailFinding.classification == classification)
        if validation_status:
            stmt = stmt.where(EmailFinding.validation_status == validation_status)
        if email_domain:
            stmt = stmt.where(EmailFinding.email_domain == email_domain)
        if search_prefix:
            # Escape SQL LIKE wildcards % and _ and \
            escaped = search_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(EmailFinding.canonical_email.like(f"{escaped}%", escape="\\"))

        if cursor_email is not None and cursor_id is not None:
            stmt = stmt.where(
                tuple_(EmailFinding.canonical_email, EmailFinding.id) > (cursor_email, cursor_id)
            )

        stmt = stmt.order_by(EmailFinding.canonical_email.asc(), EmailFinding.id.asc()).limit(
            limit + 1
        )

        res = await self._session.execute(stmt)
        rows = list(res.scalars().all())

        has_more = len(rows) > limit
        items = rows[:limit]
        return items, has_more

    async def get_representative_evidence_for_findings(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        finding_ids: list[uuid.UUID],
        max_per_finding: int = 3,
    ) -> dict[uuid.UUID, list[EmailEvidence]]:
        """Fetch top N representative evidence items per finding using 1 SQL window query."""
        if not finding_ids:
            return {}

        rank_col = (
            func.row_number()
            .over(
                partition_by=EmailEvidence.email_finding_id,
                order_by=(EmailEvidence.created_at.asc(), EmailEvidence.id.asc()),
            )
            .label("rank")
        )

        subq = (
            select(EmailEvidence, rank_col)
            .join(EmailFinding, EmailEvidence.email_finding_id == EmailFinding.id)
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
                EmailEvidence.email_finding_id.in_(finding_ids),
            )
            .subquery()
        )

        aliased_ev = aliased(EmailEvidence, subq)
        stmt = (
            select(aliased_ev)
            .where(subq.c.rank <= max_per_finding)
            .order_by(
                subq.c.email_finding_id.asc(),
                subq.c.rank.asc(),
                aliased_ev.id.asc(),
            )
        )
        res = await self._session.execute(stmt)
        evidence_rows = list(res.scalars().all())

        grouped: dict[uuid.UUID, list[EmailEvidence]] = {fid: [] for fid in finding_ids}
        for ev in evidence_rows:
            grouped[ev.email_finding_id].append(ev)
        return grouped

    async def list_findings_for_job(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        classification: str | None = None,
    ) -> list[EmailFinding]:
        """List tenant-scoped EmailFindings for a ScanJob."""
        items, _ = await self.list_findings_keyset(
            organization_id=organization_id,
            job_id=job_id,
            limit=10000,
            classification=classification,
        )
        return items

    async def get_finding_detail(
        self, organization_id: uuid.UUID, job_id: uuid.UUID, finding_id: uuid.UUID
    ) -> EmailFinding | None:
        """Retrieve tenant-scoped EmailFinding detail."""
        stmt = (
            select(EmailFinding)
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
                EmailFinding.id == finding_id,
            )
            .options(selectinload(EmailFinding.scan_url))
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()


class EmailEvidenceRepository:
    """Repository for EmailEvidence entity persistence and tenant-scoped reads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_evidence_keyset(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        finding_id: uuid.UUID,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: uuid.UUID | None = None,
    ) -> tuple[list[EmailEvidence], bool]:
        """Fetch limit + 1 tenant-scoped EmailEvidence for a finding using keyset pagination."""
        stmt = (
            select(EmailEvidence)
            .join(EmailFinding, EmailEvidence.email_finding_id == EmailFinding.id)
            .join(ScanJob, EmailFinding.scan_job_id == ScanJob.id)
            .where(
                ScanJob.organization_id == organization_id,
                ScanJob.id == job_id,
                EmailEvidence.email_finding_id == finding_id,
            )
            .options(selectinload(EmailEvidence.crawled_page))
        )

        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                tuple_(EmailEvidence.created_at, EmailEvidence.id) > (cursor_created_at, cursor_id)
            )

        stmt = stmt.order_by(EmailEvidence.created_at.asc(), EmailEvidence.id.asc()).limit(
            limit + 1
        )

        res = await self._session.execute(stmt)
        rows = list(res.scalars().all())

        has_more = len(rows) > limit
        items = rows[:limit]
        return items, has_more

    async def add_evidence(
        self,
        mapped_evidence: list[MappedEvidence],
        finding_id_map: dict[str, uuid.UUID],
        page_id_map: dict[str, uuid.UUID],
    ) -> list[EmailEvidence]:
        """Insert EmailEvidence idempotently and return list of newly created evidence instances."""
        inserted: list[EmailEvidence] = []
        for ev in mapped_evidence:
            finding_id = finding_id_map.get(ev.canonical_email)
            page_id = page_id_map.get(ev.normalized_page_url)
            if not finding_id or not page_id:
                continue

            # Check if evidence already exists
            stmt_check = select(EmailEvidence).where(
                EmailEvidence.email_finding_id == finding_id,
                EmailEvidence.crawled_page_id == page_id,
                EmailEvidence.source_type == ev.source_type,
                EmailEvidence.candidate_hash == ev.candidate_hash,
            )
            res = await self._session.execute(stmt_check)
            if res.scalar_one_or_none() is not None:
                continue

            evidence = EmailEvidence(
                email_finding_id=finding_id,
                crawled_page_id=page_id,
                source_type=ev.source_type,
                raw_candidate=ev.raw_candidate,
                snippet=ev.snippet,
                page_url=ev.page_url,
                confidence=ev.confidence,
                candidate_hash=ev.candidate_hash,
            )
            self._session.add(evidence)
            await self._session.flush()
            inserted.append(evidence)

        return inserted


class RejectedCandidateRepository:
    """Repository for RejectedEmailCandidate audit logging."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_rejected_candidates(
        self,
        job_id: uuid.UUID,
        scan_url_id: uuid.UUID,
        page_id_map: dict[str, uuid.UUID],
        mapped_rejected: list[MappedRejectedCandidate],
    ) -> list[RejectedEmailCandidate]:
        """Insert bounded RejectedEmailCandidate records idempotently."""
        inserted: list[RejectedEmailCandidate] = []
        for r in mapped_rejected:
            page_id = page_id_map.get(r.normalized_page_url) if r.normalized_page_url else None

            # Check duplicate
            stmt_check = select(RejectedEmailCandidate).where(
                RejectedEmailCandidate.scan_job_id == job_id,
                RejectedEmailCandidate.candidate_hash == r.candidate_hash,
                RejectedEmailCandidate.rejection_code == r.rejection_code,
            )
            res = await self._session.execute(stmt_check)
            if res.scalar_one_or_none() is not None:
                continue

            cand = RejectedEmailCandidate(
                scan_job_id=job_id,
                scan_url_id=scan_url_id,
                crawled_page_id=page_id,
                candidate_hash=r.candidate_hash,
                masked_candidate=r.masked_candidate,
                rejection_code=r.rejection_code,
                source_type=r.source_type,
            )
            self._session.add(cand)
            await self._session.flush()
            inserted.append(cand)

        return inserted
