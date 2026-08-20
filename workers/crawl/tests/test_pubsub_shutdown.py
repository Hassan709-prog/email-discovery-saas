"""Focused tests proving Pub/Sub listener cancellation and worker graceful shutdown teardown."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_crawl_worker.config import WorkerSettings
from email_discovery_crawl_worker.worker import CrawlWorker

pytestmark = pytest.mark.anyio


def offline_worker_settings() -> WorkerSettings:
    """Return settings that fail Redis probing promptly and use the safe local fallback."""
    return WorkerSettings(
        redis_url=SecretStr("redis://127.0.0.1:1/0"),
        redis_connect_timeout=0.05,
        redis_socket_timeout=0.05,
        redis_rate_limit_fallback_mode="single_worker_local",
    )


async def test_pubsub_listener_cancellation_unsubscribes_and_closes() -> None:
    """Verify listener cancellation explicitly unsubscribes channel and closes Pub/Sub object."""
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    mock_pubsub.get_message = AsyncMock(side_effect=asyncio.CancelledError)

    mock_redis = MagicMock()
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    mock_redis.aclose = AsyncMock()

    session_factory = MagicMock(spec=async_sessionmaker)
    settings = offline_worker_settings()

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="test-worker",
        worker_settings=settings,
        redis_client=mock_redis,
    )

    worker._running = True  # pyright: ignore[reportPrivateUsage]
    worker._shutdown_event.clear()  # pyright: ignore[reportPrivateUsage]

    listener_task = asyncio.create_task(worker._run_pubsub_listener())  # pyright: ignore[reportPrivateUsage]
    await asyncio.sleep(0.01)

    worker.request_shutdown()
    listener_task.cancel()

    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    mock_pubsub.unsubscribe.assert_called()
    mock_pubsub.close.assert_called()


async def test_shutdown_completes_promptly_with_active_subscription(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify shutdown completes promptly without waiting for poll interval."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    settings = offline_worker_settings()

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="shutdown-prompt-worker",
        poll_interval_seconds=10.0,
        worker_settings=settings,
    )

    worker_task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.2)

    start_time = asyncio.get_running_loop().time()
    worker.request_shutdown()
    await asyncio.wait_for(worker_task, timeout=5.0)
    elapsed = asyncio.get_running_loop().time() - start_time

    assert elapsed < 5.0
    assert not worker._running  # pyright: ignore[reportPrivateUsage]


async def test_cleanup_runs_exactly_once(isolated_db_engine: AsyncEngine) -> None:
    """Verify _drain_tasks is idempotent and runs cleanup exactly once."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    settings = offline_worker_settings()

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="idempotent-worker",
        worker_settings=settings,
    )

    await worker._drain_tasks()  # pyright: ignore[reportPrivateUsage]
    assert getattr(worker, "_drained", False) is True

    # Second invocation should return immediately without error
    await worker._drain_tasks()  # pyright: ignore[reportPrivateUsage]


async def test_redis_already_disconnected_does_not_hang_cleanup(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify cleanup handles pre-disconnected Redis client/pool gracefully without hanging."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    settings = offline_worker_settings()

    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock(side_effect=ConnectionError("Already disconnected"))

    mock_pool = MagicMock()
    mock_pool.disconnect = MagicMock(side_effect=RuntimeError("Pool closed"))

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="disconnected-redis-worker",
        worker_settings=settings,
        redis_client=mock_redis,
        redis_pool=mock_pool,
    )

    await asyncio.wait_for(worker._drain_tasks(), timeout=2.0)  # pyright: ignore[reportPrivateUsage]
    assert worker.redis_client is None
    assert worker.redis_pool is None


async def test_repeated_shutdown_calls_are_idempotent(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify request_shutdown can be called multiple times safely."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    settings = offline_worker_settings()

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="repeat-shutdown-worker",
        worker_settings=settings,
    )

    worker.request_shutdown()
    worker.request_shutdown()
    worker.request_shutdown()

    assert not worker._running  # pyright: ignore[reportPrivateUsage]


async def test_no_listener_or_worker_task_remains_after_shutdown(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify no subtasks remain running after worker run loop completes."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    settings = offline_worker_settings()

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="clean-tasks-worker",
        poll_interval_seconds=0.01,
        worker_settings=settings,
    )

    worker_task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.2)

    worker.request_shutdown()
    await asyncio.wait_for(worker_task, timeout=5.0)

    assert worker._pubsub_task is None  # pyright: ignore[reportPrivateUsage]
    assert worker._presence_task is None  # pyright: ignore[reportPrivateUsage]
    assert len(worker._active_tasks) == 0  # pyright: ignore[reportPrivateUsage]
