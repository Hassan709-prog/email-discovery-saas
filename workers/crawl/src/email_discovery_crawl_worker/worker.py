"""Local PostgreSQL-backed crawl worker engine with optional Redis Pub/Sub coordination."""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import hashlib
import inspect
import logging
import math
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
    LeaseLostError,
    URLClaim,
)
from email_discovery_crawl_worker.config import WorkerSettings, get_worker_settings
from email_discovery_crawl_worker.presence import WorkerPresenceManager, derive_instance_digest
from email_discovery_crawl_worker.redis_gate import RedisDomainRequestGate
from email_scanner.orchestration import SiteScanOrchestrator
from email_scanner.request_gate import DomainRequestGate

logger = logging.getLogger(__name__)


def make_claim_digest(scan_url_id: uuid.UUID) -> str:
    """Return safe 8-char SHA-256 digest of scan_url_id for logging privacy."""
    return hashlib.sha256(str(scan_url_id).encode("utf-8")).hexdigest()[:8]


class WorkerState(enum.StrEnum):
    """Lifecycle state machine for CrawlWorker."""

    STARTING = "STARTING"
    ACTIVE = "ACTIVE"
    DEGRADED_STRICT_PAUSED = "DEGRADED_STRICT_PAUSED"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"
    FAILED_STARTUP = "FAILED_STARTUP"


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
        redis_client: Any | None = None,
        redis_pool: Any | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1.")
        if poll_interval_seconds <= 0 or not math.isfinite(poll_interval_seconds):
            raise ValueError("poll_interval_seconds must be a finite number greater than zero.")
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
        self.settings = worker_settings or get_worker_settings()
        self.worker_label = (
            worker_id or self.settings.worker_label or self.settings.worker_id or "worker-default"
        )
        self.instance_id = self.settings.instance_id

        self.concurrency = concurrency
        self.max_waiting_claims = self.settings.max_waiting_claims_per_worker
        self.max_total_held_claims = self.concurrency + self.max_waiting_claims

        self.poll_interval = poll_interval_seconds
        self.lease_duration = lease_duration_seconds
        self.heartbeat_interval = heartbeat_interval_seconds
        self.max_scans = max_scans
        self.recovery_interval = recovery_interval_seconds
        self.orchestrator_factory = orchestrator_factory

        from email_scanner.dns import DeadlockFreeSingleFlightGroup, WorkerDNSCache

        self.dns_cache = WorkerDNSCache()
        self.single_flight: DeadlockFreeSingleFlightGroup[tuple[str, int, str], tuple[str, ...]] = (
            DeadlockFreeSingleFlightGroup()
        )

        self.local_gate = DomainRequestGate(
            default_minimum_interval_seconds=self.settings.min_domain_interval_ms / 1000.0
        )

        self.redis_client: Any | None = redis_client
        self.redis_pool: Any | None = redis_pool
        self.redis_gate: RedisDomainRequestGate | None = None
        self.presence_manager: WorkerPresenceManager | None = None
        self.state = WorkerState.STARTING

        self._running = False
        self._shutdown_event = asyncio.Event()
        self._work_signal_event = asyncio.Event()
        self._fill_lock = asyncio.Lock()
        self._pubsub_task: asyncio.Task[None] | None = None
        self._presence_task: asyncio.Task[None] | None = None

        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._waiting_claims: dict[uuid.UUID, URLClaim] = {}
        self._active_claims: dict[uuid.UUID, URLClaim] = {}

        self._claimed_count = 0
        self._processed_count = 0
        self._last_recovery_at = 0.0

    @property
    def worker_id(self) -> str:
        """Return logical worker label for diagnostics."""
        return self.worker_label

    @property
    def claimed_count(self) -> int:
        """Return total successfully claimed URLs."""
        return self._claimed_count

    @property
    def processed_count(self) -> int:
        """Return total processed claim tasks."""
        return self._processed_count

    @property
    def total_held_claims(self) -> int:
        """Return count of active tasks + waiting claims."""
        return len(self._active_claims)

    async def _init_redis(self) -> None:
        """Initialize worker Redis connection pool, presence manager, and Pub/Sub listener."""
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
            self.state = WorkerState.ACTIVE
            self.redis_gate = RedisDomainRequestGate(
                redis_client=self.redis_client,
                settings=self.settings,
                local_fallback_gate=self.local_gate,
            )
            self.presence_manager = WorkerPresenceManager(
                redis_client=self.redis_client,
                settings=self.settings,
            )
            logger.info(
                "event_code=WORKER_REDIS_READY instance_digest=%s",
                derive_instance_digest(self.instance_id),
            )
        except Exception as exc:
            if self.settings.redis_required:
                self.state = WorkerState.FAILED_STARTUP
                raise RuntimeError("Redis is strictly required but connection failed.") from exc

            self.state = WorkerState.ACTIVE
            self.redis_client = None
            self.redis_gate = RedisDomainRequestGate(
                redis_client=None,
                settings=self.settings,
                local_fallback_gate=self.local_gate,
            )
            logger.warning(
                "event_code=WORKER_REDIS_DEGRADED instance_digest=%s error_type=%s",
                derive_instance_digest(self.instance_id),
                type(exc).__name__,
            )

    async def _run_pubsub_listener(self) -> None:
        """Listen on Redis Pub/Sub for work_available notifications."""
        if self.redis_client is None:
            return

        channel_name = f"{self.settings.redis_key_prefix}:events:work_available"
        backoff = 1.0

        while self._running and not self._shutdown_event.is_set():
            pubsub = None
            try:
                pubsub = self.redis_client.pubsub()  # pyright: ignore[reportUnknownMemberType]
                await pubsub.subscribe(channel_name)  # pyright: ignore[reportUnknownMemberType]
                logger.info("Subscribed to Redis channel: %s", channel_name)
                backoff = 1.0

                while self._running and not self._shutdown_event.is_set():
                    message = await pubsub.get_message(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message is not None:
                        self._work_signal_event.set()
                        if self.state == WorkerState.DEGRADED_STRICT_PAUSED:
                            self.state = WorkerState.ACTIVE
            except RedisError, asyncio.CancelledError:
                if self._shutdown_event.is_set():
                    break
                self.state = WorkerState.DEGRADED_STRICT_PAUSED
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, self.settings.redis_pubsub_reconnect_max_backoff)
            except Exception:
                if self._shutdown_event.is_set():
                    break
                await asyncio.sleep(1.0)
            finally:
                if pubsub is not None:
                    try:
                        await asyncio.shield(
                            asyncio.wait_for(pubsub.unsubscribe(channel_name), timeout=2.0)  # pyright: ignore[reportUnknownMemberType]
                        )
                    except Exception:
                        pass
                    try:
                        await asyncio.shield(
                            asyncio.wait_for(pubsub.close(), timeout=2.0)  # pyright: ignore[reportUnknownMemberType]
                        )
                    except Exception:
                        pass

    async def _run_presence_loop(self) -> None:
        """Periodically refresh non-authoritative Redis presence TTL key."""
        while self._running and not self._shutdown_event.is_set():
            if self.presence_manager is not None:
                await self.presence_manager.update_presence(
                    state=self.state.value,
                    active_claims_count=self.total_held_claims,
                )
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                break

    def _idle_wait_timeout(self) -> float:
        """Return effective idle polling wait timeout in seconds.

        - If DEGRADED_STRICT_PAUSED (Redis lost in strict mode), return degraded_poll_interval.
        - If Redis Pub/Sub listener task is active and healthy:
            Returns healthy_poll_interval, unless local poll_interval was explicitly set lower
            than default settings.poll_interval.
        - Otherwise (offline/local/no Pub/Sub listener), return configured local poll_interval.
        """
        if self.state == WorkerState.DEGRADED_STRICT_PAUSED:
            timeout = self.settings.degraded_poll_interval
        elif (
            self.redis_client is not None
            and self._pubsub_task is not None
            and not self._pubsub_task.done()
            and self.state == WorkerState.ACTIVE
        ):
            if self.poll_interval < self.settings.poll_interval:
                timeout = self.poll_interval
            else:
                timeout = self.settings.healthy_poll_interval
        else:
            timeout = self.poll_interval

        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError(f"Idle wait timeout must be finite and > 0, got {timeout}")

        return timeout

    async def start(self) -> None:
        """Alias for run() to maintain compatibility with worker test runners."""
        await self.run()

    async def run(self) -> None:
        """Main worker execution loop."""
        logger.info(
            "event_code=WORKER_STARTING instance_digest=%s concurrency=%d fallback_mode=%s",
            derive_instance_digest(self.instance_id),
            self.concurrency,
            self.settings.redis_rate_limit_fallback_mode,
        )
        self._running = True
        self._shutdown_event.clear()
        now = time.monotonic()
        self._last_recovery_at = now

        await self._init_redis()
        if self.state == WorkerState.FAILED_STARTUP:
            logger.error(
                "event_code=WORKER_STARTUP_FAILED instance_digest=%s",
                derive_instance_digest(self.instance_id),
            )
            return

        self._pubsub_task = asyncio.create_task(self._run_pubsub_listener())
        self._presence_task = asyncio.create_task(self._run_presence_loop())

        # Run initial lease recovery on startup
        try:
            async with self.session_factory() as session:
                work_service = CrawlWorkService(session)
                recovered = await work_service.recover_expired_leases()
                if recovered > 0:
                    logger.info(
                        "event_code=WORKER_RECOVERY_SUMMARY instance_digest=%s recovered=%d",
                        derive_instance_digest(self.instance_id),
                        recovered,
                    )
        except Exception:
            logger.warning("Startup lease recovery exception.", exc_info=True)

        logger.info(
            "event_code=WORKER_READY instance_digest=%s concurrency=%d state=%s",
            derive_instance_digest(self.instance_id),
            self.concurrency,
            self.state.value,
        )

        try:
            while self._running and not self._shutdown_event.is_set():
                if self.state == WorkerState.ACTIVE:
                    claimed_any = await self._fill_capacity_and_claim()
                else:
                    claimed_any = False

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
                        logger.warning("Periodic lease recovery exception.", exc_info=True)

                if not self._running or self._shutdown_event.is_set():
                    break

                # Capacity fill vs idle poll wait
                if (
                    len(self._active_tasks) >= self.concurrency
                    or self.total_held_claims >= self.max_total_held_claims
                ):
                    # Wait for capacity to free up, checking shutdown signal every second.
                    while (
                        len(self._active_tasks) >= self.concurrency
                        or self.total_held_claims >= self.max_total_held_claims
                    ) and not self._shutdown_event.is_set():
                        snapshot = tuple(self._active_tasks)
                        if not snapshot:
                            break
                        done, _ = await asyncio.wait(
                            snapshot, return_when=asyncio.FIRST_COMPLETED, timeout=1.0
                        )
                        for t in done:
                            if not t.cancelled():
                                _ = t.exception()

                elif not claimed_any:
                    sleep_interval = self._idle_wait_timeout()
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
        logger.info(
            "event_code=WORKER_SHUTDOWN_REQUESTED instance_digest=%s active_claims=%d",
            derive_instance_digest(self.instance_id),
            self.total_held_claims,
        )
        self.state = WorkerState.DRAINING
        self._running = False
        self._shutdown_event.set()
        self._work_signal_event.set()

    async def _drain_tasks(self) -> None:
        """Wait for active scan tasks during graceful shutdown and release unstarted claims."""
        if getattr(self, "_drained", False):
            return
        self._drained = True
        self.state = WorkerState.DRAINING

        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            try:
                await asyncio.shield(asyncio.wait_for(self._pubsub_task, timeout=2.0))
            except (asyncio.CancelledError, TimeoutError, Exception) as exc:
                logger.debug("PubSub task cancel result: %s", type(exc).__name__)
            self._pubsub_task = None

        if self._presence_task is not None:
            self._presence_task.cancel()
            try:
                await asyncio.shield(asyncio.wait_for(self._presence_task, timeout=2.0))
            except (asyncio.CancelledError, TimeoutError, Exception) as exc:
                logger.debug("Presence task cancel result: %s", type(exc).__name__)
            self._presence_task = None

        if self.presence_manager is not None:
            try:
                await asyncio.shield(
                    asyncio.wait_for(self.presence_manager.remove_presence(), timeout=2.0)
                )
            except Exception as exc:
                logger.debug("Remove presence error: %s", type(exc).__name__)

        if self._active_tasks:
            logger.info(
                "Draining %d active scan tasks (grace period 30s)...", len(self._active_tasks)
            )
            try:
                _done, pending = await asyncio.wait(self._active_tasks, timeout=30.0)
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                pass
            self._active_tasks.clear()

        # Execute fenced release for any remaining active/held claims
        if self._active_claims:
            logger.info(
                "Releasing %d active claims during graceful drain...", len(self._active_claims)
            )
            claims_to_release = list(self._active_claims.values())
            self._active_claims.clear()

            for claim in claims_to_release:
                try:
                    async with self.session_factory() as session:
                        work_service = CrawlWorkService(session)
                        await work_service.release_fenced_claim(
                            scan_url_id=claim.scan_url_id,
                            lease_owner=self.instance_id,
                            fence_token=claim.fence_token,
                        )
                except Exception:
                    logger.warning(
                        "Error releasing claim %s during drain.",
                        make_claim_digest(claim.scan_url_id),
                    )

        if self.redis_client is not None:
            try:
                await asyncio.wait_for(self.redis_client.aclose(), timeout=2.0)
            except Exception as exc:
                logger.debug("Redis client aclose error: %s", type(exc).__name__)
            self.redis_client = None

        if self.redis_pool is not None:
            try:
                disc = self.redis_pool.disconnect()
                if asyncio.iscoroutine(disc) or inspect.isawaitable(disc):
                    await asyncio.wait_for(disc, timeout=2.0)
            except Exception as exc:
                logger.debug("Redis pool disconnect error: %s", type(exc).__name__)
            self.redis_pool = None

        await self.single_flight.shutdown()
        await self.dns_cache.clear()
        self.state = WorkerState.STOPPED
        logger.info(
            "event_code=WORKER_STOPPED instance_digest=%s processed=%d",
            derive_instance_digest(self.instance_id),
            self._processed_count,
        )

    async def _fill_capacity_and_claim(self) -> bool:
        """Poll and claim repeatedly up to total_held_claims limit."""
        async with self._fill_lock:
            claimed_any = False

            while (
                self._running
                and not self._shutdown_event.is_set()
                and len(self._active_tasks) < self.concurrency
                and self.total_held_claims < self.max_total_held_claims
            ):
                if self.max_scans is not None and self._claimed_count >= self.max_scans:
                    break

                if (
                    self.settings.redis_rate_limit_fallback_mode.lower() == "strict_pause"
                    and self.state == WorkerState.DEGRADED_STRICT_PAUSED
                ):
                    break

                async with self.session_factory() as session:
                    work_service = CrawlWorkService(session)
                    claim = await work_service.claim_next_url(
                        lease_owner=self.instance_id,
                        lease_duration_seconds=self.lease_duration,
                    )

                if claim is None:
                    break

                self._claimed_count += 1
                claimed_any = True
                self._active_claims[claim.scan_url_id] = claim

                task = asyncio.create_task(self._process_claim_task(claim))
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)

            return claimed_any

    async def _process_claim_task(self, claim: URLClaim) -> None:
        """Execute scanner outside transaction for a claimed URL and persist results."""
        claim_digest = make_claim_digest(claim.scan_url_id)
        cancel_event = asyncio.Event()
        lease_lost_event = asyncio.Event()

        heartbeat_task = asyncio.create_task(
            self._run_heartbeat(claim, cancel_event, lease_lost_event)
        )

        def is_cancelled() -> bool:
            return cancel_event.is_set() or self._shutdown_event.is_set()

        request_gate = self.redis_gate or self.local_gate
        orchestration_result = None
        attempt_number: int | None = None
        owned_resource: Any | None = None

        try:
            try:
                # Mark attempt started immediately before outbound execution
                async with self.session_factory() as session:
                    work_service = CrawlWorkService(session)
                    attempt_number = await work_service.mark_attempt_started(
                        scan_url_id=claim.scan_url_id,
                        lease_owner=self.instance_id,
                        fence_token=claim.fence_token,
                    )

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
                    owned_resource = (
                        orchestrator if callable(getattr(orchestrator, "close", None)) else fetcher
                    )

                target_url = claim.normalized_url or claim.original_input
                orchestration_result = await orchestrator.scan(target_url)
            except LeaseLostError:
                logger.warning("Lease lost before attempt start for URL claim %s", claim_digest)
                lease_lost_event.set()
            except Exception as exc:
                logger.warning(
                    "Scan execution exception for URL claim %s [error_type=%s]",
                    claim_digest,
                    type(exc).__name__,
                    exc_info=True,
                )

            cancel_event.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

            if lease_lost_event.is_set():
                logger.warning(
                    "Lease lost for URL claim %s. Dropping result persistence.",
                    claim_digest,
                )
                return

            if orchestration_result is not None and attempt_number is not None:
                try:
                    updated_claim = dataclasses.replace(claim, attempt_count=attempt_number)
                    async with self.session_factory() as session:
                        persistence_service = ResultPersistenceService(session)
                        await persistence_service.persist_fenced_result(
                            claim=updated_claim,
                            site_scan_result=orchestration_result,
                        )
                    async with self.session_factory() as session:
                        await ScanJobService(session).try_finalize_job(
                            claim.organization_id, claim.job_id
                        )
                    self._processed_count += 1
                except LeaseLostError:
                    logger.warning(
                        "Lease lost during result persistence for URL claim %s", claim_digest
                    )
                except Exception as exc:
                    logger.warning(
                        "Result persistence failed for URL claim %s [error_type=%s]",
                        claim_digest,
                        type(exc).__name__,
                        exc_info=True,
                    )
        finally:
            # Always clean up the claim to prevent _active_claims leaks.
            cancel_event.set()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError, Exception:
                    pass
            if owned_resource is not None:
                try:
                    close_result = owned_resource.close()
                    if inspect.isawaitable(close_result):
                        await close_result
                except Exception as exc:
                    logger.debug("Scanner resource close error: %s", type(exc).__name__)
            self._active_claims.pop(claim.scan_url_id, None)

    async def _run_heartbeat(
        self,
        claim: URLClaim,
        cancel_event: asyncio.Event,
        lease_lost_event: asyncio.Event,
    ) -> None:
        """Periodically renew lease in background until scan finishes or lease is lost."""
        claim_digest = make_claim_digest(claim.scan_url_id)

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
                        lease_owner=self.instance_id,
                        fence_token=claim.fence_token,
                        lease_duration_seconds=self.lease_duration,
                    )
                    if hb.status is HeartbeatStatus.LEASE_LOST:
                        logger.warning(
                            "Lease lost during heartbeat for URL claim %s (fence=%d)",
                            claim_digest,
                            claim.fence_token,
                        )
                        lease_lost_event.set()
                        cancel_event.set()
                        break
                    elif hb.status is HeartbeatStatus.CANCEL_REQUESTED:
                        logger.info(
                            "Cancel requested during heartbeat for URL claim %s", claim_digest
                        )
                        cancel_event.set()
                        break
            except Exception as exc:
                logger.warning(
                    "Heartbeat failed for URL claim %s [error_type=%s]",
                    claim_digest,
                    type(exc).__name__,
                )
