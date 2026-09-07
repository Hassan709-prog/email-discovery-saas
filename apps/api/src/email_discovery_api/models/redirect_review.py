"""Neutral policy and helper module for cross-domain redirect reviews.

Centralizes redirect approval constants, pure Python predicates,
SQL expression clauses, and model mutation helpers to ensure zero circular
dependencies across schemas, models, repositories, and services.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.sql.elements import ColumnElement

from email_discovery_api.models.enums import ScanURLStatus
from email_scanner import canonicalize_redirect_domain

REDIRECT_APPROVAL_FAILURE_CODES: tuple[str, ...] = (
    "OUT_OF_SCOPE_REDIRECT",
    "BUSINESS_DOMAIN_REDIRECT_REVIEW",
)

REDIRECT_REJECTED_CODE: str = "REDIRECT_REJECTED"

GENERIC_REDIRECT_REJECTED_MESSAGE: str = "External redirect rejected by user."


def is_url_requiring_redirect_approval(url: Any) -> bool:
    """Pure Python predicate verifying if a ScanURL requires redirect approval.

    Matches exactly the SQL filtering logic and serializer rules:
    - Status must be FAILED
    - approved_redirect_domain must be absent (None or empty/whitespace-only: spaces, tabs, \r, \n)
    - redirect_target_domain must be present and non-empty/non-whitespace
    - Effective failure code (checking last_failure_code first if non-empty,
      falling back to last_error_code) must be in REDIRECT_APPROVAL_FAILURE_CODES.
    """
    st = getattr(url, "status", None)
    if isinstance(st, ScanURLStatus):
        st = st.value
    if st != ScanURLStatus.FAILED.value:
        return False

    app_redirect = getattr(url, "approved_redirect_domain", None)
    if app_redirect is not None and bool(str(app_redirect).strip(" \t\r\n")):
        return False

    target_redirect = getattr(url, "redirect_target_domain", None)
    if target_redirect is None or not bool(str(target_redirect).strip(" \t\r\n")):
        return False

    last_fail = getattr(url, "last_failure_code", None)
    last_err = getattr(url, "last_error_code", None)

    fail_clean = (
        str(last_fail).strip(" \t\r\n")
        if (last_fail is not None and bool(str(last_fail).strip(" \t\r\n")))
        else None
    )
    err_clean = (
        str(last_err).strip(" \t\r\n")
        if (last_err is not None and bool(str(last_err).strip(" \t\r\n")))
        else None
    )
    effective_code = fail_clean or err_clean

    return effective_code in REDIRECT_APPROVAL_FAILURE_CODES


def get_requires_redirect_approval_sql_clause() -> ColumnElement[bool]:
    """Generate SQLAlchemy where-clause matching is_url_requiring_redirect_approval exactly.

    Ensures:
    1. ScanURL.status == FAILED
    2. approved_redirect_domain is NULL or whitespace-only (spaces, tabs, \r, \n)
    3. redirect_target_domain is NOT NULL and non-empty after trim
    4. Effective code (nullif on trimmed last_failure_code with coalesce to
       nullif on trimmed last_error_code) is IN REDIRECT_APPROVAL_FAILURE_CODES.
    """
    from email_discovery_api.models.scan_url import ScanURL

    effective_code = func.coalesce(
        func.nullif(func.trim(ScanURL.last_failure_code, " \t\r\n"), ""),
        func.nullif(func.trim(ScanURL.last_error_code, " \t\r\n"), ""),
    )

    return and_(
        ScanURL.status == ScanURLStatus.FAILED.value,
        or_(
            ScanURL.approved_redirect_domain.is_(None),
            func.trim(ScanURL.approved_redirect_domain, " \t\r\n") == "",
        ),
        ScanURL.redirect_target_domain.isnot(None),
        func.trim(ScanURL.redirect_target_domain, " \t\r\n") != "",
        effective_code.in_(REDIRECT_APPROVAL_FAILURE_CODES),
    )


def apply_url_redirect_approval(url: Any) -> None:
    """Apply atomic field mutations transitioning a ScanURL to QUEUED on approval.

    Preserves target domains, increments max_attempts if attempts exhausted,
    clears transient lease and failure fields.
    """
    target_domain = getattr(url, "redirect_target_domain", None)
    canonical = canonicalize_redirect_domain(target_domain)
    url.approved_redirect_domain = canonical or target_domain
    if canonical:
        url.redirect_target_domain = canonical
    url.status = ScanURLStatus.QUEUED.value

    current_attempts = getattr(url, "attempt_count", 0) or 0
    max_att = getattr(url, "max_attempts", 3) or 3
    if current_attempts >= max_att:
        url.max_attempts = current_attempts + 1

    url.completed_at = None
    url.lease_owner = None
    url.lease_expires_at = None
    url.total_duration_seconds = None
    url.last_error_code = None
    url.last_error_message = None
    url.last_failure_code = None


def apply_url_redirect_rejection(url: Any) -> None:
    """Apply atomic field mutations marking a ScanURL as REDIRECT_REJECTED.

    Preserves target domains, keeps status FAILED, sets error/failure codes,
    and assigns a generic human-readable message without exposing raw URLs.
    """
    url.status = ScanURLStatus.FAILED.value
    url.approved_redirect_domain = None
    url.last_error_code = REDIRECT_REJECTED_CODE
    url.last_failure_code = REDIRECT_REJECTED_CODE
    url.last_error_message = GENERIC_REDIRECT_REJECTED_MESSAGE
