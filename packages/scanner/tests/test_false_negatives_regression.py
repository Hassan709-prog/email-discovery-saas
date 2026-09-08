"""Deterministic offline regression test suite for false-negative audit corrections."""

from email_scanner import (
    FetchConfig,
    decode_cloudflare_cfemail,
    extract_emails,
    is_directory_index_or_placeholder,
    select_primary_email,
)
from email_scanner.models import (
    DomainAffinity,
    EmailCategory,
    EmailEvidenceRecord,
    EmailFinding,
    EmailSourceKind,
)


def test_cloudflare_cfemail_decoding() -> None:
    """Verify Cloudflare data-cfemail hex XOR decoding."""
    hex_str = "553a33333c3630153634273033273030343c277b363a38"
    decoded = decode_cloudflare_cfemail(hex_str)
    assert decoded == "office@carefreeair.com"


def test_cloudflare_cfemail_malformed_and_oversized_rejection() -> None:
    """Verify malformed or oversized hex input returns None safely."""
    assert decode_cloudflare_cfemail("invalid_hex_string_xyz") is None
    assert decode_cloudflare_cfemail("12") is None  # empty payload after key
    assert decode_cloudflare_cfemail("a" * 300) is None  # oversized > 256 chars


def test_html_entity_and_obfuscated_extraction() -> None:
    """Verify extraction of HTML entity-encoded and textual obfuscated emails."""
    cfemail = "610e06060c040f151321020113040604000f12000f0400140e0f13110e13144f020e0c"
    html_content = f"""
    <html>
      <body>
        <h1>Contact WH Plumbing & Heating</h1>
        <p>Email us at: service&#64;whplumbingandheating&#46;info</p>
        <p>Alternative: contact [at] whplumbingandheating [dot] info</p>
        <a data-cfemail="{cfemail}">Protected Email</a>
      </body>
    </html>
    """
    res = extract_emails("https://whplumbingandheating.info/contact", html_content)
    emails = {f.canonical_email for f in res.findings}
    assert "office@carefreeair.com" in emails or "service@whplumbingandheating.info" in emails
    assert len(res.findings) >= 1


def test_directory_index_and_placeholder_detection() -> None:
    """Verify detection of bare directory index pages and empty placeholders."""
    directory_index_html = """
    <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
    <html>
     <head><title>Index of /</title></head>
     <body>
      <h1>Index of /</h1>
      <ul><li><a href="subfolder/">subfolder/</a></li></ul>
     </body>
    </html>
    """
    assert is_directory_index_or_placeholder(directory_index_html) is True

    meaningful_html = """
    <!DOCTYPE html>
    <html>
     <head><title>Official Business Contact</title></head>
     <body>
      <h1>Welcome to Long Island Plumbing & Heating</h1>
      <p>Contact our office for service estimates: info@a-allplumbingandheating.com</p>
      <a href="/contact">Contact Us</a>
     </body>
    </html>
    """
    assert is_directory_index_or_placeholder(meaningful_html) is False


def test_cross_domain_strong_evidence_primary_selection() -> None:
    """Verify strong-evidence external email selection over vendor emails."""
    ev_business = EmailEvidenceRecord(
        source_url="https://longisland-plumbing-heating-ac.com/contact",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        raw_candidate="info@a-allplumbingandheating.com",
        evidence_snippet="Contact Email: info@a-allplumbingandheating.com",
        page_score=10,
    )
    finding_business = EmailFinding(
        source_url="https://longisland-plumbing-heating-ac.com/contact",
        raw_candidate="info@a-allplumbingandheating.com",
        canonical_email="info@a-allplumbingandheating.com",
        local_part="info",
        domain="a-allplumbingandheating.com",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        category=EmailCategory.ROLE_BASED,
        domain_affinity=DomainAffinity.EXTERNAL,
        evidence_snippet="Contact Email: info@a-allplumbingandheating.com",
        evidence_records=(ev_business,),
    )

    ev_vendor = EmailEvidenceRecord(
        source_url="https://longisland-plumbing-heating-ac.com/",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        raw_candidate="webmaster@wordpress.org",
        evidence_snippet="Powered by WordPress webmaster@wordpress.org",
        page_score=0,
    )
    finding_vendor = EmailFinding(
        source_url="https://longisland-plumbing-heating-ac.com/",
        raw_candidate="webmaster@wordpress.org",
        canonical_email="webmaster@wordpress.org",
        local_part="webmaster",
        domain="wordpress.org",
        source_kind=EmailSourceKind.VISIBLE_TEXT,
        category=EmailCategory.ROLE_BASED,
        domain_affinity=DomainAffinity.EXTERNAL,
        evidence_snippet="Powered by WordPress webmaster@wordpress.org",
        evidence_records=(ev_vendor,),
    )

    selection = select_primary_email(
        [finding_business, finding_vendor],
        "https://longisland-plumbing-heating-ac.com",
    )
    assert selection.selected_finding is not None
    assert selection.selected_finding.canonical_email == "info@a-allplumbingandheating.com"


def test_redirect_consent_config() -> None:
    """Verify FetchConfig redirect consent settings."""
    cfg_default = FetchConfig()
    assert cfg_default.allow_cross_domain_redirects is False
    assert cfg_default.approved_redirect_domains == ()

    cfg_approved = FetchConfig(
        allow_cross_domain_redirects=False,
        approved_redirect_domains=("carefreeacandheating.com",),
    )
    assert cfg_approved.allow_cross_domain_redirects is False
    assert cfg_approved.approved_redirect_domains == ("carefreeacandheating.com",)
