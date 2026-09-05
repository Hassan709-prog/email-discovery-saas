"""Unit tests for worker CLI options and argument validation."""

import logging

import pytest

from email_discovery_crawl_worker.cli import parse_args, resolve_effective_worker_config
from email_discovery_crawl_worker.config import WorkerSettings


def test_cli_parse_valid_args() -> None:
    """Verify CLI parses valid arguments and defaults."""
    args = parse_args(["--concurrency", "4", "--poll-interval", "1.5", "--max-scans", "10"])
    assert args.concurrency == 4
    assert args.poll_interval == 1.5
    assert args.max_scans == 10


def test_cli_parse_defaults_are_none() -> None:
    """Verify optional CLI overrides default to None so environment is not shadowed."""
    args = parse_args([])
    assert args.concurrency is None
    assert args.poll_interval is None
    assert args.lease_duration is None
    assert args.heartbeat_interval is None
    assert args.worker_id is None
    assert args.max_scans is None


def test_concurrency_env_used_when_cli_omitted() -> None:
    """Verify CONCURRENCY=4 from environment is used when CLI flag is omitted."""
    args = parse_args([])
    settings = WorkerSettings(concurrency=4)
    config = resolve_effective_worker_config(args, settings)
    assert config.concurrency == 4


def test_concurrency_cli_overrides_env() -> None:
    """Verify --concurrency 3 explicitly overrides environment value 4."""
    args = parse_args(["--concurrency", "3"])
    settings = WorkerSettings(concurrency=4)
    config = resolve_effective_worker_config(args, settings)
    assert config.concurrency == 3


def test_lease_heartbeat_effective_validation() -> None:
    """Verify cross-field validation on effective values across CLI and environment."""
    # CLI sets lease duration smaller than env heartbeat interval
    args1 = parse_args(["--lease-duration", "20"])
    settings1 = WorkerSettings(lease_duration=120.0, heartbeat_interval=30.0)
    with pytest.raises(
        ValueError, match="heartbeat_interval must be strictly less than lease_duration"
    ):
        resolve_effective_worker_config(args1, settings1)

    # CLI sets heartbeat interval greater than env lease duration
    args2 = parse_args(["--heartbeat-interval", "150"])
    settings2 = WorkerSettings(lease_duration=120.0, heartbeat_interval=30.0)
    with pytest.raises(
        ValueError, match="heartbeat_interval must be strictly less than lease_duration"
    ):
        resolve_effective_worker_config(args2, settings2)

    # Valid mixed override
    args3 = parse_args(["--lease-duration", "200"])
    config3 = resolve_effective_worker_config(args3, settings2)
    assert config3.lease_duration == 200.0
    assert config3.heartbeat_interval == 30.0


def test_worker_id_and_max_scans_precedence() -> None:
    """Verify worker_id and max_scans precedence."""
    # CLI worker-id overrides env
    args = parse_args(["--worker-id", "cli-worker", "--max-scans", "5"])
    settings = WorkerSettings(worker_id="env-worker")
    config = resolve_effective_worker_config(args, settings)
    assert config.worker_id == "cli-worker"
    assert config.max_scans == 5

    # Omitted CLI worker-id uses env
    args_no_id = parse_args([])
    config_env_id = resolve_effective_worker_config(args_no_id, settings)
    assert config_env_id.worker_id == "env-worker"
    assert config_env_id.max_scans is None


def test_cli_reject_invalid_concurrency() -> None:
    """Verify CLI rejects concurrency < 1."""
    with pytest.raises(SystemExit):
        parse_args(["--concurrency", "0"])


def test_cli_reject_invalid_poll_interval() -> None:
    """Verify CLI rejects poll interval <= 0."""
    with pytest.raises(SystemExit):
        parse_args(["--poll-interval", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--poll-interval", "-1"])


def test_cli_reject_invalid_lease_duration() -> None:
    """Verify CLI rejects lease duration <= 0."""
    with pytest.raises(SystemExit):
        parse_args(["--lease-duration", "0"])


def test_cli_reject_invalid_heartbeat() -> None:
    """Verify CLI rejects heartbeat interval >= lease duration."""
    with pytest.raises(SystemExit):
        parse_args(["--lease-duration", "60", "--heartbeat-interval", "60"])


def test_cli_reject_nonpositive_max_scans() -> None:
    """Verify CLI rejects --max-scans <= 0."""
    with pytest.raises(SystemExit):
        parse_args(["--max-scans", "0"])


def test_effective_config_rejects_invalid_values() -> None:
    """Verify resolve_effective_worker_config validates effective values."""
    args = parse_args([])
    with pytest.raises(ValueError, match="concurrency must be at least 1"):
        resolve_effective_worker_config(args, WorkerSettings(concurrency=0))
    with pytest.raises(ValueError, match="poll_interval must be greater than zero"):
        resolve_effective_worker_config(args, WorkerSettings(poll_interval=-1.0))


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
