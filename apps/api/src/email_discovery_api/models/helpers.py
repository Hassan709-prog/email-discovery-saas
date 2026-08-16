"""Pure deterministic normalization functions for email addresses and organization slugs."""

import re

# Non-alphanumeric character pattern for slug generation
NON_ALPHANUM_PATTERN = re.compile(r"[^a-z0-9]+")
DUPLICATE_HYPHEN_PATTERN = re.compile(r"-+")


def normalize_email(email: str) -> str:
    """Normalize user email by trimming whitespace and converting to lowercase."""
    cleaned = email.strip().lower()
    if not cleaned or "@" not in cleaned:
        raise ValueError("Invalid email address format")
    local, domain = cleaned.rsplit("@", 1)
    if not local or not domain:
        raise ValueError("Invalid email address format")
    return f"{local}@{domain}"


def normalize_org_slug(name_or_slug: str) -> str:
    """Normalize organization name or slug into a clean lowercase hyphen-separated string."""
    cleaned = name_or_slug.strip().lower()
    slug = NON_ALPHANUM_PATTERN.sub("-", cleaned)
    slug = DUPLICATE_HYPHEN_PATTERN.sub("-", slug).strip("-")
    if not slug:
        raise ValueError("Normalized organization slug cannot be empty")
    return slug
