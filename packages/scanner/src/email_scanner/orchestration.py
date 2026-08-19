"""Deterministic single-site scan orchestration module for scanner-core.

Sequentially crawls a single website starting from an initial URL, enforcing
robots policy, host safety, politeness delays, link discovery/ranking, and email extraction.
"""

import heapq
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from email_scanner.discovery import discover_and_rank_links
from email_scanner.errors import (
    FetchOutcomeCode,
    PageScanOutcome,
    RobotsDecisionCode,
    SiteScanOutcome,
    URLNormalizationError,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    EmailFinding,
    FetchResult,
    NormalizedURL,
    PageScanRecord,
    RejectedEmailCandidate,
    RobotsDecision,
    SiteScanConfig,
    SiteScanResult,
    SiteScanStatistics,
)
from email_scanner.normalization import normalize_url
from email_scanner.ranking import calculate_page_score
from email_scanner.request_gate import DomainRequestGate
from email_scanner.robots import RobotsPolicyEvaluator
from email_scanner.scope import is_in_scope

_SOURCE_PRIORITY: dict[str, int] = {
    "MAILTO": 3,
    "VISIBLE_TEXT": 2,
    "OBFUSCATED_TEXT": 1,
}


class HTTPFetcherProtocol(Protocol):
    """Protocol for async HTTP fetching abstractions."""

    async def fetch(
        self,
        url: str | NormalizedURL,
        allowed_content_types: tuple[str, ...] | None = None,
        redirect_validator: Callable[[NormalizedURL, NormalizedURL], bool] | None = None,
    ) -> FetchResult: ...


class RobotsEvaluatorProtocol(Protocol):
    """Protocol for robots policy evaluation abstractions."""

    async def evaluate(self, url: str | NormalizedURL) -> RobotsDecision: ...


@dataclass(frozen=True, slots=True)
class _QueueItem:
    score: int
    depth: int
    url: str
    sequence: int

    def __lt__(self, other: _QueueItem) -> bool:
        # Sort priority: score descending, depth ascending, url ascending, sequence ascending
        if self.score != other.score:
            return self.score > other.score
        if self.depth != other.depth:
            return self.depth < other.depth
        if self.url != other.url:
            return self.url < other.url
        return self.sequence < other.sequence


class SiteScanOrchestrator:
    """Orchestrates deterministic single-site scans."""

    def __init__(
        self,
        fetcher: HTTPFetcherProtocol | None = None,
        robots_evaluator: RobotsEvaluatorProtocol | None = None,
        clock: Callable[[], float] | None = None,
        async_sleeper: Callable[[float], Awaitable[None]] | None = None,
        cancellation_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._fetcher: HTTPFetcherProtocol = fetcher or AsyncHTTPFetcher()
        self._robots_evaluator: RobotsEvaluatorProtocol = robots_evaluator or RobotsPolicyEvaluator(
            fetcher=self._fetcher if isinstance(self._fetcher, AsyncHTTPFetcher) else None
        )
        self._clock = clock or time.monotonic
        self._sleeper = async_sleeper
        self._cancellation_checker = cancellation_checker

        if isinstance(self._fetcher, AsyncHTTPFetcher):
            gate = self._fetcher.request_gate
            if isinstance(gate, DomainRequestGate):
                if self._sleeper is not None:
                    gate.set_sleeper(self._sleeper)
                if clock is not None:
                    gate.set_clock(clock)

    async def scan(
        self,
        starting_url: str | NormalizedURL,
        config: SiteScanConfig | None = None,
    ) -> SiteScanResult:
        """Run an end-to-end single-site email scan."""
        cfg = config or SiteScanConfig()

        if isinstance(starting_url, str):
            try:
                start_url_norm = normalize_url(starting_url)
            except URLNormalizationError as err:
                return SiteScanResult(
                    starting_url=starting_url,
                    outcome=SiteScanOutcome.FAILED,
                    statistics=SiteScanStatistics(
                        pages_queued=0,
                        pages_attempted=0,
                        pages_fetched=0,
                        pages_blocked_by_robots=0,
                        pages_failed=0,
                        urls_discovered=0,
                        accepted_email_findings=0,
                        rejected_email_candidates=0,
                        elapsed_seconds=0.0,
                        stop_reason="INVALID_STARTING_URL",
                    ),
                    page_records=(),
                    email_findings=(),
                    rejected_email_candidates=(),
                    error_message=f"Invalid starting URL: {err}",
                )
        else:
            start_url_norm = starting_url

        start_url_str = start_url_norm.normalized_url
        start_time = self._clock()

        def redirect_validator(from_url: NormalizedURL, to_url: NormalizedURL) -> bool:
            return is_in_scope(to_url, start_url_norm, cfg.discovery_config.scope_mode)

        # Initialize priority queue with starting URL
        start_score, _ = calculate_page_score(start_url_str, "")
        queue: list[_QueueItem] = []
        sequence_counter = 0
        heapq.heappush(
            queue,
            _QueueItem(
                score=start_score,
                depth=0,
                url=start_url_str,
                sequence=sequence_counter,
            ),
        )
        sequence_counter += 1

        visited_urls: set[str] = set()
        discovered_urls_set: set[str] = {start_url_str}
        page_records: list[PageScanRecord] = []
        global_accepted_map: dict[str, EmailFinding] = {}
        global_rejected_set: set[RejectedEmailCandidate] = set()

        pages_queued = 1
        pages_attempted = 0
        pages_fetched = 0
        pages_blocked_by_robots = 0
        pages_failed = 0
        last_request_time: float | None = None
        stop_reason = "QUEUE_EXHAUSTED"

        while queue:
            # Check cancellation
            if self._cancellation_checker is not None and self._cancellation_checker():
                stop_reason = "CANCELLED"
                break

            # Check elapsed time budget
            now = self._clock()
            if cfg.max_elapsed_seconds is not None and (now - start_time) > cfg.max_elapsed_seconds:
                stop_reason = "MAX_ELAPSED_TIME_EXCEEDED"
                break

            # Check page budget
            if pages_attempted >= cfg.max_pages:
                stop_reason = "MAX_PAGES_REACHED"
                break

            # Pop highest priority URL
            queue_item = heapq.heappop(queue)
            url_str = queue_item.url
            depth = queue_item.depth
            if url_str in visited_urls:
                continue

            visited_urls.add(url_str)
            pages_attempted += 1

            try:
                norm_current = normalize_url(url_str)
            except URLNormalizationError:
                pages_failed += 1
                page_records.append(
                    PageScanRecord(
                        requested_url=url_str,
                        final_url=None,
                        depth=depth,
                        outcome=PageScanOutcome.FETCH_FAILED,
                        status_code=None,
                        robots_decision=RobotsDecision(
                            target_url=url_str,
                            decision=RobotsDecisionCode.TEMPORARY_FAILURE,
                            crawl_delay=None,
                            reason="URL normalization failed",
                        ),
                        fetch_result=None,
                        emails_found_count=0,
                        links_discovered_count=0,
                        error_message="Invalid URL normalization",
                    )
                )
                continue

            # 1. Evaluate Robots.txt policy
            robots_decision = await self._robots_evaluator.evaluate(norm_current)

            if robots_decision.decision == RobotsDecisionCode.DISALLOWED:
                pages_blocked_by_robots += 1
                page_records.append(
                    PageScanRecord(
                        requested_url=url_str,
                        final_url=None,
                        depth=depth,
                        outcome=PageScanOutcome.ROBOTS_DISALLOWED,
                        status_code=None,
                        robots_decision=robots_decision,
                        fetch_result=None,
                        emails_found_count=0,
                        links_discovered_count=0,
                        error_message="Robots disallowed crawling",
                    )
                )
                continue

            if robots_decision.decision == RobotsDecisionCode.TEMPORARY_FAILURE:
                pages_blocked_by_robots += 1
                page_records.append(
                    PageScanRecord(
                        requested_url=url_str,
                        final_url=None,
                        depth=depth,
                        outcome=PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
                        status_code=None,
                        robots_decision=robots_decision,
                        fetch_result=None,
                        emails_found_count=0,
                        links_discovered_count=0,
                        error_message="Robots.txt temporary failure",
                    )
                )
                continue

            # 2. Politeness fallback if fetcher does not have a request_gate
            if not hasattr(self._fetcher, "request_gate"):
                crawl_delay = robots_decision.crawl_delay or 0.0
                effective_delay = max(cfg.minimum_request_interval_seconds, crawl_delay)

                if last_request_time is not None:
                    elapsed_since_last = self._clock() - last_request_time
                    remaining_sleep = max(0.0, effective_delay - elapsed_since_last)

                    if remaining_sleep > 0.0:
                        if self._cancellation_checker is not None and self._cancellation_checker():
                            stop_reason = "CANCELLED"
                            break

                        if self._sleeper is not None:
                            await self._sleeper(remaining_sleep)

                        if self._cancellation_checker is not None and self._cancellation_checker():
                            stop_reason = "CANCELLED"
                            break

                last_request_time = self._clock()

            if self._cancellation_checker is not None and self._cancellation_checker():
                stop_reason = "CANCELLED"
                break

            # 3. Safe HTTP Fetch (politeness rate-limiting is handled by request_gate when present)
            fetch_result = await self._fetcher.fetch(
                norm_current,
                redirect_validator=redirect_validator,
            )

            if self._cancellation_checker is not None and self._cancellation_checker():
                stop_reason = "CANCELLED"
                break

            # Mark final_url as visited if different from requested URL
            final_url_str = fetch_result.final_url
            if final_url_str:
                visited_urls.add(final_url_str)

            if fetch_result.outcome != FetchOutcomeCode.SUCCESS:
                pages_failed += 1
                page_outcome = (
                    PageScanOutcome.UNSAFE_HOST
                    if fetch_result.outcome == FetchOutcomeCode.UNSAFE_HOST
                    else PageScanOutcome.FETCH_FAILED
                )
                page_records.append(
                    PageScanRecord(
                        requested_url=url_str,
                        final_url=final_url_str,
                        depth=depth,
                        outcome=page_outcome,
                        status_code=fetch_result.status_code,
                        robots_decision=robots_decision,
                        fetch_result=fetch_result,
                        emails_found_count=0,
                        links_discovered_count=0,
                        error_message=fetch_result.error_message,
                    )
                )
                continue

            # 4. Successful page processing
            pages_fetched += 1

            # Extract emails using final_url
            from email_scanner.email_pipeline import extract_emails

            extraction_result = extract_emails(
                final_url_str,
                fetch_result.body_text or "",
                cfg.email_config,
            )

            # Record rejected candidates
            for rejected in extraction_result.rejected_candidates:
                global_rejected_set.add(rejected)

            # Aggregate findings with global deterministic deduplication
            # and complete evidence tracking
            page_score = queue_item.score
            from email_scanner.models import EmailEvidenceRecord

            for finding in extraction_result.findings:
                canonical = finding.canonical_email
                page_ev_records = tuple(
                    EmailEvidenceRecord(
                        source_url=e.source_url,
                        source_kind=e.source_kind,
                        raw_candidate=e.raw_candidate,
                        evidence_snippet=e.evidence_snippet,
                        page_score=page_score,
                    )
                    for e in (finding.evidence_records or ())
                ) or (
                    EmailEvidenceRecord(
                        source_url=finding.source_url,
                        source_kind=finding.source_kind,
                        raw_candidate=finding.raw_candidate,
                        evidence_snippet=finding.evidence_snippet,
                        page_score=page_score,
                    ),
                )

                if canonical not in global_accepted_map:
                    global_accepted_map[canonical] = EmailFinding(
                        source_url=finding.source_url,
                        raw_candidate=finding.raw_candidate,
                        canonical_email=finding.canonical_email,
                        local_part=finding.local_part,
                        domain=finding.domain,
                        source_kind=finding.source_kind,
                        category=finding.category,
                        domain_affinity=finding.domain_affinity,
                        evidence_snippet=finding.evidence_snippet,
                        disposition=finding.disposition,
                        evidence_records=page_ev_records,
                    )
                else:
                    existing = global_accepted_map[canonical]
                    combined_ev = list(existing.evidence_records)
                    for new_rec in page_ev_records:
                        if not any(
                            r.source_url == new_rec.source_url
                            and r.source_kind == new_rec.source_kind
                            and r.evidence_snippet == new_rec.evidence_snippet
                            for r in combined_ev
                        ):
                            combined_ev.append(new_rec)
                    global_accepted_map[canonical] = EmailFinding(
                        source_url=existing.source_url,
                        raw_candidate=existing.raw_candidate,
                        canonical_email=existing.canonical_email,
                        local_part=existing.local_part,
                        domain=existing.domain,
                        source_kind=existing.source_kind,
                        category=existing.category,
                        domain_affinity=existing.domain_affinity,
                        evidence_snippet=existing.evidence_snippet,
                        disposition=existing.disposition,
                        evidence_records=tuple(combined_ev),
                    )

            # Discover links using final_url
            discovery_result = discover_and_rank_links(
                final_url_str,
                fetch_result.body_text or "",
                cfg.discovery_config,
            )

            # Queue discovered links if within depth and total URL limits
            if depth + 1 <= cfg.max_depth:
                for link in discovery_result.discovered_links:
                    target_url = link.normalized_url
                    if (
                        target_url not in discovered_urls_set
                        and target_url not in visited_urls
                        and len(discovered_urls_set) < cfg.max_total_discovered_urls
                    ):
                        discovered_urls_set.add(target_url)
                        link_score, _ = calculate_page_score(target_url, link.link_text)
                        heapq.heappush(
                            queue,
                            _QueueItem(
                                score=link_score,
                                depth=depth + 1,
                                url=target_url,
                                sequence=sequence_counter,
                            ),
                        )
                        sequence_counter += 1
                        pages_queued += 1

            page_records.append(
                PageScanRecord(
                    requested_url=url_str,
                    final_url=final_url_str,
                    depth=depth,
                    outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
                    status_code=fetch_result.status_code,
                    robots_decision=robots_decision,
                    fetch_result=fetch_result,
                    emails_found_count=len(extraction_result.findings),
                    links_discovered_count=len(discovery_result.discovered_links),
                )
            )

        # Record remaining queued items as skipped for audit completeness
        while queue:
            q_item = heapq.heappop(queue)
            if q_item.url not in visited_urls:
                page_records.append(
                    PageScanRecord(
                        requested_url=q_item.url,
                        final_url=None,
                        depth=q_item.depth,
                        outcome=PageScanOutcome.SKIPPED_BUDGET_REACHED,
                        status_code=None,
                        robots_decision=RobotsDecision(
                            target_url=q_item.url,
                            decision=RobotsDecisionCode.ALLOWED,
                            crawl_delay=None,
                            reason="Skipped due to scan budget limits",
                        ),
                        fetch_result=None,
                        emails_found_count=0,
                        links_discovered_count=0,
                        error_message="Skipped due to scan budget limits",
                    )
                )

        elapsed_time = self._clock() - start_time

        # Select primary email winner deterministically across all accepted candidates
        from email_scanner.primary_selection import select_primary_email

        selection_res = select_primary_email(global_accepted_map.values(), start_url_norm)
        sorted_findings = (
            (selection_res.selected_finding,) if selection_res.selected_finding else ()
        )

        # Format sorted rejected candidates
        sorted_rejected = tuple(
            rejected
            for rejected in sorted(
                global_rejected_set, key=lambda r: (r.raw_candidate, r.rejection_code.value)
            )
        )[: cfg.max_rejected_candidates]

        stats = SiteScanStatistics(
            pages_queued=pages_queued,
            pages_attempted=pages_attempted,
            pages_fetched=pages_fetched,
            pages_blocked_by_robots=pages_blocked_by_robots,
            pages_failed=pages_failed,
            urls_discovered=len(discovered_urls_set),
            accepted_email_findings=len(sorted_findings),
            rejected_email_candidates=len(sorted_rejected),
            elapsed_seconds=elapsed_time,
            stop_reason=stop_reason,
        )

        # Determine overall site outcome according to deterministic rules
        if stop_reason == "CANCELLED":
            site_outcome = SiteScanOutcome.CANCELLED
        elif pages_attempted == 0:
            site_outcome = SiteScanOutcome.FAILED
        elif pages_attempted == 1 and page_records[0].outcome in {
            PageScanOutcome.ROBOTS_DISALLOWED,
            PageScanOutcome.ROBOTS_TEMPORARY_FAILURE,
        }:
            site_outcome = SiteScanOutcome.ROBOTS_BLOCKED
        elif pages_fetched == 0:
            site_outcome = SiteScanOutcome.FAILED
        elif pages_failed > 0 or stop_reason == "MAX_ELAPSED_TIME_EXCEEDED":
            site_outcome = SiteScanOutcome.PARTIAL
        else:
            site_outcome = (
                SiteScanOutcome.COMPLETED
                if sorted_findings
                else SiteScanOutcome.COMPLETED_NO_EMAILS
            )

        return SiteScanResult(
            starting_url=start_url_str,
            outcome=site_outcome,
            statistics=stats,
            page_records=tuple(page_records),
            email_findings=sorted_findings,
            rejected_email_candidates=sorted_rejected,
        )
