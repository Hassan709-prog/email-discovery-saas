"""Local PostgreSQL-backed crawl worker engine."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.errors import ServiceError
from email_discovery_api.services.result_persistence import ResultPersistenceService
from email_discovery_api.services.scan_jobs import ScanJobService
from email_discovery_api.services.worker_contracts import (
    HeartbeatStatus,
    LeaseLostError,
    URLClaim,
)
from email_discovery_crawl_worker.outcome_classifier import (
    WorkerExecutionOutcome,
    classify_error_code_and_retryability,
    classify_worker_outcome,
)
from email_scanner.orchestration import SiteScanOrchestrator

logger = logging.getLogger(__name__)


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

        from email_scanner.dns import DeadlockFreeSingleFlightGroup, WorkerDNSCache

        self.dns_cache = WorkerDNSCache()
        self.single_flight: DeadlockFreeSingleFlightGroup[tuple[str, int, str], tuple[str, ...]] = (
            DeadlockFreeSingleFlightGroup()
        )

        self._running = False
        self._shutdown_event = asyncio.Event()
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

    async def start(self) -> None:
        """Start worker claim polling and background tasks."""
        self._running = True
        self._shutdown_event.clear()
        now = time.monotonic()
        self._last_recovery_at = now

        # Run initial lease recovery on startup
        async with self.session_factory() as session:
            work_service = CrawlWorkService(session)
            recovered = await work_service.recover_expired_leases()
            if recovered > 0:
                logger.info("Startup recovered %d expired leases.", recovered)

        logger.info(
            "CrawlWorker %s started (concurrency=%d, poll=%.2fs).",
            self.worker_id,
            self.concurrency,
            self.poll_interval,
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

                # Race-safe event-driven capacity refill vs idle poll sleep
                if len(self._active_tasks) >= self.concurrency:
                    snapshot = tuple(self._active_tasks)
                    if snapshot:
                        done, _ = await asyncio.wait(snapshot, return_when=asyncio.FIRST_COMPLETED)
                        for t in done:
                            if not t.cancelled():
                                _ = t.exception()
                elif not claimed_any:
                    await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Worker run loop cancelled.")
        finally:
            await self._drain_tasks()

    def request_shutdown(self) -> None:
        """Signal graceful worker shutdown without failing running jobs."""
        logger.info("Shutdown requested for worker %s.", self.worker_id)
        self._running = False
        self._shutdown_event.set()

    async def _drain_tasks(self) -> None:
        """Wait for active scan tasks during graceful shutdown."""
        if self._active_tasks:
            logger.info("Draining %d active scan tasks...", len(self._active_tasks))
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            self._active_tasks.clear()

        await self.single_flight.shutdown()
        await self.dns_cache.clear()

    async def _fill_capacity_and_claim(self) -> bool:
        """Poll and claim repeatedly until concurrency limit reached or queue exhausted.

        Returns True if at least one URL was claimed during this cycle.
        """
        claimed_any = False

        while (
            self._running
            and not self._shutdown_event.is_set()
            and len(self._active_tasks) < self.concurrency
        ):
            if self.max_scans is not None and self._claimed_count >= self.max_scans:
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

        worker_owned = self.orchestrator_factory is None
        if self.orchestrator_factory:
            orchestrator = self.orchestrator_factory()
        else:
            from email_scanner.dns import SystemDNSResolver
            from email_scanner.fetching import AsyncHTTPFetcher
            from email_scanner.robots import RobotsPolicyEvaluator

            resolver = SystemDNSResolver(
                dns_cache=self.dns_cache,
                single_flight=self.single_flight,
            )
            fetcher = AsyncHTTPFetcher(
                dns_resolver=resolver,
                cancellation_checker=is_cancelled,
            )
            robots = RobotsPolicyEvaluator(
                fetcher=fetcher,
            )
            orchestrator = SiteScanOrchestrator(
                fetcher=fetcher,
                robots_evaluator=robots,
                cancellation_checker=is_cancelled,
            )

        target_url = claim.normalized_url or claim.original_input
        site_result = None
        scan_exc: Exception | None = None

        try:
            site_result = await orchestrator.scan(target_url)
        except Exception as err:
            scan_exc = err
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if worker_owned:
                close_fn: Any = getattr(orchestrator, "close", None)
                if callable(close_fn):
                    try:
                        res: Any = close_fn()
                        if hasattr(res, "__await__"):
                            await res
                    except Exception:
                        pass

        self._processed_count += 1

        # Process Shutdown: leave lease for recovery
        if self._shutdown_event.is_set():
            logger.info(
                "Process shutdown active; leaving lease (url_id=%s, job_id=%s, attempt=%d).",
                claim.scan_url_id,
                claim.job_id,
                claim.attempt_count,
            )
            return

        # Lease Lost during scan
        if lease_lost_event.is_set():
            logger.warning(
                "Heartbeat lease lost (url_id=%s, job_id=%s, attempt=%d); skipped.",
                claim.scan_url_id,
                claim.job_id,
                claim.attempt_count,
            )
            return

        # User Job Cancellation
        if cancel_event.is_set():
            try:
                async with self.session_factory() as session:
                    persistence = ResultPersistenceService(session)
                    await persistence.persist_fenced_cancellation(claim)
            except LeaseLostError:
                logger.warning(
                    "Lease lost for cancelled ScanURL (url_id=%s, job_id=%s, attempt=%d).",
                    claim.scan_url_id,
                    claim.job_id,
                    claim.attempt_count,
                )
            except Exception:
                logger.error(
                    "Error persisting cancellation (url_id=%s, job_id=%s, attempt=%d).",
                    claim.scan_url_id,
                    claim.job_id,
                    claim.attempt_count,
                )
            await self._try_finalize_job(claim)
            return

        classified = classify_worker_outcome(
            site_scan_result=site_result,
            execution_exception=scan_exc,
            attempt_count=claim.attempt_count,
            max_attempts=claim.max_attempts,
        )

        try:
            async with self.session_factory() as session:
                persistence = ResultPersistenceService(session)
                if site_result is not None and classified in (
                    WorkerExecutionOutcome.TERMINAL_SUCCESS,
                    WorkerExecutionOutcome.TERMINAL_NO_EMAIL,
                    WorkerExecutionOutcome.TERMINAL_PARTIAL,
                    WorkerExecutionOutcome.TERMINAL_FAILURE,
                ):
                    await persistence.persist_fenced_result(claim, site_result)
                elif classified == WorkerExecutionOutcome.RETRYABLE_FAILURE:
                    err_code, _ = (
                        classify_error_code_and_retryability(site_result)
                        if site_result
                        else ("TRANSIENT_SCAN_ERROR", True)
                    )
                    err_msg = str(scan_exc) if scan_exc else "Transient scan failure"
                    await persistence.persist_transient_failure(
                        claim, error_code=err_code, error_message=err_msg
                    )
                elif classified == WorkerExecutionOutcome.TERMINAL_FAILURE:
                    err_code, _ = (
                        classify_error_code_and_retryability(site_result)
                        if site_result
                        else ("SCAN_FAILED", False)
                    )
                    err_msg = str(scan_exc) if scan_exc else "Terminal scan failure"
                    await persistence.persist_transient_failure(
                        claim, error_code=err_code, error_message=err_msg
                    )
        except LeaseLostError:
            logger.warning(
                "Lease lost or expired during persistence (url_id=%s, job_id=%s, attempt=%d).",
                claim.scan_url_id,
                claim.job_id,
                claim.attempt_count,
            )
        except ServiceError as err:
            logger.error(
                "Service error persisting result (url_id=%s, job_id=%s, attempt=%d, code=%s).",
                claim.scan_url_id,
                claim.job_id,
                claim.attempt_count,
                err.code,
            )
        except Exception:
            logger.error(
                "Unexpected error persisting result (url_id=%s, job_id=%s, attempt=%d).",
                claim.scan_url_id,
                claim.job_id,
                claim.attempt_count,
                exc_info=True,
            )

        # Perform separate T5 job finalization check
        await self._try_finalize_job(claim)

    async def _run_heartbeat(
        self,
        claim: URLClaim,
        cancel_event: asyncio.Event,
        lease_lost_event: asyncio.Event,
    ) -> None:
        """Periodically renew lease using a fresh, short-lived AsyncSession."""
        try:
            while (
                not cancel_event.is_set()
                and not lease_lost_event.is_set()
                and not self._shutdown_event.is_set()
            ):
                await asyncio.sleep(self.heartbeat_interval)
                if (
                    cancel_event.is_set()
                    or lease_lost_event.is_set()
                    or self._shutdown_event.is_set()
                ):
                    break

                async with self.session_factory() as session:
                    work_service = CrawlWorkService(session)
                    result = await work_service.renew_lease(
                        scan_url_id=claim.scan_url_id,
                        lease_owner=claim.lease_owner,
                        attempt_count=claim.attempt_count,
                        lease_duration_seconds=self.lease_duration,
                    )

                if result.status == HeartbeatStatus.CANCEL_REQUESTED:
                    logger.info(
                        "Job cancellation requested via heartbeat (url_id=%s, job_id=%s).",
                        claim.scan_url_id,
                        claim.job_id,
                    )
                    cancel_event.set()
                    break
                elif result.status == HeartbeatStatus.LEASE_LOST:
                    logger.warning(
                        "Heartbeat lease lost (url_id=%s, job_id=%s, attempt=%d).",
                        claim.scan_url_id,
                        claim.job_id,
                        claim.attempt_count,
                    )
                    lease_lost_event.set()
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error(
                "Heartbeat exception (url_id=%s, job_id=%s, attempt=%d).",
                claim.scan_url_id,
                claim.job_id,
                claim.attempt_count,
            )

    async def _try_finalize_job(self, claim: URLClaim) -> None:
        """Attempt authoritative job finalization in a separate short transaction (T5)."""
        try:
            async with self.session_factory() as session:
                job_service = ScanJobService(session)
                await job_service.try_finalize_job(claim.organization_id, claim.job_id)
        except Exception:
            logger.error(
                "Error finalizing job (job_id=%s).",
                claim.job_id,
            )
