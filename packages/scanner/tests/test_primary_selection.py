"""Unit tests for deterministic primary-email selection in scanner-core."""

import itertools

from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailDisposition,
    EmailEvidenceRecord,
    EmailFinding,
    EmailSourceKind,
)
from email_scanner.normalization import normalize_url
from email_scanner.primary_selection import (
    PRIMARY_EMAIL_SELECTION_VERSION,
    select_primary_email,
)


def _make_finding(
    raw_email: str,
    source_url: str = "https://tameernyc.com",
    source_kind: EmailSourceKind = EmailSourceKind.VISIBLE_TEXT,
    domain_affinity: DomainAffinity = DomainAffinity.SAME_REGISTRABLE_DOMAIN,
    snippet: str = "Contact us at example email",
    category: EmailCategory | None = None,
    page_score: int = 10,
) -> EmailFinding:
    local_part, domain = raw_email.lower().split("@", 1)
    if category is None:
        if local_part in {"noreply", "no-reply", "do-not-reply"}:
            category = EmailCategory.NO_REPLY
        elif local_part in {
            "info",
            "sales",
            "support",
            "contact",
            "office",
            "hello",
            "estimating",
            "estimates",
            "quote",
            "quotes",
            "jobs",
            "careers",
            "hr",
        }:
            category = EmailCategory.ROLE_BASED
        else:
            category = EmailCategory.PERSONAL_OR_NAMED

    ev_rec = EmailEvidenceRecord(
        source_url=source_url,
        source_kind=source_kind,
        raw_candidate=raw_email,
        evidence_snippet=snippet,
        page_score=page_score,
    )
    return EmailFinding(
        source_url=source_url,
        raw_candidate=raw_email,
        canonical_email=raw_email.lower(),
        local_part=local_part,
        domain=domain,
        source_kind=source_kind,
        category=category,
        domain_affinity=domain_affinity,
        evidence_snippet=snippet,
        disposition=EmailDisposition.ACCEPTED,
        evidence_records=(ev_rec,),
    )


def test_version_constant() -> None:
    assert PRIMARY_EMAIL_SELECTION_VERSION == "primary-email-selection-v1"


def test_zero_candidates_returns_none() -> None:
    res = select_primary_email([], "https://example.com")
    assert res.selected_finding is None
    assert res.winning_score == 0
    assert res.version == PRIMARY_EMAIL_SELECTION_VERSION


def test_single_suitable_email_selected() -> None:
    finding = _make_finding("contact@example.com", "https://example.com")
    res = select_primary_email([finding], "https://example.com")
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "contact@example.com"


def test_conflict_contact_info_named_jobs_noreply() -> None:
    site_url = "https://example.com"
    f_contact = _make_finding("contact@example.com", site_url)
    f_info = _make_finding("info@example.com", site_url)
    f_named = _make_finding("john.doe@example.com", site_url)
    f_jobs = _make_finding("jobs@example.com", site_url)
    f_noreply = _make_finding("noreply@example.com", site_url)

    candidates = [f_jobs, f_noreply, f_named, f_info, f_contact]
    res = select_primary_email(candidates, site_url)

    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "contact@example.com"


def test_same_domain_beats_gmail_fallback() -> None:
    site_url = "https://acme.com"
    f_same = _make_finding(
        "john@acme.com", site_url, domain_affinity=DomainAffinity.SAME_REGISTRABLE_DOMAIN
    )
    f_gmail = _make_finding(
        "contact.acme@gmail.com", site_url, domain_affinity=DomainAffinity.EXTERNAL
    )

    res = select_primary_email([f_gmail, f_same], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "john@acme.com"


def test_free_provider_selected_only_when_no_same_domain() -> None:
    site_url = "https://acme.com"
    f_gmail = _make_finding("acmeinc@gmail.com", site_url, domain_affinity=DomainAffinity.EXTERNAL)

    res = select_primary_email([f_gmail], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "acmeinc@gmail.com"


def test_suitable_role_address_beats_named_personal() -> None:
    site_url = "https://company.com"
    f_info = _make_finding("info@company.com", site_url)
    f_named = _make_finding("sarah.smith@company.com", site_url)

    res = select_primary_email([f_named, f_info], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "info@company.com"


def test_named_personal_address_allowed_when_no_top_role() -> None:
    site_url = "https://company.com"
    f_named = _make_finding("sarah.smith@company.com", site_url)
    f_jobs = _make_finding("jobs@company.com", site_url)

    res = select_primary_email([f_jobs, f_named], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "sarah.smith@company.com"


def test_noreply_candidate_never_selected() -> None:
    site_url = "https://company.com"
    f_noreply1 = _make_finding("noreply@company.com", site_url)
    f_noreply2 = _make_finding("no-reply@company.com", site_url)
    f_noreply3 = _make_finding("donotreply@company.com", site_url)

    res = select_primary_email([f_noreply1, f_noreply2, f_noreply3], site_url)
    assert res.selected_finding is None


def test_mailto_vs_visible_text_priority() -> None:
    site_url = "https://company.com"
    f_visible = _make_finding(
        "sales@company.com", site_url, source_kind=EmailSourceKind.VISIBLE_TEXT
    )
    f_mailto = _make_finding("sales@company.com", site_url, source_kind=EmailSourceKind.MAILTO)

    # Combining findings for same email should pick MAILTO as source kind
    res = select_primary_email([f_visible, f_mailto], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.source_kind == EmailSourceKind.MAILTO


def test_important_page_ranking_affects_selection() -> None:
    site_url = "https://company.com"
    f_subpage = _make_finding("sales@company.com", site_url, page_score=10)
    f_homepage = _make_finding("office@company.com", site_url, page_score=500)

    # office@ has +500 page score vs sales@ +10
    res = select_primary_email([f_subpage, f_homepage], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "office@company.com"


def test_candidate_permutations_produce_same_winner_and_signals() -> None:
    site_url = "https://tameernyc.com"
    f_est = _make_finding("estimating@tameernyc.com", site_url)
    f_hr = _make_finding("hr@tameernyc.com", site_url)
    f_jobs = _make_finding("jobs@tameernyc.com", site_url)

    cands = [f_est, f_hr, f_jobs]
    perms = list(itertools.permutations(cands))

    from typing import Any

    winners: list[str] = []
    signals_list: list[Any] = []
    scores: list[int] = []

    for perm in perms:
        res = select_primary_email(perm, site_url)
        assert res.selected_finding is not None
        winners.append(res.selected_finding.canonical_email)
        signals_list.append(res.active_signals)
        scores.append(res.winning_score)

    assert len(set(winners)) == 1
    assert winners[0] == "estimating@tameernyc.com"
    assert len(set(scores)) == 1
    assert len(set(signals_list)) == 1


def test_tameernyc_regression_fixture() -> None:
    """Tameernyc.com regression fixture test proving estimating@tameernyc.com wins."""
    site_url = "https://tameernyc.com"
    f_est = _make_finding("estimating@tameernyc.com", site_url)
    f_hr = _make_finding("hr@tameernyc.com", site_url)
    f_jobs = _make_finding("jobs@tameernyc.com", site_url)

    res = select_primary_email([f_jobs, f_hr, f_est], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "estimating@tameernyc.com"
    # Document reason: estimating@ is a top business contact role
    # while hr@ and jobs@ are lower-priority recruitment roles.


def test_subdomain_exact_host_affinity() -> None:
    norm_url = normalize_url("https://sub.company.com/page")
    f_exact = _make_finding(
        "contact@sub.company.com",
        norm_url.normalized_url,
        domain_affinity=DomainAffinity.EXACT_HOST,
    )
    f_reg = _make_finding(
        "contact@company.com",
        norm_url.normalized_url,
        domain_affinity=DomainAffinity.SAME_REGISTRABLE_DOMAIN,
    )

    res = select_primary_email([f_reg, f_exact], norm_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email == "contact@sub.company.com"


def test_bounded_placeholder_does_not_reject_legitimate_substrings() -> None:
    site_url = "https://company.com"
    f_foster = _make_finding("foster@company.com", site_url)
    f_attest = _make_finding("attest@company.com", site_url)
    f_barbara = _make_finding("barbara@company.com", site_url)

    res = select_primary_email([f_foster, f_attest, f_barbara], site_url)
    assert res.selected_finding is not None
    assert res.selected_finding.canonical_email in {
        "foster@company.com",
        "attest@company.com",
        "barbara@company.com",
    }


def test_weak_external_candidate_filtered_out_when_same_domain_or_strong_external_exists() -> None:
    """Verify weak external business candidates are excluded from competition when same-domain

    or strong external candidates exist.
    """
    site_url = "https://mycompany.com"

    f_same_domain = _make_finding(
        "info@mycompany.com",
        site_url,
        domain_affinity=DomainAffinity.SAME_REGISTRABLE_DOMAIN,
    )

    f_strong_external = _make_finding(
        "contact@partneragency.com",
        "https://mycompany.com/contact",
        source_kind=EmailSourceKind.MAILTO,
        domain_affinity=DomainAffinity.EXTERNAL,
        snippet="Mailto contact partner",
    )

    # Weak external candidate: no mailto, no contact page snippet, no page score
    weak_ev = EmailEvidenceRecord(
        source_url="https://mycompany.com/privacy",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        raw_candidate="contact@unrelated-vendor.com",
        evidence_snippet="unrelated footer text",
        page_score=0,
    )
    f_weak_external = EmailFinding(
        source_url="https://mycompany.com/privacy",
        raw_candidate="contact@unrelated-vendor.com",
        canonical_email="contact@unrelated-vendor.com",
        local_part="contact",
        domain="unrelated-vendor.com",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        category=EmailCategory.ROLE_BASED,
        domain_affinity=DomainAffinity.EXTERNAL,
        evidence_snippet="unrelated footer text",
        disposition=EmailDisposition.ACCEPTED,
        evidence_records=(weak_ev,),
    )

    # 1. Test with all 3 candidates: prove weak_external never wins and never alters winner/signals
    res_baseline = select_primary_email([f_same_domain, f_strong_external], site_url)
    assert res_baseline.selected_finding is not None
    assert res_baseline.selected_finding.canonical_email != "contact@unrelated-vendor.com"

    for perm in itertools.permutations([f_same_domain, f_strong_external, f_weak_external]):
        res = select_primary_email(perm, site_url)
        assert res.selected_finding is not None
        assert res.selected_finding.canonical_email != "contact@unrelated-vendor.com"
        assert res.selected_finding.canonical_email == res_baseline.selected_finding.canonical_email
        assert res.winning_score == res_baseline.winning_score
        assert res.active_signals == res_baseline.active_signals

    # 2. Test without same-domain candidate: strong external MUST beat weak external
    for perm in itertools.permutations([f_strong_external, f_weak_external]):
        res = select_primary_email(perm, site_url)
        assert res.selected_finding is not None
        assert res.selected_finding.canonical_email == "contact@partneragency.com"


def test_external_only_fallback_behavior() -> None:
    """Verify that when no same-domain or strong-external candidates exist, weak external candidates

    are evaluated as fallback.
    """
    site_url = "https://mycompany.com"

    weak_ev1 = EmailEvidenceRecord(
        source_url="https://mycompany.com/footer",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        raw_candidate="contact@agency.com",
        evidence_snippet="agency link",
        page_score=0,
    )
    f_weak1 = EmailFinding(
        source_url="https://mycompany.com/footer",
        raw_candidate="contact@agency.com",
        canonical_email="contact@agency.com",
        local_part="contact",
        domain="agency.com",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        category=EmailCategory.ROLE_BASED,
        domain_affinity=DomainAffinity.EXTERNAL,
        evidence_snippet="agency link",
        disposition=EmailDisposition.ACCEPTED,
        evidence_records=(weak_ev1,),
    )

    weak_ev2 = EmailEvidenceRecord(
        source_url="https://mycompany.com/footer",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        raw_candidate="support@vendor.com",
        evidence_snippet="vendor link",
        page_score=0,
    )
    f_weak2 = EmailFinding(
        source_url="https://mycompany.com/footer",
        raw_candidate="support@vendor.com",
        canonical_email="support@vendor.com",
        local_part="support",
        domain="vendor.com",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        category=EmailCategory.ROLE_BASED,
        domain_affinity=DomainAffinity.EXTERNAL,
        evidence_snippet="vendor link",
        disposition=EmailDisposition.ACCEPTED,
        evidence_records=(weak_ev2,),
    )

    res = select_primary_email([f_weak2, f_weak1], site_url)
    assert res.selected_finding is not None
    # contact@ is a higher-ranked contact role than support@
    assert res.selected_finding.canonical_email == "contact@agency.com"
