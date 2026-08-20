"""Phase 5B Multi-Worker Deterministic Result-Parity & Benchmark Test Suite."""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.organization import Organization
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.services.scan_jobs import ScanJobService
from email_discovery_crawl_worker.config import WorkerSettings
from email_discovery_crawl_worker.worker import CrawlWorker
from email_scanner.models import SiteScanOutcome, SiteScanResult, SiteScanStatistics


class DeterministicOrchestrator:
    """Offline scanner used to compare worker scheduling without network variance."""

    async def scan(self, url: str) -> SiteScanResult:
        return SiteScanResult(
            starting_url=url,
            outcome=SiteScanOutcome.COMPLETED_NO_EMAILS,
            statistics=SiteScanStatistics(
                pages_queued=1,
                pages_attempted=1,
                pages_fetched=1,
                pages_blocked_by_robots=0,
                pages_failed=0,
                urls_discovered=0,
                accepted_email_findings=0,
                rejected_email_candidates=0,
                elapsed_seconds=0.01,
                stop_reason="QUEUE_EXHAUSTED",
            ),
            page_records=(),
            email_findings=(),
            rejected_email_candidates=(),
        )


@dataclass
class ParityRunResult:
    mode_name: str
    raw_inputs: int
    accepted_targets_count: int
    completed: int
    failed: int
    duplicates: int
    job_status: str
    logical_checksum: str


def compute_logical_checksum(
    raw_inputs: int,
    accepted: int,
    completed: int,
    failed: int,
    duplicates: int,
    job_status: str,
) -> str:
    """Compute 64-character deterministic logical checksum."""
    payload = {
        "raw_inputs": raw_inputs,
        "accepted": accepted,
        "completed": completed,
        "failed": failed,
        "duplicates": duplicates,
        "job_status": job_status,
    }
    dumped = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


@pytest.mark.anyio
async def test_distributed_result_parity_across_worker_counts(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Assert identical 64-character SHA-256 logical checksums across worker setups."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    results: list[ParityRunResult] = []
    modes = [
        ("Mode 1 (1x1)", 1, 1),
        ("Mode 2 (1x4)", 1, 4),
        ("Mode 3 (2x2)", 2, 2),
        ("Mode 4 (4x2)", 4, 2),
    ]

    for mode_name, worker_count, concurrency in modes:
        org_id = uuid.uuid4()
        job_id = uuid.uuid4()

        async with session_factory.begin() as session:
            org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:6]}")
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                status=ScanJobStatus.QUEUED.value,
                total_input_count=5,
                valid_input_count=4,
                duplicate_input_count=1,
                queued_count=4,
            )
            session.add_all([org, job])

            targets = [
                "https://site-a.com",
                "https://site-b.com",
                "https://site-c.com",
                "https://failing-site.org",
            ]
            for i, target in enumerate(targets):
                session.add(
                    ScanURL(
                        id=uuid.uuid4(),
                        scan_job_id=job_id,
                        original_index=i,
                        original_input=target,
                        normalized_url=target,
                        status=ScanURLStatus.QUEUED.value,
                        fence_token=0,
                        attempt_count=0,
                        max_attempts=3,
                    )
                )

        # Run worker instances
        workers: list[CrawlWorker] = []
        tasks: list[asyncio.Task[None]] = []

        for w_idx in range(worker_count):
            w_settings = WorkerSettings(
                worker_label=f"{mode_name}-w{w_idx}",
                concurrency=concurrency,
                redis_required=False,
                redis_rate_limit_fallback_mode="single_worker_local",
                redis_url=SecretStr("redis://127.0.0.1:1/0"),
                redis_connect_timeout=0.05,
                redis_socket_timeout=0.05,
            )
            w = CrawlWorker(
                session_factory=session_factory,
                concurrency=concurrency,
                poll_interval_seconds=0.1,
                recovery_interval_seconds=60.0,
                worker_settings=w_settings,
                orchestrator_factory=DeterministicOrchestrator,
            )
            workers.append(w)

        for w in workers:
            tasks.append(asyncio.create_task(w.run()))

        for _ in range(100):
            async with session_factory() as session:
                urls_res = await session.execute(
                    select(ScanURL.status).where(ScanURL.scan_job_id == job_id)
                )
                statuses = list(urls_res.scalars())
            if all(
                status
                in (
                    ScanURLStatus.COMPLETED.value,
                    ScanURLStatus.NO_EMAIL.value,
                    ScanURLStatus.FAILED.value,
                )
                for status in statuses
            ):
                break
            await asyncio.sleep(0.05)

        for w in workers:
            w.request_shutdown()

        await asyncio.gather(*tasks, return_exceptions=True)

        async with session_factory() as session:
            await ScanJobService(session).try_finalize_job(org_id, job_id)

        async with session_factory() as session:
            job_res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
            final_job = job_res.scalar_one()

            urls_res = await session.execute(select(ScanURL).where(ScanURL.scan_job_id == job_id))
            urls = list(urls_res.scalars().all())

            completed_cnt = sum(
                1
                for u in urls
                if u.status in (ScanURLStatus.COMPLETED.value, ScanURLStatus.NO_EMAIL.value)
            )
            failed_cnt = sum(1 for u in urls if u.status == ScanURLStatus.FAILED.value)

            chk = compute_logical_checksum(
                raw_inputs=5,
                accepted=4,
                completed=completed_cnt,
                failed=failed_cnt,
                duplicates=1,
                job_status=final_job.status,
            )

            results.append(
                ParityRunResult(
                    mode_name=mode_name,
                    raw_inputs=5,
                    accepted_targets_count=4,
                    completed=completed_cnt,
                    failed=failed_cnt,
                    duplicates=1,
                    job_status=final_job.status,
                    logical_checksum=chk,
                )
            )

    # Print parity audit table
    print("\n" + "=" * 110)
    print("PHASE 5B DISTRIBUTED RESULT-PARITY AUDIT TABLE")
    print("=" * 110)
    print(
        f"{'Mode':<15} | {'Raw':<5} | {'Acc':<5} | {'Comp':<5} | "
        f"{'Fail':<5} | {'Dup':<5} | {'SHA-256 Checksum':<64}"
    )
    print("-" * 110)
    for r in results:
        print(
            f"{r.mode_name:<15} | {r.raw_inputs:<5} | {r.accepted_targets_count:<5} | "
            f"{r.completed:<5} | {r.failed:<5} | {r.duplicates:<5} | {r.logical_checksum:<64}"
        )
    print("=" * 110)

    # Assert checksums across all modes match 100%
    first_chk = results[0].logical_checksum
    for r in results:
        assert r.logical_checksum == first_chk, f"Mode {r.mode_name} checksum mismatch!"
