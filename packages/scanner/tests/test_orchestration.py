"""Tests for scanner-core single-site scan orchestration with zero network access."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from email_scanner.errors import (
    FetchOutcomeCode,
    PageScanOutcome,
    RobotsDecisionCode,
    SiteScanConfigError,
    SiteScanConfigErrorCode,
    SiteScanOutcome,
)
from email_scanner.models import (
    FetchResult,
    NormalizedURL,
    RedirectHop,
    RobotsDecision,
    SiteScanConfig,
)
from email_scanner.orchestration import SiteScanOrchestrator


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.current_time = start

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


class MockHTTPFetcher:
    def __init__(self, responses: dict[str, FetchResult] | None = None) -> None:
        self.responses: dict[str, FetchResult] = responses or {}
        self.fetched_urls: list[str] = []

    async def fetch(
        self,
        url: str | NormalizedURL,
        allowed_content_types: tuple[str, ...] | None = None,
        redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
        recorder: Any | None = None,
    ) -> FetchResult:
        url_str = url.normalized_url if isinstance(url, NormalizedURL) else url
        self.fetched_urls.append(url_str)

        if redirect_validator is not None and url_str in self.responses:
            res = self.responses[url_str]
            if res.redirect_history:
                from email_scanner.normalization import normalize_url

                for hop in res.redirect_history:
                    source_norm = normalize_url(hop.url)
                    target_norm = normalize_url(hop.location)
                    if not redirect_validator(source_norm, target_norm):
                        return FetchResult(
                            final_url=target_norm.normalized_url,
                            status_code=hop.status_code,
                            content_type="text/html",
                            body_text=None,
                            redirect_history=res.redirect_history,
                            outcome=FetchOutcomeCode.UNSAFE_HOST,
                            error_message="Redirect target is out of crawl scope",
                        )

        if url_str in self.responses:
            return self.responses[url_str]

        return FetchResult(
            final_url=url_str,
            status_code=404,
            content_type="text/html",
            body_text="<html><body>Page Not Found</body></html>",
            redirect_history=(),
            outcome=FetchOutcomeCode.HTTP_ERROR,
            error_message="404 Not Found",
        )


class MockRobotsEvaluator:
    def __init__(self, decisions: dict[str, RobotsDecision] | None = None) -> None:
        self.decisions: dict[str, RobotsDecision] = decisions or {}

    async def evaluate(
        self,
        url: str | NormalizedURL,
        user_agent_token: str | None = None,
        recorder: Any | None = None,
    ) -> RobotsDecision:
        url_str = url.normalized_url if isinstance(url, NormalizedURL) else url
        if url_str in self.decisions:
            return self.decisions[url_str]
        return RobotsDecision(
            target_url=url_str,
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="Allowed by default mock",
        )


def test_site_scan_config_nan_inf_rejection() -> None:
    with pytest.raises(SiteScanConfigError) as exc_info:
        SiteScanConfig(minimum_request_interval_seconds=float("nan"))
    assert exc_info.value.code == SiteScanConfigErrorCode.NON_FINITE_VALUE

    with pytest.raises(SiteScanConfigError) as exc_info:
        SiteScanConfig(max_elapsed_seconds=float("inf"))
    assert exc_info.value.code == SiteScanConfigErrorCode.NON_FINITE_VALUE


def test_one_page_successful_scan_with_email() -> None:
    async def _test() -> None:
        url = "https://acme.com/"
        html = "<html><body><h1>Contact Us</h1><p>Email: sales@acme.com</p></body></html>"
        fetcher = MockHTTPFetcher(
            {
                url: FetchResult(
                    final_url=url,
                    status_code=200,
                    content_type="text/html",
                    body_text=html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                )
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(url)

        assert res.outcome == SiteScanOutcome.COMPLETED
        assert res.statistics.pages_fetched == 1
        assert len(res.email_findings) == 1
        assert res.email_findings[0].canonical_email == "sales@acme.com"

    asyncio.run(_test())


def test_multi_page_scan_following_ranked_links() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        contact_url = "https://acme.com/contact"

        start_html = '<html><body><a href="/contact">Contact Page</a></body></html>'
        contact_html = "<html><body><p>Email: support@acme.com</p></body></html>"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=start_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                contact_url: FetchResult(
                    final_url=contact_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=contact_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(start_url, config=SiteScanConfig(max_pages=5))

        assert res.outcome == SiteScanOutcome.COMPLETED
        assert res.statistics.pages_fetched == 2
        assert len(res.email_findings) == 1
        assert res.email_findings[0].canonical_email == "support@acme.com"

    asyncio.run(_test())


def test_robots_blocked_starting_url() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        robots = MockRobotsEvaluator(
            {
                start_url: RobotsDecision(
                    target_url=start_url,
                    decision=RobotsDecisionCode.DISALLOWED,
                    crawl_delay=None,
                    reason="Disallowed by robots.txt",
                )
            }
        )
        fetcher = MockHTTPFetcher()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.ROBOTS_BLOCKED
        assert res.statistics.pages_blocked_by_robots == 1
        assert res.statistics.pages_fetched == 0
        assert len(fetcher.fetched_urls) == 0

    asyncio.run(_test())


def test_robots_blocked_child_page() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        blocked_url = "https://acme.com/secret"

        start_html = (
            '<html><body><a href="/secret">Secret Page</a><p>sales@acme.com</p></body></html>'
        )

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=start_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                )
            }
        )
        robots = MockRobotsEvaluator(
            {
                blocked_url: RobotsDecision(
                    target_url=blocked_url,
                    decision=RobotsDecisionCode.DISALLOWED,
                    crawl_delay=None,
                    reason="Disallowed child page",
                )
            }
        )
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.COMPLETED
        assert res.statistics.pages_fetched == 1
        assert res.statistics.pages_blocked_by_robots == 1
        assert len(res.email_findings) == 1

    asyncio.run(_test())


def test_starting_and_child_temporary_robots_failure() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        robots = MockRobotsEvaluator(
            {
                start_url: RobotsDecision(
                    target_url=start_url,
                    decision=RobotsDecisionCode.TEMPORARY_FAILURE,
                    crawl_delay=None,
                    reason="500 Internal Error on robots.txt",
                )
            }
        )
        fetcher = MockHTTPFetcher()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.ROBOTS_BLOCKED
        assert res.statistics.pages_blocked_by_robots == 1

    asyncio.run(_test())


def test_child_fetch_failure_produces_partial_outcome() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        broken_url = "https://acme.com/broken"

        start_html = (
            '<html><body><a href="/broken">Broken Page</a><p>contact@acme.com</p></body></html>'
        )

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=start_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                broken_url: FetchResult(
                    final_url=broken_url,
                    status_code=500,
                    content_type="text/html",
                    body_text=None,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.HTTP_ERROR,
                    error_message="500 Server Error",
                ),
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.PARTIAL
        assert res.statistics.pages_fetched == 1
        assert res.statistics.pages_failed == 1
        assert len(res.email_findings) == 1

    asyncio.run(_test())


def test_politeness_delay_and_exact_remaining_sleep_calculation() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        page2_url = "https://acme.com/page2"

        html1 = '<html><body><a href="/page2">Page 2</a></body></html>'
        html2 = "<html><body><p>contact@acme.com</p></body></html>"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=html1,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                page2_url: FetchResult(
                    final_url=page2_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=html2,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator(
            {
                page2_url: RobotsDecision(
                    target_url=page2_url,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=2.5,
                    reason="Robots crawl delay 2.5s",
                )
            }
        )

        clock = FakeClock(start=100.0)
        sleep_history: list[float] = []

        async def fake_sleeper(seconds: float) -> None:
            sleep_history.append(seconds)
            clock.advance(seconds)

        orchestrator = SiteScanOrchestrator(
            fetcher=fetcher,
            robots_evaluator=robots,
            clock=clock,
            async_sleeper=fake_sleeper,
        )

        config = SiteScanConfig(minimum_request_interval_seconds=1.0)
        res = await orchestrator.scan(start_url, config=config)

        assert res.outcome == SiteScanOutcome.COMPLETED
        assert len(sleep_history) == 1
        assert pytest.approx(sleep_history[0], 0.01) == 2.5

    asyncio.run(_test())


def test_cross_domain_redirect_blocked_before_request() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        ext_url = "https://external.com/page"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=ext_url,
                    status_code=302,
                    content_type="text/html",
                    body_text=None,
                    redirect_history=(
                        RedirectHop(url=start_url, status_code=302, location=ext_url),
                    ),
                    outcome=FetchOutcomeCode.UNSAFE_HOST,
                    error_message="Redirect target is out of crawl scope",
                )
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.FAILED
        assert res.statistics.pages_failed == 1
        assert res.page_records[0].outcome == PageScanOutcome.UNSAFE_HOST

    asyncio.run(_test())


def test_cancellation_with_partial_results() -> None:
    async def _test() -> None:
        start_url = "https://acme.com/"
        contact_url = "https://acme.com/contact"

        start_html = '<html><body><a href="/contact">Contact</a><p>sales@acme.com</p></body></html>'
        contact_html = "<html><body><p>support@acme.com</p></body></html>"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=start_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                contact_url: FetchResult(
                    final_url=contact_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=contact_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator()

        cancel_flag = False

        def cancellation_checker() -> bool:
            return cancel_flag

        clock = FakeClock()

        async def fake_sleeper(sec: float) -> None:
            nonlocal cancel_flag
            cancel_flag = True

        orchestrator = SiteScanOrchestrator(
            fetcher=fetcher,
            robots_evaluator=robots,
            clock=clock,
            async_sleeper=fake_sleeper,
            cancellation_checker=cancellation_checker,
        )

        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.CANCELLED
        assert res.statistics.pages_fetched == 1
        assert len(res.email_findings) == 1
        assert res.email_findings[0].canonical_email == "sales@acme.com"

    asyncio.run(_test())


def test_successful_site_outcomes_clear_recorder_failure_code() -> None:
    """Verify COMPLETED and COMPLETED_NO_EMAILS clear any stale recorder failure_code."""
    from email_scanner.errors import SiteScanFailureCode
    from email_scanner.models import SiteScanDiagnosticRecorder

    async def _test() -> None:
        start_url = "https://acme.com/"
        html_with_email = "<html><body>contact@acme.com</body></html>"
        html_no_email = "<html><body>Hello World</body></html>"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=html_no_email,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                )
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        rec = SiteScanDiagnosticRecorder()
        rec.failure_code = SiteScanFailureCode.UNEXPECTED_INTERNAL_ERROR

        res_no_email = await orchestrator.scan(start_url, recorder=rec)
        assert res_no_email.outcome == SiteScanOutcome.COMPLETED_NO_EMAILS
        assert res_no_email.diagnostics is not None
        assert res_no_email.diagnostics.failure_code is None

        fetcher_email = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=html_with_email,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                )
            }
        )
        orchestrator_email = SiteScanOrchestrator(fetcher=fetcher_email, robots_evaluator=robots)
        rec2 = SiteScanDiagnosticRecorder()
        rec2.failure_code = SiteScanFailureCode.UNEXPECTED_INTERNAL_ERROR

        res_completed = await orchestrator_email.scan(start_url, recorder=rec2)
        assert res_completed.outcome == SiteScanOutcome.COMPLETED
        assert res_completed.diagnostics is not None
        assert res_completed.diagnostics.failure_code is None

    asyncio.run(_test())
