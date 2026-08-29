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


def test_cli_default_output_path_under_ignored_dir() -> None:
    from tools.operational_load.cli import default_output_path

    success_path = default_output_path(100, 2, success=True)
    assert success_path.parent.name == ".operational-load-output"
    assert "load_report_s100_w2_" in success_path.name

    failure_path = default_output_path(500, 4, success=False)
    assert failure_path.parent.name == ".operational-load-output"
    assert "load_failure_s500_w4_" in failure_path.name


def test_failure_report_serialization() -> None:
    from tools.operational_load.models import LoadRunFailureReport

    report = LoadRunFailureReport(
        size=100,
        workers=2,
        error_type="TimeoutError",
        error_message="offline load run exceeded bound",
        phase="wait_terminal",
        elapsed_seconds=120.0,
        cleanup_errors=[],
        partial_report=None,
    )
    data = report.model_dump(mode="json")
    assert data["error_type"] == "TimeoutError"
    assert data["phase"] == "wait_terminal"
    assert data["cleanup_errors"] == []


def test_failure_report_excludes_sensitive_data() -> None:
    from tools.operational_load.models import LoadRunFailureReport

    report = LoadRunFailureReport(
        size=100,
        workers=2,
        error_type="TimeoutError",
        error_message="Offline load execution exceeded configured timeout bound",
        phase="wait_terminal",
        elapsed_seconds=120.0,
        cleanup_errors=["Worker task error during cleanup stage"],
        partial_report=None,
    )
    serialized = report.model_dump_json()
    forbidden = ["postgres", "redis://", "password", "secret", "site0000", "org:", "job:"]
    for word in forbidden:
        assert word not in serialized


@pytest.mark.anyio
async def test_unit_worker_stop_helper_graceful_path() -> None:
    import asyncio

    from tools.operational_load.harness import (
        _stop_worker_tasks,  # pyright: ignore[reportPrivateUsage]
    )

    class FakeWorker:
        def __init__(self) -> None:
            self.shutdown_requested = False

        def request_shutdown(self) -> None:
            self.shutdown_requested = True

    w = FakeWorker()

    async def fake_run() -> None:
        while not w.shutdown_requested:
            await asyncio.sleep(0.01)

    t = asyncio.create_task(fake_run())
    errors = await _stop_worker_tasks([w], [t], timeout_seconds=1.0)
    assert errors == []
    assert t.done()
    assert w.shutdown_requested is True


@pytest.mark.anyio
async def test_unit_worker_stop_helper_cancellation_path() -> None:
    import asyncio

    from tools.operational_load.harness import (
        _stop_worker_tasks,  # pyright: ignore[reportPrivateUsage]
    )

    class FakeWorker:
        def request_shutdown(self) -> None:
            pass

    w = FakeWorker()

    async def fake_blocking_run() -> None:
        await asyncio.sleep(3600)

    t = asyncio.create_task(fake_blocking_run())
    errors = await _stop_worker_tasks([w], [t], timeout_seconds=1.0)
    assert errors == []
    assert t.done()
    assert t.cancelled()


@pytest.mark.anyio
async def test_unit_cleanup_stage_error_accumulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import tracemalloc

    import tools.operational_load.harness as harness_mod
    from tools.operational_load.harness import run_load
    from tools.operational_load.models import OperationalLoadError

    original_cleanup = harness_mod._cleanup  # pyright: ignore[reportPrivateUsage]
    cleanup_count = 0

    async def failing_cleanup(*args: object, **kwargs: object) -> None:
        nonlocal cleanup_count
        cleanup_count += 1
        await original_cleanup(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        if cleanup_count > 1:
            raise RuntimeError("simulated cleanup error")

    monkeypatch.setattr(harness_mod, "_cleanup", failing_cleanup)

    database_url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/email_discovery"
    )
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    with pytest.raises(OperationalLoadError) as exc_info:
        await run_load(
            size=100,
            worker_count=1,
            database_url=database_url,
            redis_url=redis_url,
            timeout_seconds=30.0,
        )

    report = exc_info.value.report
    assert "Database and Redis cleanup error" in report.cleanup_errors
    assert not tracemalloc.is_tracing()


@pytest.mark.anyio
async def test_integration_load_timeout_with_controlled_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import tracemalloc

    import tools.operational_load.harness as harness_mod
    from tools.operational_load.fixtures import ActivityTracker, DeterministicOfflineOrchestrator
    from tools.operational_load.harness import run_load
    from tools.operational_load.models import OperationalLoadError

    original_worker = harness_mod.CrawlWorker

    def slow_worker_factory(*args: object, **kwargs: object) -> harness_mod.CrawlWorker:
        tracker = ActivityTracker()
        kwargs["orchestrator_factory"] = lambda: DeterministicOfflineOrchestrator(
            tracker, delay_seconds=60.0
        )
        return original_worker(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(harness_mod, "CrawlWorker", slow_worker_factory)

    database_url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/email_discovery"
    )
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    with pytest.raises(OperationalLoadError) as exc_info:
        await run_load(
            size=100,
            worker_count=1,
            database_url=database_url,
            redis_url=redis_url,
            timeout_seconds=1.0,
        )

    report = exc_info.value.report
    assert report.error_type == "TimeoutError"
    assert report.phase == "wait_terminal"
    assert report.error_message == "Offline load execution exceeded configured timeout bound"
    assert not tracemalloc.is_tracing()
    if report.partial_report is not None:
        assert report.partial_report.shutdown_clean is True


def test_cli_repeat_checksum_mismatch_creates_failure_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.operational_load.cli as cli_mod
    from tools.operational_load.models import LoadRunReport

    call_count = 0

    async def mock_run_load(*_args: object, **_kwargs: object) -> LoadRunReport:
        nonlocal call_count
        call_count += 1
        payload = {
            "size": 100,
            "workers": 1,
            "worker_concurrency": 2,
            "elapsed_seconds": 1.0,
            "urls_per_second": 100.0,
            "pages_per_second": 100.0,
            "p50_latency_seconds": 0.01,
            "p95_latency_seconds": 0.01,
            "p99_latency_seconds": 0.01,
            "peak_active_tasks": 2,
            "peak_active_claims": 2,
            "peak_database_connections": 4,
            "redis_operations": 10,
            "redis_fallbacks": 0,
            "retry_total": 0,
            "failure_total": 0,
            "success_total": 100,
            "partial_total": 0,
            "peak_python_memory_bytes": 1000,
            "result_checksum": ("a" if call_count == 1 else "b") * 64,
            "csv_checksum": "c" * 64,
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
        return LoadRunReport.model_validate(payload)

    monkeypatch.setattr(cli_mod, "run_load", mock_run_load)
    exit_code = cli_mod.main(
        ["--size", "100", "--workers", "1", "--timeout", "120", "--repeats", "2", "--no-warmup"]
    )
    assert exit_code == 1


def test_scan_url_csv_and_checksum_metrics_formatting() -> None:
    """Verify metrics CSV formatting uses normalized_url/original_input without referencing .url."""
    import csv
    import hashlib
    import io
    from types import SimpleNamespace

    rows = [
        SimpleNamespace(
            normalized_url="https://site0002.fixture.test/",
            original_input="https://site0002.fixture.test/",
            status="COMPLETED",
            attempt_count=1,
            pages_fetched=1,
        ),
        SimpleNamespace(
            normalized_url=None,
            original_input="invalid-url-input",
            status="INVALID",
            attempt_count=0,
            pages_fetched=0,
        ),
        SimpleNamespace(
            normalized_url="https://site0001.fixture.test/",
            original_input="https://site0001.fixture.test/",
            status="COMPLETED",
            attempt_count=1,
            pages_fetched=1,
        ),
    ]

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["url", "status", "attempts", "pages"])
    for row in sorted(rows, key=lambda item: item.normalized_url or item.original_input):
        url_display = row.normalized_url or row.original_input
        writer.writerow([url_display, row.status, row.attempt_count, row.pages_fetched])

    content = csv_buffer.getvalue()
    lines = content.strip().splitlines()
    assert len(lines) == 4
    assert lines[0] == "url,status,attempts,pages"
    assert lines[1] == "https://site0001.fixture.test/,COMPLETED,1,1"
    assert lines[2] == "https://site0002.fixture.test/,COMPLETED,1,1"
    assert lines[3] == "invalid-url-input,INVALID,0,0"

    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert len(checksum) == 64
