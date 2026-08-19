"""Conservative Pre-Scan URL Cleaning and Review Engine (Phase 4D).

Cleans, normalizes, classifies, deduplicates, and reviews raw URL lists pasted by users.
High-confidence platform URLs (search, maps, ad redirects, accounts, support, policies)
are automatically excluded by default while preserving legitimate business targets.

Key Principles:
- Zero socket, DNS, or HTTP network calls during pre-scan cleaning.
- Deterministic exact registrable-domain platform matching (via tldextract).
- Syntactically valid lookalike hostnames enter NEEDS_REVIEW (selected by default).
- Private/reserved literal IP addresses are blocked as INVALID_URL (cannot override).
- Public literal IP addresses enter NEEDS_REVIEW (selected by default).
- Canonical target deduplication consolidates www/apex variants to earliest index.
- Quota is consumed and ScanURL rows created strictly for accepted targets.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlunsplit

from email_scanner.errors import (
    URLNormalizationError,
    URLNormalizationErrorCode,
)
from email_scanner.models import HostType
from email_scanner.normalization import (
    _DOMAIN_EXTRACTOR,  # pyright: ignore[reportPrivateUsage]
    normalize_url,
)

CLEANING_POLICY_VERSION = "1.0.0"

TRACKING_QUERY_PARAMS: set[str] = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "dclid",
    "fbclid",
    "msclkid",
}

OFFICIAL_GOOGLE_DOMAINS: set[str] = {
    "google.com",
    "google.co.uk",
    "google.com.pk",
    "google.ca",
    "google.de",
    "google.fr",
    "google.es",
    "google.it",
    "google.co.jp",
    "google.com.au",
    "google.com.br",
    "google.com.mx",
    "google.com.ar",
    "googleadservices.com",
}

LOOKALIKE_KEYWORD_PREFIXES: tuple[str, ...] = (
    "google.com.",
    "google.co.",
    "google.ca.",
    "google.de.",
    "support.google.",
    "accounts.google.",
    "policies.google.",
    "maps.google.",
)


class URLCleaningDecisionCode(StrEnum):
    """Specific decision codes for pre-scan URL cleaning classification."""

    READY_TO_CHECK = "READY_TO_CHECK"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INVALID_URL = "INVALID_URL"
    UNSUPPORTED_SCHEME = "UNSUPPORTED_SCHEME"

    SEARCH_ENGINE_PAGE = "SEARCH_ENGINE_PAGE"
    MAPS_OR_DIRECTIONS = "MAPS_OR_DIRECTIONS"
    ADVERTISEMENT_OR_TRACKING_REDIRECT = "ADVERTISEMENT_OR_TRACKING_REDIRECT"
    ACCOUNT_OR_AUTH_PAGE = "ACCOUNT_OR_AUTH_PAGE"
    SUPPORT_OR_HELP_PAGE = "SUPPORT_OR_HELP_PAGE"
    PRIVACY_TERMS_OR_POLICY_PAGE = "PRIVACY_TERMS_OR_POLICY_PAGE"
    NON_TARGET_PLATFORM_PAGE = "NON_TARGET_PLATFORM_PAGE"

    DUPLICATE_URL = "DUPLICATE_URL"
    DUPLICATE_DOMAIN = "DUPLICATE_DOMAIN"


def get_ui_label_for_decision(code: URLCleaningDecisionCode) -> str:
    """Return user-facing plain language decision category label for web UI."""
    match code:
        case URLCleaningDecisionCode.READY_TO_CHECK:
            return "Ready to check"
        case URLCleaningDecisionCode.NEEDS_REVIEW:
            return "Review recommended"
        case URLCleaningDecisionCode.DUPLICATE_URL | URLCleaningDecisionCode.DUPLICATE_DOMAIN:
            return "Duplicate website"
        case (
            URLCleaningDecisionCode.SEARCH_ENGINE_PAGE
            | URLCleaningDecisionCode.MAPS_OR_DIRECTIONS
            | URLCleaningDecisionCode.ADVERTISEMENT_OR_TRACKING_REDIRECT
            | URLCleaningDecisionCode.ACCOUNT_OR_AUTH_PAGE
            | URLCleaningDecisionCode.SUPPORT_OR_HELP_PAGE
            | URLCleaningDecisionCode.PRIVACY_TERMS_OR_POLICY_PAGE
            | URLCleaningDecisionCode.NON_TARGET_PLATFORM_PAGE
        ):
            return "Unrelated platform link"
        case _:
            return "Invalid website address"


def get_explanation_for_decision(code: URLCleaningDecisionCode) -> str:
    """Return clear explanation for decision codes."""
    match code:
        case URLCleaningDecisionCode.READY_TO_CHECK:
            return "Valid website ready to scan."
        case URLCleaningDecisionCode.NEEDS_REVIEW:
            return "This address requires review before scanning."
        case URLCleaningDecisionCode.SEARCH_ENGINE_PAGE:
            return "Search engine result pages are excluded."
        case URLCleaningDecisionCode.MAPS_OR_DIRECTIONS:
            return "Map and direction pages are excluded."
        case URLCleaningDecisionCode.ADVERTISEMENT_OR_TRACKING_REDIRECT:
            return "Ad click and tracking redirect links are excluded."
        case URLCleaningDecisionCode.ACCOUNT_OR_AUTH_PAGE:
            return "Account and authentication pages are excluded."
        case URLCleaningDecisionCode.SUPPORT_OR_HELP_PAGE:
            return "Platform support and help documentation pages are excluded."
        case URLCleaningDecisionCode.PRIVACY_TERMS_OR_POLICY_PAGE:
            return "Platform privacy and terms policy pages are excluded."
        case URLCleaningDecisionCode.NON_TARGET_PLATFORM_PAGE:
            return "Platform service landing pages are excluded."
        case URLCleaningDecisionCode.UNSUPPORTED_SCHEME:
            return "Only standard website addresses (HTTP/HTTPS) can be scanned."
        case _:
            return "Invalid website address format."


@dataclass(frozen=True, slots=True)
class URLCleaningItem:
    """Classified result for a single raw URL input."""

    original_index: int
    original_input: str
    normalized_url: str | None
    canonical_target: str | None
    decision_code: URLCleaningDecisionCode
    explanation: str
    duplicate_of_index: int | None = None
    is_selected: bool = False
    user_override_permitted: bool = False
    ui_label: str = ""

    def __post_init__(self) -> None:
        if not self.ui_label:
            object.__setattr__(self, "ui_label", get_ui_label_for_decision(self.decision_code))


@dataclass(frozen=True, slots=True)
class URLCleaningBatchResult:
    """Consolidated summary result of cleaning a batch of URL inputs."""

    items: list[URLCleaningItem]
    total_input_count: int
    ready_to_check_count: int
    needs_review_count: int
    unrelated_platform_count: int
    duplicate_input_count: int
    invalid_input_count: int
    final_target_count: int
    accepted_canonical_targets: list[str]


def _check_ip_address_safety(hostname: str) -> tuple[bool, bool, str | None]:
    """Check if hostname is a literal IP and evaluate safety."""
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return False, True, None

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return (
            True,
            False,
            f"Private, loopback, or reserved IP address [{hostname}] is not allowed.",
        )

    return True, True, None


def _get_registered_domain(hostname: str) -> str:
    """Extract registered domain name under public suffix using tldextract."""
    extracted = _DOMAIN_EXTRACTOR(hostname)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}".lower()
    return ""


def _is_official_google_domain(hostname: str) -> bool:
    """Determine if hostname belongs to an official Google platform domain using tldextract."""
    extracted = _DOMAIN_EXTRACTOR(hostname)
    registered_domain = _get_registered_domain(hostname)
    domain_name = (extracted.domain or "").lower()
    return (
        registered_domain in OFFICIAL_GOOGLE_DOMAINS
        or domain_name == "googleadservices"
        or (domain_name == "google" and bool(extracted.suffix))
    )


def _is_deceptive_platform_lookalike(hostname: str) -> bool:
    """Identify hostnames with platform keyword prefixes that are NOT official."""
    full_host = hostname.lower()
    if _is_official_google_domain(hostname):
        return False
    return any(
        full_host.startswith(prefix) or f".{prefix}" in full_host
        for prefix in LOOKALIKE_KEYWORD_PREFIXES
    )


def _classify_platform_url(
    hostname: str,
    path: str,
) -> URLCleaningDecisionCode | None:
    """Examine safely parsed hostname and path for known platform exclusion rules.

    Uses tldextract to ensure exact registrable domain matching so malicious hostnames
    such as google.com.evil-domain.com are NOT misclassified as Google.
    """
    extracted = _DOMAIN_EXTRACTOR(hostname)
    domain_name = (extracted.domain or "").lower()
    subdomain = (extracted.subdomain or "").lower()
    full_host = hostname.lower()
    path_lower = path.lower()

    if not _is_official_google_domain(hostname):
        return None

    # Exact Google Ad Services domain check
    if (
        domain_name == "googleadservices"
        or full_host == "googleadservices.com"
        or full_host.endswith(".googleadservices.com")
    ):
        return URLCleaningDecisionCode.ADVERTISEMENT_OR_TRACKING_REDIRECT

    # Check subdomains on google.* domains
    if (
        subdomain == "accounts"
        or path_lower.startswith("/accounts/")
        or path_lower in ("/signoutoptions", "/servicelogin")
    ):
        return URLCleaningDecisionCode.ACCOUNT_OR_AUTH_PAGE

    if (
        subdomain == "support"
        or path_lower.startswith("/support/")
        or path_lower.startswith("/help/")
    ):
        return URLCleaningDecisionCode.SUPPORT_OR_HELP_PAGE

    if (
        subdomain == "policies"
        or path_lower.startswith("/privacy")
        or path_lower.startswith("/terms")
        or path_lower.startswith("/policies/")
    ):
        return URLCleaningDecisionCode.PRIVACY_TERMS_OR_POLICY_PAGE

    if (
        subdomain == "maps"
        or path_lower == "/maps"
        or path_lower.startswith("/maps/")
        or path_lower.startswith("/dir/")
    ):
        return URLCleaningDecisionCode.MAPS_OR_DIRECTIONS

    # Check paths on main google.* hostnames
    if (
        path_lower == "/aclk"
        or path_lower.startswith("/aclk/")
        or path_lower == "/pagead/aclk"
        or path_lower.startswith("/pagead/")
    ):
        return URLCleaningDecisionCode.ADVERTISEMENT_OR_TRACKING_REDIRECT

    if (
        path_lower == "/search"
        or path_lower.startswith("/search/")
        or path_lower in ("/webhp", "/imghp", "/shopping")
    ):
        return URLCleaningDecisionCode.SEARCH_ENGINE_PAGE

    if path_lower in ("/", "", "/index.html") or subdomain in ("", "www"):
        return URLCleaningDecisionCode.NON_TARGET_PLATFORM_PAGE

    return None


def _clean_query_params(hostname: str, query_str: str) -> str:
    """Remove documented tracking parameters while retaining functional parameters."""
    if not query_str:
        return ""

    is_google = _is_official_google_domain(hostname)
    qsl = parse_qsl(query_str, keep_blank_values=True)
    filtered: list[tuple[str, str]] = []

    for key, val in qsl:
        k_lower = key.lower()
        if k_lower in TRACKING_QUERY_PARAMS:
            continue
        if is_google and k_lower == "ved":
            continue
        filtered.append((key, val))

    return urlencode(filtered)


def clean_and_review_urls(
    raw_inputs: list[str],
    overrides: dict[int, bool] | None = None,
) -> URLCleaningBatchResult:
    """Clean, normalize, classify, deduplicate, and apply user overrides to raw URL inputs."""
    overrides = overrides or {}
    items: list[URLCleaningItem] = []

    seen_canonical_keys: dict[tuple[str, str, str], tuple[int, str]] = {}

    ready_count = 0
    review_count = 0
    unrelated_count = 0
    duplicate_count = 0
    invalid_count = 0

    for idx, raw_input in enumerate(raw_inputs):
        # Step 1: Syntax & Normalization
        try:
            norm = normalize_url(raw_input)
        except URLNormalizationError as err:
            err_code = err.code.value if hasattr(err, "code") else "INVALID_URL"
            decision_code = (
                URLCleaningDecisionCode.UNSUPPORTED_SCHEME
                if err_code == URLNormalizationErrorCode.UNSUPPORTED_SCHEME.value
                else URLCleaningDecisionCode.INVALID_URL
            )
            explanation = str(err) or get_explanation_for_decision(decision_code)
            items.append(
                URLCleaningItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=None,
                    canonical_target=None,
                    decision_code=decision_code,
                    explanation=explanation,
                    is_selected=False,
                    user_override_permitted=False,
                )
            )
            invalid_count += 1
            continue

        # Step 2: Deterministic IP Safety Check (Private/Reserved IP blocking)
        is_ip, is_safe_ip, ip_err = _check_ip_address_safety(norm.hostname)
        if is_ip and not is_safe_ip:
            items.append(
                URLCleaningItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=norm.normalized_url,
                    canonical_target=None,
                    decision_code=URLCleaningDecisionCode.INVALID_URL,
                    explanation=ip_err or "Private IP addresses are not permitted.",
                    is_selected=False,
                    user_override_permitted=False,
                )
            )
            invalid_count += 1
            continue

        # Step 2b: Validate Domain TLD (single-label hostnames without public suffix are invalid)
        if norm.host_type is HostType.DOMAIN and not norm.registrable_domain:
            items.append(
                URLCleaningItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=norm.normalized_url,
                    canonical_target=None,
                    decision_code=URLCleaningDecisionCode.INVALID_URL,
                    explanation=(
                        "Domain address must include a valid top-level domain (e.g., .com, .org)."
                    ),
                    is_selected=False,
                    user_override_permitted=False,
                )
            )
            invalid_count += 1
            continue

        # Step 3: Platform Exclusion Registry Check
        platform_decision = _classify_platform_url(norm.hostname, norm.path)
        if platform_decision is not None:
            explanation = get_explanation_for_decision(platform_decision)
            is_sel = overrides.get(idx, False)
            items.append(
                URLCleaningItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=norm.normalized_url,
                    canonical_target=norm.normalized_url if is_sel else None,
                    decision_code=platform_decision,
                    explanation=explanation,
                    is_selected=is_sel,
                    user_override_permitted=True,
                )
            )
            unrelated_count += 1
            continue

        # Step 4: Tracking Parameter & Fragment Cleanup
        clean_query = _clean_query_params(norm.hostname, norm.query)
        path = norm.path or "/"

        host_lower = norm.hostname.lower()
        apex_host = host_lower[4:] if host_lower.startswith("www.") else host_lower

        norm_path = path.rstrip("/") if path != "/" else "/"
        target_key = (apex_host, norm_path, clean_query)

        canonical_netloc = f"[{apex_host}]" if norm.host_type is HostType.IPV6 else apex_host
        if norm.port is not None:
            canonical_netloc = f"{canonical_netloc}:{norm.port}"

        canonical_path_query = norm_path
        if clean_query:
            canonical_path_query = f"{canonical_path_query}?{clean_query}"

        canonical_url = urlunsplit(("https", canonical_netloc, canonical_path_query, "", ""))

        # Step 5: Duplicate Consolidation
        if target_key in seen_canonical_keys:
            first_idx, first_canonical_url = seen_canonical_keys[target_key]
            is_sel = overrides.get(idx, False)
            explanation = (
                f"Duplicate website address of item #{first_idx + 1} ({first_canonical_url})."
            )
            items.append(
                URLCleaningItem(
                    original_index=idx,
                    original_input=raw_input,
                    normalized_url=norm.normalized_url,
                    canonical_target=None,  # Always None to prevent duplicate ScanURL creation
                    decision_code=URLCleaningDecisionCode.DUPLICATE_URL,
                    explanation=explanation,
                    duplicate_of_index=first_idx,
                    is_selected=is_sel,
                    user_override_permitted=True,
                )
            )
            duplicate_count += 1
            continue

        # Step 6: Uncertainty & Deceptive Lookalike Classification
        if _is_deceptive_platform_lookalike(norm.hostname):
            default_code = URLCleaningDecisionCode.NEEDS_REVIEW
            default_explanation = (
                "This address is not an official Google domain and "
                "should be reviewed before scanning."
            )
            review_count += 1
        elif is_ip:
            default_code = URLCleaningDecisionCode.NEEDS_REVIEW
            default_explanation = "Public literal IP address requires review before scanning."
            review_count += 1
        else:
            default_code = URLCleaningDecisionCode.READY_TO_CHECK
            default_explanation = get_explanation_for_decision(default_code)
            ready_count += 1

        is_sel = overrides.get(idx, True)
        seen_canonical_keys[target_key] = (idx, canonical_url)

        items.append(
            URLCleaningItem(
                original_index=idx,
                original_input=raw_input,
                normalized_url=norm.normalized_url,
                canonical_target=canonical_url if is_sel else None,
                decision_code=default_code,
                explanation=default_explanation,
                is_selected=is_sel,
                user_override_permitted=True,
            )
        )

    # Compute ordered accepted canonical targets without duplicate entries
    accepted_targets = list(
        dict.fromkeys(
            item.canonical_target for item in items if item.is_selected and item.canonical_target
        )
    )

    return URLCleaningBatchResult(
        items=items,
        total_input_count=len(raw_inputs),
        ready_to_check_count=ready_count,
        needs_review_count=review_count,
        unrelated_platform_count=unrelated_count,
        duplicate_input_count=duplicate_count,
        invalid_input_count=invalid_count,
        final_target_count=len(accepted_targets),
        accepted_canonical_targets=accepted_targets,
    )
