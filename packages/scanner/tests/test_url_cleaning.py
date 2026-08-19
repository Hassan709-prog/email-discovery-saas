"""Tests for Phase 4D Conservative Pre-Scan URL Cleaning and Review."""

import socket
from unittest.mock import patch

from email_scanner import (
    URLCleaningDecisionCode,
    clean_and_review_urls,
)

COMPLETE_NOISY_FIXTURE: list[str] = [
    "https://grandeurhillsgroup.com/",
    "https://www.google.com/search?q=home+builders+in+new+york",
    "https://www.google.com.pk/search?q=home+builders",
    "https://www.google.co.uk/search?q=home+builders",
    "https://www.archi-builders.com/",
    "https://archi-builders.com/",
    "https://taconicbuilders.com/",
    "https://nybuilt.com/",
    "https://desimonebuilders.com/",
    "https://www.myhomeus.com/?utm_source=gmb&utm_medium=search",
    "https://www.google.com/maps/dir//Example",
    "https://www.google.com/aclk?gclid=example",
    "https://accounts.google.com/SignOutOptions",
    "https://support.google.com/websearch/answer/181196",
    "https://policies.google.com/privacy",
    "https://policies.google.com/terms",
    "http://93.184.216.34/",
    "http://127.0.0.1/",
    "not-a-valid-url-format",
    "https://google.com.evil-domain.com/search",
    "https://support.google.com.evil-domain.org/help",
    "https://example-builder.com/search",
    "https://example-builder.com/support",
    "https://example-builder.com/account",
    "https://example-builder.com/privacy",
    "https://example-builder.com/terms",
    "https://example-builder.com/help",
]


def test_no_network_assertion_during_cleaning():
    """Verify zero socket or network calls occur during preview URL cleaning."""
    with (
        patch.object(
            socket, "getaddrinfo", side_effect=AssertionError("DNS call attempted during preview!")
        ),
        patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("Socket connection attempted during preview!"),
        ),
    ):
        result = clean_and_review_urls(COMPLETE_NOISY_FIXTURE)
        assert result.total_input_count == len(COMPLETE_NOISY_FIXTURE)


def test_complete_noisy_fixture_breakdown():
    """Run full deterministic noisy input fixture and assert exact breakdown metrics."""
    res = clean_and_review_urls(COMPLETE_NOISY_FIXTURE)

    assert res.total_input_count == 27
    assert res.ready_to_check_count == 12
    assert res.needs_review_count == 3
    assert res.unrelated_platform_count == 9
    assert res.duplicate_input_count == 1
    assert res.invalid_input_count == 2
    assert res.final_target_count == 15

    assert len(res.accepted_canonical_targets) == 15
    assert res.accepted_canonical_targets == [
        "https://grandeurhillsgroup.com/",
        "https://archi-builders.com/",
        "https://taconicbuilders.com/",
        "https://nybuilt.com/",
        "https://desimonebuilders.com/",
        "https://myhomeus.com/",
        "https://93.184.216.34/",
        "https://google.com.evil-domain.com/search",
        "https://support.google.com.evil-domain.org/help",
        "https://example-builder.com/search",
        "https://example-builder.com/support",
        "https://example-builder.com/account",
        "https://example-builder.com/privacy",
        "https://example-builder.com/terms",
        "https://example-builder.com/help",
    ]


def test_deceptive_platform_lookalike_classification():
    """Verify lookalike hostnames enter NEEDS_REVIEW with explicit explanation."""
    inputs = [
        "https://google.com.evil-domain.com/search",
        "https://support.google.com.evil-domain.org/help",
        "https://notgoogle.com/search",
    ]
    res = clean_and_review_urls(inputs)

    assert res.needs_review_count == 2
    assert res.ready_to_check_count == 1

    item1 = res.items[0]
    assert item1.decision_code == URLCleaningDecisionCode.NEEDS_REVIEW
    assert item1.is_selected is True
    assert (
        "This address is not an official Google domain and should be reviewed before scanning."
        in item1.explanation
    )

    item2 = res.items[1]
    assert item2.decision_code == URLCleaningDecisionCode.NEEDS_REVIEW
    assert item2.is_selected is True

    item3 = res.items[2]
    assert item3.decision_code == URLCleaningDecisionCode.READY_TO_CHECK
    assert item3.is_selected is True


def test_duplicate_override_safety():
    """Verify overriding a duplicate item never produces duplicate canonical targets."""
    inputs = [
        "https://www.archi-builders.com/",
        "https://archi-builders.com/",
    ]
    overrides = {1: True}
    res = clean_and_review_urls(inputs, overrides)

    assert res.items[1].decision_code == URLCleaningDecisionCode.DUPLICATE_URL
    assert res.items[1].is_selected is True
    assert res.items[1].canonical_target is None

    assert res.final_target_count == 1
    assert res.accepted_canonical_targets == ["https://archi-builders.com/"]
