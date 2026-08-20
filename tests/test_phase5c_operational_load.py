"""Bounded CLI, serialization, and offline-isolation tests for the Phase 5C harness."""

import socket

import pytest

from tools.operational_load.cli import build_parser
from tools.operational_load.fixtures import ActivityTracker, DeterministicOfflineOrchestrator
from tools.operational_load.models import LoadRunReport


def test_cli_accepts_only_controlled_sizes_and_worker_counts() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--size", "100", "--workers", "4", "--timeout", "120", "--repeats", "2"]
    )
    assert (args.size, args.workers, args.timeout, args.repeats) == (100, 4, 120, 2)
    with pytest.raises(SystemExit):
        parser.parse_args(["--size", "10000", "--workers", "4", "--timeout", "120"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--size", "100", "--workers", "8", "--timeout", "120"])


def test_report_json_round_trip() -> None:
    payload = {
        "size": 100,
        "workers": 1,
        "worker_concurrency": 2,
        "elapsed_seconds": 1,
        "urls_per_second": 100,
        "pages_per_second": 100,
        "p50_latency_seconds": 0.1,
        "p95_latency_seconds": 0.2,
        "p99_latency_seconds": 0.3,
        "peak_active_tasks": 2,
        "peak_active_claims": 2,
        "peak_database_connections": 2,
        "redis_operations": 10,
        "redis_fallbacks": 0,
        "retry_total": 0,
        "failure_total": 0,
        "success_total": 100,
        "partial_total": 0,
        "peak_python_memory_bytes": 1000,
        "result_checksum": "a" * 64,
        "csv_checksum": "b" * 64,
        "attempt_rows": 100,
        "finding_rows": 100,
        "duplicate_attempt_groups": 0,
        "duplicate_finding_groups": 0,
        "sequential_attempts": True,
        "stale_fence_zero_writes": True,
        "expired_fence_zero_writes": True,
        "nonterminal_rows": 0,
        "uncleared_claims": 0,
        "job_counters_match": True,
        "shutdown_clean": True,
    }
    report = LoadRunReport.model_validate(payload)
    assert LoadRunReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.anyio
async def test_offline_fixture_never_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external network access attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    result = await DeterministicOfflineOrchestrator(ActivityTracker(), delay_seconds=0).scan(
        "https://site0000.fixture.test/"
    )
    assert result.statistics.pages_fetched == 1
    assert len(result.email_findings) == 1
