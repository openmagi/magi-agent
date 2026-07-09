"""Unit tests for PR-2 additions to audit_labels.py.

Tests 17-18: new constants (RESOLVED, ACKNOWLEDGED, IGNORED, ADVISORY) and
verify_finding_display_label() label matrix.

Style: no em-dashes.
"""
from __future__ import annotations

import pytest

from magi_agent.evidence.audit_labels import (
    RESOLVED,
    ACKNOWLEDGED,
    IGNORED,
    ADVISORY,
    UNKNOWN,
    verify_finding_display_label,
    classify_verdict_severity,
)


# ---------------------------------------------------------------------------
# Test 17: constants exported with correct values
# ---------------------------------------------------------------------------


def test_new_constants_exported():
    """RESOLVED, ACKNOWLEDGED, IGNORED, ADVISORY are uppercase string constants."""
    assert RESOLVED == "RESOLVED"
    assert ACKNOWLEDGED == "ACKNOWLEDGED"
    assert IGNORED == "IGNORED"
    assert ADVISORY == "ADVISORY"


# ---------------------------------------------------------------------------
# Test 18: verify_finding_display_label matrix (test 17 and 18 per spec)
# ---------------------------------------------------------------------------

# (confidence, resolution) -> expected_label, expected_severity
_MATRIX = [
    # Advisory findings always project ADVISORY/info regardless of resolution
    ("advisory", "resolved", ADVISORY, "info"),
    ("advisory", "acknowledged_shipped", ADVISORY, "info"),
    ("advisory", "ignored", ADVISORY, "info"),
    ("advisory", "", ADVISORY, "info"),
    # High confidence with resolution
    ("high", "resolved", RESOLVED, "pass"),
    ("high", "acknowledged_shipped", ACKNOWLEDGED, "review"),
    ("high", "ignored", IGNORED, "deny"),
    # High confidence with empty/unrecognized resolution falls to UNKNOWN
    ("high", "", UNKNOWN, "info"),
    ("high", "unknown_resolution", UNKNOWN, "info"),
]


@pytest.mark.parametrize(
    "confidence,resolution,expected_label,expected_severity",
    _MATRIX,
    ids=[f"{c}/{r}" for c, r, *_ in _MATRIX],
)
def test_verify_finding_display_label_matrix(
    confidence, resolution, expected_label, expected_severity
):
    """verify_finding_display_label maps (confidence, resolution) to (label, severity)."""
    label = verify_finding_display_label(confidence, resolution)
    assert label == expected_label, (
        f"verify_finding_display_label({confidence!r}, {resolution!r}) "
        f"returned {label!r}, expected {expected_label!r}"
    )
    severity = classify_verdict_severity(label)
    assert severity == expected_severity, (
        f"classify_verdict_severity({label!r}) returned {severity!r}, "
        f"expected {expected_severity!r}"
    )


def test_finding_label_severities():
    """RESOLVED->pass, ACKNOWLEDGED->review, IGNORED->deny, ADVISORY->info."""
    assert classify_verdict_severity(RESOLVED) == "pass"
    assert classify_verdict_severity(ACKNOWLEDGED) == "review"
    assert classify_verdict_severity(IGNORED) == "deny"
    assert classify_verdict_severity(ADVISORY) == "info"
