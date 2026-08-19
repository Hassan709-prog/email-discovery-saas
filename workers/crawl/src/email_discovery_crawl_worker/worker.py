"""Local PostgreSQL-backed crawl worker engine with optional Redis Pub/Sub coordination."""

from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.scan_jobs import ScanJobService
from email_discovery_api.services.worker_contracts import (
    HeartbeatStatus,
    URLClaim,
)
from email_discovery_crawl_worker.config import WorkerSettings, get_worker_settings
from email_discovery_crawl_worker.redis_gate import RedisDomainRequestGate
from email_scanner.orchestration import SiteScanOrchestrator
from email_scanner.request_gate import DomainRequestGate

logger = logging.getLogger(__name__)


class WorkerRedisHealthState(enum.StrEnum):
    """Unified Redis health state for worker process."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"


class CrawlWorker:
    """Async worker polling PostgreSQL for queued scan URLs and running scans outside DB locks."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str | None = None,
        concurrency: int = 2,
        poll_interval_seconds: float = 2.0,
        lease_duration_seconds: float = 120.0,
        heartbeat_interval_seconds: float = 30.0,
        max_scans: int | None = None,
        recovery_interval_seconds: float = 15.0,
        orchestrator_factory: Any | None = None,
        worker_settings: WorkerSettings | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1.")
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be greater than zero.")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be greater than zero.")
        if heartbeat_interval_seconds >= lease_duration_seconds:
            raise ValueError(
                "heartbeat_interval_seconds must be strictly less than lease_duration_seconds."
            )
        if max_scans is not None and max_scans <= 0:
            raise ValueError("max_scans must be greater than zero.")

        self.session_factory = session_factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.concurrency = concurrency
        self.poll_interval = poll_interval_seconds
        self.lease_duration = lease_duration_seconds
        self.heartbeat_interval = heartbeat_interval_seconds
        self.max_scans = max_scans
        self.recovery_interval = recovery_interval_seconds
        self.orchestrator_factory = orchestrator_factory
        self.settings = worker_settings or get_worker_settings()

        from email_scanner.dns import DeadlockFreeSingleFlightGroup, WorkerDNSCache

        self.dns_cache = WorkerDNSCache()
        self.single_flight: DeadlockFreeSingleFlightGroup[tuple[str, int, str], tuple[str, ...]] = (
            DeadlockFreeSingleFlightGroup()
        )

        self.local_gate = DomainRequestGate(
            default_minimum_interval_seconds=self.settings.min_domain_interval_ms / 1000.0
        )

        # Redis lifecycle state
        self.redis_pool: redis.ConnectionPool | None = None
        self.redis_client: redis.Redis | None = None
        self.redis_gate: RedisDomainRequestGate | None = None
        self.health_state = WorkerRedisHealthState.DISCONNECTED

        self._running = False
        self._shutdown_event = asyncio.Event()
        self._work_signal_event = asyncio.Event()
        self._fill_lock = asyncio.Lock()
        self._pubsub_task: asyncio.Task[None] | None = None

        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._claimed_count = 0
        self._processed_count = 0
        self._last_recovery_at = 0.0

    @property
    def claimed_count(self) -> int:
        """Return total successfully claimed URLs."""
        return self._claimed_count

    @property
    def processed_count(self) -> int:
        """Return total processed claim tasks."""
        return self._processed_count

    async def _init_redis(self) -> None:
        """Initialize worker Redis connection pool and Pub/Sub listener."""
        raw_url = self.settings.redis_url.get_secret_value()
        try:
            self.redis_pool = redis.ConnectionPool.from_url(  # pyright: ignore[reportUnknownMemberType]
                raw_url,
                max_connections=self.settings.redis_max_connections,
                socket_timeout=self.settings.redis_socket_timeout,
                socket_connect_timeout=self.settings.redis_connect_timeout,
            )
            self.redis_client = redis.Redis(connection_pool=self.redis_pool)
            await asyncio.wait_for(
                self.redis_client.ping(),  # pyright: ignore[reportUnknownMemberType]
                timeout=self.settings.redis_connect_timeout,
            )
            self.health_state = WorkerRedisHealthState.HEALTHY
            self.redis_gate = RedisDomainRequestGate(
                redis_client=self.redis_client,
                settings=self.settings,
                local_fallback_gate=self.local_gate,
            )
            logger.info("Worker %s connected to Redis successfully.", self.worker_id)
        except Exception as exc:
            self.health_state = WorkerRedisHealthState.DEGRADED
            self.redis_client = None
            self.redis_gate = RedisDomainRequestGate(
                redis_client=None,
                settings=self.settings,
                local_fallback_gate=self.local_gate,
            )
            logger.warning(
                "Worker Redis probe failed [code=WORKER_REDIS_INIT_FAILED, error_type=%s]",
                type(exc).__name__,
            )

    async def _run_pubsub_listener(self) -> None:
        """Background task maintaining Redis Pub/Sub subscription with auto-reconnect backoff."""
        channel_name = f"{self.settings.redis_key_prefix}:events:work_available"
        backoff = 2.0
        max_backoff = self.settings.redis_pubsub_reconnect_max_backoff

        while self._running and not self._shutdown_event.is_set():
            if self.redis_client is None:
                await asyncio.sleep(backoff)
                try:
                    await self._init_redis()
                    if self.redis_client is not None:
                        backoff = 2.0  # Reset backoff on stable reconnect
                except Exception:
                    backoff = min(max_backoff, backoff * 2.0)
                continue

            pubsub = self.redis_client.pubsub()  # pyright: ignore[reportUnknownMemberType]
            try:
                await pubsub.subscribe(channel_name)  # pyright: ignore[reportUnknownMemberType]
                self.health_state = WorkerRedisHealthState.HEALTHY
                logger.info("Subscribed to wake-up channel %s", channel_name)

                while self._running and not self._shutdown_event.is_set():
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)  # pyright: ignore[reportUnknownVariableType]
                    if msg is not None:
                        # Coalesce wake signals without spawning duplicate capacity loops
                        self._work_signal_event.set()

            except (RedisError, asyncio.CancelledError, Exception) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    break
                self.health_state = WorkerRedisHealthState.DEGRADED
                logger.warning(
                    "Worker Pub/Sub listener error [code=PUBSUB_LISTENER_ERROR, error_type=%s]",
                    type(exc).__name__,
                )
                try:
                    await pubsub.unsubscribe(channel_name)  # pyright: ignore[reportUnknownMemberType]
                    await pubsub.close()
                except Exception:
                    pass

                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2.0)

    async def start(self) -> None:
        """Start worker claim polling and background tasks."""
        self._running = True
        self._shutdown_event.clear()
        now = time.monotonic()
        self._last_recovery_at = now

        await self._init_redis()
        self._pubsub_task = asyncio.create_task(self._run_pubsub_listener())

        # Run initial lease recovery on startup
        async with self.session_factory() as session:
            work_service = CrawlWorkService(session)
            recovered = await work_service.recover_expired_leases()
            if recovered > 0:
                logger.info("Startup recovered %d expired leases.", recovered)

        logger.info(
            "CrawlWorker %s started (concurrency=%d, health=%s).",
            self.worker_id,
            self.concurrency,
            self.health_state.value,
        )

        try:
            while self._running and not self._shutdown_event.is_set():
                claimed_any = await self._fill_capacity_and_claim()
                if self.max_scans is not None and self._claimed_count >= self.max_scans:
                    logger.info(
                        "Reached max_scans limit (%d). Stopping claim loop.", self.max_scans
                    )
                    self._running = False
                    break

                now = time.monotonic()
                if now - self._last_recovery_at >= self.recovery_interval:
                    self._last_recovery_at = now
                    try:
                        async with self.session_factory() as session:
                            work_service = CrawlWorkService(session)
                            await work_service.recover_expired_leases()
                            job_service = ScanJobService(session)
                            await job_service.finalize_eligible_stuck_jobs()
                    except Exception:
                        logger.warning(
                            "Periodic lease recovery encountered an exception.", exc_info=True
                        )

                if not self._running or self._shutdown_event.is_set():
                    break

                # Capacity refill vs idle poll wait
                if len(self._active_tasks) >= self.concurrency:
                    snapshot = tuple(self._active_tasks)
                    if snapshot:
                        done, _ = await asyncio.wait(snapshot, return_when=asyncio.FIRST_COMPLETED)
                        for t in done:
                            if not t.cancelled():
                                _ = t.exception()
                elif not claimed_any:
                    # Select polling interval based on Redis health state
                    sleep_interval = (
                        self.settings.healthy_poll_interval
                        if self.health_state is WorkerRedisHealthState.HEALTHY
                        else self.settings.degraded_poll_interval
                    )
                    try:
                        await asyncio.wait_for(
                            self._work_signal_event.wait(), timeout=sleep_interval
                        )
                        self._work_signal_event.clear()
                    except TimeoutError:
                        pass

        except asyncio.CancelledError:
            logger.info("Worker run loop cancelled.")
        finally:
            await self._drain_tasks()

    def request_shutdown(self) -> None:
        """Signal graceful worker shutdown without failing running jobs."""
        logger.info("Shutdown requested for worker %s.", self.worker_id)
        self._running = False
        self._shutdown_event.set()
        self._work_signal_event.set()

    async def _drain_tasks(self) -> None:
        """Wait for active scan tasks during graceful shutdown and release resources."""
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            self._pubsub_task = None

        if self._active_tasks:
            logger.info("Draining %d active scan tasks...", len(self._active_tasks))
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            self._active_tasks.clear()

        if self.redis_client is not None:
            try:
                await self.redis_client.aclose()
            except Exception:
                pass
            self.redis_client = None

        if self.redis_pool is not None:
            try:
                await self.redis_pool.disconnect()
            except Exception:
                pass
            self.redis_pool = None

        await self.single_flight.shutdown()
        await self.dns_cache.clear()

    async def _fill_capacity_and_claim(self) -> bool:
        """Poll and claim repeatedly under a single-flight lock to prevent overlapping loops."""
        async with self._fill_lock:
            claimed_any = False

            while (
                self._running
                and not self._shutdown_event.is_set()
                and len(self._active_tasks) < self.concurrency
            ):
                if self.max_scans is not None and self._claimed_count >= self.max_scans:
                    break

                # Pre-claim Redis health check in strict_pause mode
                if (
                    self.settings.redis_rate_limit_fallback_mode.lower() == "strict_pause"
                    and self.health_state is not WorkerRedisHealthState.HEALTHY
                ):
                    logger.debug(
                        "Pre-claim check: Redis unavailable in strict_pause mode. Deferring claim."
                    )
                    break

                async with self.session_factory() as session:
                    work_service = CrawlWorkService(session)
                    claim = await work_service.claim_next_url(
                        lease_owner=self.worker_id,
                        lease_duration_seconds=self.lease_duration,
                    )

                if claim is None:
                    break

                self._claimed_count += 1
                claimed_any = True

                task = asyncio.create_task(self._process_claim_task(claim))
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)

            return claimed_any

    async def _process_claim_task(self, claim: URLClaim) -> None:
        """Execute scanner outside transaction for a claimed URL and persist results."""
        cancel_event = asyncio.Event()
        lease_lost_event = asyncio.Event()

        heartbeat_task = asyncio.create_task(
            self._run_heartbeat(claim, cancel_event, lease_lost_event)
        )

        def is_cancelled() -> bool:
            return cancel_event.is_set() or self._shutdown_event.is_set()

        request_gate = self.redis_gate or self.local_gate

        if self.orchestrator_factory:
            orchestrator = self.orchestrator_factory()
        else:
            from email_scanner.dns import SystemDNSResolver
            from email_scanner.fetching import AsyncHTTPFetcher

            dns_resolver = SystemDNSResolver(
                dns_cache=self.dns_cache,
                single_flight=self.single_flight,
            )
            fetcher = AsyncHTTPFetcher(
                dns_resolver=dns_resolver,
                request_gate=request_gate,
                cancellation_checker=is_cancelled,
            )
            orchestrator = SiteScanOrchestrator(
                fetcher=fetcher,
                cancellation_checker=is_cancelled,
            )

        orchestration_result = None

        try:
            target_url = claim.normalized_url or claim.original_input
            orchestration_result = await orchestrator.scan(
                starting_url=target_url,
            )
        except Exception:
            logger.warning(
                "Scan execution failed for URL claim %s (%s)",
                claim.scan_url_id,
                claim.normalized_url,
                exc_info=True,
            )

        cancel_event.set()
        await heartbeat_task

        if lease_lost_event.is_set():
            logger.warning(
                "Lease lost for URL claim %s (%s). Dropping result persistence.",
                claim.scan_url_id,
                claim.normalized_url,
            )
            return

        if orchestration_result is not None:
            async with self.session_factory() as session:
                persistence_service = ResultPersistenceService(session)
                await persistence_service.persist_fenced_result(
                    claim=claim,
                    site_scan_result=orchestration_result,
                )
        self._processed_count += 1

    async def _run_heartbeat(
        self,
        claim: URLClaim,
        cancel_event: asyncio.Event,
        lease_lost_event: asyncio.Event,
    ) -> None:
        """Periodically renew lease in background until scan finishes or lease is lost."""
        while not cancel_event.is_set() and not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                break

            if cancel_event.is_set() or self._shutdown_event.is_set():
                break

            try:
                async with self.session_factory() as session:
                    work_service = CrawlWorkService(session)
                    hb = await work_service.renew_lease(
                        scan_url_id=claim.scan_url_id,
                        lease_owner=self.worker_id,
                        attempt_count=claim.attempt_count,
                        lease_duration_seconds=self.lease_duration,
                    )
                    if hb.status is HeartbeatStatus.LEASE_LOST:
                        logger.warning(
                            "Lease lost during heartbeat for URL %s (attempt=%d)",
                            claim.scan_url_id,
                            claim.attempt_count,
                        )
                        lease_lost_event.set()
                        cancel_event.set()
                        break
                    elif hb.status is HeartbeatStatus.CANCEL_REQUESTED:
                        logger.info(
                            "Cancel requested during heartbeat for URL %s", claim.scan_url_id
                        )
                        cancel_event.set()
                        break
            except Exception:
                logger.warning(
                    "Heartbeat failed for URL %s due to an exception.",
                    claim.scan_url_id,
                    exc_info=True,
                )
