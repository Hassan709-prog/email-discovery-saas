"""PostgreSQL-backed bounded and read-only operational diagnostic tests."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models import Organization, ScanJob, ScanURL
from email_discovery_api.services.operations import OperationalService

pytestmark = pytest.mark.anyio


async def test_diagnostics_detects_mismatches_and_due_work_without_writes(
    isolated_db_engine: AsyncEngine,
) -> None:
    org_id, job_id = uuid.uuid4(), uuid.uuid4()
    expired_id, retry_id = uuid.uuid4(), uuid.uuid4()
    factory = async_sessionmaker(isolated_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        async with session.begin():
            session.add(Organization(id=org_id, name="diagnostic fixture", slug=f"diag-{org_id}"))
            session.add(
                ScanJob(
                    id=job_id,
                    organization_id=org_id,
                    status="RUNNING",
                    total_input_count=2,
                    valid_input_count=2,
                    queued_count=2,
                    running_count=0,
                    completed_count=0,
                    failed_count=0,
                )
            )
            session.add_all(
                [
                    ScanURL(
                        id=expired_id,
                        scan_job_id=job_id,
                        original_index=0,
                        original_input="private-input-one",
                        status="SCANNING",
                        lease_owner="private-worker",
                        lease_expires_at=datetime.now(UTC) - timedelta(seconds=5),
                        attempt_count=1,
                    ),
                    ScanURL(
                        id=retry_id,
                        scan_job_id=job_id,
                        original_index=1,
                        original_input="private-input-two",
                        status="RETRY_WAIT",
                        next_retry_at=datetime.now(UTC) - timedelta(seconds=5),
                    ),
                ]
            )
    query_count = 0

    def count_queries(*_args: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(isolated_db_engine.sync_engine, "before_cursor_execute", count_queries)
    settings = SimpleNamespace(
        operations_query_timeout_seconds=2,
        operations_max_presence_records=256,
        redis_key_prefix="test",
    )
    redis_manager = SimpleNamespace(client=None)
    async with factory() as session:
        service = OperationalService(
            session,
            db_manager=SimpleNamespace(),
            redis_manager=redis_manager,
            settings=settings,
        )
        report = await service.diagnostics(limit=10)
    event.remove(isolated_db_engine.sync_engine, "before_cursor_execute", count_queries)
    assert query_count <= 4
    assert report.expired_leases.total == 1
    assert report.due_retries.total == 1
    assert report.counter_mismatches.total == 1
    serialized = report.model_dump_json()
    for private in (str(org_id), str(job_id), str(expired_id), str(retry_id), "private"):
        assert private not in serialized
    async with factory() as session:
        states = list(
            (
                await session.execute(
                    select(ScanURL.status).where(ScanURL.scan_job_id == job_id).order_by(ScanURL.id)
                )
            ).scalars()
        )
    assert sorted(states) == ["RETRY_WAIT", "SCANNING"]


async def test_redis_presence_limit_enforced_and_handles_zero() -> None:
    class MockRedis:
        def __init__(self) -> None:
            self.requested_stop = None

        async def zrevrange(
            self, key: str, start: int, stop: int, withscores: bool = False
        ) -> list[tuple[bytes, float]]:
            self.requested_stop = stop
            return [(f"{i:016x}".encode(), 1000.0) for i in range(stop + 1)]

        async def mget(self, keys: list[str]) -> list[str]:
            return ['{"state": "healthy", "concurrency": 2}'] * len(keys)

    redis_client = MockRedis()
    redis_manager = SimpleNamespace(client=redis_client)

    # Test limit=256 requests stop=255
    settings_256 = SimpleNamespace(
        operations_query_timeout_seconds=2,
        operations_max_presence_records=256,
        redis_key_prefix="test",
    )
    service_256 = OperationalService(
        session=None,  # pyright: ignore[reportArgumentType]
        db_manager=SimpleNamespace(),
        redis_manager=redis_manager,
        settings=settings_256,
    )
    records_256, _ = await service_256._read_presence(datetime.now(UTC))  # pyright: ignore[reportPrivateUsage]
    assert redis_client.requested_stop == 255
    assert len(records_256) == 256

    # Test limit=0 returns empty list safely
    settings_0 = SimpleNamespace(
        operations_query_timeout_seconds=2,
        operations_max_presence_records=0,
        redis_key_prefix="test",
    )
    service_0 = OperationalService(
        session=None,  # pyright: ignore[reportArgumentType]
        db_manager=SimpleNamespace(),
        redis_manager=redis_manager,
        settings=settings_0,
    )
    records_0, expired_0 = await service_0._read_presence(datetime.now(UTC))  # pyright: ignore[reportPrivateUsage]
    assert records_0 == []
    assert expired_0 == []


async def test_diagnostics_independent_truncation_per_category(
    isolated_db_engine: AsyncEngine,
) -> None:
    org_id = uuid.uuid4()
    factory = async_sessionmaker(isolated_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        async with session.begin():
            session.add(Organization(id=org_id, name="truncation fixture", slug=f"trunc-{org_id}"))
            # Add 5 eligible jobs (all work terminal)
            for _ in range(5):
                job_id = uuid.uuid4()
                session.add(
                    ScanJob(
                        id=job_id,
                        organization_id=org_id,
                        status="QUEUED",
                        total_input_count=1,
                        valid_input_count=1,
                        queued_count=0,
                        running_count=0,
                        completed_count=1,
                        failed_count=0,
                    )
                )
                session.add(
                    ScanURL(
                        id=uuid.uuid4(),
                        scan_job_id=job_id,
                        original_index=0,
                        original_input="input",
                        status="COMPLETED",
                    )
                )
            # Add 1 inactive job (no active work, terminal < valid)
            inactive_job_id = uuid.uuid4()
            session.add(
                ScanJob(
                    id=inactive_job_id,
                    organization_id=org_id,
                    status="QUEUED",
                    total_input_count=2,
                    valid_input_count=2,
                    queued_count=0,
                    running_count=0,
                    completed_count=0,
                    failed_count=0,
                )
            )

    settings = SimpleNamespace(
        operations_query_timeout_seconds=2,
        operations_max_presence_records=256,
        redis_key_prefix="test",
    )
    redis_manager = SimpleNamespace(client=None)
    async with factory() as session:
        service = OperationalService(
            session,
            db_manager=SimpleNamespace(),
            redis_manager=redis_manager,
            settings=settings,
        )
        report = await service.diagnostics(limit=3)

    assert report.jobs_eligible_for_finalization.total == 5
    assert report.jobs_eligible_for_finalization.truncated is True
    assert len(report.jobs_eligible_for_finalization.items) == 3

    assert report.nonterminal_jobs_without_active_work.total == 1
    assert report.nonterminal_jobs_without_active_work.truncated is False
    assert len(report.nonterminal_jobs_without_active_work.items) == 1

    assert report.counter_mismatches.total == 0
    assert report.counter_mismatches.truncated is False
    assert len(report.counter_mismatches.items) == 0
