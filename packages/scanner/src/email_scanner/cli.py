"""Experimental command-line interface for email-scanner core."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, cast

import httpx

from email_scanner.errors import SiteScanConfigError, URLNormalizationError
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import SiteScanConfig, SiteScanResult
from email_scanner.orchestration import SiteScanOrchestrator
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
    """Execute scan CLI asynchronously with guaranteed resource cleanup."""
    sys.stderr.write(
        "WARNING: email_scanner CLI is experimental. "
        "Note: DNS pre-checking does not yet provide connection pinning against rebinding.\n"
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

    async with httpx.AsyncClient() as client:
        fetcher = AsyncHTTPFetcher(client=client)
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

    # Determine exit code based on outcome
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


def main(cli_args: list[str] | None = None) -> int:
    """CLI entry point for email_scanner."""
    parser = argparse.ArgumentParser(
        prog="python -m email_scanner",
        description="Experimental single-site email scanner core CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan a website for email addresses")
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

    args = parser.parse_args(cli_args)

    if args.command == "scan":
        exit_code, json_output = asyncio.run(run_scan_cli(args))
        sys.stdout.write(json_output + "\n")
        return exit_code

    return 1


if __name__ == "__main__":
    sys.exit(main())
