"""CLI entry point for running the local PostgreSQL-backed crawl worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import signal
import sys
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from email_discovery_api.config import get_settings
from email_discovery_crawl_worker.config import WorkerSettings, get_worker_settings
from email_discovery_crawl_worker.worker import CrawlWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("email_discovery_crawl_worker.cli")


@dataclass(frozen=True, slots=True)
class EffectiveWorkerConfig:
    """Resolved worker configuration with explicit CLI over environment precedence."""

    worker_id: str | None
    concurrency: int
    poll_interval: float
    lease_duration: float
    heartbeat_interval: float
    max_scans: int | None


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate command line arguments."""
    parser = argparse.ArgumentParser(
        description="Local PostgreSQL-backed crawl worker for Email Discovery SaaS."
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Unique worker identifier (defaults to generated worker-<hash>)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Maximum concurrent scan tasks for this worker (default: from settings or 2)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="Polling interval in seconds when idle (default: from settings or 2.0)",
    )
    parser.add_argument(
        "--lease-duration",
        type=float,
        default=None,
        help="Lease duration in seconds (default: from settings or 120.0)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=None,
        help="Lease heartbeat renewal interval in seconds (default: from settings or 30.0)",
    )
    parser.add_argument(
        "--max-scans",
        type=int,
        default=None,
        help="Maximum total scans to execute before exiting (useful for testing)",
    )

    ns = parser.parse_args(args)
    if ns.concurrency is not None and ns.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if ns.poll_interval is not None and (
        ns.poll_interval <= 0 or not math.isfinite(ns.poll_interval)
    ):
        parser.error("--poll-interval must be greater than zero")
    if ns.lease_duration is not None and (
        ns.lease_duration <= 0 or not math.isfinite(ns.lease_duration)
    ):
        parser.error("--lease-duration must be greater than zero")
    if ns.heartbeat_interval is not None and (
        ns.heartbeat_interval <= 0 or not math.isfinite(ns.heartbeat_interval)
    ):
        parser.error("--heartbeat-interval must be greater than zero")
    if (
        ns.heartbeat_interval is not None
        and ns.lease_duration is not None
        and ns.heartbeat_interval >= ns.lease_duration
    ):
        parser.error("--heartbeat-interval must be strictly less than --lease-duration")
    if ns.max_scans is not None and ns.max_scans <= 0:
        parser.error("--max-scans must be positive")

    return ns


def resolve_effective_worker_config(
    cli_args: argparse.Namespace,
    worker_settings: WorkerSettings | None = None,
) -> EffectiveWorkerConfig:
    """Resolve effective worker configuration with strict precedence and invariant checks.

    Precedence order:
    1. Explicit CLI argument (if not None)
    2. Environment-backed WorkerSettings (if set or model default)
    """
    settings = worker_settings or get_worker_settings()

    worker_id = (
        cli_args.worker_id
        if cli_args.worker_id is not None
        else (settings.worker_id or settings.worker_label)
    )

    concurrency = cli_args.concurrency if cli_args.concurrency is not None else settings.concurrency

    poll_interval = (
        cli_args.poll_interval if cli_args.poll_interval is not None else settings.poll_interval
    )

    lease_duration = (
        cli_args.lease_duration if cli_args.lease_duration is not None else settings.lease_duration
    )

    heartbeat_interval = (
        cli_args.heartbeat_interval
        if cli_args.heartbeat_interval is not None
        else settings.heartbeat_interval
    )

    max_scans = cli_args.max_scans

    # Cross-field invariant validation
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if poll_interval <= 0 or not math.isfinite(poll_interval):
        raise ValueError("poll_interval must be greater than zero")
    if lease_duration <= 0 or not math.isfinite(lease_duration):
        raise ValueError("lease_duration must be greater than zero")
    if heartbeat_interval <= 0 or not math.isfinite(heartbeat_interval):
        raise ValueError("heartbeat_interval must be greater than zero")
    if heartbeat_interval >= lease_duration:
        raise ValueError("heartbeat_interval must be strictly less than lease_duration")
    if max_scans is not None and max_scans <= 0:
        raise ValueError("max_scans must be positive")

    return EffectiveWorkerConfig(
        worker_id=worker_id,
        concurrency=concurrency,
        poll_interval=poll_interval,
        lease_duration=lease_duration,
        heartbeat_interval=heartbeat_interval,
        max_scans=max_scans,
    )


async def run_worker(cli_args: argparse.Namespace) -> None:
    """Initialize DB connection pool and run crawl worker."""
    worker_settings = get_worker_settings()
    config = resolve_effective_worker_config(cli_args, worker_settings)
    api_settings = get_settings()
    db_url = api_settings.database_url.get_secret_value()

    engine = create_async_engine(
        db_url,
        pool_size=10,
        max_overflow=5,
        future=True,
    )

    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    worker = CrawlWorker(
        session_factory=session_factory,
        worker_id=config.worker_id,
        concurrency=config.concurrency,
        poll_interval_seconds=config.poll_interval,
        lease_duration_seconds=config.lease_duration,
        heartbeat_interval_seconds=config.heartbeat_interval,
        max_scans=config.max_scans,
        worker_settings=worker_settings,
    )

    loop = asyncio.get_running_loop()

    def _on_signal(_signum: int = 0, _frame: object = None) -> None:
        logger.info("Signal received. Requesting worker shutdown...")
        worker.request_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError, RuntimeError:
            try:
                signal.signal(sig, _on_signal)
            except Exception:
                pass

    try:
        await worker.start()
    finally:
        await engine.dispose()
        logger.info("Worker database connections closed cleanly.")


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    try:
        asyncio.run(run_worker(args))
    except KeyboardInterrupt:
        logger.info("Worker interrupted by keyboard.")
        sys.exit(0)


if __name__ == "__main__":
    main()
