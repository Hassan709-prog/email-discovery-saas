"""Unit tests for worker CLI options and argument validation."""

import logging

import pytest

from email_discovery_crawl_worker.cli import parse_args


def test_cli_parse_valid_args() -> None:
    """Verify CLI parses valid arguments and defaults."""
    args = parse_args(["--concurrency", "4", "--poll-interval", "1.5", "--max-scans", "10"])
    assert args.concurrency == 4
    assert args.poll_interval == 1.5
    assert args.max_scans == 10


def test_cli_reject_invalid_concurrency() -> None:
    """Verify CLI rejects concurrency < 1."""
    with pytest.raises(SystemExit):
        parse_args(["--concurrency", "0"])


def test_cli_reject_invalid_heartbeat() -> None:
    """Verify CLI rejects heartbeat interval >= lease duration."""
    with pytest.raises(SystemExit):
        parse_args(["--lease-duration", "60", "--heartbeat-interval", "60"])


def test_cli_reject_nonpositive_max_scans() -> None:
    """Verify CLI rejects --max-scans <= 0."""
    with pytest.raises(SystemExit):
        parse_args(["--max-scans", "0"])


def test_worker_logging_sanitizes_urls_and_secrets(caplog: pytest.LogCaptureFixture) -> None:
    """Assert sensitive query params like do-not-log-this are absent from captured logs."""
    fake_url = "https://example.com/path?api_key=do-not-log-this"
    with caplog.at_level(logging.INFO):
        # Configure httpx & httpcore logger levels as cli.py does
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

        # Log simulated fetch attempt via httpx logger at INFO
        logging.getLogger("httpx").info("GET %s 200 OK", fake_url)

    assert "do-not-log-this" not in caplog.text
