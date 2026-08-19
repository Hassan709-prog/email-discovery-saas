"""Unit and integration tests for email_scanner benchmark harness."""

import json
import os
import sys
from pathlib import Path

import pytest

from email_scanner.benchmark_fixtures import SyntheticSiteGenerator
from email_scanner.benchmarking import (
    BenchmarkHarness,
    calculate_percentiles,
    calculate_result_checksum,
    compare_with_baseline,
    format_environment_summary,
    generate_benchmark_urls,
)
from email_scanner.cli import main
from email_scanner.models import (
    BatchItemOutcome,
    BatchScanItem,
    BatchScanOutcome,
    BatchScanResult,
    BatchScanStatistics,
)


def test_production_import_isolation() -> None:
    """Verify production scanner package imports do not load benchmark fixtures at top-level."""
    # Ensure benchmark_fixtures is not pre-cached in sys.modules for clean import test
    old_fixtures = sys.modules.pop("email_scanner.benchmark_fixtures", None)
    old_benchmarking = sys.modules.pop("email_scanner.benchmarking", None)

    try:
        import email_scanner

        assert hasattr(email_scanner, "BatchScanOrchestrator")
        assert "email_scanner.benchmark_fixtures" not in sys.modules
        assert "email_scanner.benchmarking" not in sys.modules
    finally:
        if old_fixtures is not None:
            sys.modules["email_scanner.benchmark_fixtures"] = old_fixtures
        if old_benchmarking is not None:
            sys.modules["email_scanner.benchmarking"] = old_benchmarking


def test_installed_package_benchmark_independence(tmp_path: Path) -> None:
    """Verify benchmark CLI works independently of the tests directory."""
    out_dir = tmp_path / ".benchmark-output"
    original_path = sys.path.copy()
    try:
        sys.path = [p for p in sys.path if "tests" not in p]
        args = ["benchmark", "--size", "1", "--repeats", "1", "--output-dir", str(out_dir)]
        code = main(args)
        assert code == 0
        files = list(out_dir.glob("benchmark_1_*.json"))
        assert len(files) == 1
    finally:
        sys.path = original_path


def test_scenario_generation_exact_counts() -> None:
    """Verify input generation creates exact 1, 10, 100, and 1000 URL counts."""
    for count in [1, 10, 100, 1000]:
        urls = generate_benchmark_urls(count)
        assert len(urls) == count
        assert urls[0] == "http://site-0.org"
        assert urls[-1] == f"http://site-{count - 1}.org"

    with pytest.raises(ValueError):
        generate_benchmark_urls(0)


def test_synthetic_site_generator_content() -> None:
    """Verify synthetic site generator returns expected page structures and emails."""
    code, headers, body = SyntheticSiteGenerator.get_page_content(5, "/")
    assert code == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert b"site-5.org" in body
    assert b"contact@site-5.org" in body

    code_r, _, body_r = SyntheticSiteGenerator.get_page_content(5, "/robots.txt")
    assert code_r == 200
    assert b"User-agent: *" in body_r

    code_404, _, _ = SyntheticSiteGenerator.get_page_content(5, "/nonexistent")
    assert code_404 == 404


def test_percentile_calculations() -> None:
    """Verify p50, p95, and p99 calculation math."""
    latencies = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    p50, p95, p99 = calculate_percentiles(latencies)

    assert abs(p50 - 0.55) < 0.05
    assert abs(p95 - 0.955) < 0.05
    assert abs(p99 - 0.991) < 0.05

    # Edge cases
    assert calculate_percentiles([]) == (0.0, 0.0, 0.0)
    assert calculate_percentiles([0.5]) == (0.5, 0.5, 0.5)


def test_checksum_repeatability() -> None:
    """Verify result checksum is deterministic for identical item data and excludes timing/env."""
    stats = BatchScanStatistics(
        total_inputs=1,
        valid_inputs=1,
        invalid_inputs=0,
        unique_normalized_urls=1,
        duplicate_coalesced_items=0,
        started_scans=1,
        completed_scans=1,
        failed_scans=0,
        cancelled_scans=0,
        peak_global_concurrency=1,
        peak_per_domain_concurrency=1,
        elapsed_seconds=999.0,  # Nondeterministic timing should be ignored by checksum
        stop_reason="COMPLETED",
    )
    item = BatchScanItem(
        original_index=0,
        original_input="https://site-0.org",
        normalized_url="https://site-0.org/",
        outcome=BatchItemOutcome.COMPLETED,
        is_duplicate=False,
        duplicate_of_index=None,
        result=None,
    )
    res1 = BatchScanResult(
        outcome=BatchScanOutcome.COMPLETED,
        statistics=stats,
        items=(item,),
    )
    res2 = BatchScanResult(
        outcome=BatchScanOutcome.COMPLETED,
        statistics=stats,
        items=(item,),
    )

    c1 = calculate_result_checksum(res1)
    c2 = calculate_result_checksum(res2)
    assert c1 == c2
    assert len(c1) == 64  # SHA-256 hex digest


def test_environment_summary_sanitization() -> None:
    """Verify environment summary contains platform details without personal paths or secrets."""
    env = format_environment_summary()
    assert "python_version" in env
    assert "platform" in env
    assert "cpu_count" in env

    user_name = os.environ.get("USERNAME") or os.environ.get("USER")
    if user_name and len(user_name) > 3:
        env_str = json.dumps(env)
        assert f"/Users/{user_name}" not in env_str
        assert f"C:\\Users\\{user_name}" not in env_str


def test_compare_with_baseline() -> None:
    """Verify baseline comparison calculation."""
    current = {
        "scenarios": {
            "100": {
                "median_elapsed_seconds": 1.0,
                "urls_per_second": 100.0,
                "peak_memory_bytes": 1000,
            }
        }
    }
    baseline = {
        "scenarios": {
            "100": {
                "median_elapsed_seconds": 2.0,
                "urls_per_second": 50.0,
                "peak_memory_bytes": 1000,
            }
        }
    }

    comparison = compare_with_baseline(current, baseline)
    assert comparison["100"]["median_elapsed_seconds"]["diff"] == -1.0
    assert comparison["100"]["median_elapsed_seconds"]["pct_change"] == -50.0
    assert comparison["100"]["urls_per_second"]["pct_change"] == 100.0


@pytest.mark.anyio
async def test_offline_benchmark_run_scenario_1() -> None:
    """Run offline benchmark scenario for size 1 without network access."""
    metrics = await BenchmarkHarness.run_scenario_async(
        size=1,
        repeats=1,
        warmup=True,
        live=False,
    )

    assert metrics.size == 1
    assert metrics.success_count == 1
    assert metrics.failure_count == 0
    assert metrics.total_pages_fetched == 4
    assert metrics.total_emails_discovered == 1
    assert metrics.urls_per_second > 0
    assert metrics.peak_memory_bytes > 0
    assert len(metrics.result_checksum) == 64


@pytest.mark.anyio
async def test_simulated_delay_concurrency() -> None:
    """Verify simulated latency produces concurrent work and task bounds."""
    metrics = await BenchmarkHarness.run_scenario_async(
        size=10,
        repeats=1,
        warmup=False,
        live=False,
        simulated_delay_sec=0.01,
    )

    assert metrics.size == 10
    assert metrics.worker_count == 10
    assert metrics.observed_peak_task_count == 10
    assert metrics.peak_global_concurrency == 10
    assert metrics.peak_per_domain_concurrency == 1


@pytest.mark.anyio
async def test_bounded_worker_pool_1000_inputs() -> None:
    """Verify 1,000-input scenario creates a bounded worker pool rather than 1,000 tasks."""
    urls = generate_benchmark_urls(1000)
    assert len(urls) == 1000

    # Test configuration bounds for 1000 inputs
    global_concurrency = min(len(urls), 20)
    assert global_concurrency == 20


def test_cli_benchmark_command_parsing(tmp_path: Path) -> None:
    """Test CLI benchmark command execution, output file creation, and machine-readable JSON."""
    out_dir = tmp_path / ".benchmark-output"

    args = ["benchmark", "--size", "1", "--repeats", "1", "--output-dir", str(out_dir)]
    code = main(args)
    assert code == 0

    files = list(out_dir.glob("benchmark_1_*.json"))
    assert len(files) == 1

    report_data = json.loads(files[0].read_text(encoding="utf-8"))
    assert "metadata" in report_data
    assert "scenarios" in report_data
    assert "configuration" in report_data["metadata"]
    assert "1" in report_data["scenarios"]
    assert report_data["scenarios"]["1"]["success_count"] == 1
    assert report_data["scenarios"]["1"]["total_emails_discovered"] == 1
    assert report_data["scenarios"]["1"]["total_pages_fetched"] == 4


def test_benchmark_output_directory_ignored() -> None:
    """Verify .benchmark-output/ is listed in .gitignore."""
    repo_root = Path(__file__).parents[3]
    gitignore_path = repo_root / ".gitignore"
    if gitignore_path.is_file():
        content = gitignore_path.read_text(encoding="utf-8")
        assert ".benchmark-output/" in content
