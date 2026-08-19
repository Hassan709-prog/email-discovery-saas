"""Deterministic primary-email selection module for scanner-core."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import tldextract

from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailEvidenceRecord,
    EmailFinding,
    EmailSourceKind,
    NormalizedURL,
)

PRIMARY_EMAIL_SELECTION_VERSION = "primary-email-selection-v1"

_FREE_EMAIL_PROVIDERS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "yahoo.ca",
        "hotmail.com",
        "hotmail.co.uk",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "zoho.com",
        "yandex.com",
        "mail.com",
    }
)

_TOP_BUSINESS_CONTACT_ROLES = (
    "contact",
    "info",
    "sales",
    "estimating",
    "estimates",
    "quote",
    "quotes",
    "office",
    "hello",
    "enquiries",
    "inquiries",
    "support",
)

_RECRUITMENT_ROLES = frozenset(
    {
        "jobs",
        "careers",
        "hr",
        "recruitment",
        "employment",
        "work",
    }
)

_UTILITY_LEGAL_ROLES = frozenset(
    {
        "privacy",
        "legal",
        "security",
        "webmaster",
        "postmaster",
        "billing",
        "abuse",
        "admin",
        "help",
        "media",
        "press",
        "marketing",
        "team",
        "staff",
    }
)

_SOURCE_KIND_WEIGHTS: dict[EmailSourceKind, int] = {
    EmailSourceKind.MAILTO: 300,
    EmailSourceKind.VISIBLE_TEXT: 200,
    EmailSourceKind.OBFUSCATED_TEXT: 100,
}


@dataclass(frozen=True, slots=True)
class PrimaryEmailSelectionSignal:
    """Diagnostic signal produced during primary email selection."""

    name: str
    value: str
    score_contribution: int


@dataclass(frozen=True, slots=True)
class PrimaryEmailSelectionResult:
    """Outcome of deterministic primary email selection."""

    selected_finding: EmailFinding | None
    winning_score: int
    active_signals: tuple[PrimaryEmailSelectionSignal, ...]
    version: str = PRIMARY_EMAIL_SELECTION_VERSION


def is_free_email_provider(domain: str) -> bool:
    """Check whether a domain is a known public free email provider."""
    dom_lower = domain.strip().lower()
    if dom_lower in _FREE_EMAIL_PROVIDERS:
        return True
    ext = tldextract.extract(dom_lower)
    if ext.top_domain_under_public_suffix:
        return ext.top_domain_under_public_suffix.lower() in _FREE_EMAIL_PROVIDERS
    return False


def _is_eligible_candidate(finding: EmailFinding) -> bool:
    """Check basic eligibility filter for candidate findings."""
    if not finding.canonical_email or "@" not in finding.canonical_email:
        return False
    if finding.category == EmailCategory.NO_REPLY:
        return False
    local_lower = finding.local_part.lower()
    if local_lower in {
        "noreply",
        "no-reply",
        "do-not-reply",
        "donotreply",
        "nobody",
        "bounce",
        "mailer-daemon",
        "null",
    }:
        return False
    return True


def sort_evidence_records(
    evidence_records: Iterable[EmailEvidenceRecord],
) -> tuple[EmailEvidenceRecord, ...]:
    """Deterministically deduplicate and sort evidence records."""
    seen: set[tuple[str, EmailSourceKind, str]] = set()
    unique_records: list[EmailEvidenceRecord] = []

    for rec in evidence_records:
        key = (rec.source_url.strip(), rec.source_kind, rec.evidence_snippet.strip())
        if key not in seen:
            seen.add(key)
            unique_records.append(rec)

    # Sort priority:
    # 1. source_kind weight descending
    # 2. page_score descending
    # 3. source_url ascending
    # 4. evidence_snippet ascending
    unique_records.sort(
        key=lambda r: (
            -_SOURCE_KIND_WEIGHTS.get(r.source_kind, 0),
            -r.page_score,
            r.source_url,
            r.evidence_snippet,
        )
    )
    return tuple(unique_records)


def select_primary_email(
    findings: Iterable[EmailFinding],
    website_url: str | NormalizedURL,
) -> PrimaryEmailSelectionResult:
    """Select at most one deterministic primary email from accepted candidates for a website.

    Two-Stage Selection Protocol:
      Stage 1: Eligibility & Domain Affinity Tier Grouping
        Tier A: Same-Domain (EXACT_HOST or SAME_REGISTRABLE_DOMAIN)
        Tier B: External Business Domain (EXTERNAL and not free provider)
        Tier C: Free Provider Fallback (EXTERNAL and free provider)
        If Tier A has eligible candidates, Tiers B & C are strictly excluded.
        Else if Tier B has eligible candidates, Tier C is strictly excluded.

      Stage 2: Score evaluation within highest active tier:
        - Business suitability role priority
        - Evidence strength (source kind, page score, evidence count)
        - Final tie-breaker: canonical_email ascending
    """
    eligible = [f for f in findings if _is_eligible_candidate(f)]
    if not eligible:
        return PrimaryEmailSelectionResult(
            selected_finding=None,
            winning_score=0,
            active_signals=(),
            version=PRIMARY_EMAIL_SELECTION_VERSION,
        )

    # Stage 1: Categorize eligible candidates into domain-affinity tiers
    tier_a_same_domain: list[EmailFinding] = []
    tier_b_external_business: list[EmailFinding] = []
    tier_c_free_provider: list[EmailFinding] = []

    for f in eligible:
        if f.domain_affinity in (DomainAffinity.EXACT_HOST, DomainAffinity.SAME_REGISTRABLE_DOMAIN):
            tier_a_same_domain.append(f)
        elif is_free_email_provider(f.domain):
            tier_c_free_provider.append(f)
        else:
            tier_b_external_business.append(f)

    # Apply strict exclusion
    if tier_a_same_domain:
        active_candidates = tier_a_same_domain
        affinity_tier_name = "SAME_DOMAIN"
    elif tier_b_external_business:
        active_candidates = tier_b_external_business
        affinity_tier_name = "EXTERNAL_BUSINESS"
    else:
        active_candidates = tier_c_free_provider
        affinity_tier_name = "FREE_PROVIDER_FALLBACK"

    # Stage 2: Evaluate candidates in active tier
    best_candidate: EmailFinding | None = None
    best_score = -1
    best_signals: tuple[PrimaryEmailSelectionSignal, ...] = ()

    # Group by canonical email so duplicate instances are combined deterministically
    candidate_map: dict[str, list[EmailFinding]] = {}
    for cand in active_candidates:
        candidate_map.setdefault(cand.canonical_email, []).append(cand)

    for _canon, instance_list in sorted(candidate_map.items()):
        first_cand = instance_list[0]
        local_lower = first_cand.local_part.lower()

        # Combine all evidence records across instances
        all_ev_records: list[EmailEvidenceRecord] = []
        for inst in instance_list:
            all_ev_records.extend(inst.evidence_records)

        sorted_ev = sort_evidence_records(all_ev_records)

        signals: list[PrimaryEmailSelectionSignal] = []
        score = 0

        # 1. Domain Tier Signal
        if affinity_tier_name == "SAME_DOMAIN":
            if first_cand.domain_affinity == DomainAffinity.EXACT_HOST:
                signals.append(PrimaryEmailSelectionSignal("domain_affinity", "EXACT_HOST", 15000))
                score += 15000
            else:
                signals.append(
                    PrimaryEmailSelectionSignal("domain_affinity", "SAME_REGISTRABLE_DOMAIN", 10000)
                )
                score += 10000
        elif affinity_tier_name == "EXTERNAL_BUSINESS":
            signals.append(
                PrimaryEmailSelectionSignal("domain_affinity", "EXTERNAL_BUSINESS", 2000)
            )
            score += 2000
        else:
            signals.append(
                PrimaryEmailSelectionSignal("domain_affinity", "FREE_PROVIDER_FALLBACK", 1000)
            )
            score += 1000

        # 2. Business Suitability Signal
        if local_lower in _TOP_BUSINESS_CONTACT_ROLES:
            idx = _TOP_BUSINESS_CONTACT_ROLES.index(local_lower)
            role_bonus = len(_TOP_BUSINESS_CONTACT_ROLES) - idx
            role_score = 5000 + role_bonus
            signals.append(
                PrimaryEmailSelectionSignal(
                    "business_suitability", f"TOP_CONTACT_ROLE:{local_lower}", role_score
                )
            )
            score += role_score
        elif first_cand.category == EmailCategory.PERSONAL_OR_NAMED:
            signals.append(
                PrimaryEmailSelectionSignal(
                    "business_suitability", f"PERSONAL_NAMED:{local_lower}", 3000
                )
            )
            score += 3000
        elif local_lower in _RECRUITMENT_ROLES:
            signals.append(
                PrimaryEmailSelectionSignal(
                    "business_suitability", f"RECRUITMENT_ROLE:{local_lower}", 1000
                )
            )
            score += 1000
        elif local_lower in _UTILITY_LEGAL_ROLES:
            signals.append(
                PrimaryEmailSelectionSignal(
                    "business_suitability", f"UTILITY_LEGAL_ROLE:{local_lower}", 500
                )
            )
            score += 500
        else:
            signals.append(
                PrimaryEmailSelectionSignal(
                    "business_suitability", f"ROLE_BASED:{local_lower}", 800
                )
            )
            score += 800

        # 3. Evidence Strength Signals
        best_source_kind = EmailSourceKind.OBFUSCATED_TEXT
        max_page_score = 0
        for ev in sorted_ev:
            if _SOURCE_KIND_WEIGHTS.get(ev.source_kind, 0) > _SOURCE_KIND_WEIGHTS.get(
                best_source_kind, 0
            ):
                best_source_kind = ev.source_kind
            if ev.page_score > max_page_score:
                max_page_score = ev.page_score

        source_weight = _SOURCE_KIND_WEIGHTS.get(best_source_kind, 100)
        signals.append(
            PrimaryEmailSelectionSignal(
                "evidence_source_kind", best_source_kind.value, source_weight
            )
        )
        score += source_weight

        if max_page_score > 0:
            signals.append(
                PrimaryEmailSelectionSignal("page_score", str(max_page_score), max_page_score)
            )
            score += max_page_score

        ev_count_bonus = min(50, len(sorted_ev) * 10)
        if ev_count_bonus > 0:
            signals.append(
                PrimaryEmailSelectionSignal(
                    "evidence_frequency", str(len(sorted_ev)), ev_count_bonus
                )
            )
            score += ev_count_bonus

        # Sort signals deterministically by name then contribution
        signals.sort(key=lambda s: (s.name, -s.score_contribution))

        # Check winner (highest score wins; if equal, canonical_email ascending tie-breaker)
        if score > best_score:
            best_score = score
            best_signals = tuple(signals)
            # Reconstruct candidate with sorted evidence records
            best_candidate = EmailFinding(
                source_url=sorted_ev[0].source_url if sorted_ev else first_cand.source_url,
                raw_candidate=sorted_ev[0].raw_candidate if sorted_ev else first_cand.raw_candidate,
                canonical_email=first_cand.canonical_email,
                local_part=first_cand.local_part,
                domain=first_cand.domain,
                source_kind=best_source_kind,
                category=first_cand.category,
                domain_affinity=first_cand.domain_affinity,
                evidence_snippet=sorted_ev[0].evidence_snippet
                if sorted_ev
                else first_cand.evidence_snippet,
                disposition=first_cand.disposition,
                evidence_records=sorted_ev,
            )

    return PrimaryEmailSelectionResult(
        selected_finding=best_candidate,
        winning_score=best_score,
        active_signals=best_signals,
        version=PRIMARY_EMAIL_SELECTION_VERSION,
    )
