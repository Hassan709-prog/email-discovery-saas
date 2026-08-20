"""Focused unit and integration tests proving CrawlWorker polling behavior."""

import asyncio
import math
import time

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_crawl_worker.worker import CrawlWorker, WorkerState

pytestmark = pytest.mark.anyio


async def test_offline_mode_honors_poll_interval(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Prove no-Redis/offline mode uses configured poll_interval_seconds."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="offline-poll-worker",
        poll_interval_seconds=0.05,
    )

    assert worker._idle_wait_timeout() == 0.05  # pyright: ignore[reportPrivateUsage]


async def test_small_explicit_interval_does_not_become_healthy_interval(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Prove small explicit polling interval (0.01) is not overridden by healthy_poll_interval."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="small-interval-worker",
        poll_interval_seconds=0.01,
    )
    worker.state = WorkerState.ACTIVE

    # Without active Pub/Sub, _idle_wait_timeout must return 0.01, not healthy_poll_interval (10.0)
    assert worker._idle_wait_timeout() == 0.01  # pyright: ignore[reportPrivateUsage]
    assert worker._idle_wait_timeout() != worker.settings.healthy_poll_interval  # pyright: ignore[reportPrivateUsage]


async def test_healthy_pubsub_wakes_worker_before_fallback_timeout(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Prove healthy Pub/Sub signals wake idle wait immediately before fallback timeout."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="pubsub-wake-worker",
        poll_interval_seconds=2.0,
    )
    worker.state = WorkerState.ACTIVE

    # Simulate active pubsub task and redis client
    class DummyTask:
        def done(self) -> bool:
            return False

    worker.redis_client = object()  # type: ignore[assignment]
    worker._pubsub_task = DummyTask()  # type: ignore[assignment]

    assert worker._idle_wait_timeout() == worker.settings.healthy_poll_interval  # pyright: ignore[reportPrivateUsage]

    # Test immediate wake on signal
    start_time = time.monotonic()

    async def trigger_signal() -> None:
        await asyncio.sleep(0.02)
        worker._work_signal_event.set()  # pyright: ignore[reportPrivateUsage]

    task = asyncio.create_task(trigger_signal())
    await asyncio.wait_for(
        worker._work_signal_event.wait(),  # pyright: ignore[reportPrivateUsage]
        timeout=worker._idle_wait_timeout(),  # pyright: ignore[reportPrivateUsage]
    )
    elapsed = time.monotonic() - start_time
    await task

    assert elapsed < 0.5


async def test_shutdown_interrupts_idle_waiting_immediately(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Prove requesting shutdown unblocks idle wait instantly."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="shutdown-idle-worker",
        poll_interval_seconds=5.0,
    )

    start_time = time.monotonic()

    async def trigger_shutdown() -> None:
        await asyncio.sleep(0.02)
        worker.request_shutdown()

    shutdown_task = asyncio.create_task(trigger_shutdown())

    try:
        await asyncio.wait_for(
            worker._work_signal_event.wait(),  # pyright: ignore[reportPrivateUsage]
            timeout=worker._idle_wait_timeout(),  # pyright: ignore[reportPrivateUsage]
        )
    except TimeoutError:
        pytest.fail("Shutdown did not wake idle wait immediately")

    elapsed = time.monotonic() - start_time
    await shutdown_task

    assert elapsed < 0.5
    assert worker._shutdown_event.is_set()  # pyright: ignore[reportPrivateUsage]


async def test_no_busy_loop_validation_and_positive_intervals(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Prove invalid non-positive or non-finite intervals raise ValueError."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    with pytest.raises(ValueError, match="must be a finite number greater than zero"):
        CrawlWorker(
            session_factory=session_factory,
            worker_id="invalid-worker",
            poll_interval_seconds=0.0,
        )

    with pytest.raises(ValueError, match="must be a finite number greater than zero"):
        CrawlWorker(
            session_factory=session_factory,
            worker_id="invalid-worker-neg",
            poll_interval_seconds=-1.0,
        )

    with pytest.raises(ValueError, match="must be a finite number greater than zero"):
        CrawlWorker(
            session_factory=session_factory,
            worker_id="invalid-worker-nan",
            poll_interval_seconds=float("nan"),
        )

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="valid-worker",
        poll_interval_seconds=2.0,
    )
    timeout = worker._idle_wait_timeout()  # pyright: ignore[reportPrivateUsage]
    assert math.isfinite(timeout)
    assert timeout > 0


async def test_redis_strict_pause_behavior_remains_intact(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Prove DEGRADED_STRICT_PAUSED state returns degraded_poll_interval."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="strict-pause-worker",
        poll_interval_seconds=0.01,
    )
    worker.state = WorkerState.DEGRADED_STRICT_PAUSED

    assert worker._idle_wait_timeout() == worker.settings.degraded_poll_interval  # pyright: ignore[reportPrivateUsage]
    assert worker._idle_wait_timeout() != 0.01  # pyright: ignore[reportPrivateUsage]
