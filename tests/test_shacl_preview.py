"""Tests for magi_agent.customize.shacl_compiler — Task 3.1 (pure, no model).

TDD: written before implementation.  Tests cover:
  1. available_fields() includes BUILTIN_EVIDENCE_TYPES entries; deterministic.
  2. preview_cases: violating record → conforms=False / status='failed' / non-empty violations;
     passing record → conforms=True / status='ok'.
  3. preview_cases deterministic (two calls equal).
  4. preview_cases fail-safe: malformed shape → each case status='unknown', no exception.

Zero model/LLM calls.  Spec: docs/plans/2026-06-18-shacl-PR3-compiler-tasks.md Task 3.1
"""
from __future__ import annotations

import pytest

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_OBSERVED_AT = 1_718_000_001


def _make_record(
    *,
    type: str = "Calculation",
    status: str = "ok",
    fields: dict | None = None,
    observed_at: int = _OBSERVED_AT,
) -> EvidenceRecord:
    return EvidenceRecord(
        type=type,
        status=status,  # type: ignore[arg-type]
        observedAt=observed_at,
        source=EvidenceSource(kind="verifier"),
        fields=fields or {},
    )


# A minimal SHACL shape: sh:maxInclusive 3000 on magi:field_amount for magi:Evidence
_SHAPE_AMOUNT_MAX_3000 = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:AmountShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_amount ;
        sh:maxInclusive 3000 ;
        sh:message "amount must not exceed 3000" ;
    ] .
"""

_BROKEN_TTL = "this is not valid turtle @@@"


# ---------------------------------------------------------------------------
# Test 1 — available_fields: contains BUILTIN_EVIDENCE_TYPES, deterministic
# ---------------------------------------------------------------------------


def test_available_fields_contains_builtin_types() -> None:
    """available_fields() must include every BUILTIN_EVIDENCE_TYPES entry.

    Each item must be a dict with at least an 'evidenceType' key whose value
    is a string matching a member of BUILTIN_EVIDENCE_TYPES.
    """
    from magi_agent.evidence.types import BUILTIN_EVIDENCE_TYPES
    from magi_agent.customize.shacl_compiler import available_fields

    menu = available_fields()

    assert isinstance(menu, list), f"available_fields() must return a list, got {type(menu)}"
    assert len(menu) >= 1, "available_fields() must be non-empty"

    returned_types = {item["evidenceType"] for item in menu}
    for builtin_type in BUILTIN_EVIDENCE_TYPES:
        assert builtin_type in returned_types, (
            f"BUILTIN_EVIDENCE_TYPES entry {builtin_type!r} missing from available_fields()"
        )


def test_available_fields_deterministic() -> None:
    """Two consecutive calls to available_fields() must return identical results."""
    from magi_agent.customize.shacl_compiler import available_fields

    first = available_fields()
    second = available_fields()

    assert first == second, (
        "available_fields() is not deterministic — two calls returned different results"
    )


def test_available_fields_items_have_required_keys() -> None:
    """Each item in available_fields() must have 'evidenceType' and 'fields' keys."""
    from magi_agent.customize.shacl_compiler import available_fields

    menu = available_fields()
    for item in menu:
        assert "evidenceType" in item, f"item missing 'evidenceType': {item}"
        assert "fields" in item, f"item missing 'fields': {item}"
        assert isinstance(item["fields"], list), (
            f"item['fields'] must be a list, got {type(item['fields'])}: {item}"
        )


# ---------------------------------------------------------------------------
# Test 2 — preview_cases: violating and passing records
# ---------------------------------------------------------------------------


def test_preview_cases_violating_record() -> None:
    """A record with amount=4200 against maxInclusive 3000 → conforms=False, status='failed',
    non-empty violations."""
    from magi_agent.customize.shacl_compiler import preview_cases

    record = _make_record(fields={"amount": 4200})
    results = preview_cases(_SHAPE_AMOUNT_MAX_3000, [record], observed_at=_OBSERVED_AT)

    assert isinstance(results, list), f"preview_cases must return a list, got {type(results)}"
    assert len(results) == 1, f"Expected 1 result for 1 sample, got {len(results)}"

    case = results[0]
    assert case["conforms"] is False, (
        f"Expected conforms=False for violating record, got {case['conforms']}"
    )
    assert case["status"] == "failed", (
        f"Expected status='failed' for violating record, got {case['status']!r}"
    )
    assert len(case["violations"]) >= 1, (
        f"Expected non-empty violations for violating record, got {case['violations']}"
    )


def test_preview_cases_passing_record() -> None:
    """A record with amount=1000 against maxInclusive 3000 → conforms=True, status='ok'."""
    from magi_agent.customize.shacl_compiler import preview_cases

    record = _make_record(fields={"amount": 1000})
    results = preview_cases(_SHAPE_AMOUNT_MAX_3000, [record], observed_at=_OBSERVED_AT)

    assert isinstance(results, list)
    assert len(results) == 1

    case = results[0]
    assert case["conforms"] is True, (
        f"Expected conforms=True for passing record, got {case['conforms']}"
    )
    assert case["status"] == "ok", (
        f"Expected status='ok' for passing record, got {case['status']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — preview_cases deterministic
# ---------------------------------------------------------------------------


def test_preview_cases_deterministic() -> None:
    """Two calls with identical inputs must return identical results."""
    from magi_agent.customize.shacl_compiler import preview_cases

    records = [
        _make_record(fields={"amount": 4200}),
        _make_record(fields={"amount": 500}),
    ]

    r1 = preview_cases(_SHAPE_AMOUNT_MAX_3000, records, observed_at=_OBSERVED_AT)
    r2 = preview_cases(_SHAPE_AMOUNT_MAX_3000, records, observed_at=_OBSERVED_AT)

    assert r1 == r2, (
        "preview_cases is not deterministic — two calls with identical inputs returned "
        f"different results:\n  r1={r1}\n  r2={r2}"
    )


# ---------------------------------------------------------------------------
# Test 4 — preview_cases fail-safe: malformed shape → status='unknown', no exception
# ---------------------------------------------------------------------------


def test_preview_cases_failsafe_malformed_shape() -> None:
    """A malformed shape_ttl must not raise.  Each case must have status='unknown'."""
    from magi_agent.customize.shacl_compiler import preview_cases

    records = [
        _make_record(fields={"amount": 100}),
        _make_record(fields={"amount": 9999}),
    ]

    # Must not raise any exception
    try:
        results = preview_cases(_BROKEN_TTL, records, observed_at=_OBSERVED_AT)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"preview_cases raised an exception for malformed shape: {exc!r}. "
            "Must be fail-safe (status='unknown' per case, no raise)."
        )

    assert isinstance(results, list), f"Expected list, got {type(results)}"
    assert len(results) == len(records), (
        f"Expected {len(records)} results for {len(records)} samples, got {len(results)}"
    )
    for i, case in enumerate(results):
        assert case["status"] == "unknown", (
            f"Case {i}: expected status='unknown' for malformed shape, got {case['status']!r}"
        )
