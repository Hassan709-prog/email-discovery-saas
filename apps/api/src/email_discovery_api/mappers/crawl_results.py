"""Pure deterministic mapping layer between email_scanner result types and persistence DTOs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from email_discovery_api.services.result_policies import ResultPersistencePolicy
from email_scanner.models import (
    SiteScanResult,
)

CONTROL_CHARS_PATTERN = re.compile(r"[\r\n\t\x00-\x1f\x7f-\x9f]+")
MULTIPLE_SPACES_PATTERN = re.compile(r"\s+")


def sanitize_text(text: str | None, max_length: int = 500) -> str | None:
    """Normalize whitespace, remove control characters, and truncate text deterministically."""
    if text is None:
        return None
    cleaned = CONTROL_CHARS_PATTERN.sub(" ", text)
    cleaned = MULTIPLE_SPACES_PATTERN.sub(" ", cleaned).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def sanitize_url(url_str: str | None) -> str:
    """Sanitize URL by removing userinfo, query parameters, and fragments.

    Only scheme, normalized hostname, effective non-default port, and path are preserved.
    """
    if not url_str:
        return ""
    try:
        parts = urlsplit(url_str.strip())
        scheme = parts.scheme.lower()
        hostname = (parts.hostname or "").lower()
        if not hostname:
            return parts.path.strip()

        port = parts.port
        if port is not None:
            if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
                netloc = hostname
            else:
                netloc = f"{hostname}:{port}"
        else:
            netloc = hostname

        path = parts.path or "/"
        return urlunsplit((scheme, netloc, path, "", ""))
    except Exception:
        return sanitize_text(url_str, max_length=2000) or ""


def mask_email_candidate(candidate: str | None) -> str | None:
    """Mask email candidate to protect user privacy (e.g. john.doe@acme.com -> j***e@acme.com)."""
    if not candidate:
        return None
    cleaned = candidate.strip().lower()
    if "@" not in cleaned:
        return "***"
    local, domain = cleaned.rsplit("@", 1)
    if not local:
        return f"***@{domain}"
    if len(local) <= 2:
        masked_local = f"{local[0]}*" if len(local) == 2 else "*"
    elif len(local) == 3:
        masked_local = f"{local[0]}*{local[-1]}"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def compute_candidate_hash(raw_candidate: str, snippet: str | None = None) -> str:
    """Compute deterministic SHA-256 hex digest of a candidate string."""
    normalized = f"{raw_candidate.strip().lower()}:{(snippet or '').strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MappedAttempt:
    """DTO representing mapped CrawlAttempt data."""

    attempt_number: int
    outcome: str
    retryable: bool
    requested_url: str
    final_url: str | None
    status_code: int | None
    error_code: str | None
    error_message: str | None
    redirect_history: list[dict[str, Any]] | None
    connection_attempts: list[dict[str, Any]] | None
    started_at: datetime
    completed_at: datetime | None
    elapsed_seconds: float | None
    result_checksum: str


@dataclass(frozen=True, slots=True)
class MappedPage:
    """DTO representing mapped CrawledPage data."""

    normalized_url: str
    final_url: str
    depth: int
    outcome: str
    status_code: int | None
    content_type: str | None
    content_sha256: str | None
    page_score: int
    ranking_version: str
    robots_decision: str | None
    links_discovered_count: int
    emails_found_count: int
    fetched_at: datetime | None


@dataclass(frozen=True, slots=True)
class MappedFinding:
    """DTO representing mapped EmailFinding data."""

    canonical_email: str
    email_domain: str
    classification: str
    is_role_based: bool
    validation_status: str


@dataclass(frozen=True, slots=True)
class MappedEvidence:
    """DTO representing mapped EmailEvidence data."""

    canonical_email: str
    normalized_page_url: str
    source_type: str
    raw_candidate: str | None
    snippet: str | None
    page_url: str
    confidence: float
    candidate_hash: str


@dataclass(frozen=True, slots=True)
class MappedRejectedCandidate:
    """DTO representing mapped RejectedEmailCandidate data."""

    candidate_hash: str
    masked_candidate: str | None
    rejection_code: str
    source_type: str
    normalized_page_url: str | None

    def __repr__(self) -> str:
        """Custom repr preventing raw candidates from appearing in logs."""
        return (
            f"MappedRejectedCandidate(candidate_hash={self.candidate_hash!r}, "
            f"masked_candidate={self.masked_candidate!r}, rejection_code={self.rejection_code!r})"
        )


def compute_result_checksum(
    starting_url: str,
    outcome: str,
    attempt_number: int,
    mapped_pages: list[MappedPage],
    mapped_findings: list[MappedFinding],
    mapped_evidence: list[MappedEvidence],
    mapped_rejected: list[MappedRejectedCandidate],
) -> str:
    """Compute explicit canonical SHA-256 checksum over mapped scanner execution DTOs."""
    payload = {
        "schema_version": "v1",
        "starting_url": sanitize_url(starting_url),
        "attempt_number": attempt_number,
        "outcome": outcome,
        "pages": [
            {
                "normalized_url": p.normalized_url,
                "final_url": p.final_url,
                "depth": p.depth,
                "outcome": p.outcome,
                "status_code": p.status_code,
                "content_type": p.content_type,
                "content_sha256": p.content_sha256,
                "page_score": p.page_score,
                "ranking_version": p.ranking_version,
                "robots_decision": p.robots_decision,
                "links_discovered_count": p.links_discovered_count,
                "emails_found_count": p.emails_found_count,
            }
            for p in sorted(mapped_pages, key=lambda x: (x.depth, x.normalized_url))
        ],
        "findings": [
            {
                "canonical_email": f.canonical_email,
                "classification": f.classification,
                "is_role_based": f.is_role_based,
            }
            for f in sorted(mapped_findings, key=lambda x: x.canonical_email)
        ],
        "evidence": [
            {
                "canonical_email": e.canonical_email,
                "page_url": e.page_url,
                "source_type": e.source_type,
                "candidate_hash": e.candidate_hash,
                "confidence": e.confidence,
            }
            for e in sorted(
                mapped_evidence, key=lambda x: (x.canonical_email, x.source_type, x.candidate_hash)
            )
        ],
        "rejected": [
            {
                "candidate_hash": r.candidate_hash,
                "rejection_code": r.rejection_code,
                "source_type": r.source_type,
            }
            for r in sorted(mapped_rejected, key=lambda x: (x.candidate_hash, x.rejection_code))
        ],
    }
    json_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(json_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class CrawlAttemptResult:
    """Result container for CrawlAttempt persistence operations."""

    attempt: Any
    is_replay: bool


def compute_transient_attempt_checksum(
    scan_url_id: Any,
    attempt_number: int,
    outcome: str,
    error_code: str,
    retryable: bool,
    requested_url: str,
) -> str:
    """Compute canonical SHA-256 checksum for a transient failure attempt."""
    payload = {
        "schema_version": "v1",
        "scan_url_id": str(scan_url_id),
        "attempt_number": attempt_number,
        "outcome": outcome,
        "error_code": error_code,
        "retryable": retryable,
        "requested_url": sanitize_url(requested_url),
    }
    json_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(json_bytes).hexdigest()


def map_site_scan_result(
    site_scan_result: SiteScanResult,
    attempt_number: int,
    now: datetime,
    policy: ResultPersistencePolicy | None = None,
) -> tuple[
    MappedAttempt,
    list[MappedPage],
    list[MappedFinding],
    list[MappedEvidence],
    list[MappedRejectedCandidate],
]:
    if policy is None:
        from email_discovery_api.services.result_policies import ResultPersistencePolicy

        pol = ResultPersistencePolicy()
    else:
        pol = policy
    requested_url = sanitize_url(site_scan_result.starting_url)

    # 1. Map pages
    mapped_pages: list[MappedPage] = []
    for rec in site_scan_result.page_records[: pol.max_pages_per_result]:
        page_norm_url = sanitize_url(rec.requested_url)
        page_final_url = sanitize_url(rec.final_url) if rec.final_url else page_norm_url
        page_score = 0
        content_type = None
        status_code = rec.status_code

        if rec.fetch_result:
            content_type = sanitize_text(rec.fetch_result.content_type, max_length=100)
            if status_code is None:
                status_code = rec.fetch_result.status_code

        robots_dec = str(rec.robots_decision.decision) if rec.robots_decision else None

        mapped_pages.append(
            MappedPage(
                normalized_url=page_norm_url,
                final_url=page_final_url,
                depth=rec.depth,
                outcome=str(rec.outcome),
                status_code=status_code,
                content_type=content_type,
                content_sha256=None,  # No raw HTML stored
                page_score=page_score,
                ranking_version="v1",
                robots_decision=robots_dec,
                links_discovered_count=rec.links_discovered_count,
                emails_found_count=rec.emails_found_count,
                fetched_at=now if rec.fetch_result else None,
            )
        )

    # Sort pages deterministically by (depth, normalized_url)
    mapped_pages.sort(key=lambda p: (p.depth, p.normalized_url))

    # 2. Map findings and evidence
    mapped_findings_map: dict[str, MappedFinding] = {}
    mapped_evidence_list: list[MappedEvidence] = []

    for f in site_scan_result.email_findings[: pol.max_findings_per_result]:
        canon = f.canonical_email.strip().lower()
        dom = f.domain.strip().lower()
        is_role = f.category == "ROLE_BASED"

        if canon not in mapped_findings_map:
            mapped_findings_map[canon] = MappedFinding(
                canonical_email=canon,
                email_domain=dom,
                classification=str(f.category),
                is_role_based=is_role,
                validation_status="UNVERIFIED",
            )

        snip = sanitize_text(f.evidence_snippet, max_length=pol.max_snippet_length)
        raw_cand = sanitize_text(f.raw_candidate, max_length=255)
        cand_hash = compute_candidate_hash(canon, snip)
        page_url = sanitize_url(f.source_url)

        mapped_evidence_list.append(
            MappedEvidence(
                canonical_email=canon,
                normalized_page_url=page_url,
                source_type=str(f.source_kind),
                raw_candidate=raw_cand,
                snippet=snip,
                page_url=page_url,
                confidence=1.0,
                candidate_hash=cand_hash,
            )
        )

    mapped_findings = sorted(mapped_findings_map.values(), key=lambda f: f.canonical_email)
    mapped_evidence_list.sort(key=lambda e: (e.canonical_email, e.source_type, e.candidate_hash))

    # 3. Map rejected candidates
    mapped_rejected: list[MappedRejectedCandidate] = []
    seen_rejected: set[tuple[str, str]] = set()

    for r in site_scan_result.rejected_email_candidates[: pol.max_rejected_candidates_per_result]:
        raw = r.raw_candidate.strip().lower()
        cand_hash = compute_candidate_hash(raw)
        code = str(r.rejection_code)
        key = (cand_hash, code)

        if key in seen_rejected:
            continue
        seen_rejected.add(key)

        masked = mask_email_candidate(r.raw_candidate)
        page_url = sanitize_url(r.source_url) if r.source_url else None

        mapped_rejected.append(
            MappedRejectedCandidate(
                candidate_hash=cand_hash,
                masked_candidate=masked,
                rejection_code=code,
                source_type=str(r.source_kind),
                normalized_page_url=page_url,
            )
        )

    mapped_rejected.sort(key=lambda r: (r.candidate_hash, r.rejection_code))

    # 4. Map attempt JSONB history
    redirect_history_clean: list[dict[str, Any]] = []
    connection_attempts_clean: list[dict[str, Any]] = []
    first_page_rec = site_scan_result.page_records[0] if site_scan_result.page_records else None
    attempt_status_code = first_page_rec.status_code if first_page_rec else None

    if first_page_rec and first_page_rec.fetch_result:
        for hop in first_page_rec.fetch_result.redirect_history[: pol.max_redirect_hops]:
            redirect_history_clean.append(
                {
                    "url": sanitize_url(hop.url),
                    "status_code": int(hop.status_code),
                    "location": sanitize_url(hop.location),
                }
            )
        for att in first_page_rec.fetch_result.attempts[: pol.max_connection_attempts]:
            for conn in att.connection_attempts[: pol.max_connection_attempts]:
                connection_attempts_clean.append(
                    {
                        "target_host": sanitize_text(conn.target_host, max_length=255),
                        "target_port": int(conn.target_port),
                        "attempted_ip": sanitize_text(conn.attempted_ip, max_length=100),
                        "success": bool(conn.success),
                        "error_message": sanitize_text(conn.error_message, max_length=200),
                    }
                )

    attempt_outcome = str(site_scan_result.outcome)
    retryable = attempt_outcome in ("FAILED", "ROBOTS_BLOCKED")
    err_msg = sanitize_text(site_scan_result.error_message, max_length=pol.max_error_message_length)
    elapsed = site_scan_result.statistics.elapsed_seconds if site_scan_result.statistics else 0.0

    checksum = compute_result_checksum(
        starting_url=requested_url,
        outcome=attempt_outcome,
        attempt_number=attempt_number,
        mapped_pages=mapped_pages,
        mapped_findings=mapped_findings,
        mapped_evidence=mapped_evidence_list,
        mapped_rejected=mapped_rejected,
    )

    final_url_val = (
        sanitize_url(first_page_rec.final_url)
        if first_page_rec and first_page_rec.final_url
        else requested_url
    )

    mapped_attempt = MappedAttempt(
        attempt_number=attempt_number,
        outcome=attempt_outcome,
        retryable=retryable,
        requested_url=requested_url,
        final_url=final_url_val,
        status_code=attempt_status_code,
        error_code=None,
        error_message=err_msg,
        redirect_history=redirect_history_clean if redirect_history_clean else None,
        connection_attempts=connection_attempts_clean if connection_attempts_clean else None,
        started_at=now,
        completed_at=now,
        elapsed_seconds=elapsed,
        result_checksum=checksum,
    )

    return (
        mapped_attempt,
        mapped_pages,
        mapped_findings,
        mapped_evidence_list,
        mapped_rejected,
    )
