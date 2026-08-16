"""Tests for experimental email_scanner CLI adapter."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from email_scanner.cli import main, serialize_scan_result
from email_scanner.errors import (
    BatchScanOutcome,
    PageScanOutcome,
    RobotsDecisionCode,
    SiteScanOutcome,
)
from email_scanner.models import (
    BatchScanResult,
    BatchScanStatistics,
    PageScanRecord,
    RobotsDecision,
    SiteScanResult,
    SiteScanStatistics,
)


def test_serialize_scan_result_enums_and_dataclasses() -> None:
    stats = SiteScanStatistics(
        pages_queued=1,
        pages_attempted=1,
        pages_fetched=1,
        pages_blocked_by_robots=0,
        pages_failed=0,
        urls_discovered=1,
        accepted_email_findings=0,
        rejected_email_candidates=0,
        elapsed_seconds=0.1,
        stop_reason="QUEUE_EXHAUSTED",
    )
    rec = PageScanRecord(
        requested_url="https://acme.com/",
        final_url="https://acme.com/",
        depth=0,
        outcome=PageScanOutcome.FETCHED_AND_PROCESSED,
        status_code=200,
        robots_decision=RobotsDecision(
            target_url="https://acme.com/",
            decision=RobotsDecisionCode.ALLOWED,
            crawl_delay=None,
            reason="Allowed",
        ),
        fetch_result=None,
        emails_found_count=0,
        links_discovered_count=0,
    )
    res = SiteScanResult(
        starting_url="https://acme.com/",
        outcome=SiteScanOutcome.COMPLETED_NO_EMAILS,
        statistics=stats,
        page_records=(rec,),
        email_findings=(),
        rejected_email_candidates=(),
    )

    serialized = serialize_scan_result(res)
    assert serialized["outcome"] == "COMPLETED_NO_EMAILS"
    assert serialized["page_records"][0]["outcome"] == "FETCHED_AND_PROCESSED"

    json_str = json.dumps(serialized, indent=2, sort_keys=True)
    assert '"outcome": "COMPLETED_NO_EMAILS"' in json_str


def test_cli_invalid_config_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan", "https://acme.com", "--max-pages", "0"])
    captured = capsys.readouterr()

    assert code == 1
    assert "WARNING: email_scanner CLI is experimental" in captured.err
    assert '"code": "INVALID_LIMIT"' in captured.out


def test_cli_invalid_url_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan", "ftp://example.com"])
    captured = capsys.readouterr()

    assert code == 2
    assert "WARNING: email_scanner CLI is experimental" in captured.err
    assert '"outcome": "FAILED"' in captured.out


@patch("email_scanner.cli.SiteScanOrchestrator.scan", new_callable=AsyncMock)
def test_cli_completed_exit_code_0(
    mock_scan: AsyncMock, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_scan.return_value = SiteScanResult(
        starting_url="https://acme.com/",
        outcome=SiteScanOutcome.COMPLETED,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=1,
            rejected_email_candidates=0,
            elapsed_seconds=0.05,
            stop_reason="QUEUE_EXHAUSTED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    code = main(["scan", "https://acme.com"])
    captured = capsys.readouterr()

    assert code == 0
    assert "WARNING: email_scanner CLI is experimental" in captured.err
    assert '"outcome": "COMPLETED"' in captured.out


@patch("email_scanner.cli.SiteScanOrchestrator.scan", new_callable=AsyncMock)
def test_cli_robots_blocked_exit_code_2(
    mock_scan: AsyncMock, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_scan.return_value = SiteScanResult(
        starting_url="https://acme.com/",
        outcome=SiteScanOutcome.ROBOTS_BLOCKED,
        statistics=SiteScanStatistics(
            pages_queued=1,
            pages_attempted=1,
            pages_fetched=0,
            pages_blocked_by_robots=1,
            pages_failed=0,
            urls_discovered=1,
            accepted_email_findings=0,
            rejected_email_candidates=0,
            elapsed_seconds=0.01,
            stop_reason="ROBOTS_DISALLOWED",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    code = main(["scan", "https://acme.com"])
    captured = capsys.readouterr()

    assert code == 2
    assert '"outcome": "ROBOTS_BLOCKED"' in captured.out


@patch("email_scanner.cli.SiteScanOrchestrator.scan", new_callable=AsyncMock)
def test_cli_partial_exit_code_3(mock_scan: AsyncMock, capsys: pytest.CaptureFixture[str]) -> None:
    mock_scan.return_value = SiteScanResult(
        starting_url="https://acme.com/",
        outcome=SiteScanOutcome.PARTIAL,
        statistics=SiteScanStatistics(
            pages_queued=2,
            pages_attempted=2,
            pages_fetched=1,
            pages_blocked_by_robots=0,
            pages_failed=1,
            urls_discovered=2,
            accepted_email_findings=1,
            rejected_email_candidates=0,
            elapsed_seconds=0.1,
            stop_reason="PAGE_FETCH_ERROR",
        ),
        page_records=(),
        email_findings=(),
        rejected_email_candidates=(),
    )

    code = main(["scan", "https://acme.com"])
    captured = capsys.readouterr()

    assert code == 3
    assert '"outcome": "PARTIAL"' in captured.out


def test_cli_batch_invalid_config_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan-batch", "--input", "nonexistent.txt", "--max-inputs", "0"])
    captured = capsys.readouterr()

    assert code == 1
    assert '"code": "INVALID_LIMIT"' in captured.out


def test_cli_batch_file_not_found_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan-batch", "--input", "nonexistent_file_path.txt"])
    captured = capsys.readouterr()

    assert code == 1
    assert '"error": "Input file not found"' in captured.out


@patch("email_scanner.cli.BatchScanOrchestrator.scan_batch", new_callable=AsyncMock)
def test_cli_batch_completed_exit_code_0(
    mock_scan_batch: AsyncMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_file = tmp_path / "urls.txt"
    input_file.write_text("https://acme.com\nhttps://company.com\n", encoding="utf-8")

    stats = BatchScanStatistics(
        total_inputs=2,
        valid_inputs=2,
        invalid_inputs=0,
        unique_normalized_urls=2,
        duplicate_coalesced_items=0,
        started_scans=2,
        completed_scans=2,
        failed_scans=0,
        cancelled_scans=0,
        peak_global_concurrency=2,
        peak_per_domain_concurrency=1,
        elapsed_seconds=0.1,
        stop_reason="COMPLETED",
    )
    mock_scan_batch.return_value = BatchScanResult(
        outcome=BatchScanOutcome.COMPLETED,
        statistics=stats,
        items=(),
    )

    code = main(["scan-batch", "--input", str(input_file)])
    captured = capsys.readouterr()

    assert code == 0
    assert "WARNING: email_scanner CLI is experimental" in captured.err
    assert '"outcome": "COMPLETED"' in captured.out
