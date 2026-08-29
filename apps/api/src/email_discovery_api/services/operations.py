"""Bounded, privacy-safe system operational metrics and diagnostics."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, literal, select, union_all
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
        if maximum <= 0:
            return [], []
        stale_before_ms = int((now - timedelta(seconds=45)).timestamp() * 1000)
        try:
            raw_registry = await self._bounded(
                client.zrevrange(registry_key, 0, maximum - 1, withscores=True)
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

        job_stats = (
            select(
                ScanJob.id.label("job_id"),
                ScanJob.valid_input_count,
                ScanJob.queued_count,
                ScanJob.running_count,
                ScanJob.completed_count,
                ScanJob.failed_count,
                ScanJob.created_at,
                func.coalesce(func.count().filter(ScanURL.status.in_(ACTIVE_URL_STATES)), 0).label(
                    "active_urls"
                ),
                func.coalesce(
                    func.count().filter(ScanURL.status.in_(TERMINAL_URL_STATES)), 0
                ).label("terminal_urls"),
                func.coalesce(
                    func.count().filter(ScanURL.status.in_(("QUEUED", "RETRY_WAIT"))), 0
                ).label("actual_queued"),
                func.coalesce(
                    func.count().filter(ScanURL.status.in_(("LEASED", "SCANNING"))), 0
                ).label("actual_running"),
                func.coalesce(
                    func.count().filter(ScanURL.status.in_(("COMPLETED", "NO_EMAIL"))), 0
                ).label("actual_completed"),
                func.coalesce(
                    func.count().filter(ScanURL.status.in_(("FAILED", "INVALID"))), 0
                ).label("actual_failed"),
            )
            .select_from(ScanJob)
            .outerjoin(ScanURL, ScanJob.id == ScanURL.scan_job_id)
            .where(ScanJob.status.in_(ACTIVE_JOB_STATES))
            .group_by(
                ScanJob.id,
                ScanJob.valid_input_count,
                ScanJob.queued_count,
                ScanJob.running_count,
                ScanJob.completed_count,
                ScanJob.failed_count,
                ScanJob.created_at,
            )
        ).subquery()

        is_eligible = (job_stats.c.active_urls == 0) & (
            job_stats.c.terminal_urls >= job_stats.c.valid_input_count
        )
        is_inactive = (job_stats.c.active_urls == 0) & (
            job_stats.c.terminal_urls < job_stats.c.valid_input_count
        )
        is_mismatch = (
            (job_stats.c.actual_queued != job_stats.c.queued_count)
            | (job_stats.c.actual_running != job_stats.c.running_count)
            | (job_stats.c.actual_completed != job_stats.c.completed_count)
            | (job_stats.c.actual_failed != job_stats.c.failed_count)
        )

        q_eligible = select(
            literal("eligible").label("category"),
            job_stats.c.job_id,
            literal("all_work_terminal").label("reason"),
            func.row_number()
            .over(order_by=(job_stats.c.created_at.asc(), job_stats.c.job_id.asc()))
            .label("rn"),
            func.count().over().label("cat_total"),
        ).where(is_eligible)

        q_inactive = select(
            literal("inactive").label("category"),
            job_stats.c.job_id,
            literal("no_active_work").label("reason"),
            func.row_number()
            .over(order_by=(job_stats.c.created_at.asc(), job_stats.c.job_id.asc()))
            .label("rn"),
            func.count().over().label("cat_total"),
        ).where(is_inactive)

        q_mismatch = select(
            literal("mismatch").label("category"),
            job_stats.c.job_id,
            literal("counter_mismatch").label("reason"),
            func.row_number()
            .over(order_by=(job_stats.c.created_at.asc(), job_stats.c.job_id.asc()))
            .label("rn"),
            func.count().over().label("cat_total"),
        ).where(is_mismatch)

        combined_subq = union_all(q_eligible, q_inactive, q_mismatch).subquery()
        classified_stmt = select(
            combined_subq.c.category,
            combined_subq.c.job_id,
            combined_subq.c.reason,
            combined_subq.c.cat_total,
        ).where(combined_subq.c.rn <= limit)

        classified_rows = (await self._bounded(self.session.execute(classified_stmt))).all()

        eligible_items: list[DiagnosticItem] = []
        eligible_total = 0
        inactive_items: list[DiagnosticItem] = []
        inactive_total = 0
        mismatch_items: list[DiagnosticItem] = []
        mismatch_total = 0

        for cat, job_id, reason, cat_total in classified_rows:
            digest = safe_digest(job_id)
            item = DiagnosticItem(reference_digest=digest, reason=str(reason))
            if cat == "eligible":
                eligible_items.append(item)
                eligible_total = int(cat_total)
            elif cat == "inactive":
                inactive_items.append(item)
                inactive_total = int(cat_total)
            elif cat == "mismatch":
                mismatch_items.append(item)
                mismatch_total = int(cat_total)

        async def due_items(kind: str) -> tuple[list[DiagnosticItem], int]:
            if kind == "expired_lease":
                predicate = (ScanURL.status.in_(("LEASED", "SCANNING"))) & (
                    ScanURL.lease_expires_at < now
                )
                ordering = ScanURL.lease_expires_at
            else:
                predicate = (ScanURL.status == "RETRY_WAIT") & (ScanURL.next_retry_at <= now)
                ordering = ScanURL.next_retry_at

            subq = (
                select(
                    ScanURL.id,
                    func.count().over().label("cat_total"),
                    func.row_number().over(order_by=(ordering.asc(), ScanURL.id.asc())).label("rn"),
                ).where(predicate)
            ).subquery()

            stmt = select(subq.c.id, subq.c.cat_total).where(subq.c.rn <= limit)
            rows = list((await self._bounded(self.session.execute(stmt))).all())
            if not rows:
                return [], 0
            items = [
                DiagnosticItem(reference_digest=safe_digest(row_id), reason=kind)
                for row_id, _ in rows
            ]
            cat_total = int(rows[0][1])
            return items, cat_total

        expired_items, expired_total = await due_items("expired_lease")
        retry_items, retry_total = await due_items("retry_due")
        _, expired_presence = await self._read_presence(now)

        return OperationalDiagnosticsResponse(
            generated_at=now,
            jobs_eligible_for_finalization=self._category(
                eligible_items, limit, total=eligible_total
            ),
            nonterminal_jobs_without_active_work=self._category(
                inactive_items, limit, total=inactive_total
            ),
            expired_leases=self._category(expired_items, limit, total=expired_total),
            due_retries=self._category(retry_items, limit, total=retry_total),
            counter_mismatches=self._category(mismatch_items, limit, total=mismatch_total),
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
