"""Tests for scanner-core multi-URL batch scan orchestration."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from email_scanner.batch_orchestration import BatchScanOrchestrator
from email_scanner.errors import (
    BatchItemOutcome,
    BatchScanConfigError,
    BatchScanConfigErrorCode,
    BatchScanOutcome,
    FetchOutcomeCode,
    RobotsDecisionCode,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    BatchScanConfig,
    FetchResult,
    NormalizedURL,
    SiteScanConfig,
    SiteScanOutcome,
    SiteScanResult,
    SiteScanStatistics,
)
from email_scanner.orchestration import SiteScanOrchestrator
from email_scanner.request_gate import DomainRequestGate
from email_scanner.robots import RobotsPolicyEvaluator


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.current_time = start

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


class MockHTTPFetcher:
    def __init__(self, responses: dict[str, FetchResult] | None = None) -> None:
        self.responses: dict[str, FetchResult] = responses or {}
        self.fetched_urls: list[str] = []
        self.request_gate = DomainRequestGate()

    async def fetch(
        self,
        url: str | NormalizedURL,
        allowed_content_types: tuple[str, ...] | None = None,
        redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
    ) -> FetchResult:
        url_str = url.normalized_url if isinstance(url, NormalizedURL) else url
        self.fetched_urls.append(url_str)

        if url_str in self.responses:
            return self.responses[url_str]

        return FetchResult(
            final_url=url_str,
            status_code=200,
            content_type="text/html",
            body_text="<html><body><p>sales@acme.com</p></body></html>",
            redirect_history=(),
            outcome=FetchOutcomeCode.SUCCESS,
        )


def test_batch_config_nan_inf_rejection() -> None:
    with pytest.raises(BatchScanConfigError) as exc_info:
        BatchScanConfig(default_minimum_domain_interval_seconds=float("nan"))
    assert exc_info.value.code == BatchScanConfigErrorCode.NON_FINITE_VALUE

    with pytest.raises(BatchScanConfigError) as exc_info:
        BatchScanConfig(max_elapsed_batch_seconds=float("inf"))
    assert exc_info.value.code == BatchScanConfigErrorCode.NON_FINITE_VALUE


def test_robots_cache_single_flight() -> None:
    async def _test() -> None:
        fetch_count = 0

        class CountingFetcher(AsyncHTTPFetcher):
            async def fetch(
                self,
                url: str | NormalizedURL,
                allowed_content_types: tuple[str, ...] | None = None,
                redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
                recorder: Any | None = None,
            ) -> FetchResult:
                nonlocal fetch_count
                fetch_count += 1
                await asyncio.sleep(0.01)  # Simulate small delay
                url_str = url.normalized_url if isinstance(url, NormalizedURL) else url
                return FetchResult(
                    final_url=url_str,
                    status_code=200,
                    content_type="text/plain",
                    body_text="User-agent: *\nAllow: /",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                )

        fetcher = CountingFetcher()
        evaluator = RobotsPolicyEvaluator(fetcher=fetcher)

        url1 = "https://acme.com/page1"
        url2 = "https://acme.com/page2"
        url3 = "https://acme.com/page3"

        # Concurrently evaluate robots policy for 3 URLs sharing origin https://acme.com
        decisions = await asyncio.gather(
            evaluator.evaluate(url1),
            evaluator.evaluate(url2),
            evaluator.evaluate(url3),
        )

        assert len(decisions) == 3
        assert all(d.decision == RobotsDecisionCode.ALLOWED for d in decisions)
        # Single-flight lock guarantees only ONE logical /robots.txt fetch occurred
        assert fetch_count == 1

    asyncio.run(_test())


def test_input_order_output_despite_out_of_order_completion() -> None:
    async def _test() -> None:
        clock = FakeClock()

        class OutOfOrderOrchestrator(SiteScanOrchestrator):
            async def scan(
                self,
                starting_url: str | NormalizedURL,
                config: SiteScanConfig | None = None,
                recorder: Any | None = None,
            ) -> SiteScanResult:
                url_str = (
                    starting_url.normalized_url
                    if isinstance(starting_url, NormalizedURL)
                    else starting_url
                )
                if "slow" in url_str:
                    await asyncio.sleep(0.05)
                else:
                    await asyncio.sleep(0.01)

                stats = SiteScanStatistics(
                    pages_queued=1,
                    pages_attempted=1,
                    pages_fetched=1,
                    pages_blocked_by_robots=0,
                    pages_failed=0,
                    urls_discovered=1,
                    accepted_email_findings=1,
                    rejected_email_candidates=0,
                    elapsed_seconds=0.01,
                    stop_reason="QUEUE_EXHAUSTED",
                )
                return SiteScanResult(
                    starting_url=url_str,
                    outcome=SiteScanOutcome.COMPLETED,
                    statistics=stats,
                    page_records=(),
                    email_findings=(),
                    rejected_email_candidates=(),
                )

        orchestrator = BatchScanOrchestrator(
            orchestrator_factory=lambda f, r: OutOfOrderOrchestrator(fetcher=f, robots_evaluator=r),
            clock=clock,
        )

        inputs = [
            "https://domain1.com/slow",
            "https://domain2.com/fast",
            "https://domain3.com/slow2",
        ]

        res = await orchestrator.scan_batch(inputs, config=BatchScanConfig(global_concurrency=3))

        assert res.outcome == BatchScanOutcome.COMPLETED
        assert len(res.items) == 3

        # Results strictly ordered by original_index
        assert res.items[0].original_index == 0
        assert res.items[0].original_input == "https://domain1.com/slow"

        assert res.items[1].original_index == 1
        assert res.items[1].original_input == "https://domain2.com/fast"

        assert res.items[2].original_index == 2
        assert res.items[2].original_input == "https://domain3.com/slow2"

    asyncio.run(_test())


def test_exact_duplicate_coalescing_and_path_separation() -> None:
    async def _test() -> None:
        clock = FakeClock()

        class DummyOrchestrator(SiteScanOrchestrator):
            async def scan(
                self,
                starting_url: str | NormalizedURL,
                config: SiteScanConfig | None = None,
                recorder: Any | None = None,
            ) -> SiteScanResult:
                url_str = (
                    starting_url.normalized_url
                    if isinstance(starting_url, NormalizedURL)
                    else starting_url
                )
                stats = SiteScanStatistics(
                    pages_queued=1,
                    pages_attempted=1,
                    pages_fetched=1,
                    pages_blocked_by_robots=0,
                    pages_failed=0,
                    urls_discovered=1,
                    accepted_email_findings=1,
                    rejected_email_candidates=0,
                    elapsed_seconds=0.01,
                    stop_reason="QUEUE_EXHAUSTED",
                )
                return SiteScanResult(
                    starting_url=url_str,
                    outcome=SiteScanOutcome.COMPLETED,
                    statistics=stats,
                    page_records=(),
                    email_findings=(),
                    rejected_email_candidates=(),
                )

        orchestrator = BatchScanOrchestrator(
            orchestrator_factory=lambda f, r: DummyOrchestrator(fetcher=f, robots_evaluator=r),
            clock=clock,
        )

        inputs = [
            "https://acme.com/page",
            "https://acme.com/page#fragment",  # Exact duplicate normalized URL
            "https://acme.com/other_path",  # Different path -> not coalesced
        ]

        res = await orchestrator.scan_batch(inputs)

        assert res.outcome == BatchScanOutcome.COMPLETED
        assert res.statistics.total_inputs == 3
        assert res.statistics.valid_inputs == 3
        assert res.statistics.unique_normalized_urls == 2
        assert res.statistics.duplicate_coalesced_items == 1

        # Item 0 is canonical scan
        assert res.items[0].is_duplicate is False
        assert res.items[0].outcome == BatchItemOutcome.COMPLETED

        # Item 1 is duplicate coalesced
        assert res.items[1].is_duplicate is True
        assert res.items[1].duplicate_of_index == 0
        assert res.items[1].outcome == BatchItemOutcome.DUPLICATE_COALESCED
        assert res.items[1].result == res.items[0].result

        # Item 2 is separate path
        assert res.items[2].is_duplicate is False
        assert res.items[2].outcome == BatchItemOutcome.COMPLETED

    asyncio.run(_test())


def test_item_exception_isolation() -> None:
    async def _test() -> None:
        clock = FakeClock()

        class FaultyOrchestrator(SiteScanOrchestrator):
            async def scan(
                self,
                starting_url: str | NormalizedURL,
                config: SiteScanConfig | None = None,
                recorder: Any | None = None,
            ) -> SiteScanResult:
                url_str = (
                    starting_url.normalized_url
                    if isinstance(starting_url, NormalizedURL)
                    else starting_url
                )
                if "faulty" in url_str:
                    raise RuntimeError("Simulated site scan failure")

                stats = SiteScanStatistics(
                    pages_queued=1,
                    pages_attempted=1,
                    pages_fetched=1,
                    pages_blocked_by_robots=0,
                    pages_failed=0,
                    urls_discovered=1,
                    accepted_email_findings=1,
                    rejected_email_candidates=0,
                    elapsed_seconds=0.01,
                    stop_reason="QUEUE_EXHAUSTED",
                )
                return SiteScanResult(
                    starting_url=url_str,
                    outcome=SiteScanOutcome.COMPLETED,
                    statistics=stats,
                    page_records=(),
                    email_findings=(),
                    rejected_email_candidates=(),
                )

        orchestrator = BatchScanOrchestrator(
            orchestrator_factory=lambda f, r: FaultyOrchestrator(fetcher=f, robots_evaluator=r),
            clock=clock,
        )

        inputs = [
            "https://acme.com/good1",
            "https://acme.com/faulty",
            "https://acme.com/good2",
        ]

        res = await orchestrator.scan_batch(inputs)

        # Batch outcome is PARTIAL due to item failure
        assert res.outcome == BatchScanOutcome.PARTIAL
        assert res.statistics.failed_scans == 1
        assert res.items[1].outcome == BatchItemOutcome.FAILED
        assert "Simulated site scan failure" in (res.items[1].error_message or "")

        # Unrelated items succeed
        assert res.items[0].outcome == BatchItemOutcome.COMPLETED
        assert res.items[2].outcome == BatchItemOutcome.COMPLETED

    asyncio.run(_test())


def test_statistics_counter_invariants() -> None:
    async def _test() -> None:
        clock = FakeClock()

        class SimpleOrchestrator(SiteScanOrchestrator):
            async def scan(
                self,
                starting_url: str | NormalizedURL,
                config: SiteScanConfig | None = None,
                recorder: Any | None = None,
            ) -> SiteScanResult:
                url_str = (
                    starting_url.normalized_url
                    if isinstance(starting_url, NormalizedURL)
                    else starting_url
                )
                stats = SiteScanStatistics(
                    pages_queued=1,
                    pages_attempted=1,
                    pages_fetched=1,
                    pages_blocked_by_robots=0,
                    pages_failed=0,
                    urls_discovered=1,
                    accepted_email_findings=1,
                    rejected_email_candidates=0,
                    elapsed_seconds=0.01,
                    stop_reason="QUEUE_EXHAUSTED",
                )
                return SiteScanResult(
                    starting_url=url_str,
                    outcome=SiteScanOutcome.COMPLETED,
                    statistics=stats,
                    page_records=(),
                    email_findings=(),
                    rejected_email_candidates=(),
                )

        orchestrator = BatchScanOrchestrator(
            orchestrator_factory=lambda f, r: SimpleOrchestrator(fetcher=f, robots_evaluator=r),
            clock=clock,
        )

        inputs = [
            "https://acme.com/page1",
            "ftp://invalid-url-string",
            "https://acme.com/page1",  # duplicate
        ]

        res = await orchestrator.scan_batch(inputs)

        s = res.statistics
        assert s.total_inputs == 3
        assert s.valid_inputs == 2
        assert s.invalid_inputs == 1
        assert s.unique_normalized_urls == 1
        assert s.duplicate_coalesced_items == 1

        # Verify statistical invariants
        assert s.total_inputs == s.valid_inputs + s.invalid_inputs
        assert s.valid_inputs == s.unique_normalized_urls + s.duplicate_coalesced_items
        assert len(res.items) == s.total_inputs

    asyncio.run(_test())
