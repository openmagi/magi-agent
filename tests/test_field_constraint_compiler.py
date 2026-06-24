"""Tests for magi_agent.customize.field_constraint_compiler (PR-F3 backend).

Spec: docs/plans/2026-06-23-customize-depth-enrichment-design.md §PR-F3

The compiler is a *deterministic* (no-LLM) SHACL-shape synthesizer for a
structured IR.  Two payload shapes are supported:

Single-record::

    {
      "kind": "field_constraint",
      "payload": {
        "evidenceType": "TestRun",
        "field": "exitCode",
        "operator": "eq"|"neq"|"gt"|"lt"|"ge"|"le"|"exists"|"notExists",
        "value": <any>,           # optional for exists/notExists
      },
    }

Cross-record (forEachExistsCovering)::

    {
      "kind": "field_constraint",
      "payload": {
        "operator": "forEachExistsCovering",
        "source": {"evidenceType": "GitDiff",  "field": "changedFiles"},
        "target": {"evidenceType": "TestRun",  "field": "command",
                    "covering": "source.entry"},
      },
    }

Tests:
  1. Per-operator compile produces a SHACL shape with the expected predicates.
  2. The validator rejects an unknown field (``honest-degrade`` at compile time).
  3. The synthesized TTL behaves as expected when round-tripped through the
     real ``run_shacl_rule`` runtime gate.
"""

from __future__ import annotations

import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource


_OBSERVED_AT = 1_730_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    *,
    type: str,
    fields: dict,
    status: str = "ok",
    observed_at: int = _OBSERVED_AT,
) -> EvidenceRecord:
    return EvidenceRecord(
        type=type,
        status=status,  # type: ignore[arg-type]
        observedAt=observed_at,
        source=EvidenceSource(kind="verifier"),
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Per-operator compile tests (Stage A — RED → GREEN)
# ---------------------------------------------------------------------------


def test_compile_eq_single_record_runs_ok_on_matching_value() -> None:
    """eq operator → sh:hasValue.  exitCode==0 passes; exitCode==1 fails."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    payload = {
        "evidenceType": "TestRun",
        "field": "exitCode",
        "operator": "eq",
        "value": 0,
    }
    ttl = compile_to_shacl_ttl(payload)

    # Shape body must reference the canonical predicate and constraint.
    assert "magi:field_exitCode" in ttl
    assert "sh:hasValue" in ttl
    assert "sh:targetClass" in ttl

    passing = [_record(type="TestRun", fields={"command": "pytest", "exitCode": 0})]
    failing = [_record(type="TestRun", fields={"command": "pytest", "exitCode": 1})]

    ok = run_shacl_rule(passing, ttl, "eq-pass", observed_at=_OBSERVED_AT)
    bad = run_shacl_rule(failing, ttl, "eq-fail", observed_at=_OBSERVED_AT)

    assert ok.status == "ok", f"expected pass for exitCode=0, got {ok.fields}"
    assert bad.status == "failed", f"expected fail for exitCode=1, got {bad.fields}"


def test_compile_neq_uses_sh_not_with_hasvalue() -> None:
    """neq operator → sh:not [sh:hasValue ...]."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    payload = {
        "evidenceType": "TestRun",
        "field": "exitCode",
        "operator": "neq",
        "value": 0,
    }
    ttl = compile_to_shacl_ttl(payload)

    assert "sh:not" in ttl
    assert "sh:hasValue" in ttl

    passing = [_record(type="TestRun", fields={"command": "pytest", "exitCode": 1})]
    failing = [_record(type="TestRun", fields={"command": "pytest", "exitCode": 0})]

    ok = run_shacl_rule(passing, ttl, "neq-pass", observed_at=_OBSERVED_AT)
    bad = run_shacl_rule(failing, ttl, "neq-fail", observed_at=_OBSERVED_AT)

    assert ok.status == "ok"
    assert bad.status == "failed"


def test_compile_gt_uses_min_exclusive() -> None:
    """gt operator → sh:minExclusive."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    payload = {
        "evidenceType": "CodeDiagnostics",
        "field": "errorCount",
        "operator": "gt",
        "value": 0,
    }
    ttl = compile_to_shacl_ttl(payload)

    assert "sh:minExclusive" in ttl

    # errorCount=5 must pass (5 > 0); errorCount=0 must fail (not > 0).
    passing = [_record(
        type="CodeDiagnostics",
        fields={
            "checker": "ruff",
            "errorCount": 5,
            "fileDigest": "x",
            "diagnosticsDigest": "y",
        },
    )]
    failing = [_record(
        type="CodeDiagnostics",
        fields={
            "checker": "ruff",
            "errorCount": 0,
            "fileDigest": "x",
            "diagnosticsDigest": "y",
        },
    )]

    ok = run_shacl_rule(passing, ttl, "gt-pass", observed_at=_OBSERVED_AT)
    bad = run_shacl_rule(failing, ttl, "gt-fail", observed_at=_OBSERVED_AT)

    assert ok.status == "ok"
    assert bad.status == "failed"


def test_compile_ge_uses_min_inclusive() -> None:
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    payload = {
        "evidenceType": "CodeDiagnostics",
        "field": "errorCount",
        "operator": "ge",
        "value": 1,
    }
    ttl = compile_to_shacl_ttl(payload)
    assert "sh:minInclusive" in ttl

    fields_base = {
        "checker": "ruff",
        "fileDigest": "x",
        "diagnosticsDigest": "y",
    }
    ok = run_shacl_rule(
        [_record(type="CodeDiagnostics", fields={**fields_base, "errorCount": 1})],
        ttl, "ge-pass", observed_at=_OBSERVED_AT,
    )
    bad = run_shacl_rule(
        [_record(type="CodeDiagnostics", fields={**fields_base, "errorCount": 0})],
        ttl, "ge-fail", observed_at=_OBSERVED_AT,
    )
    assert ok.status == "ok"
    assert bad.status == "failed"


def test_compile_lt_uses_max_exclusive() -> None:
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    assert "sh:maxExclusive" in compile_to_shacl_ttl({
        "evidenceType": "CodeDiagnostics",
        "field": "errorCount",
        "operator": "lt",
        "value": 10,
    })


def test_compile_le_uses_max_inclusive() -> None:
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    assert "sh:maxInclusive" in compile_to_shacl_ttl({
        "evidenceType": "CodeDiagnostics",
        "field": "errorCount",
        "operator": "le",
        "value": 10,
    })


def test_compile_exists_uses_min_count_one() -> None:
    """exists → sh:minCount 1 (no value required)."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    payload = {
        "evidenceType": "TestRun",
        "field": "exitCode",
        "operator": "exists",
    }
    ttl = compile_to_shacl_ttl(payload)
    assert "sh:minCount" in ttl

    ok = run_shacl_rule(
        [_record(type="TestRun", fields={"command": "pytest", "exitCode": 0})],
        ttl, "exists-pass", observed_at=_OBSERVED_AT,
    )
    bad = run_shacl_rule(
        [_record(type="TestRun", fields={"command": "pytest"})],
        ttl, "exists-fail", observed_at=_OBSERVED_AT,
    )

    assert ok.status == "ok"
    assert bad.status == "failed"


def test_compile_not_exists_uses_max_count_zero() -> None:
    """notExists → sh:maxCount 0 (field must NOT be present)."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    payload = {
        "evidenceType": "TestRun",
        "field": "exitCode",
        "operator": "notExists",
    }
    ttl = compile_to_shacl_ttl(payload)
    assert "sh:maxCount" in ttl

    ok = run_shacl_rule(
        [_record(type="TestRun", fields={"command": "pytest"})],
        ttl, "notexists-pass", observed_at=_OBSERVED_AT,
    )
    bad = run_shacl_rule(
        [_record(type="TestRun", fields={"command": "pytest", "exitCode": 0})],
        ttl, "notexists-fail", observed_at=_OBSERVED_AT,
    )

    assert ok.status == "ok"
    assert bad.status == "failed"


# ---------------------------------------------------------------------------
# Cross-record: forEachExistsCovering
# ---------------------------------------------------------------------------


def test_compile_for_each_exists_covering_emits_qualified_value_shape() -> None:
    """forEachExistsCovering compiles to a shape that uses sh:qualifiedValueShape
    + sh:qualifiedMinCount on the target side, anchored on the source.field
    entries.

    Round-trip semantics: when every entry of source.field is covered by at
    least one matching target record (target.field hasValue == that entry),
    the shape passes; otherwise it fails.

    Uses verified field pairs from ``available_fields()`` so the test is
    independent of F2's GitDiff promotion:
      WebSearch.sourceIds (list-valued) → SourceInspection.sourceId (scalar)
    Semantics: "for each source id returned by WebSearch, there exists a
    SourceInspection record whose sourceId equals that entry".
    """
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    payload = {
        "operator": "forEachExistsCovering",
        "source": {"evidenceType": "WebSearch", "field": "sourceIds"},
        "target": {"evidenceType": "SourceInspection", "field": "sourceId",
                   "covering": "source.entry"},
    }
    ttl = compile_to_shacl_ttl(payload)

    # Synthesized shape must reference both predicates and the SHACL idioms.
    assert "magi:field_sourceIds" in ttl
    assert "magi:field_sourceId" in ttl
    assert "sh:qualifiedValueShape" in ttl
    assert "sh:qualifiedMinCount" in ttl

    # WebSearch lists two source ids; both must be inspected.
    websearch = _record(
        type="WebSearch",
        fields={
            "query": "magi shacl",
            "resultCount": 2,
            "sourceKind": "web",
            "sourceIds": ("src-a", "src-b"),
        },
    )
    insp_a = _record(
        type="SourceInspection",
        fields={"sourceId": "src-a", "sourceIds": ("src-a",),
                "sourceKind": "web", "inspected": True},
    )
    insp_b = _record(
        type="SourceInspection",
        fields={"sourceId": "src-b", "sourceIds": ("src-b",),
                "sourceKind": "web", "inspected": True},
    )

    ok = run_shacl_rule(
        [websearch, insp_a, insp_b],
        ttl, "cover-pass", observed_at=_OBSERVED_AT,
    )
    # Only one of the two source ids is covered → fail.
    partial = run_shacl_rule(
        [websearch, insp_a],
        ttl, "cover-partial", observed_at=_OBSERVED_AT,
    )

    assert ok.status == "ok", f"expected pass, got {ok.status} {ok.fields}"
    assert partial.status == "failed", (
        f"expected fail for partial coverage, got {partial.status} {partial.fields}"
    )


# ---------------------------------------------------------------------------
# Validator: unknown field rejected (honest-degrade)
# ---------------------------------------------------------------------------


def test_validator_rejects_unknown_field_on_single_record() -> None:
    """payload.field must appear in available_fields(payload.evidenceType);
    unknown fields raise ValueError before any TTL is generated.

    TestRun's verified hints are ['command', 'exitCode']; 'coverageRatio' is
    not one of them, so the compile must refuse.
    """
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl

    with pytest.raises(ValueError, match="coverageRatio"):
        compile_to_shacl_ttl({
            "evidenceType": "TestRun",
            "field": "coverageRatio",  # not in available_fields("TestRun")
            "operator": "eq",
            "value": 1.0,
        })


def test_validator_rejects_unknown_field_on_inert_producer_type() -> None:
    """Types whose ``available_fields`` entry is empty have no authorable
    field — any field name must be rejected (honest-degrade)."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl

    # GitDiff is in _BUILTIN_FIELD_HINTS as ``[]`` (no verified producer fields
    # before F2; F2 promotes changedFiles).  Either way, an empty hint list at
    # call time means we must refuse anything other than what the catalog lists.
    # Use a fabricated name that is guaranteed not to be in any verified hint.
    with pytest.raises(ValueError):
        compile_to_shacl_ttl({
            "evidenceType": "GitDiff",
            "field": "definitelyNotAVerifiedField",
            "operator": "exists",
        })


def test_validator_rejects_unknown_field_on_cross_record() -> None:
    """forEachExistsCovering: both source.field and target.field must appear
    in their respective ``available_fields`` lists."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl

    with pytest.raises(ValueError):
        compile_to_shacl_ttl({
            "operator": "forEachExistsCovering",
            "source": {"evidenceType": "GitDiff", "field": "bogus"},
            "target": {"evidenceType": "TestRun", "field": "command",
                       "covering": "source.entry"},
        })


def test_validator_rejects_unknown_evidence_type() -> None:
    """Evidence types not in the builtin catalog are rejected."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl

    with pytest.raises(ValueError):
        compile_to_shacl_ttl({
            "evidenceType": "ThisTypeDoesNotExist",
            "field": "anything",
            "operator": "exists",
        })


def test_validator_rejects_unknown_operator() -> None:
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl

    with pytest.raises(ValueError, match="operator"):
        compile_to_shacl_ttl({
            "evidenceType": "TestRun",
            "field": "exitCode",
            "operator": "matches",  # not in the frozen v1 set
            "value": "x",
        })


def test_validator_rejects_missing_value_for_comparison_operators() -> None:
    """eq/neq/gt/lt/ge/le require a ``value`` key."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl

    for op in ("eq", "neq", "gt", "lt", "ge", "le"):
        with pytest.raises(ValueError):
            compile_to_shacl_ttl({
                "evidenceType": "TestRun",
                "field": "exitCode",
                "operator": op,
                # value omitted
            })


def test_compile_is_deterministic() -> None:
    """Same input → identical output bytes (no LLM, no I/O)."""
    from magi_agent.customize.field_constraint_compiler import compile_to_shacl_ttl

    payload = {
        "evidenceType": "TestRun",
        "field": "exitCode",
        "operator": "eq",
        "value": 0,
    }
    a = compile_to_shacl_ttl(payload)
    b = compile_to_shacl_ttl(payload)
    assert a == b
