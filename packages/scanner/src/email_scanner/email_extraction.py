"""HTML parser and candidate email extractors for scanner-core."""

import html
import html.parser
import re
import urllib.parse

from email_scanner.models import EmailSourceKind

_PLAIN_EMAIL_REGEX = re.compile(r"[^\s@:<>()\",;]+@[^\s@:<>()\",;]+\.[^\s@:<>()\",;]+")

_OBFUSCATED_EMAIL_REGEX = re.compile(
    r"\b([a-zA-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\bat\b)\s*([a-zA-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\bdot\b)\s*([a-zA-Z]{2,})\b",
    re.IGNORECASE,
)

_PUNCTUATION_TO_TRIM = ".,;:!?)]\"'"


class HTMLEmailExtractor(html.parser.HTMLParser):
    """HTML parser extracting visible text and mailto links while ignoring scripts/styles."""

    def __init__(self) -> None:
        super().__init__()
        self.visible_text_parts: list[str] = []
        self.mailto_hrefs: list[str] = []
        self._ignored_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in {"script", "style", "template", "noscript"}:
            self._ignored_stack.append(tag_lower)
            return

        if self._ignored_stack:
            return

        if tag_lower == "a":
            attr_dict = {k.lower(): v for k, v in attrs if v is not None}
            href = attr_dict.get("href")
            if href and href.strip().lower().startswith("mailto:"):
                self.mailto_hrefs.append(href.strip())

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._ignored_stack and self._ignored_stack[-1] == tag_lower:
            self._ignored_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._ignored_stack:
            return
        if data and data.strip():
            self.visible_text_parts.append(data)


def build_evidence_snippet(
    text: str,
    target: str,
    max_length: int = 120,
) -> str:
    """Build a deterministic, bounded evidence snippet around a target match."""
    pos = text.find(target)
    if pos == -1:
        snippet = text[:max_length]
    else:
        margin = max(0, (max_length - len(target)) // 2)
        start = max(0, pos - margin)
        end = min(len(text), pos + len(target) + margin)
        snippet = text[start:end]

    normalized = " ".join(snippet.split())
    if len(normalized) > max_length:
        return normalized[:max_length]
    return normalized


def extract_mailto_candidates(
    raw_href: str,
    max_evidence_length: int = 120,
) -> list[tuple[str, str]]:
    """Extract (raw_candidate, evidence_snippet) tuples from a mailto: href."""
    unescaped = html.unescape(raw_href)
    unquoted = urllib.parse.unquote(unescaped)

    # Remove query string parameters (?subject=..., ?body=...)
    mailto_body = unquoted.split("?", 1)[0]
    # Strip mailto: prefix case-insensitively
    if mailto_body.lower().startswith("mailto:"):
        mailto_body = mailto_body[7:]

    # Split comma and semicolon separated recipients
    recipients = [r.strip() for r in re.split(r"[,;]", mailto_body) if r.strip()]
    results: list[tuple[str, str]] = []

    evidence = f"mailto:{mailto_body}"
    snippet = " ".join(evidence.split())[:max_evidence_length]

    for recipient in recipients:
        cleaned = recipient.strip(_PUNCTUATION_TO_TRIM)
        if cleaned:
            results.append((cleaned, snippet))

    return results


def extract_visible_candidates(
    text: str,
    max_evidence_length: int = 120,
) -> list[tuple[str, EmailSourceKind, str]]:
    """Extract plain email candidates from visible text with bounded snippets."""
    results: list[tuple[str, EmailSourceKind, str]] = []

    for match in _PLAIN_EMAIL_REGEX.finditer(text):
        raw = match.group(0)
        cleaned = raw.strip(_PUNCTUATION_TO_TRIM)
        if cleaned and "@" in cleaned:
            snippet = build_evidence_snippet(text, raw, max_evidence_length)
            results.append((cleaned, EmailSourceKind.VISIBLE_TEXT, snippet))

    return results


def extract_obfuscated_candidates(
    text: str,
    max_evidence_length: int = 120,
) -> list[tuple[str, str, EmailSourceKind, str]]:
    """Extract obfuscated email candidates (reconstructed_raw, match_text, kind, snippet)."""
    results: list[tuple[str, str, EmailSourceKind, str]] = []

    for match in _OBFUSCATED_EMAIL_REGEX.finditer(text):
        match_str = match.group(0)
        local = match.group(1).strip()
        domain_name = match.group(2).strip()
        tld = match.group(3).strip()

        # Reject if local or domain part contains ordinary words that fail syntax
        reconstructed = f"{local}@{domain_name}.{tld}"
        snippet = build_evidence_snippet(text, match_str, max_evidence_length)
        results.append((reconstructed, match_str, EmailSourceKind.OBFUSCATED_TEXT, snippet))

    return results
