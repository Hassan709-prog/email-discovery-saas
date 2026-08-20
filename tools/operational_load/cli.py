"""CLI for bounded deterministic Phase 5C load scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from tools.operational_load.harness import ALLOWED_SIZES, ALLOWED_WORKERS, run_load
from tools.operational_load.models import LoadRunReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic offline production-path load")
    parser.add_argument("--size", type=int, choices=ALLOWED_SIZES, required=True)
    parser.add_argument("--workers", type=int, choices=ALLOWED_WORKERS, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


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
            raise AssertionError("measured repeat checksums differ")
        return {
            "warmup_completed": not args.no_warmup,
            "measured_repeats": args.repeats,
            "runs": [item.model_dump(mode="json") for item in reports],
        }

    payload = json.dumps(asyncio.run(execute()), indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
