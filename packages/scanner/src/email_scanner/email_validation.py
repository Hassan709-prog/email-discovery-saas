"""Email address syntax validation, cleaning, and filtering for scanner-core."""

import re

import tldextract

from email_scanner.errors import EmailRejectionCode

_PLACEHOLDER_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.edu",
        "invalid",
        "localhost",
        "test",
        "local",
        "test.com",
        "test.org",
        "test.net",
        "sample.com",
        "yourdomain.com",
        "mysite.com",
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

_DUMMY_TEST_LOCAL_PARTS = frozenset(
    {
        "test",
        "testing",
        "dummy",
        "asdf",
        "qwerty",
        "foo",
        "bar",
    }
)

_ASSET_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".css",
    ".js",
)

_LABEL_REGEX = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


def validate_email_candidate(
    local_part: str,
    domain: str,
    reject_no_reply: bool = True,
    reject_dummy_test: bool = True,
) -> tuple[str, str, str] | tuple[EmailRejectionCode, str]:
    """Validate email components.

    Returns (canonical_email, local_part, idna_domain) if accepted,
    or (EmailRejectionCode, reason_message) if rejected.
    """
    # 1. Unicode local part check
    if not all(ord(c) < 128 for c in local_part):
        return (
            EmailRejectionCode.INVALID_SYNTAX,
            "Unicode local parts are not supported",
        )

    # 2. Local part length check
    if len(local_part) > 64:
        return (
            EmailRejectionCode.LOCAL_PART_TOO_LONG,
            f"Local part length ({len(local_part)}) exceeds 64 characters",
        )

    # 3. Dots syntax check
    if ".." in local_part or ".." in domain:
        return (
            EmailRejectionCode.INVALID_SYNTAX,
            "Email contains consecutive dots",
        )

    if (
        local_part.startswith(".")
        or local_part.endswith(".")
        or domain.startswith(".")
        or domain.endswith(".")
    ):
        return (
            EmailRejectionCode.INVALID_SYNTAX,
            "Email contains leading or trailing dots",
        )

    # 4. IDNA domain normalization
    try:
        idna_domain = domain.encode("idna").decode("ascii").lower()
    except Exception:
        return (
            EmailRejectionCode.INVALID_DOMAIN_LABEL,
            f"Invalid IDNA domain encoding: {domain}",
        )

    canonical_email = f"{local_part.lower()}@{idna_domain}"

    # 5. Total length check
    if len(canonical_email) > 254:
        return (
            EmailRejectionCode.TOTAL_LENGTH_TOO_LONG,
            f"Total email length ({len(canonical_email)}) exceeds 254 characters",
        )

    # 6. Domain label validation
    labels = idna_domain.split(".")
    if len(labels) < 2:
        return (
            EmailRejectionCode.INVALID_DOMAIN_LABEL,
            "Domain must contain at least two labels",
        )

    for label in labels:
        if "_" in label:
            return (
                EmailRejectionCode.INVALID_DOMAIN_LABEL,
                f"Domain label contains underscore: {label}",
            )
        if not _LABEL_REGEX.match(label):
            return (
                EmailRejectionCode.INVALID_DOMAIN_LABEL,
                f"Domain label is invalid: {label}",
            )

    # 7. Public suffix validation via offline tldextract
    extracted = tldextract.extract(idna_domain)
    if not extracted.suffix or not extracted.top_domain_under_public_suffix:
        return (
            EmailRejectionCode.NO_PUBLIC_SUFFIX,
            f"Domain lacks a valid public suffix: {idna_domain}",
        )

    # 8. Placeholder domain check
    if (
        idna_domain in _PLACEHOLDER_DOMAINS
        or idna_domain.endswith(".invalid")
        or idna_domain.endswith(".example")
    ):
        return (
            EmailRejectionCode.PLACEHOLDER_DOMAIN,
            f"Domain is a reserved/placeholder domain: {idna_domain}",
        )

    # 9. Asset filename false-positive check
    local_lower = local_part.lower()
    if any(local_lower.endswith(ext) for ext in _ASSET_EXTENSIONS):
        return (
            EmailRejectionCode.FILE_EXTENSION_LIKE,
            f"Local part appears to be a static asset filename: {local_part}",
        )

    # 10. Configurable no-reply & dummy test local part filters
    if reject_no_reply and local_lower in _NO_REPLY_LOCAL_PARTS:
        return (
            EmailRejectionCode.NO_REPLY_ADDRESS,
            f"No-reply address rejected by policy: {local_part}",
        )

    if reject_dummy_test and local_lower in _DUMMY_TEST_LOCAL_PARTS:
        return (
            EmailRejectionCode.DUMMY_TEST_ADDRESS,
            f"Dummy/test address rejected by policy: {local_part}",
        )

    return (canonical_email, local_part, idna_domain)
