"""Tests for scanner-core deterministic email extraction and pipeline."""

import pytest

from email_scanner.email_pipeline import extract_emails
from email_scanner.errors import (
    EmailRejectionCode,
    ExtractionConfigError,
    ExtractionConfigErrorCode,
    ExtractionOutcomeCode,
)
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailDisposition,
    EmailExtractionConfig,
    EmailSourceKind,
)


def test_extraction_config_validation() -> None:
    with pytest.raises(ExtractionConfigError) as exc_info:
        EmailExtractionConfig(max_html_chars=0)
    assert exc_info.value.code == ExtractionConfigErrorCode.INVALID_LIMIT

    with pytest.raises(ExtractionConfigError) as exc_info:
        EmailExtractionConfig(max_evidence_length=5)
    assert exc_info.value.code == ExtractionConfigErrorCode.INVALID_LIMIT


def test_visible_and_nested_html_entity_extraction() -> None:
    html = """
    <html>
      <body>
        <p>Contact our support team at: <span>support&#64;acme&#46;com</span></p>
        <div>Direct: alex.smith@acme.com</div>
      </body>
    </html>
    """
    res = extract_emails("https://acme.com/contact", html)

    assert res.outcome == ExtractionOutcomeCode.SUCCESS
    assert len(res.findings) == 2

    canonical_emails = [f.canonical_email for f in res.findings]
    assert "alex.smith@acme.com" in canonical_emails
    assert "support@acme.com" in canonical_emails

    support_finding = next(f for f in res.findings if f.canonical_email == "support@acme.com")
    assert support_finding.category == EmailCategory.ROLE_BASED
    assert support_finding.domain_affinity == DomainAffinity.EXACT_HOST
    assert support_finding.disposition == EmailDisposition.ACCEPTED


def test_mailto_extraction_with_queries_percent_encoding_and_multiple_recipients() -> None:
    html = """
    <html>
      <body>
        <a href="MailTo:user1%40acme.com,user2@acme.com?subject=Hello&body=World">Email Us</a>
      </body>
    </html>
    """
    res = extract_emails("https://acme.com/", html)

    assert res.outcome == ExtractionOutcomeCode.SUCCESS
    assert len(res.findings) == 2

    canonical_emails = [f.canonical_email for f in res.findings]
    assert "user1@acme.com" in canonical_emails
    assert "user2@acme.com" in canonical_emails

    for f in res.findings:
        assert f.source_kind == EmailSourceKind.MAILTO


def test_script_style_template_noscript_ignored() -> None:
    html = """
    <html>
      <head>
        <style> /* fake@acme.com */ </style>
        <script> const email = "script_fake@acme.com"; </script>
      </head>
      <body>
        <template><span>template_fake@acme.com</span></template>
        <noscript>noscript_fake@acme.com</noscript>
        <!-- comment_fake@acme.com -->
        <p>Real: real@acme.com</p>
      </body>
    </html>
    """
    res = extract_emails("https://acme.com/", html)

    assert res.outcome == ExtractionOutcomeCode.SUCCESS
    assert len(res.findings) == 1
    assert res.findings[0].canonical_email == "real@acme.com"


def test_obfuscation_forms_and_negative_prose_test() -> None:
    html = """
    <html>
      <body>
        <p>Email: contact [at] acme [dot] com</p>
        <p>Sales: sales (at) acme (dot) com</p>
        <p>Info: info at acme dot com</p>
        <p>We look at the dot on the screen and talk at length.</p>
      </body>
    </html>
    """
    res = extract_emails("https://acme.com/", html)

    assert res.outcome == ExtractionOutcomeCode.SUCCESS
    canonical_emails = [f.canonical_email for f in res.findings]

    assert "contact@acme.com" in canonical_emails
    assert "sales@acme.com" in canonical_emails
    assert "info@acme.com" in canonical_emails

    # Negative prose test: prose must not create false email findings
    assert not any("screen" in f.canonical_email for f in res.findings)


def test_idna_domain_normalization_and_unicode_local_part_rejection() -> None:
    html = """
    <html>
      <body>
        <p>Valid IDNA: info@münchen.de</p>
        <p>Unicode Local: münchen@acme.com</p>
      </body>
    </html>
    """
    res = extract_emails("https://acme.com/", html)

    canonical_emails = [f.canonical_email for f in res.findings]
    assert "info@xn--mnchen-3ya.de" in canonical_emails

    # Unicode local part must be in rejected candidates
    rejected_raws = [r.raw_candidate for r in res.rejected_candidates]
    assert "münchen@acme.com" in rejected_raws

    rejected_obj = next(r for r in res.rejected_candidates if r.raw_candidate == "münchen@acme.com")
    assert rejected_obj.rejection_code == EmailRejectionCode.INVALID_SYNTAX
    assert rejected_obj.disposition == EmailDisposition.REJECTED


def test_mailto_source_priority_wins_duplicate_evidence() -> None:
    html = """
    <html>
      <body>
        <p>Contact: info@acme.com</p>
        <a href="mailto:info@acme.com">Email Us</a>
      </body>
    </html>
    """
    res = extract_emails("https://acme.com/", html)

    assert len(res.findings) == 1
    finding = res.findings[0]
    assert finding.canonical_email == "info@acme.com"
    # MAILTO source must win over VISIBLE_TEXT
    assert finding.source_kind == EmailSourceKind.MAILTO


def test_domain_affinity_exact_same_registrable_and_external() -> None:
    html = """
    <html>
      <body>
        <p>Exact: user1@sub.acme.com</p>
        <p>Same Registrable: user2@acme.com</p>
        <p>External: user3@external.com</p>
      </body>
    </html>
    """
    res = extract_emails("https://sub.acme.com/", html)

    assert len(res.findings) == 3

    exact = next(f for f in res.findings if f.canonical_email == "user1@sub.acme.com")
    assert exact.domain_affinity == DomainAffinity.EXACT_HOST

    same_reg = next(f for f in res.findings if f.canonical_email == "user2@acme.com")
    assert same_reg.domain_affinity == DomainAffinity.SAME_REGISTRABLE_DOMAIN

    ext = next(f for f in res.findings if f.canonical_email == "user3@external.com")
    assert ext.domain_affinity == DomainAffinity.EXTERNAL


def test_ip_hosts_affinity_never_treats_none_as_matching() -> None:
    html = """
    <html>
      <body>
        <p>Match: user1@192.168.1.1</p>
        <p>Diff IP: user2@192.168.1.2</p>
      </body>
    </html>
    """
    # Source is IP 192.168.1.1
    res = extract_emails("http://192.168.1.1/page", html)

    # IP host emails fail public suffix validation (no TLD) so are rejected with NO_PUBLIC_SUFFIX
    assert len(res.rejected_candidates) >= 2
    assert all(
        r.rejection_code == EmailRejectionCode.NO_PUBLIC_SUFFIX for r in res.rejected_candidates
    )


def test_optional_external_domain_rejection() -> None:
    html = """
    <html>
      <body>
        <p>Internal: contact@acme.com</p>
        <p>External: partner@otherdomain.com</p>
      </body>
    </html>
    """
    config = EmailExtractionConfig(allow_external_domains=False)
    res = extract_emails("https://acme.com/", html, config=config)

    assert len(res.findings) == 1
    assert res.findings[0].canonical_email == "contact@acme.com"

    rejected = [
        r
        for r in res.rejected_candidates
        if r.rejection_code == EmailRejectionCode.EXTERNAL_DOMAIN_REJECTED
    ]
    assert len(rejected) == 1
    assert rejected[0].raw_candidate == "partner@otherdomain.com"


def test_placeholder_domain_rejection_and_actual_domain_accepted() -> None:
    html = """
    <html>
      <body>
        <p>Placeholder: contact@example.com</p>
        <p>Real: sales@domain.com</p>
      </body>
    </html>
    """
    res = extract_emails("https://domain.com/", html)

    # sales@domain.com must be accepted (domain.com is a real domain)
    assert len(res.findings) == 1
    assert res.findings[0].canonical_email == "sales@domain.com"

    # contact@example.com must be in rejected_candidates with PLACEHOLDER_DOMAIN
    rejected_codes = [r.rejection_code for r in res.rejected_candidates]
    assert EmailRejectionCode.PLACEHOLDER_DOMAIN in rejected_codes


def test_html_too_large_outcome() -> None:
    html = "<html>" + ("a" * 500) + "</html>"
    config = EmailExtractionConfig(max_html_chars=100)
    res = extract_emails("https://acme.com/", html, config=config)

    assert res.outcome == ExtractionOutcomeCode.HTML_TOO_LARGE
    assert res.findings == ()
    assert "exceeds maximum allowed limit" in (res.error_message or "")


def test_bounded_evidence_and_repeatability() -> None:
    html = "<p>Reach us at " + ("word " * 50) + "contact@acme.com " + ("more " * 50) + "</p>"
    config = EmailExtractionConfig(max_evidence_length=50)

    res1 = extract_emails("https://acme.com/", html, config=config)
    res2 = extract_emails("https://acme.com/", html, config=config)

    # Repeatability
    assert res1 == res2

    # Evidence snippet length bounded
    assert len(res1.findings[0].evidence_snippet) <= 50
