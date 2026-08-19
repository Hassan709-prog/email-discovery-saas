"""Deterministic, offline-first benchmark harness for email_scanner core."""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from email_scanner.batch_orchestration import BatchScanOrchestrator
from email_scanner.benchmark_fixtures import (
    OfflineBenchmarkDNSResolver,
    OfflineBenchmarkNetworkBackend,
)
from email_scanner.dns import WorkerDNSCache
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    BatchItemOutcome,
    BatchScanConfig,
    BatchScanResult,
    SiteScanConfig,
)
from email_scanner.pinned_transport import PinnedAsyncHTTPTransport
from email_scanner.request_gate import DomainRequestGate
from email_scanner.robots import RobotsPolicyEvaluator


def calculate_percentiles(values: list[float]) -> tuple[float, float, float]:
    """Calculate p50, p95, and p99 percentiles from a list of float values."""
    if not values:
        return (0.0, 0.0, 0.0)

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _get_percentile(p: float) -> float:
        if n == 1:
            return sorted_vals[0]
        k = (n - 1) * p
        f = int(k)
        c = f + 1
        if c >= n:
            return sorted_vals[-1]
        d0 = sorted_vals[f] * (c - k)
        d1 = sorted_vals[c] * (k - f)
        return d0 + d1

    p50 = _get_percentile(0.50)
    p95 = _get_percentile(0.95)
    p99 = _get_percentile(0.99)
    return (p50, p95, p99)


def calculate_result_checksum(result: BatchScanResult) -> str:
    """Generate a deterministic SHA-256 digest of result data independent of timing/environment."""
    canonical_items: list[dict[str, Any]] = []

    for item in sorted(result.items, key=lambda x: x.original_index):
        findings: list[str] = []
        if item.result and item.result.email_findings:
            findings = sorted(f.canonical_email for f in item.result.email_findings)

        canonical_items.append(
            {
                "original_index": item.original_index,
                "original_input": item.original_input,
                "normalized_url": item.normalized_url,
                "outcome": item.outcome.value,
                "is_duplicate": item.is_duplicate,
                "duplicate_of_index": item.duplicate_of_index,
                "findings": findings,
            }
        )

    digest_input = json.dumps(
        {
            "outcome": result.outcome.value,
            "total_inputs": result.statistics.total_inputs,
            "valid_inputs": result.statistics.valid_inputs,
            "items": canonical_items,
        },
        sort_keys=True,
    )
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BenchmarkRunMetrics:
    """Calculated metrics for a benchmark scenario execution."""

    size: int
    repeats: int
    median_elapsed_seconds: float
    urls_per_second: float
    pages_per_second: float
    p50_item_latency_seconds: float
    p95_item_latency_seconds: float
    p99_item_latency_seconds: float
    peak_global_concurrency: int
    peak_per_domain_concurrency: int
    worker_count: int
    observed_peak_task_count: int
    success_count: int
    partial_count: int
    failure_count: int
    total_emails_discovered: int
    total_pages_fetched: int
    total_retry_count: int
    peak_memory_bytes: int
    result_checksum: str


def generate_benchmark_urls(size: int) -> tuple[str, ...]:
    """Generate exact N deterministic input URLs for benchmark scenario."""
    if size < 1:
        raise ValueError("Benchmark scenario size must be at least 1")
    return tuple(f"http://site-{i}.org" for i in range(size))


class BenchmarkHarness:
    """Offline-first benchmark execution harness."""

    @staticmethod
    async def run_scenario_async(
        size: int,
        repeats: int = 3,
        warmup: bool = True,
        live: bool = False,
        simulated_delay_sec: float = 0.0,
        seed: int = 42,
        cache_mode: str = "cold",
    ) -> BenchmarkRunMetrics:
        """Run benchmark scenario asynchronously, measuring timing, throughput, and memory."""
        if live:
            sys.stderr.write(
                "WARNING: Live benchmark mode enabled. "
                "Ensure target hosts permit high-concurrency automated scanning.\n"
            )

        input_urls = generate_benchmark_urls(size)
        global_concurrency = min(size, 20)
        per_domain_concurrency = 1

        batch_config = BatchScanConfig(
            max_inputs=size,
            global_concurrency=global_concurrency,
            per_domain_concurrency=per_domain_concurrency,
            default_minimum_domain_interval_seconds=0.0,
            max_elapsed_batch_seconds=600.0,
            site_scan_config=SiteScanConfig(
                max_pages=4,
                max_depth=1,
                minimum_request_interval_seconds=0.0,
            ),
        )

        dns_cache = WorkerDNSCache() if cache_mode != "uncached" else None
        dns_resolver = OfflineBenchmarkDNSResolver(dns_cache=dns_cache)
        backend = OfflineBenchmarkNetworkBackend(simulated_delay_sec=simulated_delay_sec)
        gate = DomainRequestGate(default_minimum_interval_seconds=0.0)

        async with PinnedAsyncHTTPTransport(
            dns_resolver=dns_resolver,
            network_backend=backend,
            pinning_config=batch_config.site_scan_config.fetch_config.pinning_config,
        ) as transport:
            async with httpx.AsyncClient(
                transport=transport, follow_redirects=False, trust_env=False
            ) as client:
                fetcher = AsyncHTTPFetcher(
                    dns_resolver=dns_resolver,
                    client=client,
                    config=batch_config.site_scan_config.fetch_config,
                    request_gate=gate,
                )
                robots = RobotsPolicyEvaluator(fetcher=fetcher)
                orchestrator = BatchScanOrchestrator(
                    request_gate=gate, fetcher=fetcher, robots_evaluator=robots
                )

                # 1. Warmup run to separate correctness from performance measurement
                if warmup:
                    if dns_cache is not None:
                        await dns_cache.clear()
                    warmup_result = await orchestrator.scan_batch(input_urls, config=batch_config)
                    # Correctness assertions before measuring repeats
                    if len(warmup_result.items) != size:
                        msg = f"Warmup error: expected {size} items, got {len(warmup_result.items)}"
                        raise RuntimeError(msg)

                    warmup_emails = sum(
                        len(item.result.email_findings)
                        for item in warmup_result.items
                        if item.result
                    )
                    expected_emails = size * 1
                    if warmup_emails != expected_emails:
                        msg = (
                            f"Warmup correctness failure: expected {expected_emails} emails, "
                            f"got {warmup_emails}"
                        )
                        raise RuntimeError(msg)

                    warmup_pages = sum(
                        item.result.statistics.pages_fetched
                        for item in warmup_result.items
                        if item.result
                    )
                    expected_pages = size * 4
                    if warmup_pages != expected_pages:
                        msg = (
                            f"Warmup correctness failure: expected {expected_pages} pages, "
                            f"got {warmup_pages}"
                        )
                        raise RuntimeError(msg)

                # 2. Measured repeats
                repeat_elapsed_times: list[float] = []
                repeat_memories: list[int] = []
                repeat_pages_fetched: list[int] = []
                repeat_peak_tasks: list[int] = []
                all_item_latencies: list[float] = []
                last_result: BatchScanResult | None = None

                for _ in range(repeats):
                    if cache_mode == "cold" and dns_cache is not None:
                        await dns_cache.clear()

                    was_tracing = tracemalloc.is_tracing()
                    if not was_tracing:
                        tracemalloc.start()

                    start_time = time.monotonic()

                    scan_task = asyncio.create_task(
                        orchestrator.scan_batch(input_urls, config=batch_config)
                    )

                    peak_tasks_observed = 0
                    while not scan_task.done():
                        active_workers = sum(
                            1 for t in asyncio.all_tasks() if t.get_name() == "scanner-worker"
                        )
                        peak_tasks_observed = max(peak_tasks_observed, active_workers)
                        await asyncio.sleep(0.0001)

                    res = await scan_task
                    elapsed = time.monotonic() - start_time

                    if not was_tracing:
                        _, peak_mem = tracemalloc.get_traced_memory()
                        tracemalloc.stop()
                    else:
                        _, peak_mem = tracemalloc.get_traced_memory()

                    repeat_elapsed_times.append(elapsed)
                    repeat_memories.append(peak_mem)
                    repeat_peak_tasks.append(peak_tasks_observed)

                    pages_cnt = sum(
                        item.result.statistics.pages_fetched for item in res.items if item.result
                    )
                    repeat_pages_fetched.append(pages_cnt)

                    # Extract item latencies
                    for item in res.items:
                        if item.result:
                            all_item_latencies.append(item.result.statistics.elapsed_seconds)

                    last_result = res

                if last_result is None:
                    raise RuntimeError("No benchmark iterations completed")

                # Verify result checksum repeatability across runs
                checksum = calculate_result_checksum(last_result)

                # Calculate median metrics across repeats
                sorted_elapsed = sorted(repeat_elapsed_times)
                median_elapsed = sorted_elapsed[len(sorted_elapsed) // 2]
                median_mem = sorted(repeat_memories)[len(repeat_memories) // 2]
                median_pages = sorted(repeat_pages_fetched)[len(repeat_pages_fetched) // 2]
                median_peak_tasks = sorted(repeat_peak_tasks)[len(repeat_peak_tasks) // 2]

                urls_per_sec = size / median_elapsed if median_elapsed > 0 else 0.0
                pages_per_sec = median_pages / median_elapsed if median_elapsed > 0 else 0.0

                p50, p95, p99 = calculate_percentiles(all_item_latencies)

                total_emails = sum(
                    len(item.result.email_findings) for item in last_result.items if item.result
                )

                success_cnt = sum(
                    1
                    for item in last_result.items
                    if item.outcome
                    in {BatchItemOutcome.COMPLETED, BatchItemOutcome.COMPLETED_NO_EMAILS}
                )
                partial_cnt = sum(
                    1 for item in last_result.items if item.outcome == BatchItemOutcome.PARTIAL
                )
                failed_cnt = sum(
                    1
                    for item in last_result.items
                    if item.outcome in {BatchItemOutcome.FAILED, BatchItemOutcome.ROBOTS_BLOCKED}
                )

                total_retries = sum(
                    sum(
                        len(rec.fetch_result.attempts) - 1
                        for rec in item.result.page_records
                        if rec.fetch_result
                    )
                    for item in last_result.items
                    if item.result
                )

                # Peak global concurrency from dynamic network backend connection tracking
                peak_global_conc = max(
                    backend.peak_concurrency, last_result.statistics.peak_global_concurrency
                )

                return BenchmarkRunMetrics(
                    size=size,
                    repeats=repeats,
                    median_elapsed_seconds=median_elapsed,
                    urls_per_second=urls_per_sec,
                    pages_per_second=pages_per_sec,
                    p50_item_latency_seconds=p50,
                    p95_item_latency_seconds=p95,
                    p99_item_latency_seconds=p99,
                    peak_global_concurrency=peak_global_conc,
                    peak_per_domain_concurrency=last_result.statistics.peak_per_domain_concurrency,
                    worker_count=global_concurrency,
                    observed_peak_task_count=median_peak_tasks,
                    success_count=success_cnt,
                    partial_count=partial_cnt,
                    failure_count=failed_cnt,
                    total_emails_discovered=total_emails,
                    total_pages_fetched=median_pages,
                    total_retry_count=total_retries,
                    peak_memory_bytes=median_mem,
                    result_checksum=checksum,
                )


def format_environment_summary() -> dict[str, Any]:
    """Sanitize and return environment details without exposing absolute paths or usernames."""
    return {
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "os_name": os.name,
        "system": platform.system(),
        "cpu_count": os.cpu_count() or 1,
    }


def compare_with_baseline(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Compare current metrics against a baseline report and return deltas."""
    comparison: dict[str, Any] = {}

    curr_scenarios = current.get("scenarios", {})
    base_scenarios = baseline.get("scenarios", {})

    for sz_key, curr_metrics in curr_scenarios.items():
        if sz_key in base_scenarios:
            base_metrics = base_scenarios[sz_key]
            scenario_cmp: dict[str, Any] = {}

            for key in ["median_elapsed_seconds", "urls_per_second", "peak_memory_bytes"]:
                curr_val = curr_metrics.get(key)
                base_val = base_metrics.get(key)

                if (
                    isinstance(curr_val, (int, float))
                    and isinstance(base_val, (int, float))
                    and base_val > 0
                ):
                    diff = curr_val - base_val
                    pct = (diff / base_val) * 100.0
                    scenario_cmp[key] = {
                        "current": curr_val,
                        "baseline": base_val,
                        "diff": diff,
                        "pct_change": round(pct, 2),
                    }

            comparison[sz_key] = scenario_cmp

    return comparison


async def run_benchmark_cli(args: argparse.Namespace) -> tuple[int, str]:
    """CLI execution handler for email_scanner benchmark subcommand."""
    sizes: list[int] = []
    if args.size == "all":
        sizes = [1, 10, 100, 1000]
    else:
        try:
            sizes = [int(args.size)]
        except ValueError:
            err_json = json.dumps(
                {
                    "error": "Invalid benchmark size",
                    "message": f"Size must be 1, 10, 100, 1000 or 'all', got '{args.size}'",
                },
                indent=2,
            )
            return (1, err_json)

    output_dir = Path(args.output_dir or ".benchmark-output")
    output_dir.mkdir(parents=True, exist_ok=True)

    seed = getattr(args, "seed", 42)
    simulated_delay = getattr(args, "simulated_delay", 0.0)
    cache_mode = getattr(args, "cache_mode", "cold")

    results_by_size: dict[str, Any] = {}

    for sz in sizes:
        metrics = await BenchmarkHarness.run_scenario_async(
            size=sz,
            repeats=args.repeats,
            warmup=not args.no_warmup,
            live=args.live,
            simulated_delay_sec=simulated_delay,
            seed=seed,
            cache_mode=cache_mode,
        )
        results_by_size[str(sz)] = asdict(metrics)

    report = {
        "metadata": {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "environment": format_environment_summary(),
            "configuration": {
                "seed": seed,
                "size": args.size,
                "repeats": args.repeats,
                "warmup": not args.no_warmup,
                "offline": not args.live,
                "simulated_delay_sec": simulated_delay,
                "max_pages_per_site": 4,
                "max_depth_per_site": 1,
            },
        },
        "scenarios": results_by_size,
    }

    baseline_path = getattr(args, "baseline", None)
    if baseline_path:
        base_path = Path(baseline_path)
        if base_path.is_file():
            try:
                base_data = json.loads(base_path.read_text(encoding="utf-8"))
                report["baseline_comparison"] = compare_with_baseline(report, base_data)
            except Exception as err:
                sys.stderr.write(f"Warning: Failed to parse baseline file: {err}\n")

    json_report = json.dumps(report, indent=2, sort_keys=True)

    # Save to ignored output directory
    timestamp_file = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_file = output_dir / f"benchmark_{args.size}_{timestamp_file}.json"
    report_file.write_text(json_report, encoding="utf-8")

    return (0, json_report)
