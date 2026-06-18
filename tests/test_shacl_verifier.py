"""Tests for magi_agent.evidence.shacl_verifier.run_shacl_rule.

TDD: written before implementation — all tests must fail initially.
Spec: docs/plans/2026-06-18-shacl-PR1-engine-tasks.md Task 1.2
"""
from __future__ import annotations

import collections.abc

import pytest

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OBSERVED_AT = 1_718_000_000


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


# A minimal SHACL shape that enforces sh:maxInclusive 3000 on magi:field_amount
# for nodes that are a magi:Evidence.
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

# A shape that would only fire under RDFS subclass inference (never under inference="none")
_SHAPE_RDFS_INFERENCE_ONLY = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:SubEvidence rdfs:subClassOf magi:Evidence .

magi:SubEvidenceAmountShape
    a sh:NodeShape ;
    sh:targetClass magi:SubEvidence ;
    sh:property [
        sh:path magi:field_amount ;
        sh:maxInclusive 0 ;
        sh:message "would only fire if RDFS inference were active" ;
    ] .
"""

_BROKEN_TTL = "this is not valid turtle syntax @@@"


# ---------------------------------------------------------------------------
# Test 1 — no violations: status="ok", conforms=True, violations=()
# ---------------------------------------------------------------------------


def test_no_violation_status_ok() -> None:
    """Records with amount<=3000 must yield status='ok', conforms=True, violations=()."""
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 1000})]
    result = run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "test-rule-ok", observed_at=_OBSERVED_AT)

    assert result.type == "custom:ShaclConstraintCheck"
    assert result.status == "ok"
    assert result.fields["ruleId"] == "test-rule-ok"
    assert result.fields["conforms"] is True
    assert result.fields["violations"] == ()


# ---------------------------------------------------------------------------
# Test 2 — violations present: status="failed", conforms=False, violations carry info
# ---------------------------------------------------------------------------


def test_violation_status_failed() -> None:
    """Records with amount=4200 vs maxInclusive 3000 must yield status='failed',
    conforms=False, and violations containing path/value/message."""
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 4200})]
    result = run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "test-rule-fail", observed_at=_OBSERVED_AT)

    assert result.type == "custom:ShaclConstraintCheck"
    assert result.status == "failed"
    assert result.fields["ruleId"] == "test-rule-fail"
    assert result.fields["conforms"] is False

    violations = result.fields["violations"]
    assert isinstance(violations, tuple), f"violations must be a tuple, got {type(violations)}"
    assert len(violations) >= 1, "Expected at least one violation"

    v = violations[0]
    # EvidenceRecord freezes nested dicts to MappingProxyType; check Mapping (superset of dict)
    assert isinstance(v, collections.abc.Mapping), f"Each violation must be a Mapping, got {type(v)}"
    # Must have path and message
    assert "resultPath" in v, f"violation missing 'resultPath': {v}"
    assert "message" in v, f"violation missing 'message': {v}"
    # Message should reference the shape message
    assert "3000" in str(v["message"]) or "amount" in str(v["message"]).lower(), (
        f"Expected shape message content in violation message, got: {v['message']}"
    )
    # pyshacl includes sh:value for sh:maxInclusive violations
    assert "value" in v, f"violation missing 'value' (pyshacl sh:value): {v}"
    assert v["value"] is not None, "violation 'value' must not be None for maxInclusive"


# ---------------------------------------------------------------------------
# Test 3 — fail-safe: malformed shape TTL → status="unknown", error field, no exception
# ---------------------------------------------------------------------------


def test_failsafe_malformed_shape_ttl() -> None:
    """A syntax-error shape_ttl must NOT raise. Return status='unknown' (not 'failed'),
    with an 'error' field populated."""
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 100})]

    # Must not raise any exception
    result = run_shacl_rule(records, _BROKEN_TTL, "test-rule-broken", observed_at=_OBSERVED_AT)

    assert result.status == "unknown", (
        f"Expected 'unknown' on parse failure, got '{result.status}'. "
        "NEVER return 'failed' on an internal error."
    )
    assert "error" in result.fields, f"Expected 'error' key in fields: {result.fields}"
    assert isinstance(result.fields["error"], str) and result.fields["error"], (
        "error field must be a non-empty string"
    )
    assert result.fields["ruleId"] == "test-rule-broken"
    assert result.fields.get("conforms") is None


# ---------------------------------------------------------------------------
# Test 4 — fail-safe: pyshacl internal exception → status="unknown"
# ---------------------------------------------------------------------------


def test_failsafe_pyshacl_internal_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """If pyshacl.validate raises internally, run_shacl_rule must catch it and
    return status='unknown' without re-raising."""
    import magi_agent.evidence.shacl_verifier as mod

    def _exploding_validate(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated pyshacl internal failure")

    monkeypatch.setattr(mod, "_pyshacl_validate", _exploding_validate)

    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 100})]
    result = run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "test-rule-except", observed_at=_OBSERVED_AT)

    assert result.status == "unknown", (
        f"Expected 'unknown' on pyshacl exception, got '{result.status}'"
    )
    assert "error" in result.fields
    assert result.fields.get("conforms") is None


# ---------------------------------------------------------------------------
# Test 5 — determinism: same input twice → identical EvidenceRecord
# ---------------------------------------------------------------------------


def test_determinism_same_input_same_record() -> None:
    """Calling run_shacl_rule twice with identical inputs must produce byte-identical
    records (including violations order)."""
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 4200})]

    r1 = run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "det-rule", observed_at=_OBSERVED_AT)
    r2 = run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "det-rule", observed_at=_OBSERVED_AT)

    assert r1.status == r2.status
    assert r1.fields["conforms"] == r2.fields["conforms"]
    assert r1.fields["violations"] == r2.fields["violations"], (
        "violations tuple order must be stable across identical calls"
    )
    # Full field equality
    assert r1.fields == r2.fields, f"fields differ:\n  r1={r1.fields}\n  r2={r2.fields}"


# ---------------------------------------------------------------------------
# Test 6 — inference="none" is always forwarded to _pyshacl_validate
# ---------------------------------------------------------------------------


def test_inference_none_kwarg_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_shacl_rule must always pass inference="none" to _pyshacl_validate.

    Monkeypatches the module-level seam to capture the kwarg, then delegates
    to the real validate so the result is a genuine (conforms, graph, text) tuple.
    """
    import magi_agent.evidence.shacl_verifier as mod

    captured: dict[str, object] = {}
    _real_validate = mod._pyshacl_validate  # noqa: SLF001

    def _spy_validate(
        data_graph: object,
        shacl_graph: object,
        inference: str,
    ) -> object:
        captured["inference"] = inference
        return _real_validate(data_graph, shacl_graph, inference)  # type: ignore[arg-type]

    monkeypatch.setattr(mod, "_pyshacl_validate", _spy_validate)

    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 1000})]
    result = run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "test-rule-inference", observed_at=_OBSERVED_AT)

    # The spy must have been called (i.e. no early bailout)
    assert "inference" in captured, "spy was never called — early bailout before pyshacl?"
    assert captured["inference"] == "none", (
        f"INFERENCE KWARG BUG: expected 'none', got {captured['inference']!r}. "
        "run_shacl_rule must always pass inference='none' to _pyshacl_validate."
    )
    # Validate still returned a real result (not a fail-safe unknown)
    assert result.status in ("ok", "failed"), (
        f"Expected ok/failed from real validate, got {result.status!r}"
    )
