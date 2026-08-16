"""Experimental command-line interface for email-scanner core."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

import httpx

from email_scanner.batch_orchestration import BatchScanOrchestrator
from email_scanner.errors import (
    BatchScanConfigError,
    SiteScanConfigError,
    URLNormalizationError,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    BatchScanConfig,
    BatchScanResult,
    SiteScanConfig,
    SiteScanResult,
)
from email_scanner.orchestration import SiteScanOrchestrator
from email_scanner.request_gate import DomainRequestGate
from email_scanner.robots import RobotsPolicyEvaluator


def serialize_scan_result(obj: object) -> Any:
    """JSON serializer for dataclasses, StrEnum, and nested scanner values."""
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        dict_val = cast(dict[str, object], asdict(obj))
        return {str(k): serialize_scan_result(v) for k, v in dict_val.items()}
    if isinstance(obj, (tuple, list)):
        seq = cast(tuple[object, ...] | list[object], obj)
        return [serialize_scan_result(v) for v in seq]
    if isinstance(obj, dict):
        mapping = cast(dict[object, object], obj)
        return {str(k): serialize_scan_result(v) for k, v in mapping.items()}
    return obj


async def run_scan_cli(args: argparse.Namespace) -> tuple[int, str]:
    """Execute single scan CLI asynchronously with guaranteed resource cleanup."""
    sys.stderr.write(
        "WARNING: email_scanner CLI is experimental. "
        "All network requests use DNS-pinned transport and bounded rate limiting.\n"
    )

    try:
        config = SiteScanConfig(
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            max_total_discovered_urls=args.max_discovered_urls,
            max_email_findings=args.max_emails,
            minimum_request_interval_seconds=args.min_interval,
            max_elapsed_seconds=args.max_elapsed_seconds,
        )
    except SiteScanConfigError as err:
        error_json = json.dumps(
            {"error": "Invalid configuration", "code": err.code.value, "message": str(err)},
            indent=2,
            sort_keys=True,
        )
        return (1, error_json)

    gate = DomainRequestGate(
        default_minimum_interval_seconds=config.minimum_request_interval_seconds
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        fetcher = AsyncHTTPFetcher(client=client, config=config.fetch_config, request_gate=gate)
        robots = RobotsPolicyEvaluator(fetcher=fetcher)
        orchestrator = SiteScanOrchestrator(fetcher=fetcher, robots_evaluator=robots)

        try:
            result: SiteScanResult = await orchestrator.scan(args.url, config=config)
        except URLNormalizationError as err:
            error_json = json.dumps(
                {"error": "Invalid starting URL", "code": err.code.value, "message": str(err)},
                indent=2,
                sort_keys=True,
            )
            return (1, error_json)
        except Exception as err:
            error_json = json.dumps(
                {"error": "Unexpected scan exception", "message": str(err)},
                indent=2,
                sort_keys=True,
            )
            return (4, error_json)

    serialized = serialize_scan_result(result)
    output_json = json.dumps(serialized, indent=2, sort_keys=True)

    outcome_str = result.outcome.value
    if outcome_str in {"COMPLETED", "COMPLETED_NO_EMAILS"}:
        exit_code = 0
    elif outcome_str in {"ROBOTS_BLOCKED", "FAILED"}:
        exit_code = 2
    elif outcome_str in {"PARTIAL", "CANCELLED"}:
        exit_code = 3
    else:
        exit_code = 4

    return (exit_code, output_json)


async def run_scan_batch_cli(args: argparse.Namespace) -> tuple[int, str]:
    """Execute multi-URL batch scan CLI with bounded line streaming and resource cleanup."""
    sys.stderr.write(
        "WARNING: email_scanner CLI is experimental. "
        "All network requests use DNS-pinned transport and bounded rate limiting.\n"
    )

    try:
        batch_config = BatchScanConfig(
            max_inputs=args.max_inputs,
            global_concurrency=args.global_concurrency,
            per_domain_concurrency=args.per_domain_concurrency,
            default_minimum_domain_interval_seconds=args.min_interval,
            max_elapsed_batch_seconds=args.max_elapsed_seconds,
        )
    except BatchScanConfigError as err:
        error_json = json.dumps(
            {"error": "Invalid batch configuration", "code": err.code.value, "message": str(err)},
            indent=2,
            sort_keys=True,
        )
        return (1, error_json)

    # Stream lines from file or stdin up to max_inputs + 1
    input_lines: list[str] = []

    try:
        if args.input == "-":
            file_stream = sys.stdin
        else:
            path = Path(args.input)
            if not path.is_file():
                error_json = json.dumps(
                    {"error": "Input file not found", "path": str(path)},
                    indent=2,
                    sort_keys=True,
                )
                return (1, error_json)
            file_stream = path.open("r", encoding="utf-8")

        try:
            for raw_line in file_stream:
                if len(raw_line) > 2048:
                    error_json = json.dumps(
                        {"error": "Input line length exceeds 2048 characters"},
                        indent=2,
                        sort_keys=True,
                    )
                    return (1, error_json)

                line = raw_line.strip()
                if line:
                    input_lines.append(line)
                    if len(input_lines) > batch_config.max_inputs:
                        error_json = json.dumps(
                            {
                                "error": "Input lines exceed max_inputs limit",
                                "max_inputs": batch_config.max_inputs,
                            },
                            indent=2,
                            sort_keys=True,
                        )
                        return (1, error_json)
        finally:
            if args.input != "-":
                file_stream.close()

    except Exception as err:
        error_json = json.dumps(
            {"error": "Failed to read batch input source", "message": str(err)},
            indent=2,
            sort_keys=True,
        )
        return (1, error_json)

    gate = DomainRequestGate(
        default_minimum_interval_seconds=batch_config.default_minimum_domain_interval_seconds
    )

    async with httpx.AsyncClient(follow_redirects=False) as client:
        fetcher = AsyncHTTPFetcher(
            client=client,
            config=batch_config.site_scan_config.fetch_config,
            request_gate=gate,
        )
        robots = RobotsPolicyEvaluator(fetcher=fetcher)
        orchestrator = BatchScanOrchestrator(
            request_gate=gate, fetcher=fetcher, robots_evaluator=robots
        )

        try:
            result: BatchScanResult = await orchestrator.scan_batch(
                input_lines, config=batch_config
            )
        except Exception as err:
            error_json = json.dumps(
                {"error": "Unexpected batch scan exception", "message": str(err)},
                indent=2,
                sort_keys=True,
            )
            return (4, error_json)

    serialized = serialize_scan_result(result)
    output_json = json.dumps(serialized, indent=2, sort_keys=True)

    outcome_str = result.outcome.value
    if outcome_str == "COMPLETED":
        exit_code = 0
    elif outcome_str == "FAILED":
        exit_code = 2
    elif outcome_str in {"PARTIAL", "CANCELLED"}:
        exit_code = 3
    else:
        exit_code = 4

    return (exit_code, output_json)


def main(cli_args: list[str] | None = None) -> int:
    """CLI entry point for email_scanner."""
    parser = argparse.ArgumentParser(
        prog="python -m email_scanner",
        description="Experimental email scanner core CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan a single website for email addresses")
    scan_parser.add_argument("url", help="Target starting URL to scan")
    scan_parser.add_argument("--max-pages", type=int, default=10, help="Maximum pages to fetch")
    scan_parser.add_argument("--max-depth", type=int, default=2, help="Maximum crawl depth")
    scan_parser.add_argument(
        "--max-discovered-urls", type=int, default=100, help="Maximum discovered URLs"
    )
    scan_parser.add_argument(
        "--max-emails", type=int, default=50, help="Maximum accepted email findings"
    )
    scan_parser.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="Minimum request interval in seconds",
    )
    scan_parser.add_argument(
        "--max-elapsed-seconds",
        type=float,
        default=60.0,
        help="Maximum total scan time in seconds",
    )

    # scan-batch command
    batch_parser = subparsers.add_parser(
        "scan-batch", help="Scan a batch of URLs from file or stdin"
    )
    batch_parser.add_argument(
        "--input",
        required=True,
        help="Input file path containing URLs (one per line) or '-' for stdin",
    )
    batch_parser.add_argument(
        "--max-inputs", type=int, default=100, help="Maximum total input URLs"
    )
    batch_parser.add_argument(
        "--global-concurrency", type=int, default=5, help="Maximum global concurrent scans"
    )
    batch_parser.add_argument(
        "--per-domain-concurrency", type=int, default=1, help="Maximum per-domain concurrent scans"
    )
    batch_parser.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="Default minimum domain request interval in seconds",
    )
    batch_parser.add_argument(
        "--max-elapsed-seconds",
        type=float,
        default=300.0,
        help="Maximum total batch elapsed time in seconds",
    )

    args = parser.parse_args(cli_args)

    if args.command == "scan":
        exit_code, json_output = asyncio.run(run_scan_cli(args))
        sys.stdout.write(json_output + "\n")
        return exit_code

    if args.command == "scan-batch":
        exit_code, json_output = asyncio.run(run_scan_batch_cli(args))
        sys.stdout.write(json_output + "\n")
        return exit_code

    return 1


if __name__ == "__main__":
    sys.exit(main())
