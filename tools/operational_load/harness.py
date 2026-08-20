"""Real production-path deterministic offline load execution."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import time
import tracemalloc
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import redis.asyncio as redis
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from email_discovery_api.models import (
    CrawlAttempt,
    EmailFinding,
    Organization,
    ScanJob,
    ScanURL,
)
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.worker_contracts import LeaseLostError, URLClaim
from email_discovery_crawl_worker.config import WorkerSettings
from email_discovery_crawl_worker.worker import CrawlWorker
from tools.operational_load.fixtures import ActivityTracker, DeterministicOfflineOrchestrator
from tools.operational_load.models import LoadRunReport

TERMINAL_URLS = ("COMPLETED", "NO_EMAIL", "FAILED", "CANCELLED")
ALLOWED_SIZES = (100, 500, 1000)
ALLOWED_WORKERS = (1, 2, 4)
NAMESPACE = uuid.UUID("65bd0332-6c0e-4f5e-a620-f9e00ca26828")


def _ids(size: int) -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid5(NAMESPACE, f"org:{size}"), uuid.uuid5(NAMESPACE, f"job:{size}")


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _redis_commands(client: redis.Redis) -> int:
    info: dict[str, Any] = await client.info(  # pyright: ignore[reportUnknownMemberType]
        "commandstats"
    )
    calls = 0
    for value in info.values():
        if isinstance(value, dict):
            calls += int(cast(dict[str, Any], value).get("calls", 0))
    return calls


class ConnectionTracker:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    def checkout(self, *_: object) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)

    def checkin(self, *_: object) -> None:
        self.active = max(0, self.active - 1)


async def _cleanup(
    session_factory: async_sessionmaker[AsyncSession], client: redis.Redis, size: int
) -> None:
    org_id, job_id = _ids(size)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(delete(ScanJob).where(ScanJob.id == job_id))
            await session.execute(delete(Organization).where(Organization.id == org_id))
    prefix = f"phase5c:load:{size}"
    cursor: int = 0
    while True:
        cursor, keys = await client.scan(  # pyright: ignore[reportUnknownMemberType]
            cursor=cursor, match=f"{prefix}:*", count=100
        )
        if keys:
            await client.delete(*keys)
        if cursor == 0:
            break


async def _seed(session_factory: async_sessionmaker[AsyncSession], size: int) -> uuid.UUID:
    org_id, job_id = _ids(size)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Organization(
                    id=org_id,
                    name="Phase 5C offline fixture",
                    slug=f"phase5c-offline-{size}",
                    status="ACTIVE",
                )
            )
            session.add(
                ScanJob(
                    id=job_id,
                    organization_id=org_id,
                    status=ScanJobStatus.QUEUED.value,
                    total_input_count=size,
                    valid_input_count=size,
                    duplicate_input_count=0,
                    queued_count=size,
                    running_count=0,
                    completed_count=0,
                    failed_count=0,
                    email_finding_count=0,
                )
            )
            session.add_all(
                ScanURL(
                    id=uuid.uuid5(NAMESPACE, f"url:{size}:{index}"),
                    scan_job_id=job_id,
                    original_index=index,
                    original_input=f"https://site{index:04d}.fixture.test/",
                    normalized_url=f"https://site{index:04d}.fixture.test/",
                    normalized_domain=f"site{index:04d}.fixture.test",
                    status=ScanURLStatus.QUEUED.value,
                    max_attempts=3,
                )
                for index in range(size)
            )
    return job_id


async def _wait_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    workers: list[CrawlWorker],
    timeout_seconds: float,
) -> tuple[float, int, int]:
    started = time.perf_counter()
    peak_tasks = 0
    peak_claims = 0
    deadline = started + timeout_seconds
    while time.perf_counter() < deadline:
        peak_tasks = max(peak_tasks, sum(len(worker._active_tasks) for worker in workers))  # pyright: ignore[reportPrivateUsage]
        peak_claims = max(peak_claims, sum(worker.total_held_claims for worker in workers))
        async with session_factory() as session:
            status = await session.scalar(select(ScanJob.status).where(ScanJob.id == job_id))
        if status in (
            ScanJobStatus.COMPLETED.value,
            ScanJobStatus.COMPLETED_WITH_ERRORS.value,
            ScanJobStatus.FAILED.value,
        ):
            return time.perf_counter() - started, peak_tasks, peak_claims
        await asyncio.sleep(0.02)
    raise TimeoutError(f"offline load run exceeded {timeout_seconds:.1f}s bound")


async def _fence_audit(
    session_factory: async_sessionmaker[AsyncSession], size: int, result: Any
) -> tuple[bool, bool]:
    org_id, job_id = _ids(size)
    url_id = uuid.uuid5(NAMESPACE, f"url:{size}:0")
    owner = "phase5c-fence-audit"
    async with session_factory() as session:
        before_attempts = await session.scalar(
            select(func.count()).select_from(CrawlAttempt).where(CrawlAttempt.scan_url_id == url_id)
        )
        before_findings = await session.scalar(
            select(func.count()).select_from(EmailFinding).where(EmailFinding.scan_url_id == url_id)
        )
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                update(ScanURL)
                .where(ScanURL.id == url_id)
                .values(
                    status="SCANNING",
                    lease_owner=owner,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    fence_token=10,
                    attempt_count=2,
                )
            )
    stale_claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="offline-fixture",
        normalized_url=None,
        normalized_domain=None,
        lease_owner=owner,
        fence_token=9,
        attempt_count=2,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    stale_rejected = False
    async with session_factory() as session:
        try:
            await ResultPersistenceService(session).persist_fenced_result(stale_claim, result)
        except LeaseLostError:
            stale_rejected = True
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                update(ScanURL)
                .where(ScanURL.id == url_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
    expired_claim = replace(stale_claim, fence_token=10)
    expired_rejected = False
    async with session_factory() as session:
        try:
            await ResultPersistenceService(session).persist_fenced_result(expired_claim, result)
        except LeaseLostError:
            expired_rejected = True
    async with session_factory() as session:
        after_attempts = await session.scalar(
            select(func.count()).select_from(CrawlAttempt).where(CrawlAttempt.scan_url_id == url_id)
        )
        after_findings = await session.scalar(
            select(func.count()).select_from(EmailFinding).where(EmailFinding.scan_url_id == url_id)
        )
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                update(ScanURL)
                .where(ScanURL.id == url_id)
                .values(
                    status="COMPLETED",
                    lease_owner=None,
                    lease_expires_at=None,
                    attempt_count=1,
                    fence_token=1,
                )
            )
    unchanged = before_attempts == after_attempts and before_findings == after_findings
    return stale_rejected and unchanged, expired_rejected and unchanged


async def run_load(
    *,
    size: int,
    worker_count: int,
    database_url: str,
    redis_url: str,
    timeout_seconds: float,
) -> LoadRunReport:
    if size not in ALLOWED_SIZES or worker_count not in ALLOWED_WORKERS:
        raise ValueError("size must be 100/500/1000 and workers must be 1/2/4")
    engine = create_async_engine(
        database_url,
        pool_size=max(4, worker_count * 2),
        max_overflow=0,
        pool_timeout=5,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    client = redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        redis_url, decode_responses=True, max_connections=16
    )
    await asyncio.wait_for(
        client.ping(),  # pyright: ignore[reportUnknownMemberType]
        timeout=2,
    )
    connection_tracker = ConnectionTracker()
    event.listen(engine.sync_engine, "checkout", connection_tracker.checkout)
    event.listen(engine.sync_engine, "checkin", connection_tracker.checkin)
    await _cleanup(session_factory, client, size)
    job_id = await _seed(session_factory, size)
    tracker = ActivityTracker()
    settings = [
        WorkerSettings(
            instance_id=uuid.uuid5(NAMESPACE, f"worker:{size}:{worker_count}:{index}").hex,
            concurrency=2,
            poll_interval=0.02,
            healthy_poll_interval=0.1,
            degraded_poll_interval=0.05,
            lease_duration=30,
            heartbeat_interval=5,
            redis_url=SecretStr(redis_url),
            redis_required=True,
            redis_key_prefix=f"phase5c:load:{size}",
        )
        for index in range(worker_count)
    ]
    workers = [
        CrawlWorker(
            session_factory,
            concurrency=2,
            poll_interval_seconds=0.02,
            lease_duration_seconds=30,
            heartbeat_interval_seconds=5,
            recovery_interval_seconds=1,
            orchestrator_factory=lambda: DeterministicOfflineOrchestrator(tracker),
            worker_settings=worker_settings,
        )
        for worker_settings in settings
    ]
    redis_before = await _redis_commands(client)
    tracemalloc.start()
    tasks = [asyncio.create_task(worker.run()) for worker in workers]
    try:
        elapsed, peak_tasks, peak_claims = await _wait_terminal(
            session_factory, job_id, workers, timeout_seconds
        )
    finally:
        for worker in workers:
            worker.request_shutdown()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=35)
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    redis_after = await _redis_commands(client)

    async with session_factory() as session:
        job = await session.get(ScanJob, job_id)
        urls = list(
            (await session.execute(select(ScanURL).where(ScanURL.scan_job_id == job_id))).scalars()
        )
        attempts = list(
            (
                await session.execute(
                    select(CrawlAttempt)
                    .join(ScanURL, CrawlAttempt.scan_url_id == ScanURL.id)
                    .where(ScanURL.scan_job_id == job_id)
                )
            ).scalars()
        )
        findings = (
            await session.execute(
                select(
                    EmailFinding.canonical_email,
                    EmailFinding.email_domain,
                    EmailFinding.classification,
                    EmailFinding.validation_status,
                    EmailFinding.is_role_based,
                )
                .where(EmailFinding.scan_job_id == job_id)
                .order_by(EmailFinding.canonical_email.asc())
            )
        ).all()
        duplicate_attempts = int(
            await session.scalar(
                select(func.count()).select_from(
                    select(CrawlAttempt.scan_url_id, CrawlAttempt.attempt_number)
                    .join(ScanURL, CrawlAttempt.scan_url_id == ScanURL.id)
                    .where(ScanURL.scan_job_id == job_id)
                    .group_by(CrawlAttempt.scan_url_id, CrawlAttempt.attempt_number)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            or 0
        )
        duplicate_findings = int(
            await session.scalar(
                select(func.count()).select_from(
                    select(EmailFinding.scan_url_id, EmailFinding.canonical_email)
                    .where(EmailFinding.scan_job_id == job_id)
                    .group_by(EmailFinding.scan_url_id, EmailFinding.canonical_email)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            or 0
        )
    if job is None:
        raise AssertionError("load job disappeared")
    latencies = [
        attempt.elapsed_seconds for attempt in attempts if attempt.elapsed_seconds is not None
    ]
    sequential = all(attempt.attempt_number == 1 for attempt in attempts)
    nonterminal = sum(row.status not in TERMINAL_URLS for row in urls)
    uncleared = sum(row.lease_owner is not None or row.lease_expires_at is not None for row in urls)
    finding_rows = [list(row) for row in findings]
    logical_payload = {
        "size": size,
        "statuses": sorted(row.status for row in urls),
        "findings": finding_rows,
    }
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer, lineterminator="\r\n")
    writer.writerow(
        ["canonical_email", "email_domain", "classification", "validation_status", "is_role_based"]
    )
    writer.writerows(finding_rows)
    fixture_result = await DeterministicOfflineOrchestrator(ActivityTracker(), 0).scan(
        "https://site0000.fixture.test/"
    )
    stale_zero, expired_zero = await _fence_audit(session_factory, size, fixture_result)
    counters_match = (
        job.queued_count == 0
        and job.running_count == 0
        and job.completed_count == size
        and job.failed_count == 0
        and job.email_finding_count == size
    )
    shutdown_clean = all(task.done() for task in tasks) and connection_tracker.active == 0
    report = LoadRunReport(
        size=size,
        workers=worker_count,
        worker_concurrency=2,
        elapsed_seconds=elapsed,
        urls_per_second=size / elapsed,
        pages_per_second=sum(row.pages_fetched or 0 for row in urls) / elapsed,
        p50_latency_seconds=_percentile(latencies, 0.50),
        p95_latency_seconds=_percentile(latencies, 0.95),
        p99_latency_seconds=_percentile(latencies, 0.99),
        peak_active_tasks=peak_tasks,
        peak_active_claims=peak_claims,
        peak_database_connections=connection_tracker.peak,
        redis_operations=max(0, redis_after - redis_before),
        redis_fallbacks=0,
        retry_total=sum(row.retry_count or 0 for row in urls),
        failure_total=sum(row.status == "FAILED" for row in urls),
        success_total=sum(row.status in ("COMPLETED", "NO_EMAIL") for row in urls),
        partial_total=sum(attempt.outcome == "PARTIAL" for attempt in attempts),
        peak_python_memory_bytes=peak_memory,
        result_checksum=_sha(logical_payload),
        csv_checksum=hashlib.sha256(csv_buffer.getvalue().encode("utf-8")).hexdigest(),
        attempt_rows=len(attempts),
        finding_rows=len(findings),
        duplicate_attempt_groups=duplicate_attempts,
        duplicate_finding_groups=duplicate_findings,
        sequential_attempts=sequential,
        stale_fence_zero_writes=stale_zero,
        expired_fence_zero_writes=expired_zero,
        nonterminal_rows=nonterminal,
        uncleared_claims=uncleared,
        job_counters_match=counters_match,
        shutdown_clean=shutdown_clean,
    )
    violations = (
        report.success_total != size
        or report.failure_total != 0
        or report.attempt_rows != size
        or report.finding_rows != size
        or report.duplicate_attempt_groups != 0
        or report.duplicate_finding_groups != 0
        or not report.sequential_attempts
        or not report.stale_fence_zero_writes
        or not report.expired_fence_zero_writes
        or report.nonterminal_rows != 0
        or report.uncleared_claims != 0
        or not report.job_counters_match
        or not report.shutdown_clean
        or report.peak_active_tasks > worker_count * 2
        or report.peak_active_claims > worker_count * 6
        or report.peak_database_connections > max(4, worker_count * 2)
    )
    await _cleanup(session_factory, client, size)
    await client.aclose()
    await engine.dispose()
    if violations:
        raise AssertionError(report.model_dump_json())
    return report
