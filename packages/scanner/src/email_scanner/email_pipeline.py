"""Deterministic email extraction pipeline module for scanner-core."""

import tldextract

from email_scanner.email_extraction import (
    HTMLEmailExtractor,
    extract_mailto_candidates,
    extract_obfuscated_candidates,
    extract_visible_candidates,
)
from email_scanner.email_validation import validate_email_candidate
from email_scanner.errors import (
    EmailRejectionCode,
    ExtractionOutcomeCode,
    URLNormalizationError,
)
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailDisposition,
    EmailEvidenceRecord,
    EmailExtractionConfig,
    EmailExtractionResult,
    EmailFinding,
    EmailSourceKind,
    NormalizedURL,
    RejectedEmailCandidate,
)
from email_scanner.normalization import normalize_url

_ROLE_LOCAL_PARTS = frozenset(
    {
        "info",
        "sales",
        "support",
        "contact",
        "office",
        "hello",
        "admin",
        "help",
        "billing",
        "jobs",
        "careers",
        "media",
        "press",
        "marketing",
        "privacy",
        "security",
        "legal",
        "webmaster",
        "postmaster",
        "team",
        "staff",
        "enquiries",
        "inquiries",
        "estimating",
        "estimates",
        "quote",
        "quotes",
        "hr",
    }
)

_NO_REPLY_LOCAL_PARTS = frozenset(
    {
        "noreply",
        "no-reply",
        "do-not-reply",
        "donotreply",
        "nobody",
        "bounce",
        "mailer-daemon",
        "null",
    }
)

_SOURCE_PRIORITY: dict[EmailSourceKind, int] = {
    EmailSourceKind.MAILTO: 3,
    EmailSourceKind.VISIBLE_TEXT: 2,
    EmailSourceKind.OBFUSCATED_TEXT: 1,
}


def classify_email_category(local_part: str) -> EmailCategory:
    """Classify email category structurally without inferring personal identity."""
    local_lower = local_part.lower()
    if local_lower in _NO_REPLY_LOCAL_PARTS:
        return EmailCategory.NO_REPLY

    if local_lower in _ROLE_LOCAL_PARTS:
        return EmailCategory.ROLE_BASED

    if any(c.isalpha() for c in local_lower):
        return EmailCategory.PERSONAL_OR_NAMED

    return EmailCategory.UNKNOWN


def calculate_domain_affinity(
    candidate_domain: str,
    source_url: NormalizedURL,
) -> DomainAffinity:
    """Calculate domain affinity relative to source page URL."""
    cand_domain_lower = candidate_domain.lower()
    source_host_lower = source_url.hostname.lower()

    if cand_domain_lower == source_host_lower:
        return DomainAffinity.EXACT_HOST

    cand_extracted = tldextract.extract(cand_domain_lower)
    cand_reg = cand_extracted.top_domain_under_public_suffix or None

    if cand_reg is not None and source_url.registrable_domain is not None:
        if cand_reg.lower() == source_url.registrable_domain.lower():
            return DomainAffinity.SAME_REGISTRABLE_DOMAIN

    return DomainAffinity.EXTERNAL


def extract_emails(
    source_url: str | NormalizedURL,
    html_content: str,
    config: EmailExtractionConfig | None = None,
) -> EmailExtractionResult:
    """Extract, clean, validate, filter, and classify emails from HTML content."""
    cfg = config or EmailExtractionConfig()

    if len(html_content) > cfg.max_html_chars:
        src_url_str = (
            source_url.normalized_url if isinstance(source_url, NormalizedURL) else source_url
        )
        return EmailExtractionResult(
            source_url=src_url_str,
            findings=(),
            rejected_candidates=(),
            outcome=ExtractionOutcomeCode.HTML_TOO_LARGE,
            error_message=(
                f"HTML content length ({len(html_content)}) exceeds maximum "
                f"allowed limit of {cfg.max_html_chars} characters"
            ),
        )

    if isinstance(source_url, str):
        try:
            norm_source = normalize_url(source_url)
        except URLNormalizationError as err:
            return EmailExtractionResult(
                source_url=source_url,
                findings=(),
                rejected_candidates=(),
                outcome=ExtractionOutcomeCode.INVALID_SOURCE_URL,
                error_message=str(err),
            )
    else:
        norm_source = source_url

    # Parse HTML
    parser = HTMLEmailExtractor()
    try:
        parser.feed(html_content)
    except Exception as err:
        return EmailExtractionResult(
            source_url=norm_source.normalized_url,
            findings=(),
            rejected_candidates=(),
            outcome=ExtractionOutcomeCode.PARSING_ERROR,
            error_message=f"HTML parsing error: {err}",
        )

    visible_text_full = " ".join(parser.visible_text_parts)

    # Collect raw candidate tuples: (raw_candidate, source_kind, evidence_snippet)
    # Mailto candidates are processed first to ensure source priority under raw candidate limits
    raw_candidates_list: list[tuple[str, EmailSourceKind, str]] = []

    for raw_href in parser.mailto_hrefs:
        extracted = extract_mailto_candidates(raw_href, cfg.max_evidence_length)
        for raw_cand, snippet in extracted:
            if len(raw_candidates_list) < cfg.max_raw_candidates:
                raw_candidates_list.append((raw_cand, EmailSourceKind.MAILTO, snippet))

    if visible_text_full:
        for raw_cand, kind, snippet in extract_visible_candidates(
            visible_text_full, cfg.max_evidence_length
        ):
            if len(raw_candidates_list) < cfg.max_raw_candidates:
                raw_candidates_list.append((raw_cand, kind, snippet))

        if cfg.allow_obfuscated:
            for reconstructed, _raw_match, kind, snippet in extract_obfuscated_candidates(
                visible_text_full, cfg.max_evidence_length
            ):
                if len(raw_candidates_list) < cfg.max_raw_candidates:
                    raw_candidates_list.append((reconstructed, kind, snippet))

    accepted_map: dict[str, EmailFinding] = {}
    rejected_set: set[tuple[str, str, EmailRejectionCode, str, EmailSourceKind, str]] = set()

    for raw_candidate, source_kind, snippet in raw_candidates_list:
        if "@" not in raw_candidate:
            continue

        local_part, domain = raw_candidate.rsplit("@", 1)

        val_result = validate_email_candidate(
            local_part,
            domain,
            reject_no_reply=cfg.reject_no_reply,
            reject_dummy_test=cfg.reject_dummy_test,
        )

        if len(val_result) == 2:
            rejection_code, reason = val_result
            rejected_set.add(
                (
                    norm_source.normalized_url,
                    raw_candidate,
                    rejection_code,
                    reason,
                    source_kind,
                    snippet,
                )
            )
            continue

        canonical_email, clean_local, idna_domain = val_result

        affinity = calculate_domain_affinity(idna_domain, norm_source)

        if not cfg.allow_external_domains and affinity == DomainAffinity.EXTERNAL:
            rejected_set.add(
                (
                    norm_source.normalized_url,
                    raw_candidate,
                    EmailRejectionCode.EXTERNAL_DOMAIN_REJECTED,
                    f"External domain rejected by policy: {idna_domain}",
                    source_kind,
                    snippet,
                )
            )
            continue

        category = classify_email_category(clean_local)

        ev_rec = EmailEvidenceRecord(
            source_url=norm_source.normalized_url,
            source_kind=source_kind,
            raw_candidate=raw_candidate,
            evidence_snippet=snippet,
            page_score=0,
        )

        if canonical_email not in accepted_map:
            accepted_map[canonical_email] = EmailFinding(
                source_url=norm_source.normalized_url,
                raw_candidate=raw_candidate,
                canonical_email=canonical_email,
                local_part=clean_local,
                domain=idna_domain,
                source_kind=source_kind,
                category=category,
                domain_affinity=affinity,
                evidence_snippet=snippet,
                disposition=EmailDisposition.ACCEPTED,
                evidence_records=(ev_rec,),
            )
        else:
            existing = accepted_map[canonical_email]
            existing_ev = list(existing.evidence_records)
            if not any(
                e.source_url == ev_rec.source_url
                and e.source_kind == ev_rec.source_kind
                and e.evidence_snippet == ev_rec.evidence_snippet
                for e in existing_ev
            ):
                existing_ev.append(ev_rec)

            existing_prio = _SOURCE_PRIORITY[existing.source_kind]
            new_prio = _SOURCE_PRIORITY[source_kind]

            best_source_kind = existing.source_kind
            best_raw_candidate = existing.raw_candidate
            best_snippet = existing.evidence_snippet
            best_source_url = existing.source_url

            if new_prio > existing_prio:
                best_source_kind = source_kind
                best_raw_candidate = raw_candidate
                best_snippet = snippet
                best_source_url = norm_source.normalized_url
            elif new_prio == existing_prio:
                if len(snippet) > len(existing.evidence_snippet):
                    best_source_kind = source_kind
                    best_raw_candidate = raw_candidate
                    best_snippet = snippet
                    best_source_url = norm_source.normalized_url
                elif len(snippet) == len(existing.evidence_snippet):
                    if raw_candidate < existing.raw_candidate:
                        best_source_kind = source_kind
                        best_raw_candidate = raw_candidate
                        best_snippet = snippet
                        best_source_url = norm_source.normalized_url

            accepted_map[canonical_email] = EmailFinding(
                source_url=best_source_url,
                raw_candidate=best_raw_candidate,
                canonical_email=canonical_email,
                local_part=clean_local,
                domain=idna_domain,
                source_kind=best_source_kind,
                category=category,
                domain_affinity=affinity,
                evidence_snippet=best_snippet,
                disposition=EmailDisposition.ACCEPTED,
                evidence_records=tuple(existing_ev),
            )

    # Format findings deterministically sorted by canonical_email
    sorted_findings = tuple(
        finding for _, finding in sorted(accepted_map.items(), key=lambda item: item[0])
    )[: cfg.max_accepted_findings]

    # Format rejected candidates deterministically
    sorted_rejected_tuple = tuple(
        RejectedEmailCandidate(
            source_url=src,
            raw_candidate=raw,
            rejection_code=code,
            reason=reas,
            source_kind=kind,
            evidence_snippet=snip,
            disposition=EmailDisposition.REJECTED,
        )
        for src, raw, code, reas, kind, snip in sorted(
            rejected_set, key=lambda item: (item[1], item[2].value)
        )
    )[: cfg.max_rejected_candidates]

    outcome = (
        ExtractionOutcomeCode.SUCCESS if sorted_findings else ExtractionOutcomeCode.NO_EMAILS_FOUND
    )

    return EmailExtractionResult(
        source_url=norm_source.normalized_url,
        findings=sorted_findings,
        rejected_candidates=sorted_rejected_tuple,
        outcome=outcome,
    )
