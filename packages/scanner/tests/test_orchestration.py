"""Tests for scanner-core single-site scan orchestration with zero network access."""

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest

from email_scanner.errors import (
    FetchOutcomeCode,
    PageScanOutcome,
    RobotsDecisionCode,
    SiteScanConfigError,
    SiteScanConfigErrorCode,
    SiteScanFailureCode,
    SiteScanOutcome,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    FetchConfig,
    FetchResult,
    NormalizedURL,
    RedirectHop,
    RobotsDecision,
    SiteScanConfig,
    SiteScanDiagnosticRecorder,
)
from email_scanner.orchestration import SiteScanOrchestrator
from email_scanner.robots import RobotsPolicyEvaluator


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


def test_robots_403_unavailable_proceeds_to_homepage() -> None:
    """A robots.txt 403 is unavailable, not an explicit crawl prohibition."""

    class NoOpRequestGate:
        def update_domain_interval(self, *_args: object) -> None:
            return None

    class Robots403Fetcher(MockHTTPFetcher):
        config = FetchConfig()
        request_gate = NoOpRequestGate()

    async def _test() -> None:
        start_url = "https://acme.com/"
        robots_url = "https://acme.com/robots.txt"
        fetcher = Robots403Fetcher(
            {
                robots_url: FetchResult(
                    final_url=robots_url,
                    status_code=403,
                    content_type="text/plain",
                    body_text="Forbidden",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.HTTP_ERROR,
                    error_message="HTTP status 403",
                ),
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text="<html><body>Email: hello@acme.com</body></html>",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        typed_fetcher = cast(AsyncHTTPFetcher, fetcher)
        robots = RobotsPolicyEvaluator(fetcher=typed_fetcher)
        orchestrator = SiteScanOrchestrator(fetcher=typed_fetcher, robots_evaluator=robots)

        result = await orchestrator.scan(start_url)

        assert fetcher.fetched_urls[:2] == [robots_url, start_url]
        assert result.outcome == SiteScanOutcome.COMPLETED
        assert result.statistics.pages_fetched == 1
        assert [finding.canonical_email for finding in result.email_findings] == ["hello@acme.com"]

    asyncio.run(_test())


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


def test_orchestration_homepage_permanent_dns_failure() -> None:
    async def _test() -> None:
        start_url = "https://nonexistent.example"

        class MockPermanentDNSFetcher(AsyncHTTPFetcher):
            def __init__(self) -> None:
                super().__init__(config=FetchConfig(), pinned=False)

            async def fetch(
                self,
                url: str | NormalizedURL,
                allowed_content_types: tuple[str, ...] | None = None,
                redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
                recorder: Any | None = None,
            ) -> FetchResult:
                if recorder is not None:
                    recorder.failure_code = SiteScanFailureCode.DNS_NAME_NOT_FOUND
                return FetchResult(
                    final_url=str(url),
                    status_code=None,
                    content_type=None,
                    body_text=None,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.DNS_NAME_NOT_FOUND,
                    error_message="Host nonexistent.example does not exist",
                )

        fetcher = MockPermanentDNSFetcher()
        robots = MockRobotsEvaluator()  # Allows crawling
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        rec = SiteScanDiagnosticRecorder()
        res = await orchestrator.scan(start_url, recorder=rec)

        assert res.outcome == SiteScanOutcome.FAILED
        assert len(res.page_records) == 1
        assert res.page_records[0].outcome == PageScanOutcome.FETCH_FAILED
        assert res.page_records[0].fetch_result is not None
        assert res.page_records[0].fetch_result.outcome == FetchOutcomeCode.DNS_NAME_NOT_FOUND
        assert res.diagnostics is not None
        assert res.diagnostics.failure_code == SiteScanFailureCode.DNS_NAME_NOT_FOUND
        assert res.diagnostics.retry_count == 0

    asyncio.run(_test())


def test_orchestration_robots_permanent_dns_failure() -> None:
    async def _test() -> None:
        start_url = "https://nonexistent.example"

        class MockRobotsPermanentDNS(RobotsPolicyEvaluator):
            def __init__(self) -> None:
                pass

            async def evaluate(
                self,
                url: str | NormalizedURL,
                user_agent_token: str | None = None,
                recorder: Any | None = None,
            ) -> RobotsDecision:
                if recorder is not None:
                    recorder.failure_code = SiteScanFailureCode.DNS_NAME_NOT_FOUND
                return RobotsDecision(
                    target_url=str(url),
                    decision=RobotsDecisionCode.TEMPORARY_FAILURE,
                    crawl_delay=None,
                    reason="robots.txt fetch error: Host nonexistent.example does not exist",
                )

        fetcher = MockHTTPFetcher({})
        robots = MockRobotsPermanentDNS()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        rec = SiteScanDiagnosticRecorder()
        res = await orchestrator.scan(start_url, recorder=rec)

        assert res.outcome == SiteScanOutcome.ROBOTS_BLOCKED
        assert len(res.page_records) == 1
        assert res.page_records[0].outcome == PageScanOutcome.ROBOTS_TEMPORARY_FAILURE
        assert res.diagnostics is not None
        assert res.diagnostics.failure_code == SiteScanFailureCode.DNS_NAME_NOT_FOUND
        assert res.diagnostics.retry_count == 0

    asyncio.run(_test())


def test_orchestration_recovers_and_extracts_when_href_malformed() -> None:
    """Orchestration continues, discovers valid links, and extracts emails
    when a malformed href is present.
    """

    async def _test() -> None:
        start_url = "https://hardyarchitecture.com/"
        home_html = """
        <html>
          <body>
            <h1>Hardy Architecture</h1>
            <a href="http://[album-1]">DeKalb County, GA Courthouse</a>
            <a href="/contact">Contact Page</a>
            <p>Direct info: info@hardyarchitecture.com</p>
          </body>
        </html>
        """
        contact_html = """
        <html>
          <body>
            <h1>Contact Us</h1>
            <p>Reach out: team@hardyarchitecture.com</p>
          </body>
        </html>
        """
        responses = {
            "https://hardyarchitecture.com/": FetchResult(
                final_url="https://hardyarchitecture.com/",
                status_code=200,
                content_type="text/html",
                body_text=home_html,
                redirect_history=(),
                outcome=FetchOutcomeCode.SUCCESS,
            ),
            "https://hardyarchitecture.com/contact": FetchResult(
                final_url="https://hardyarchitecture.com/contact",
                status_code=200,
                content_type="text/html",
                body_text=contact_html,
                redirect_history=(),
                outcome=FetchOutcomeCode.SUCCESS,
            ),
        }

        class MockRobotsAllowed(RobotsPolicyEvaluator):
            def __init__(self) -> None:
                pass

            async def evaluate(
                self,
                url: str | NormalizedURL,
                user_agent_token: str | None = None,
                recorder: Any | None = None,
            ) -> RobotsDecision:
                return RobotsDecision(
                    target_url=str(url),
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="Allowed",
                )

        fetcher = MockHTTPFetcher(responses)
        robots = MockRobotsAllowed()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        res = await orchestrator.scan(start_url)
        assert res.outcome == SiteScanOutcome.COMPLETED
        assert len(res.email_findings) == 1
        assert res.email_findings[0].canonical_email == "info@hardyarchitecture.com"
        fetched_pages = {p.requested_url for p in res.page_records}
        assert "https://hardyarchitecture.com/" in fetched_pages
        assert "https://hardyarchitecture.com/contact" in fetched_pages
        assert res.page_records[1].emails_found_count == 1

    asyncio.run(_test())


def test_fallback_robots_allowed_proceeds() -> None:
    """Allowed fallback variant proceeds with fetch and crawling."""

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        placeholder_html = (
            "<html><head><title>Index of /</title></head><body>Directory Index</body></html>"
        )
        var_html = '<html><body><a href="/contact">Contact</a><p>sales@acme.org</p></body></html>'

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=placeholder_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                var_url: FetchResult(
                    final_url=var_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=var_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                "https://www.acme.org/contact": FetchResult(
                    final_url="https://www.acme.org/contact",
                    status_code=200,
                    content_type="text/html",
                    body_text="<html><body>Contact page</body></html>",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator(
            {
                start_url: RobotsDecision(
                    target_url=start_url,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="Allowed",
                ),
                var_url: RobotsDecision(
                    target_url=var_url,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="Allowed",
                ),
            }
        )
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)
        res = await orchestrator.scan(start_url)

        assert var_url in fetcher.fetched_urls
        assert res.outcome == SiteScanOutcome.COMPLETED
        assert len(res.page_records) >= 2
        assert res.page_records[1].requested_url == var_url
        assert res.page_records[1].outcome == PageScanOutcome.FETCHED_AND_PROCESSED

    asyncio.run(_test())


def test_fallback_robots_disallowed_does_not_fetch() -> None:
    """Disallowed fallback variant does not fetch or crawl."""

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        placeholder_html = (
            "<html><head><title>Index of /</title></head><body>Directory Index</body></html>"
        )

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=placeholder_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator(
            {
                start_url: RobotsDecision(
                    target_url=start_url,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="Allowed",
                ),
                var_url: RobotsDecision(
                    target_url=var_url,
                    decision=RobotsDecisionCode.DISALLOWED,
                    crawl_delay=None,
                    reason="Disallowed by robots.txt",
                ),
            }
        )
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)
        res = await orchestrator.scan(start_url)

        assert var_url not in fetcher.fetched_urls
        assert res.outcome == SiteScanOutcome.ROBOTS_BLOCKED
        assert any(p.outcome == PageScanOutcome.ROBOTS_DISALLOWED for p in res.page_records)

    asyncio.run(_test())


def test_fallback_robots_temporary_failure_does_not_fetch_and_is_retryable() -> None:
    """Temporary robots failure on fallback does not fetch and maintains retryability."""
    from email_discovery_crawl_worker.outcome_classifier import (
        classify_error_code_and_retryability,
    )

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        placeholder_html = (
            "<html><head><title>Index of /</title></head><body>Directory Index</body></html>"
        )

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=placeholder_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator(
            {
                start_url: RobotsDecision(
                    target_url=start_url,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="Allowed",
                ),
                var_url: RobotsDecision(
                    target_url=var_url,
                    decision=RobotsDecisionCode.TEMPORARY_FAILURE,
                    crawl_delay=None,
                    reason="Robots.txt temporary failure",
                ),
            }
        )
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)
        res = await orchestrator.scan(start_url)

        assert var_url not in fetcher.fetched_urls
        assert res.outcome == SiteScanOutcome.ROBOTS_BLOCKED
        assert any(p.outcome == PageScanOutcome.ROBOTS_TEMPORARY_FAILURE for p in res.page_records)

        err_code, is_retryable = classify_error_code_and_retryability(res)
        assert err_code == "ROBOTS_FETCH_ERROR"
        assert is_retryable is True

    asyncio.run(_test())


def test_fallback_cancellation_propagates() -> None:
    """Cancellation during fallback stops scan immediately and sets CANCELLED outcome."""

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        placeholder_html = (
            "<html><head><title>Index of /</title></head><body>Directory Index</body></html>"
        )

        cancelled = False

        def cancel_check() -> bool:
            return cancelled

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=placeholder_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )

        class CancellingRobots(RobotsPolicyEvaluator):
            def __init__(self) -> None:
                pass

            async def evaluate(
                self,
                url: str | NormalizedURL,
                user_agent_token: str | None = None,
                recorder: Any | None = None,
            ) -> RobotsDecision:
                nonlocal cancelled
                target_s = url.normalized_url if isinstance(url, NormalizedURL) else url
                if "www." in target_s:
                    cancelled = True
                return RobotsDecision(
                    target_url=target_s,
                    decision=RobotsDecisionCode.ALLOWED,
                    crawl_delay=None,
                    reason="Allowed",
                )

        orchestrator = SiteScanOrchestrator(
            fetcher=fetcher,
            robots_evaluator=CancellingRobots(),
            cancellation_checker=cancel_check,
        )
        res = await orchestrator.scan(start_url)
        assert res.outcome == SiteScanOutcome.CANCELLED
        assert var_url not in fetcher.fetched_urls

    asyncio.run(_test())


def test_fallback_email_only_on_fallback_homepage() -> None:
    """Email found only on fallback homepage is correctly extracted with clean page metadata."""

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        placeholder_html = (
            "<html><head><title>Index of /</title></head><body>Directory Index</body></html>"
        )
        var_html = "<html><body>Contact us: fallback_sales@acme.org</body></html>"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=placeholder_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                var_url: FetchResult(
                    final_url=var_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=var_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)
        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.COMPLETED
        assert len(res.email_findings) == 1
        assert res.email_findings[0].canonical_email == "fallback_sales@acme.org"
        assert res.page_records[0].requested_url == start_url
        assert res.page_records[0].emails_found_count == 0
        assert res.page_records[1].requested_url == var_url
        assert res.page_records[1].emails_found_count == 1
        assert res.page_records[1].final_url == var_url

    asyncio.run(_test())


def test_fallback_email_on_fallback_contact_page() -> None:
    """Fallback links are discovered and crawled, finding emails on fallback subpages."""

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        var_contact_url = "https://www.acme.org/contact"
        placeholder_html = (
            "<html><head><title>Index of /</title></head><body>Directory Index</body></html>"
        )
        var_home_html = '<html><body><a href="/contact">Contact</a></body></html>'
        var_contact_html = "<html><body>Email: fallback_support@acme.org</body></html>"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=placeholder_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                var_url: FetchResult(
                    final_url=var_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=var_home_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                var_contact_url: FetchResult(
                    final_url=var_contact_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=var_contact_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)
        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.COMPLETED
        assert len(res.email_findings) == 1
        assert res.email_findings[0].canonical_email == "fallback_support@acme.org"
        assert any(p.requested_url == var_contact_url for p in res.page_records)

    asyncio.run(_test())


def test_fallback_same_email_on_both_variants_no_double_count() -> None:
    """Same email found on both original and fallback variants is aggregated exactly once."""

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        orig_html = "<html><head><title>Index of /</title></head><body>info@acme.org</body></html>"
        var_html = "<html><body>Welcome! Contact: info@acme.org</body></html>"

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=orig_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                var_url: FetchResult(
                    final_url=var_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=var_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)
        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.COMPLETED
        assert len(res.email_findings) == 1
        assert res.statistics.accepted_email_findings == 1
        assert res.email_findings[0].canonical_email == "info@acme.org"

    asyncio.run(_test())


def test_fallback_with_malformed_links_handled_safely() -> None:
    """Fallback page containing malformed or unparseable links does not crash."""

    async def _test() -> None:
        start_url = "https://acme.org/"
        var_url = "https://www.acme.org/"
        placeholder_html = (
            "<html><head><title>Index of /</title></head><body>Directory Index</body></html>"
        )
        var_html = (
            "<html><body>"
            '<a href="javascript:void(0)">JS</a>'
            '<a href="http://:invalid">Invalid Port</a>'
            '<a href="mailto:sales@acme.org">Mail</a>'
            '<a href="/valid-team">Team</a>'
            "</body></html>"
        )

        fetcher = MockHTTPFetcher(
            {
                start_url: FetchResult(
                    final_url=start_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=placeholder_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                var_url: FetchResult(
                    final_url=var_url,
                    status_code=200,
                    content_type="text/html",
                    body_text=var_html,
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
                "https://www.acme.org/valid-team": FetchResult(
                    final_url="https://www.acme.org/valid-team",
                    status_code=200,
                    content_type="text/html",
                    body_text="<html><body>team@acme.org</body></html>",
                    redirect_history=(),
                    outcome=FetchOutcomeCode.SUCCESS,
                ),
            }
        )
        robots = MockRobotsEvaluator()
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)
        res = await orchestrator.scan(start_url)

        assert res.outcome == SiteScanOutcome.COMPLETED
        emails = {f.canonical_email for f in res.email_findings}
        assert "sales@acme.org" in emails or "team@acme.org" in emails

    asyncio.run(_test())
