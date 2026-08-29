"""CLI for bounded deterministic Phase 5C load scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tools.operational_load.harness import ALLOWED_SIZES, ALLOWED_WORKERS, run_load
from tools.operational_load.models import LoadRunReport, OperationalLoadError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic offline production-path load")
    parser.add_argument("--size", type=int, choices=ALLOWED_SIZES, required=True)
    parser.add_argument("--workers", type=int, choices=ALLOWED_WORKERS, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def default_output_path(size: int, workers: int, success: bool) -> Path:
    now_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    rand_str = uuid.uuid4().hex[:8]
    status_str = "report" if success else "failure"
    filename = f"load_{status_str}_s{size}_w{workers}_{now_str}_{rand_str}.json"
    return Path(".operational-load-output") / filename


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/email_discovery"
    )
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    async def execute() -> dict[str, object]:
        if not args.no_warmup:
            await run_load(
                size=args.size,
                worker_count=args.workers,
                database_url=database_url,
                redis_url=redis_url,
                timeout_seconds=args.timeout,
            )
        reports: list[LoadRunReport] = []
        for _ in range(args.repeats):
            reports.append(
                await run_load(
                    size=args.size,
                    worker_count=args.workers,
                    database_url=database_url,
                    redis_url=redis_url,
                    timeout_seconds=args.timeout,
                )
            )
        checksum_pairs = {(item.result_checksum, item.csv_checksum) for item in reports}
        if len(checksum_pairs) != 1:
            from tools.operational_load.models import LoadRunFailureReport

            raise OperationalLoadError(
                LoadRunFailureReport(
                    size=args.size,
                    workers=args.workers,
                    error_type="ChecksumMismatch",
                    error_message="Measured repeat runs produced differing checksums",
                    phase="verification",
                    elapsed_seconds=sum(r.elapsed_seconds for r in reports),
                    cleanup_errors=[],
                    partial_report=reports[-1] if reports else None,
                )
            )
        return {
            "status": "SUCCESS",
            "warmup_completed": not args.no_warmup,
            "measured_repeats": args.repeats,
            "runs": [item.model_dump(mode="json") for item in reports],
        }

    out_path = args.output
    try:
        data = asyncio.run(execute())
        payload = json.dumps(data, indent=2, sort_keys=True)
        target_path = (
            out_path
            if out_path is not None
            else default_output_path(args.size, args.workers, success=True)
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0
    except OperationalLoadError as err:
        failure_data = {
            "status": "FAILURE",
            "failure": err.report.model_dump(mode="json"),
        }
        payload = json.dumps(failure_data, indent=2, sort_keys=True)
        target_path = (
            out_path
            if out_path is not None
            else default_output_path(args.size, args.workers, success=False)
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
