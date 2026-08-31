"""Tests proving worker graceful shutdown leaves active leases recoverable."""

import asyncio

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_crawl_worker.config import WorkerSettings
from email_discovery_crawl_worker.worker import CrawlWorker

pytestmark = pytest.mark.anyio


async def test_worker_request_shutdown_stops_polling(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify requesting shutdown stops worker loop cleanly."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id="shutdown-worker",
        poll_interval_seconds=0.05,
        worker_settings=WorkerSettings(
            redis_url=SecretStr("redis://127.0.0.1:1/0"),
            redis_connect_timeout=0.05,
            redis_socket_timeout=0.05,
            redis_rate_limit_fallback_mode="single_worker_local",
        ),
    )

    worker_task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.1)

    worker.request_shutdown()
    await asyncio.wait_for(worker_task, timeout=1.0)
    assert not worker._running  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "invalid_grace",
    [0.0, -1.0, -60.0, float("nan"), float("inf"), float("-inf"), 600.1, 1000.0],
)
def test_shutdown_grace_period_rejected(invalid_grace: float) -> None:
    """Verify zero, negative, non-finite, and > 600 shutdown grace periods are rejected."""
    with pytest.raises(ValueError, match="shutdown_grace_period"):
        WorkerSettings(shutdown_grace_period=invalid_grace)

    with pytest.raises(ValueError, match="shutdown_grace_period"):
        CrawlWorker(
            session_factory=None,  # pyright: ignore[reportArgumentType]
            shutdown_grace_period_seconds=invalid_grace,
        )


def test_shutdown_grace_period_accepted() -> None:
    """Verify 60 seconds shutdown grace period is accepted."""
    settings = WorkerSettings(shutdown_grace_period=60.0)
    assert settings.shutdown_grace_period == 60.0

    worker = CrawlWorker(
        session_factory=None,  # pyright: ignore[reportArgumentType]
        shutdown_grace_period_seconds=60.0,
    )
    assert worker.shutdown_grace_period == 60.0
