"""Tests for magi_agent.evidence.shacl_verifier.run_shacl_rule.

TDD: written before implementation — all tests must fail initially.
Spec: docs/plans/2026-06-18-shacl-PR1-engine-tasks.md Task 1.2

Review fixes (2026-06-18):
  F1 — multi-valued sh:message determinism
  F2 — sh:sparql DoS / byte-size cap / timeout guards
  F3 — flatten_error and extraction_error fail-safe branches
  F4 — sh:Warning/sh:Info severity → violations non-empty
"""
from __future__ import annotations

import collections.abc
import time

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


# ---------------------------------------------------------------------------
# F1 — multi-valued sh:message determinism
# ---------------------------------------------------------------------------

# A shape that declares TWO sh:message values — "ZZZ" and "AAA".
# The extracted message must always be "AAA" (lexicographically smallest),
# regardless of Python hash seed / set-iteration order.
_SHAPE_MULTI_MESSAGE = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:MultiMessageShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_amount ;
        sh:maxInclusive 0 ;
        sh:message "ZZZ — high amount" ;
        sh:message "AAA — amount exceeded" ;
    ] .
"""


def test_multi_valued_message_selects_lexicographic_min() -> None:
    """F1: when sh:message has multiple values, the extracted message must be the
    lexicographically smallest one ('AAA…'), not dependent on hash-seed ordering."""
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 9999})]
    result = run_shacl_rule(records, _SHAPE_MULTI_MESSAGE, "multi-msg-rule", observed_at=_OBSERVED_AT)

    assert result.status == "failed", f"Expected 'failed', got {result.status!r}"
    violations = result.fields["violations"]
    assert isinstance(violations, tuple) and len(violations) >= 1

    msg = violations[0].get("message", "")
    assert msg is not None, "message must not be None for a multi-message shape"
    # Must be the lexicographically minimal message, not 'ZZZ…'
    assert str(msg).startswith("AAA"), (
        f"F1 FAIL: expected lexicographic-min message starting with 'AAA', got: {msg!r}. "
        "This means _first_value is using set-iteration order (nondeterministic)."
    )


def test_multi_valued_message_is_stable_across_calls() -> None:
    """F1: two identical calls must return the same message (cross-invocation stability)."""
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 9999})]
    r1 = run_shacl_rule(records, _SHAPE_MULTI_MESSAGE, "multi-msg-det", observed_at=_OBSERVED_AT)
    r2 = run_shacl_rule(records, _SHAPE_MULTI_MESSAGE, "multi-msg-det", observed_at=_OBSERVED_AT)

    assert r1.fields["violations"] == r2.fields["violations"], (
        "F1 FAIL: violations differ across identical calls — message selection is nondeterministic."
    )


# ---------------------------------------------------------------------------
# F2a — oversized shape TTL → status="unknown", never raise
# ---------------------------------------------------------------------------


def test_oversized_shape_returns_unknown() -> None:
    """F2a: a shape_ttl exceeding _MAX_SHAPE_BYTES must return status='unknown' (not 'failed'),
    never raise, and include an 'error' field."""
    from magi_agent.evidence import shacl_verifier as mod
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    # Build a shape larger than the cap
    padding = "# " + "x" * 1024 + "\n"
    oversized = padding * (mod._MAX_SHAPE_BYTES // len(padding.encode()) + 2)

    records = [_make_record(fields={"amount": 1})]
    result = run_shacl_rule(records, oversized, "oversized-rule", observed_at=_OBSERVED_AT)

    assert result.status == "unknown", (
        f"F2a FAIL: expected 'unknown' for oversized shape, got {result.status!r}"
    )
    assert "error" in result.fields, "error field must be set for oversized shape"
    assert result.fields.get("conforms") is None


# ---------------------------------------------------------------------------
# F2b — sh:sparql in shape → status="unknown" (DoS prevention)
# ---------------------------------------------------------------------------

_SHAPE_WITH_SPARQL = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .

magi:SparqlShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:sparql [
        a sh:SPARQLConstraint ;
        sh:message "SPARQL constraint" ;
        sh:select \"\"\"
            SELECT $this
            WHERE { $this a magi:Evidence . }
        \"\"\" ;
    ] .
"""


def test_sparql_shape_returns_unknown() -> None:
    """F2b: a shape containing sh:sparql must return status='unknown' (never execute SPARQL),
    never raise, and include an 'error' field explaining the refusal."""
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 1})]
    result = run_shacl_rule(records, _SHAPE_WITH_SPARQL, "sparql-rule", observed_at=_OBSERVED_AT)

    assert result.status == "unknown", (
        f"F2b FAIL: expected 'unknown' for sh:sparql shape, got {result.status!r}"
    )
    assert "error" in result.fields, "error field must be set when sh:sparql is detected"
    err = result.fields["error"]
    assert isinstance(err, str) and err, "error must be a non-empty string"
    assert result.fields.get("conforms") is None


# ---------------------------------------------------------------------------
# F2c — validation timeout → status="unknown", no hang
# ---------------------------------------------------------------------------


def test_timeout_returns_unknown_without_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    """F2c: if _pyshacl_validate takes longer than _VALIDATE_TIMEOUT_S, run_shacl_rule
    must return status='unknown' without hanging and without raising."""
    import magi_agent.evidence.shacl_verifier as mod

    def _slow_validate(*args: object, **kwargs: object) -> None:
        # Sleep longer than the timeout constant
        time.sleep(mod._VALIDATE_TIMEOUT_S + 2)
        # Should never reach here in the test
        raise AssertionError("timeout did not trigger")

    monkeypatch.setattr(mod, "_pyshacl_validate", _slow_validate)

    records = [_make_record(fields={"amount": 1})]
    start = time.monotonic()
    result = run_shacl_rule = mod.run_shacl_rule
    outcome = run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "timeout-rule", observed_at=_OBSERVED_AT)
    elapsed = time.monotonic() - start

    assert outcome.status == "unknown", (
        f"F2c FAIL: expected 'unknown' on timeout, got {outcome.status!r}"
    )
    assert "error" in outcome.fields, "error field must be set on timeout"
    # Must not hang: elapsed should be less than sleep duration (5+2=7s with default 5s timeout)
    assert elapsed < mod._VALIDATE_TIMEOUT_S + 2.5, (
        f"F2c FAIL: run_shacl_rule took {elapsed:.1f}s — timeout did not fire in time"
    )


# ---------------------------------------------------------------------------
# F3 — flatten_error fail-safe branch
# ---------------------------------------------------------------------------


def test_failsafe_flatten_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """F3: if evidence_records_to_graph raises, run_shacl_rule must return
    status='unknown' (not 'failed'), include error field, and never raise."""
    import magi_agent.evidence.shacl_verifier as mod

    def _exploding_flatten(*args: object, **kwargs: object) -> None:
        raise ValueError("Simulated flattening failure")

    monkeypatch.setattr(mod, "evidence_records_to_graph", _exploding_flatten)

    records = [_make_record(fields={"amount": 1})]
    result = mod.run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "flatten-err-rule", observed_at=_OBSERVED_AT)

    assert result.status == "unknown", (
        f"F3 FAIL: expected 'unknown' on flatten error, got {result.status!r}"
    )
    assert "error" in result.fields, "error field must be set for flatten_error"
    assert result.fields.get("conforms") is None


# ---------------------------------------------------------------------------
# F3 — extraction_error fail-safe branch (conforms=True/False must not leak)
# ---------------------------------------------------------------------------


def test_failsafe_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """F3: if _extract_violations raises after a real conforms=False result, run_shacl_rule
    must return status='unknown' (NOT 'failed'), include error field, and never raise.
    The conforms value must NOT leak as status='failed' — internal error overrides."""
    import magi_agent.evidence.shacl_verifier as mod

    def _exploding_extract(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated violation extraction failure")

    monkeypatch.setattr(mod, "_extract_violations", _exploding_extract)

    # Use a shape that WOULD produce conforms=False (to test conforms doesn't leak)
    records = [_make_record(fields={"amount": 4200})]
    result = mod.run_shacl_rule(records, _SHAPE_AMOUNT_MAX_3000, "extract-err-rule", observed_at=_OBSERVED_AT)

    assert result.status == "unknown", (
        f"F3 FAIL: expected 'unknown' on extraction_error (not 'failed'), got {result.status!r}. "
        "Internal errors must never leak as 'failed' (which triggers a block)."
    )
    assert "error" in result.fields, "error field must be set for extraction_error"
    assert result.fields.get("conforms") is None


# ---------------------------------------------------------------------------
# F4 — sh:Warning severity → status="failed", violations non-empty, severity key
# ---------------------------------------------------------------------------

_SHAPE_WARNING_SEVERITY = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:WarningAmountShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_amount ;
        sh:maxInclusive 100 ;
        sh:severity sh:Warning ;
        sh:message "amount should not exceed 100 (Warning)" ;
    ] .
"""


def test_warning_severity_status_failed_violations_non_empty() -> None:
    """F4: a shape with sh:severity sh:Warning that fires must yield:
    - status='failed' (conforms=False still → failed)
    - violations is non-empty (not an empty tuple)
    - each violation carries a 'severity' key == 'Warning'
    """
    from magi_agent.evidence.shacl_verifier import run_shacl_rule

    records = [_make_record(fields={"amount": 9999})]
    result = run_shacl_rule(records, _SHAPE_WARNING_SEVERITY, "warning-rule", observed_at=_OBSERVED_AT)

    assert result.status == "failed", (
        f"F4 FAIL: expected status='failed' for Warning violation, got {result.status!r}"
    )
    violations = result.fields["violations"]
    assert isinstance(violations, tuple), f"violations must be a tuple, got {type(violations)}"
    assert len(violations) >= 1, (
        "F4 FAIL: violations must be non-empty when sh:Warning shape fires. "
        "Previously _extract_violations only collected sh:Violation severity nodes, "
        "leaving violations=() for Warning/Info severity (silent block with no explanation)."
    )
    v = violations[0]
    assert "severity" in v, (
        f"F4 FAIL: violation must carry a 'severity' key, got keys: {list(v.keys())}"
    )
    assert v["severity"] == "Warning", (
        f"F4 FAIL: expected severity='Warning', got {v['severity']!r}"
    )
