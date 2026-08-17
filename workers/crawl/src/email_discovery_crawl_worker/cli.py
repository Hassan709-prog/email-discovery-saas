"""CLI entry point for running the local PostgreSQL-backed crawl worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from email_discovery_api.config import get_settings
from email_discovery_crawl_worker.worker import CrawlWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("email_discovery_crawl_worker.cli")


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
        default=2,
        help="Maximum concurrent scan tasks for this worker (default: 2)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds when idle (default: 2.0)",
    )
    parser.add_argument(
        "--lease-duration",
        type=float,
        default=120.0,
        help="Lease duration in seconds (default: 120.0)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=30.0,
        help="Lease heartbeat renewal interval in seconds (default: 30.0)",
    )
    parser.add_argument(
        "--max-scans",
        type=int,
        default=None,
        help="Maximum total scans to execute before exiting (useful for testing)",
    )

    ns = parser.parse_args(args)
    if ns.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if ns.lease_duration <= 0:
        parser.error("--lease-duration must be greater than zero")
    if ns.heartbeat_interval <= 0:
        parser.error("--heartbeat-interval must be greater than zero")
    if ns.heartbeat_interval >= ns.lease_duration:
        parser.error("--heartbeat-interval must be strictly less than --lease-duration")
    if ns.max_scans is not None and ns.max_scans <= 0:
        parser.error("--max-scans must be positive")

    return ns


async def run_worker(cli_args: argparse.Namespace) -> None:
    """Initialize DB connection pool and run crawl worker."""
    settings = get_settings()
    db_url = settings.database_url.get_secret_value()

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
        worker_id=cli_args.worker_id,
        concurrency=cli_args.concurrency,
        poll_interval_seconds=cli_args.poll_interval,
        lease_duration_seconds=cli_args.lease_duration,
        heartbeat_interval_seconds=cli_args.heartbeat_interval,
        max_scans=cli_args.max_scans,
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
