"""Offline deterministic orchestration fixture with no transport dependency."""

from __future__ import annotations

import asyncio

from email_scanner.errors import SiteScanOutcome
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailFinding,
    EmailSourceKind,
    SiteScanDiagnostics,
    SiteScanResult,
    SiteScanStatistics,
)


class ActivityTracker:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self._lock = asyncio.Lock()

    async def enter(self) -> None:
        async with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    async def leave(self) -> None:
        async with self._lock:
            self.active -= 1


class DeterministicOfflineOrchestrator:
    """Return a stable result while exercising the production worker persistence path."""

    def __init__(self, tracker: ActivityTracker, delay_seconds: float = 0.005) -> None:
        self.tracker = tracker
        self.delay_seconds = delay_seconds

    async def scan(self, url: str) -> SiteScanResult:
        await self.tracker.enter()
        try:
            await asyncio.sleep(self.delay_seconds)
            index = int(url.rstrip("/").rsplit("site", 1)[1].split(".", 1)[0])
            email = f"contact{index:04d}@fixture{index:04d}.test"
            finding = EmailFinding(
                source_url=url,
                raw_candidate=email,
                canonical_email=email,
                local_part=f"contact{index:04d}",
                domain=f"fixture{index:04d}.test",
                source_kind=EmailSourceKind.VISIBLE_TEXT,
                category=EmailCategory.ROLE_BASED,
                domain_affinity=DomainAffinity.EXACT_HOST,
                evidence_snippet="deterministic offline contact",
            )
            return SiteScanResult(
                starting_url=url,
                outcome=SiteScanOutcome.COMPLETED,
                statistics=SiteScanStatistics(
                    pages_queued=1,
                    pages_attempted=1,
                    pages_fetched=1,
                    pages_blocked_by_robots=0,
                    pages_failed=0,
                    urls_discovered=0,
                    accepted_email_findings=1,
                    rejected_email_candidates=0,
                    elapsed_seconds=self.delay_seconds,
                    stop_reason="completed",
                ),
                page_records=(),
                email_findings=(finding,),
                rejected_email_candidates=(),
                diagnostics=SiteScanDiagnostics(
                    total_duration_seconds=self.delay_seconds,
                    http_fetch_duration_seconds=self.delay_seconds,
                ),
            )
        finally:
            await self.tracker.leave()
