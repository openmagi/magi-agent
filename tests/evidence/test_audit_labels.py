"""Cover the canonical verdict-status -> display-label projection.

Exercises every value of the four runtime enums the projection folds, the
persisted RuleVerdict vocabulary, the reviewer special-case, case-insensitivity,
the UNKNOWN fallback, severity buckets, and the enforcement-kind predicate.

Hermetic: pure functions, no env, no I/O.
"""
from __future__ import annotations

import typing

import pytest

from magi_agent.evidence.audit_labels import (
    ENFORCEMENT_EVENT_KINDS,
    classify_verdict_severity,
    is_enforced_kind,
    verdict_to_display_label,
)


# Source-of-truth enum value lists (kept in sync with the real enums).
_EVIDENCE_VERDICT_STATE = ["audit", "pass", "missing", "failed", "block_ready"]
_VERIFIER_STATUS = ["pass", "failed", "missing", "approval_required", "audit"]
_VALIDATOR_ACTION = ["pass", "repair", "ask_user", "abstain", "block"]
_HARNESS_VERIFIER_STATUS = ["started", "passed", "failed", "skipped", "error"]
_RULE_VERDICT = ["ok", "violation", "pending"]


@pytest.mark.parametrize(
    "status,expected",
    [
        # EvidenceVerdictState
        ("audit", "AUDIT"),
        ("pass", "VERIFIED"),
        ("missing", "MISSING"),
        ("failed", "FAILED"),
        ("block_ready", "BLOCKED"),
        # VerifierStatus
        ("approval_required", "NEEDS APPROVAL"),
        # ValidatorAction
        ("repair", "REPAIRED"),
        ("ask_user", "NEEDS APPROVAL"),
        ("abstain", "ABSTAINED"),
        ("block", "BLOCKED"),
        # HarnessVerifierStatus
        ("started", "PENDING"),
        ("passed", "VERIFIED"),
        ("skipped", "MISSING"),
        ("error", "FAILED"),
        # RuleVerdict (persisted public vocabulary)
        ("ok", "VERIFIED"),
        ("violation", "BLOCKED"),
        ("pending", "PENDING"),
        # extra block synonyms
        ("blocked", "BLOCKED"),
        ("denied", "BLOCKED"),
        ("deny", "BLOCKED"),
        ("rejected", "REJECTED BY REVIEWER"),
    ],
)
def test_status_maps_to_expected_label(status: str, expected: str) -> None:
    assert verdict_to_display_label(status) == expected


def test_every_enum_value_has_a_non_unknown_label() -> None:
    all_values = (
        _EVIDENCE_VERDICT_STATE
        + _VERIFIER_STATUS
        + _VALIDATOR_ACTION
        + _HARNESS_VERIFIER_STATUS
        + _RULE_VERDICT
    )
    for value in all_values:
        assert verdict_to_display_label(value) != "UNKNOWN", value


def test_real_enum_members_all_map_to_non_unknown_label() -> None:
    """Drift guard: import the REAL runtime enums/Literals and assert every
    member projects to a non-UNKNOWN label.

    This catches a future 6th enum value being added upstream that silently maps
    to UNKNOWN (the local _XXX lists above could drift out of sync; these read
    the source-of-truth definitions directly).
    """
    from magi_agent.evidence.types import EvidenceVerdictState
    from magi_agent.evidence.validator_taxonomy import ValidatorAction
    from magi_agent.harness.audit import HarnessVerifierStatus
    from magi_agent.harness.verifier_bus import VerifierStatus

    literal_values: list[str] = []
    for literal in (EvidenceVerdictState, VerifierStatus, ValidatorAction):
        literal_values.extend(typing.get_args(literal))
    # StrEnum: iterate members.
    enum_values = [member.value for member in HarnessVerifierStatus]

    assert literal_values, "expected non-empty Literal members (typing.get_args)"
    assert enum_values, "expected non-empty StrEnum members"

    for value in literal_values + enum_values:
        assert verdict_to_display_label(value) != "UNKNOWN", value


def test_enforcement_event_kinds_derives_from_taxonomy() -> None:
    """ENFORCEMENT_EVENT_KINDS must equal the taxonomy's policy category so the
    two single-source-of-truth definitions cannot drift apart."""
    from magi_agent.observability.taxonomy import CATEGORIES

    assert ENFORCEMENT_EVENT_KINDS == set(CATEGORIES["policy"])


def test_status_is_case_insensitive() -> None:
    assert verdict_to_display_label("PASS") == "VERIFIED"
    assert verdict_to_display_label("Block_Ready") == "BLOCKED"
    assert verdict_to_display_label("  Approval_Required  ") == "NEEDS APPROVAL"


def test_unknown_fallback() -> None:
    assert verdict_to_display_label("totally-unrecognized") == "UNKNOWN"
    assert verdict_to_display_label("") == "UNKNOWN"
    assert verdict_to_display_label(None) == "UNKNOWN"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reviewer source-type special-case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["failed", "block", "violation", "audit", "missing"])
def test_reviewer_non_pass_is_rejected(status: str) -> None:
    assert (
        verdict_to_display_label(status, source_type="reviewer")
        == "REJECTED BY REVIEWER"
    )


@pytest.mark.parametrize("status", ["pass", "passed", "ok"])
def test_reviewer_pass_is_verified(status: str) -> None:
    assert verdict_to_display_label(status, source_type="reviewer") == "VERIFIED"


def test_reviewer_source_type_case_insensitive() -> None:
    assert verdict_to_display_label("failed", source_type="Reviewer") == "REJECTED BY REVIEWER"


def test_non_reviewer_source_type_uses_normal_mapping() -> None:
    assert verdict_to_display_label("failed", source_type="verifier") == "FAILED"


# ---------------------------------------------------------------------------
# Severity buckets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,bucket",
    [
        ("VERIFIED", "pass"),
        ("REPAIRED", "pass"),
        ("FAILED", "deny"),
        ("BLOCKED", "deny"),
        ("REJECTED BY REVIEWER", "deny"),
        ("NEEDS APPROVAL", "review"),
        ("ABSTAINED", "review"),
        ("AUDIT", "review"),
        ("MISSING", "review"),
        ("PENDING", "info"),
        ("UNKNOWN", "info"),
    ],
)
def test_severity_buckets(label: str, bucket: str) -> None:
    assert classify_verdict_severity(label) == bucket


def test_severity_unknown_label_is_info() -> None:
    assert classify_verdict_severity("SOMETHING ELSE") == "info"


# ---------------------------------------------------------------------------
# Enforcement kind predicate
# ---------------------------------------------------------------------------


def test_enforcement_kinds_membership() -> None:
    assert ENFORCEMENT_EVENT_KINDS == frozenset({"rule_check", "rule_violation"})
    assert is_enforced_kind("rule_check") is True
    assert is_enforced_kind("rule_violation") is True


@pytest.mark.parametrize(
    "kind",
    ["tool_start", "tool_end", "turn_start", "policy_decision", "guardrail_observed", "", "source_inspected"],
)
def test_non_enforcement_kinds(kind: str) -> None:
    assert is_enforced_kind(kind) is False


# ---------------------------------------------------------------------------
# Source-citation gate labels (Wave 4b Piece E)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "citation_verdict,expected_label,expected_severity",
    [
        ("cited", "SOURCES CITED", "pass"),
        ("partial", "PARTIALLY CITED", "review"),
        ("uncited", "UNCITED CLAIMS", "review"),
    ],
)
def test_citation_source_type_labels(
    citation_verdict: str, expected_label: str, expected_severity: str
) -> None:
    label = verdict_to_display_label(citation_verdict, source_type="citation")
    assert label == expected_label
    assert classify_verdict_severity(label) == expected_severity


def test_citation_source_type_case_insensitive() -> None:
    assert verdict_to_display_label("CITED", source_type="Citation") == "SOURCES CITED"


def test_citation_unknown_verdict_falls_back_to_status_map() -> None:
    # An unrecognized citation value degrades to the generic status map rather
    # than dropping the row (ok -> VERIFIED).
    assert verdict_to_display_label("ok", source_type="citation") == "VERIFIED"
