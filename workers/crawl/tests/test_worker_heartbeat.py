"""Tests for worker lease heartbeat renewal and fast cancellation observation."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import select

from email_discovery_api.models import ScanJob, ScanURL
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.worker_contracts import URLClaim
from email_discovery_crawl_worker.worker import CrawlWorker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def seeded_scanning_url(
    isolated_db_engine: AsyncEngine, test_user_and_token: dict[str, Any]
) -> dict[str, Any]:
    """Seed database with an active SCANNING URL."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=1,
                valid_input_count=1,
                queued_count=0,
                running_count=1,
                completed_count=0,
                failed_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://example.com",
                normalized_url="https://example.com/",
                normalized_domain="example.com",
                status=ScanURLStatus.SCANNING.value,
                lease_owner="worker-hb-1",
                fence_token=1,
                attempt_count=1,
            )
            session.add_all([job, url])

    claim = URLClaim(
        scan_url_id=url_id,
        organization_id=org_id,
        job_id=job_id,
        original_input="https://example.com",
        normalized_url="https://example.com/",
        normalized_domain="example.com",
        lease_owner="worker-hb-1",
        fence_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=None,  # type: ignore
    )

    return {
        "org_id": org_id,
        "job_id": job_id,
        "url_id": url_id,
        "claim": claim,
        "session_factory": session_factory,
    }


async def test_heartbeat_observes_job_cancellation_fast(
    seeded_scanning_url: dict[str, Any],
) -> None:
    """Verify heartbeat task detects job cancellation and sets cancel_event signal fast."""
    session_factory = seeded_scanning_url["session_factory"]
    claim: URLClaim = seeded_scanning_url["claim"]
    job_id = seeded_scanning_url["job_id"]

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="worker-hb-1",
        heartbeat_interval_seconds=0.05,
        lease_duration_seconds=10.0,
    )

    cancel_event = asyncio.Event()
    lease_lost_event = asyncio.Event()

    # Mark job CANCELLING in DB
    async with session_factory() as session:
        async with session.begin():
            job = (await session.execute(select(ScanJob).where(ScanJob.id == job_id))).scalar_one()
            job.status = ScanJobStatus.CANCELLING.value

    hb_task = asyncio.create_task(
        worker._run_heartbeat(claim, cancel_event, lease_lost_event)  # pyright: ignore[reportPrivateUsage]
    )

    # Wait for the heartbeat signal itself instead of assuming database scheduling latency.
    await asyncio.wait_for(cancel_event.wait(), timeout=1.0)
    assert cancel_event.is_set()
    hb_task.cancel()
    await asyncio.gather(hb_task, return_exceptions=True)
