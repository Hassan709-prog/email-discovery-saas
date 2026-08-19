"""Offline-first consistency audit harness for email_scanner core.

Validates exact 100% logical consistency across multiple repeat scan executions
independent of network latency or environment timing variations.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from email_scanner.batch_orchestration import BatchScanOrchestrator
from email_scanner.benchmark_fixtures import (
    OfflineBenchmarkDNSResolver,
    OfflineBenchmarkNetworkBackend,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    BatchScanConfig,
    BatchScanItem,
    BatchScanResult,
    SiteScanConfig,
)
from email_scanner.pinned_transport import PinnedAsyncHTTPTransport
from email_scanner.primary_selection import PRIMARY_EMAIL_SELECTION_VERSION
from email_scanner.request_gate import DomainRequestGate
from email_scanner.robots import RobotsPolicyEvaluator

CONSISTENCY_CHECKSUM_SCHEMA_VERSION = "consistency-audit-v1"


def sanitize_canonical_url(url_str: str | None) -> str | None:
    """Remove userinfo, query, and fragment parameters from URL for audit identity."""
    if not url_str:
        return None
    try:
        parts = urlsplit(url_str)
        # Reconstruct with empty userinfo, query, and fragment
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        clean = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
        return clean
    except Exception:
        return url_str.split("?")[0].split("#")[0]


def compute_evidence_identity(item: BatchScanItem) -> list[dict[str, Any]]:
    """Compute canonical evidence identity list for an item's selected primary email."""
    if not item.result or not item.result.email_findings:
        return []

    ev_identities: list[dict[str, Any]] = []
    for finding in item.result.email_findings:
        canonical_email = finding.canonical_email
        ev_records = finding.evidence_records or ()
        for ev in ev_records:
            clean_url = sanitize_canonical_url(ev.source_url) or ""
            snip_hash = hashlib.sha256(ev.evidence_snippet.encode("utf-8")).hexdigest()[:16]
            ev_identities.append(
                {
                    "canonical_email": canonical_email,
                    "source_url": clean_url,
                    "source_kind": str(ev.source_kind),
                    "snippet_hash": snip_hash,
                }
            )

    ev_identities.sort(
        key=lambda x: (x["canonical_email"], x["source_url"], x["source_kind"], x["snippet_hash"])
    )
    return ev_identities


def compute_item_logical_checksum(item: BatchScanItem) -> str:
    """Compute deterministic SHA-256 logical checksum excluding timing fields."""
    selected_email = None
    selection_ver = None

    if item.result and item.result.email_findings:
        selected_email = item.result.email_findings[0].canonical_email
        selection_ver = PRIMARY_EMAIL_SELECTION_VERSION

    failure_code = None
    if item.result and item.result.diagnostics:
        failure_code = item.result.diagnostics.failure_code

    ev_identity = compute_evidence_identity(item)

    payload = {
        "schema_version": CONSISTENCY_CHECKSUM_SCHEMA_VERSION,
        "original_index": item.original_index,
        "normalized_url": item.normalized_url,
        "outcome": item.outcome.value,
        "failure_code": failure_code,
        "selected_email": selected_email,
        "selection_version": selection_ver,
        "evidence_identity": ev_identity,
    }

    json_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(json_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class WebsiteAuditRepeatRecord:
    """Repeat execution record for one website."""

    repeat_index: int
    outcome: str
    failure_code: str | None
    selected_email: str | None
    selection_version: str | None
    logical_checksum: str


@dataclass(frozen=True, slots=True)
class WebsiteAuditSummary:
    """Audit summary across all repeats for a single website."""

    input_index: int
    normalized_url: str
    is_consistent: bool
    mismatch_reasons: tuple[str, ...]
    repeats: tuple[WebsiteAuditRepeatRecord, ...]


async def run_consistency_audit(args: argparse.Namespace) -> tuple[int, str]:
    """Execute consistency audit across multiple repeat runs."""
    is_live = getattr(args, "live", False)

    if is_live:
        input_file = getattr(args, "input_file", None)
        acknowledged = getattr(args, "acknowledge_live_warning", False)

        if not input_file:
            err_resp = {"error": "Live mode requires an explicit --input-file parameter."}
            return (1, json.dumps(err_resp, indent=2))
        if not acknowledged:
            err_resp = {"error": "Live mode requires explicit --acknowledge-live-warning flag."}
            return (1, json.dumps(err_resp, indent=2))

        sys.stderr.write(
            "WARNING: Running consistency audit in LIVE network mode.\n"
            "Live network audits interact with public websites.\n"
            "Network fluctuations and server rate limits can affect repeat results.\n"
        )
    else:
        sys.stderr.write(
            "Running offline consistency audit using synthetic deterministic fixtures.\n"
        )

    size = int(getattr(args, "size", 100))
    repeats = int(getattr(args, "repeats", 3))
    seed = int(getattr(args, "seed", 42))
    output_dir = Path(getattr(args, "output_dir", ".consistency-audit-output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate synthetic input URLs for offline audit
    if is_live:
        path = Path(args.input_file)
        if not path.is_file():
            return (1, json.dumps({"error": f"Input file not found: {path}"}, indent=2))
        raw_lines = path.read_text(encoding="utf-8").splitlines()
        input_urls = [line.strip() for line in raw_lines if line.strip()][:size]
    else:
        input_urls = [f"http://site-{i}.org" for i in range(1, size + 1)]

    repeat_results: list[BatchScanResult] = []

    for _rep in range(repeats):
        batch_config = BatchScanConfig(
            max_inputs=size,
            global_concurrency=10,
            per_domain_concurrency=1,
            default_minimum_domain_interval_seconds=0.0 if not is_live else 1.0,
            max_elapsed_batch_seconds=600.0,
            site_scan_config=SiteScanConfig(
                max_pages=5,
                minimum_request_interval_seconds=0.0 if not is_live else 1.0,
                max_elapsed_seconds=30.0,
            ),
        )
        gate = DomainRequestGate(default_minimum_interval_seconds=0.0 if not is_live else 1.0)

        if not is_live:
            dns_resolver = OfflineBenchmarkDNSResolver()
            network_backend = OfflineBenchmarkNetworkBackend()
            async with PinnedAsyncHTTPTransport(
                dns_resolver=dns_resolver,
                network_backend=network_backend,
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
                    batch_res = await orchestrator.scan_batch(input_urls, config=batch_config)
                    repeat_results.append(batch_res)
        else:
            async with PinnedAsyncHTTPTransport(
                pinning_config=batch_config.site_scan_config.fetch_config.pinning_config
            ) as transport:
                async with httpx.AsyncClient(
                    transport=transport, follow_redirects=False, trust_env=False
                ) as client:
                    fetcher = AsyncHTTPFetcher(
                        client=client,
                        config=batch_config.site_scan_config.fetch_config,
                        request_gate=gate,
                    )
                    robots = RobotsPolicyEvaluator(fetcher=fetcher)
                    orchestrator = BatchScanOrchestrator(
                        request_gate=gate, fetcher=fetcher, robots_evaluator=robots
                    )
                    batch_res = await orchestrator.scan_batch(input_urls, config=batch_config)
                    repeat_results.append(batch_res)

    # Compare results across repeats
    website_summaries: list[WebsiteAuditSummary] = []
    consistent_count = 0
    mismatched_count = 0

    outcome_dist: dict[str, int] = {}
    email_dist: dict[str, int] = {}
    failure_dist: dict[str, int] = {}

    num_inputs = len(input_urls)
    for idx in range(num_inputs):
        repeat_records: list[WebsiteAuditRepeatRecord] = []
        norm_url = ""

        for r_idx, b_res in enumerate(repeat_results):
            if idx < len(b_res.items):
                item = b_res.items[idx]
                norm_url = item.normalized_url or item.original_input
                outcome_str = item.outcome.value
                outcome_dist[outcome_str] = outcome_dist.get(outcome_str, 0) + 1

                sel_email = None
                sel_ver = None
                if item.result and item.result.email_findings:
                    sel_email = item.result.email_findings[0].canonical_email
                    sel_ver = PRIMARY_EMAIL_SELECTION_VERSION
                    email_dist[sel_email] = email_dist.get(sel_email, 0) + 1

                fail_code = None
                if item.result and item.result.diagnostics:
                    fail_code = item.result.diagnostics.failure_code
                if fail_code:
                    failure_dist[fail_code] = failure_dist.get(fail_code, 0) + 1

                l_checksum = compute_item_logical_checksum(item)

                repeat_records.append(
                    WebsiteAuditRepeatRecord(
                        repeat_index=r_idx + 1,
                        outcome=outcome_str,
                        failure_code=fail_code,
                        selected_email=sel_email,
                        selection_version=sel_ver,
                        logical_checksum=l_checksum,
                    )
                )

        # Evaluate consistency across repeats
        reasons: list[str] = []
        if repeat_records:
            first_rec = repeat_records[0]
            for rec in repeat_records[1:]:
                if rec.outcome != first_rec.outcome:
                    reasons.append(f"Outcome mismatch: {first_rec.outcome} vs {rec.outcome}")
                if rec.failure_code != first_rec.failure_code:
                    reasons.append(
                        f"Failure code mismatch: {first_rec.failure_code} vs {rec.failure_code}"
                    )
                if rec.selected_email != first_rec.selected_email:
                    reasons.append(
                        f"Email mismatch: {first_rec.selected_email} vs {rec.selected_email}"
                    )
                if rec.logical_checksum != first_rec.logical_checksum:
                    reasons.append(
                        f"Checksum mismatch: {first_rec.logical_checksum} vs {rec.logical_checksum}"
                    )

        is_consistent = len(reasons) == 0
        if is_consistent:
            consistent_count += 1
        else:
            mismatched_count += 1

        website_summaries.append(
            WebsiteAuditSummary(
                input_index=idx,
                normalized_url=norm_url,
                is_consistent=is_consistent,
                mismatch_reasons=tuple(reasons),
                repeats=tuple(repeat_records),
            )
        )

    overall_pct = (consistent_count / num_inputs * 100.0) if num_inputs > 0 else 100.0

    report_data = {
        "audit_version": CONSISTENCY_CHECKSUM_SCHEMA_VERSION,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "is_live_mode": is_live,
        "seed": seed,
        "size": size,
        "repeats": repeats,
        "total_websites_audited": num_inputs,
        "consistent_websites_count": consistent_count,
        "mismatched_websites_count": mismatched_count,
        "overall_consistency_percentage": round(overall_pct, 2),
        "outcome_distribution": outcome_dist,
        "selected_email_distribution": email_dist,
        "failure_code_distribution": failure_dist,
        "mismatched_websites": [
            {
                "input_index": s.input_index,
                "normalized_url": s.normalized_url,
                "mismatch_reasons": list(s.mismatch_reasons),
            }
            for s in website_summaries
            if not s.is_consistent
        ],
    }

    report_filename = f"consistency_audit_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    report_path = output_dir / report_filename
    report_path.write_text(json.dumps(report_data, indent=2, sort_keys=True), encoding="utf-8")

    exit_code = 0 if (is_live or overall_pct == 100.0) else 2
    return (exit_code, json.dumps(report_data, indent=2, sort_keys=True))
