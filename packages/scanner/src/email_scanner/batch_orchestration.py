"""Deterministic multi-URL batch scan orchestration module for scanner-core.

Manages bounded async concurrency, round-robin domain fairness, shared in-process
rate-limiting, duplicate coalescing, cancellation, and statistics.
"""

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from email_scanner.errors import (
    BatchScanConfigError,
    BatchScanConfigErrorCode,
    URLNormalizationError,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    BatchItemOutcome,
    BatchScanConfig,
    BatchScanItem,
    BatchScanOutcome,
    BatchScanResult,
    BatchScanStatistics,
    NormalizedURL,
    SiteScanResult,
)
from email_scanner.normalization import normalize_url
from email_scanner.orchestration import SiteScanOrchestrator
from email_scanner.request_gate import (
    DomainRequestGate,
    RequestGateProtocol,
    get_domain_key,
)
from email_scanner.robots import RobotsPolicyEvaluator


@dataclass(slots=True)
class _WorkItem:
    original_index: int
    original_input: str
    norm_url: NormalizedURL
    domain_key: str


class BatchScanOrchestrator:
    """Orchestrates deterministic multi-URL batch scans."""

    def __init__(
        self,
        request_gate: RequestGateProtocol | None = None,
        fetcher: AsyncHTTPFetcher | None = None,
        robots_evaluator: RobotsPolicyEvaluator | None = None,
        orchestrator_factory: Callable[
            [AsyncHTTPFetcher, RobotsPolicyEvaluator], SiteScanOrchestrator
        ]
        | None = None,
        clock: Callable[[], float] | None = None,
        async_sleeper: Callable[[float], Awaitable[None]] | None = None,
        cancellation_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._sleeper = async_sleeper
        self._cancellation_checker = cancellation_checker
        self._request_gate = request_gate or DomainRequestGate(
            clock=self._clock, async_sleeper=self._sleeper
        )
        self._fetcher = fetcher
        self._robots_evaluator = robots_evaluator
        self._orchestrator_factory = orchestrator_factory

    async def scan_batch(
        self,
        inputs: tuple[str, ...] | list[str],
        config: BatchScanConfig | None = None,
    ) -> BatchScanResult:
        """Run an asynchronous multi-URL batch scan."""
        cfg = config or BatchScanConfig()
        start_time = self._clock()

        if len(inputs) > cfg.max_inputs:
            raise BatchScanConfigError(
                BatchScanConfigErrorCode.INVALID_LIMIT,
                f"Number of inputs ({len(inputs)}) exceeds max_inputs ({cfg.max_inputs})",
            )

        # Setup shared HTTP resources
        should_close_client = False
        client: httpx.AsyncClient | None = None
        fetcher = self._fetcher

        if fetcher is None:
            client = httpx.AsyncClient(follow_redirects=False)
            should_close_client = True
            fetcher = AsyncHTTPFetcher(
                client=client,
                config=cfg.site_scan_config.fetch_config,
                request_gate=self._request_gate,
            )

        robots_evaluator = self._robots_evaluator or RobotsPolicyEvaluator(
            fetcher=fetcher,
            clock=self._clock,
        )

        try:
            return await self._execute_batch(
                inputs=inputs,
                cfg=cfg,
                start_time=start_time,
                fetcher=fetcher,
                robots_evaluator=robots_evaluator,
            )
        finally:
            if should_close_client and client is not None:
                await client.aclose()

    async def _execute_batch(
        self,
        inputs: tuple[str, ...] | list[str],
        cfg: BatchScanConfig,
        start_time: float,
        fetcher: AsyncHTTPFetcher,
        robots_evaluator: RobotsPolicyEvaluator,
    ) -> BatchScanResult:
        total_inputs = len(inputs)
        valid_inputs = 0
        invalid_inputs = 0
        unique_normalized_urls = 0
        duplicate_coalesced_items = 0

        work_items: list[_WorkItem] = []
        canonical_owner_map: dict[str, int] = {}
        items_by_index: dict[int, BatchScanItem] = {}

        # 1. Input normalization and duplicate coalescing pass
        for idx, raw_input in enumerate(inputs):
            cleaned_input = raw_input.strip()
            if not cleaned_input:
                invalid_inputs += 1
                items_by_index[idx] = BatchScanItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=None,
                    outcome=BatchItemOutcome.INVALID_INPUT,
                    is_duplicate=False,
                    duplicate_of_index=None,
                    result=None,
                    error_message="Input URL string is empty or blank",
                )
                continue

            try:
                norm_url = normalize_url(cleaned_input)
            except URLNormalizationError as err:
                invalid_inputs += 1
                items_by_index[idx] = BatchScanItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=None,
                    outcome=BatchItemOutcome.INVALID_INPUT,
                    is_duplicate=False,
                    duplicate_of_index=None,
                    result=None,
                    error_message=str(err),
                )
                continue

            valid_inputs += 1
            norm_str = norm_url.normalized_url

            if cfg.coalesce_duplicate_urls and norm_str in canonical_owner_map:
                duplicate_coalesced_items += 1
                canonical_idx = canonical_owner_map[norm_str]
                items_by_index[idx] = BatchScanItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=norm_str,
                    outcome=BatchItemOutcome.DUPLICATE_COALESCED,
                    is_duplicate=True,
                    duplicate_of_index=canonical_idx,
                    result=None,
                )
            else:
                canonical_owner_map[norm_str] = idx
                unique_normalized_urls += 1
                d_key = get_domain_key(norm_url)
                work_items.append(
                    _WorkItem(
                        original_index=idx,
                        original_input=raw_input,
                        norm_url=norm_url,
                        domain_key=d_key,
                    )
                )

        # 2. Setup domain queues and scheduler state
        domain_queues: dict[str, deque[_WorkItem]] = defaultdict(deque)
        for item in work_items:
            domain_queues[item.domain_key].append(item)

        # Lexically sorted domain keys for deterministic initial queueing
        ready_domains: deque[str] = deque(sorted(domain_queues.keys()))

        active_global = 0
        active_by_domain: dict[str, int] = defaultdict(int)
        peak_global_concurrency = 0
        peak_per_domain_concurrency = 0

        started_scans = 0
        completed_scans = 0
        failed_scans = 0
        cancelled_scans = 0

        canonical_results: dict[
            int, tuple[BatchItemOutcome, SiteScanResult | None, str | None]
        ] = {}

        state_lock = asyncio.Lock()
        completion_event = asyncio.Event()

        stop_reason: str = "COMPLETED"

        async def _run_work_item(w_item: _WorkItem) -> None:
            nonlocal started_scans, completed_scans, failed_scans, cancelled_scans, stop_reason

            async with state_lock:
                started_scans += 1

            item_outcome = BatchItemOutcome.FAILED
            site_result: SiteScanResult | None = None
            err_msg: str | None = None

            # Check deadline or cancellation before starting item scan
            now = self._clock()
            is_cancelled = self._cancellation_checker is not None and self._cancellation_checker()
            is_expired = (
                cfg.max_elapsed_batch_seconds is not None
                and (now - start_time) > cfg.max_elapsed_batch_seconds
            )

            if is_cancelled:
                item_outcome = BatchItemOutcome.CANCELLED
                err_msg = "Batch scan cancelled"
                cancelled_scans += 1
            elif is_expired:
                item_outcome = BatchItemOutcome.SKIPPED_BUDGET_REACHED
                err_msg = "Batch scan elapsed time deadline reached"
                stop_reason = "MAX_ELAPSED_TIME_EXCEEDED"
            else:
                try:
                    orchestrator = (
                        self._orchestrator_factory(fetcher, robots_evaluator)
                        if self._orchestrator_factory is not None
                        else SiteScanOrchestrator(
                            fetcher=fetcher,
                            robots_evaluator=robots_evaluator,
                            clock=self._clock,
                            async_sleeper=self._sleeper,
                            cancellation_checker=self._cancellation_checker,
                        )
                    )

                    site_result = await orchestrator.scan(
                        w_item.norm_url, config=cfg.site_scan_config
                    )

                    # Map site scan outcome to batch item outcome
                    s_outcome = site_result.outcome.value
                    if s_outcome == "COMPLETED":
                        item_outcome = BatchItemOutcome.COMPLETED
                        completed_scans += 1
                    elif s_outcome == "COMPLETED_NO_EMAILS":
                        item_outcome = BatchItemOutcome.COMPLETED_NO_EMAILS
                        completed_scans += 1
                    elif s_outcome == "PARTIAL":
                        item_outcome = BatchItemOutcome.PARTIAL
                        completed_scans += 1
                    elif s_outcome == "ROBOTS_BLOCKED":
                        item_outcome = BatchItemOutcome.ROBOTS_BLOCKED
                        failed_scans += 1
                    elif s_outcome == "CANCELLED":
                        item_outcome = BatchItemOutcome.CANCELLED
                        cancelled_scans += 1
                    else:
                        item_outcome = BatchItemOutcome.FAILED
                        failed_scans += 1

                except Exception as exc:
                    item_outcome = BatchItemOutcome.FAILED
                    err_msg = f"Item scan failed: {exc}"
                    failed_scans += 1

            async with state_lock:
                canonical_results[w_item.original_index] = (item_outcome, site_result, err_msg)
                active_by_domain[w_item.domain_key] -= 1
                nonlocal active_global
                active_global -= 1

                if domain_queues[w_item.domain_key] and w_item.domain_key not in ready_domains:
                    ready_domains.append(w_item.domain_key)

                _dispatch_work_locked()

                if active_global == 0 and not _has_pending_work_locked():
                    completion_event.set()

        def _has_pending_work_locked() -> bool:
            return any(len(q) > 0 for q in domain_queues.values())

        def _dispatch_work_locked() -> None:
            nonlocal active_global, peak_global_concurrency, peak_per_domain_concurrency

            if self._cancellation_checker is not None and self._cancellation_checker():
                return

            now = self._clock()
            if (
                cfg.max_elapsed_batch_seconds is not None
                and (now - start_time) > cfg.max_elapsed_batch_seconds
            ):
                return

            rounds = len(ready_domains)
            for _ in range(rounds):
                if active_global >= cfg.global_concurrency or not ready_domains:
                    break

                d_key = ready_domains.popleft()
                if active_by_domain[d_key] >= cfg.per_domain_concurrency:
                    ready_domains.append(d_key)
                    continue

                if domain_queues[d_key]:
                    w_item = domain_queues[d_key].popleft()
                    active_global += 1
                    active_by_domain[d_key] += 1

                    peak_global_concurrency = max(peak_global_concurrency, active_global)
                    peak_per_domain_concurrency = max(
                        peak_per_domain_concurrency, active_by_domain[d_key]
                    )

                    if domain_queues[d_key]:
                        ready_domains.append(d_key)

                    asyncio.create_task(_run_work_item(w_item), name="scanner-worker")

        # Start initial worker dispatch under state_lock
        async with state_lock:
            if not _has_pending_work_locked():
                completion_event.set()
            else:
                _dispatch_work_locked()

        await completion_event.wait()

        # Mark remaining unstarted items if cancelled or expired
        for w_item in work_items:
            if w_item.original_index not in canonical_results:
                is_cancelled = (
                    self._cancellation_checker is not None and self._cancellation_checker()
                )
                out = (
                    BatchItemOutcome.CANCELLED
                    if is_cancelled
                    else BatchItemOutcome.SKIPPED_BUDGET_REACHED
                )
                canonical_results[w_item.original_index] = (
                    out,
                    None,
                    "Skipped due to cancellation or deadline",
                )

        # Build final items mapping
        for w_item in work_items:
            out, s_res, err_msg = canonical_results[w_item.original_index]
            items_by_index[w_item.original_index] = BatchScanItem(
                original_index=w_item.original_index,
                original_input=w_item.original_input,
                normalized_url=w_item.norm_url.normalized_url,
                outcome=out,
                is_duplicate=False,
                duplicate_of_index=None,
                result=s_res,
                error_message=err_msg,
            )

        # 3. Populate duplicate coalesced items with canonical results
        for idx in range(total_inputs):
            if idx in items_by_index and items_by_index[idx].is_duplicate:
                dup_item = items_by_index[idx]
                if (
                    dup_item.duplicate_of_index is not None
                    and dup_item.duplicate_of_index in items_by_index
                ):
                    canon_item = items_by_index[dup_item.duplicate_of_index]
                    items_by_index[idx] = BatchScanItem(
                        original_index=dup_item.original_index,
                        original_input=dup_item.original_input,
                        normalized_url=dup_item.normalized_url,
                        outcome=BatchItemOutcome.DUPLICATE_COALESCED,
                        is_duplicate=True,
                        duplicate_of_index=dup_item.duplicate_of_index,
                        result=canon_item.result,
                        error_message=None,
                    )

        # 4. Format final items ordered strictly by original_index
        ordered_items = tuple(items_by_index[i] for i in range(total_inputs))

        elapsed_time = self._clock() - start_time

        stats = BatchScanStatistics(
            total_inputs=total_inputs,
            valid_inputs=valid_inputs,
            invalid_inputs=invalid_inputs,
            unique_normalized_urls=unique_normalized_urls,
            duplicate_coalesced_items=duplicate_coalesced_items,
            started_scans=started_scans,
            completed_scans=completed_scans,
            failed_scans=failed_scans,
            cancelled_scans=cancelled_scans,
            peak_global_concurrency=peak_global_concurrency,
            peak_per_domain_concurrency=peak_per_domain_concurrency,
            elapsed_seconds=elapsed_time,
            stop_reason=stop_reason,
        )

        # Determine overall batch outcome
        is_cancelled_batch = self._cancellation_checker is not None and self._cancellation_checker()
        if is_cancelled_batch:
            batch_outcome = BatchScanOutcome.CANCELLED
        elif total_inputs == 0:
            batch_outcome = BatchScanOutcome.FAILED
        elif (
            invalid_inputs > 0
            or failed_scans > 0
            or str(stop_reason) == "MAX_ELAPSED_TIME_EXCEEDED"
        ):
            batch_outcome = BatchScanOutcome.PARTIAL
        else:
            batch_outcome = BatchScanOutcome.COMPLETED

        return BatchScanResult(
            outcome=batch_outcome,
            statistics=stats,
            items=ordered_items,
        )
