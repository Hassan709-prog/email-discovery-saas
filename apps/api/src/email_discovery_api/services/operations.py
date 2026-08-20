"""Bounded, privacy-safe system operational metrics and diagnostics."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models import AuditLog, ScanJob, ScanURL
from email_discovery_api.schemas.operations import (
    AdvisoryWorkerPresence,
    DependencyReadiness,
    DiagnosticCategory,
    DiagnosticItem,
    JobOperationalMetrics,
    OperationalDiagnosticsResponse,
    OperationalMetricsResponse,
    RecoveryResponse,
    URLOperationalMetrics,
    WorkerOperationalMetrics,
    WorkerStateCount,
)
from email_discovery_api.services.scan_jobs import ScanJobService

ACTIVE_JOB_STATES = ("DRAFT", "QUEUED", "RUNNING", "CANCELLING")
TERMINAL_JOB_STATES = ("CANCELLED", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED")
ACTIVE_URL_STATES = ("PENDING", "QUEUED", "LEASED", "SCANNING", "RETRY_WAIT")
TERMINAL_URL_STATES = ("COMPLETED", "NO_EMAIL", "FAILED", "CANCELLED")


def safe_digest(value: UUID | str) -> str:
    """Return a stable, non-reversible short reference for operator output."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _age_seconds(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds())


class OperationalService:
    """Read bounded system aggregates and invoke explicitly authorized recovery."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        db_manager: Any,
        redis_manager: Any,
        settings: Any,
    ) -> None:
        self.session = session
        self.db_manager = db_manager
        self.redis_manager = redis_manager
        self.settings = settings
        self.timeout = settings.operations_query_timeout_seconds

    async def _bounded(self, awaitable: Any) -> Any:
        return await asyncio.wait_for(awaitable, timeout=self.timeout)

    async def _read_presence(
        self, now: datetime
    ) -> tuple[list[AdvisoryWorkerPresence], list[DiagnosticItem]]:
        client = getattr(self.redis_manager, "client", None)
        if client is None:
            return [], []
        prefix = self.settings.redis_key_prefix
        registry_key = f"{prefix}:workers:presence_registry"
        maximum = self.settings.operations_max_presence_records
        stale_before_ms = int((now - timedelta(seconds=45)).timestamp() * 1000)
        try:
            raw_registry = await self._bounded(
                client.zrevrange(registry_key, 0, maximum, withscores=True)
            )
            entries: list[tuple[str, float]] = []
            for member, score in raw_registry:
                digest = member.decode() if isinstance(member, bytes) else str(member)
                if len(digest) == 16 and all(c in "0123456789abcdef" for c in digest):
                    entries.append((digest, float(score)))
            keys = [f"{prefix}:workers:{digest}" for digest, _ in entries]
            payloads = (
                cast(list[bytes | str | None], await self._bounded(client.mget(keys)))
                if keys
                else []
            )
        except Exception:
            return [], []

        records: list[AdvisoryWorkerPresence] = []
        expired: list[DiagnosticItem] = []
        for (digest, score), raw in zip(entries, payloads, strict=True):
            stale = int(score) < stale_before_ms or raw is None
            if raw is None:
                expired.append(DiagnosticItem(reference_digest=digest, reason="presence_expired"))
                continue
            try:
                payload = json.loads(raw)
                records.append(
                    AdvisoryWorkerPresence(
                        instance_digest=digest,
                        state=str(payload.get("state", "unknown"))[:32],
                        configured_concurrency=max(0, int(payload.get("concurrency", 0))),
                        active_claims=max(0, int(payload.get("active_claims", 0))),
                        last_seen_at=datetime.fromtimestamp(score / 1000.0, tz=UTC),
                        stale=stale,
                    )
                )
            except TypeError, ValueError, json.JSONDecodeError:
                expired.append(DiagnosticItem(reference_digest=digest, reason="presence_invalid"))
        records.sort(key=lambda item: item.instance_digest)
        expired.sort(key=lambda item: item.reference_digest)
        return records, expired[:maximum]

    async def metrics(self, window_seconds: int) -> OperationalMetricsResponse:
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=window_seconds)
        url_stmt = select(
            func.count().filter(ScanURL.status == "QUEUED"),
            func.count().filter(ScanURL.status == "LEASED"),
            func.count().filter(ScanURL.status == "SCANNING"),
            func.count().filter(ScanURL.status == "RETRY_WAIT"),
            func.count().filter(ScanURL.status == "COMPLETED"),
            func.count().filter(ScanURL.status == "NO_EMAIL"),
            func.count().filter(ScanURL.status == "FAILED"),
            func.count().filter(
                (ScanURL.status.in_(("LEASED", "SCANNING"))) & (ScanURL.lease_expires_at < now)
            ),
            func.min(ScanURL.created_at).filter(ScanURL.status == "QUEUED"),
            func.coalesce(func.sum(ScanURL.retry_count), 0),
            func.count().filter(
                (ScanURL.status.in_(TERMINAL_URL_STATES))
                & (ScanURL.completed_at >= window_start)
                & (ScanURL.completed_at < now)
            ),
        )
        row = (await self._bounded(self.session.execute(url_stmt))).one()

        job_stmt = select(
            func.count().filter(ScanJob.status.in_(ACTIVE_JOB_STATES)),
            func.count().filter(ScanJob.status.in_(TERMINAL_JOB_STATES)),
            func.min(func.coalesce(ScanJob.started_at, ScanJob.created_at)).filter(
                ScanJob.status.in_(ACTIVE_JOB_STATES)
            ),
        )
        job_row = (await self._bounded(self.session.execute(job_stmt))).one()

        failure_stmt = (
            select(ScanURL.last_failure_code, func.count())
            .where(
                ScanURL.status == "FAILED",
                ScanURL.completed_at >= window_start,
                ScanURL.completed_at < now,
                ScanURL.last_failure_code.is_not(None),
            )
            .group_by(ScanURL.last_failure_code)
            .order_by(func.count().desc(), ScanURL.last_failure_code.asc())
            .limit(20)
        )
        failure_rows = (await self._bounded(self.session.execute(failure_stmt))).all()
        records, expired = await self._read_presence(now)
        state_counts = Counter(record.state for record in records)
        workers = WorkerOperationalMetrics(
            present=len(records),
            stale=sum(record.stale for record in records) + len(expired),
            configured_concurrency=sum(record.configured_concurrency for record in records),
            active_claims=sum(record.active_claims for record in records),
            states=[
                WorkerStateCount(state=state, count=count)
                for state, count in sorted(state_counts.items())
            ],
            records=records,
        )
        db_ok = bool(
            await self._bounded(
                self.db_manager.check_health(self.settings.db_health_timeout_seconds)
            )
        )
        redis_ok = bool(await self._bounded(self.redis_manager.check_health()))
        redis_status = (
            "ok" if redis_ok else ("unavailable" if self.settings.redis_required else "degraded")
        )
        return OperationalMetricsResponse(
            generated_at=now,
            window_seconds=window_seconds,
            readiness=DependencyReadiness(
                postgresql="ok" if db_ok else "unavailable",
                redis=redis_status,
                redis_required=self.settings.redis_required,
            ),
            workers=workers,
            urls=URLOperationalMetrics(
                queued=int(row[0]),
                leased=int(row[1]),
                scanning=int(row[2]),
                retry_wait=int(row[3]),
                completed=int(row[4]),
                no_email=int(row[5]),
                failed=int(row[6]),
                expired_leases=int(row[7]),
                oldest_queued_age_seconds=_age_seconds(now, row[8]),
                retry_total=int(row[9]),
                recent_terminal_count=int(row[10]),
                recent_throughput_per_second=int(row[10]) / window_seconds,
                failure_reasons=[
                    WorkerStateCount(state=str(reason), count=int(count))
                    for reason, count in failure_rows
                ],
            ),
            jobs=JobOperationalMetrics(
                active=int(job_row[0]),
                terminal=int(job_row[1]),
                oldest_active_age_seconds=_age_seconds(now, job_row[2]),
            ),
        )

    @staticmethod
    def _category(
        items: list[DiagnosticItem], limit: int, total: int | None = None
    ) -> DiagnosticCategory:
        actual_total = len(items) if total is None else total
        ordered = sorted(items, key=lambda item: (item.reference_digest, item.reason))[:limit]
        return DiagnosticCategory(
            total=actual_total, truncated=actual_total > len(ordered), items=ordered
        )

    async def diagnostics(self, limit: int) -> OperationalDiagnosticsResponse:
        now = datetime.now(UTC)
        candidate_stmt = (
            select(ScanJob)
            .where(ScanJob.status.in_(ACTIVE_JOB_STATES))
            .order_by(ScanJob.created_at.asc(), ScanJob.id.asc())
            .limit(limit + 1)
        )
        jobs = list((await self._bounded(self.session.execute(candidate_stmt))).scalars().all())
        selected_jobs = jobs[:limit]
        job_ids = [job.id for job in selected_jobs]
        counts_by_job: dict[UUID, dict[str, int]] = {job_id: {} for job_id in job_ids}
        if job_ids:
            count_stmt = (
                select(ScanURL.scan_job_id, ScanURL.status, func.count())
                .where(ScanURL.scan_job_id.in_(job_ids))
                .group_by(ScanURL.scan_job_id, ScanURL.status)
            )
            for job_id, state, count in (
                await self._bounded(self.session.execute(count_stmt))
            ).all():
                counts_by_job[job_id][state] = int(count)

        eligible: list[DiagnosticItem] = []
        inactive: list[DiagnosticItem] = []
        mismatches: list[DiagnosticItem] = []
        for job in selected_jobs:
            counts = counts_by_job[job.id]
            active = sum(counts.get(state, 0) for state in ACTIVE_URL_STATES)
            terminal = sum(counts.get(state, 0) for state in TERMINAL_URL_STATES)
            digest = safe_digest(job.id)
            if active == 0 and terminal >= job.valid_input_count:
                eligible.append(DiagnosticItem(reference_digest=digest, reason="all_work_terminal"))
            if active == 0 and terminal < job.valid_input_count:
                inactive.append(DiagnosticItem(reference_digest=digest, reason="no_active_work"))
            actual = (
                counts.get("QUEUED", 0) + counts.get("RETRY_WAIT", 0),
                counts.get("LEASED", 0) + counts.get("SCANNING", 0),
                counts.get("COMPLETED", 0) + counts.get("NO_EMAIL", 0),
                counts.get("FAILED", 0) + counts.get("INVALID", 0),
            )
            stored = (job.queued_count, job.running_count, job.completed_count, job.failed_count)
            if actual != stored:
                mismatches.append(
                    DiagnosticItem(reference_digest=digest, reason="counter_mismatch")
                )

        async def due_items(kind: str) -> tuple[list[DiagnosticItem], bool]:
            if kind == "expired_lease":
                predicate = (ScanURL.status.in_(("LEASED", "SCANNING"))) & (
                    ScanURL.lease_expires_at < now
                )
                ordering = ScanURL.lease_expires_at
            else:
                predicate = (ScanURL.status == "RETRY_WAIT") & (ScanURL.next_retry_at <= now)
                ordering = ScanURL.next_retry_at
            stmt = (
                select(ScanURL.id)
                .where(predicate)
                .order_by(ordering.asc(), ScanURL.id.asc())
                .limit(limit + 1)
            )
            rows = list((await self._bounded(self.session.execute(stmt))).scalars().all())
            return [
                DiagnosticItem(reference_digest=safe_digest(row_id), reason=kind)
                for row_id in rows[:limit]
            ], len(rows) > limit

        expired_items, expired_more = await due_items("expired_lease")
        retry_items, retry_more = await due_items("retry_due")
        _, expired_presence = await self._read_presence(now)
        return OperationalDiagnosticsResponse(
            generated_at=now,
            jobs_eligible_for_finalization=self._category(eligible, limit),
            nonterminal_jobs_without_active_work=self._category(inactive, limit),
            expired_leases=self._category(
                expired_items, limit, len(expired_items) + int(expired_more)
            ),
            due_retries=self._category(retry_items, limit, len(retry_items) + int(retry_more)),
            counter_mismatches=self._category(mismatches, limit),
            expired_worker_presence=self._category(expired_presence, limit),
        )

    async def recover_job(
        self,
        *,
        organization_id: UUID,
        job_id: UUID,
        actor_user_id: UUID,
        request_id: str | None,
    ) -> RecoveryResponse:
        job = await ScanJobService(self.session).reconcile_and_recover_stuck_job(
            organization_id, job_id
        )
        digest = safe_digest(job_id)
        outcome = "reconciled" if job is not None else "not_found"
        if job is None:
            return RecoveryResponse(
                reference_digest=digest, outcome="not_found", audit_recorded=False
            )
        async with self.session.begin():
            self.session.add(
                AuditLog(
                    organization_id=organization_id,
                    actor_user_id=actor_user_id,
                    action="OPERATIONS_JOB_RECOVERY",
                    target_type="scan_job_digest",
                    target_id=digest,
                    request_id=request_id,
                    metadata_={"outcome": outcome, "explicit_confirmation": True},
                )
            )
        return RecoveryResponse(reference_digest=digest, outcome="reconciled", audit_recorded=True)
