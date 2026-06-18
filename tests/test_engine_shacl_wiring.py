"""Task 2.3 — engine.py pre-final SHACL wiring (TDD, RED first).

Tests exercise the pure helper ``_run_shacl_rules_for_turn`` extracted from
``magi_agent/cli/engine.py``, plus end-to-end integration at the
``execute_pre_final_verifier_bus`` seam.

Key contracts
-------------
1. flag OFF → helper returns (); run_shacl_rule not invoked; bus sees shacl_gate_enabled=False.
2. flag ON + violating rule + violating record → bus decision=="block".
3. flag ON + passing rule → bus decision=="pass" (no block).
4. flag ON + policy is None → helper returns (), no error.
5. fail-safe: malformed shape → record status=="unknown"; bus does NOT block.
"""
from __future__ import annotations

import pytest

from magi_agent.customize.verification_policy import CustomizeVerificationPolicy
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus

# ---------------------------------------------------------------------------
# Import the helper once tests are green (will fail until implemented)
# ---------------------------------------------------------------------------
from magi_agent.cli.engine import _run_shacl_rules_for_turn  # noqa: E402  # TDD: RED


# ---------------------------------------------------------------------------
# Minimal SHACL TTL fixtures
# ---------------------------------------------------------------------------

# A shape that requires every magi:Evidence node to have magi:type.
# Evidence records normally lack this property in the RDF graph, so this
# ONLY fires on an explicit test node — we use a totally custom namespace to
# ensure the shape only matches what we intend.
_VALID_SHAPE_PASS = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <http://example.org/> .

ex:AlwaysPassShape
    a sh:NodeShape ;
    sh:targetNode ex:ImaginaryNode ;
    sh:property [
        sh:path ex:requiredProp ;
        sh:minCount 1 ;
    ] .
"""

# This shape requires a minimum count on a property that our real evidence
# nodes do NOT have — but we use sh:targetClass to match nothing unless
# something is explicitly typed.  For a true "always pass" we point at a
# non-existent target class so nothing matches.
_VALID_SHAPE_NO_VIOLATION = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <http://example.org/ns#> .

ex:NeverFireShape
    a sh:NodeShape ;
    sh:targetClass ex:NeverExistClass ;
    sh:property [
        sh:path ex:prop ;
        sh:minCount 1 ;
    ] .
"""

# Malformed / un-parseable Turtle
_INVALID_SHAPE_TTL = "@@@ this is not valid turtle @@@"

# ---------------------------------------------------------------------------
# Helper: build a minimal CustomizeVerificationPolicy with one shacl rule
# ---------------------------------------------------------------------------


def _policy_with_shacl_rule(shape_ttl: str, rule_id: str = "test-rule") -> CustomizeVerificationPolicy:
    overrides = {
        "verification": {
            "custom_rules": [
                {
                    "id": rule_id,
                    "enabled": True,
                    "what": {
                        "kind": "shacl_constraint",
                        "payload": {
                            "ruleId": rule_id,
                            "shapeTtl": shape_ttl,
                        },
                    },
                }
            ]
        }
    }
    return CustomizeVerificationPolicy.from_overrides(overrides)


def _policy_empty() -> CustomizeVerificationPolicy:
    return CustomizeVerificationPolicy.from_overrides({})


# ---------------------------------------------------------------------------
# Minimal evidence records (no real tool calls needed)
# ---------------------------------------------------------------------------


def _dummy_evidence_record() -> EvidenceRecord:
    return EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=1_000_000,
        source=EvidenceSource(kind="tool_trace"),
        fields={"command": "pytest", "exitCode": 0},
    )


# ---------------------------------------------------------------------------
# Test 1 — flag OFF → helper returns ()
# ---------------------------------------------------------------------------


def test_flag_off_helper_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """When enabled=False (flag OFF), _run_shacl_rules_for_turn returns ()."""
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "0")

    policy = _policy_with_shacl_rule(_VALID_SHAPE_NO_VIOLATION)
    records = (_dummy_evidence_record(),)

    result = _run_shacl_rules_for_turn(
        policy,
        records,
        enabled=False,
        observed_at=1_000_000,
    )
    assert result == ()


# ---------------------------------------------------------------------------
# Test 2 — flag ON + passing rule → "ok" record, bus does NOT block
# ---------------------------------------------------------------------------


def test_flag_on_passing_rule_no_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag ON + shape that matches nothing → status='ok', bus decision='pass'."""
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")

    policy = _policy_with_shacl_rule(_VALID_SHAPE_NO_VIOLATION, rule_id="pass-rule")
    records = (_dummy_evidence_record(),)

    shacl_records = _run_shacl_rules_for_turn(
        policy,
        records,
        enabled=True,
        observed_at=1_000_000,
    )

    # Should have exactly one record, status "ok" (no violations)
    assert len(shacl_records) == 1
    record = shacl_records[0]
    assert hasattr(record, "status")
    assert record.status == "ok"

    # Feed to bus: shacl_gate_enabled=True but no failed records → "pass"
    bus_result = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(*records, *shacl_records),
        shacl_gate_enabled=True,
    )
    assert bus_result["decision"] == "pass"


# ---------------------------------------------------------------------------
# Test 3 — flag ON + policy is None → returns (), no error
# ---------------------------------------------------------------------------


def test_flag_on_policy_none_returns_empty() -> None:
    """When policy is None, _run_shacl_rules_for_turn returns () without error."""
    result = _run_shacl_rules_for_turn(
        None,
        (_dummy_evidence_record(),),
        enabled=True,
        observed_at=1_000_000,
    )
    assert result == ()


# ---------------------------------------------------------------------------
# Test 4 — flag ON + no rules in policy → returns (), bus unaffected
# ---------------------------------------------------------------------------


def test_flag_on_no_rules_returns_empty() -> None:
    """Policy with no shacl rules → helper returns (), gate stays off."""
    policy = _policy_empty()
    result = _run_shacl_rules_for_turn(
        policy,
        (_dummy_evidence_record(),),
        enabled=True,
        observed_at=1_000_000,
    )
    assert result == ()


# ---------------------------------------------------------------------------
# Test 5 — fail-safe: malformed shape → status="unknown", bus does NOT block
# ---------------------------------------------------------------------------


def test_fail_safe_malformed_shape_no_block() -> None:
    """Malformed shape TTL → run_shacl_rule returns status='unknown'; bus does NOT block."""
    policy = _policy_with_shacl_rule(_INVALID_SHAPE_TTL, rule_id="bad-shape")
    records = (_dummy_evidence_record(),)

    shacl_records = _run_shacl_rules_for_turn(
        policy,
        records,
        enabled=True,
        observed_at=1_000_000,
    )

    # run_shacl_rule is fail-safe: parse error → status="unknown"
    assert len(shacl_records) == 1
    record = shacl_records[0]
    assert hasattr(record, "status")
    assert record.status == "unknown"

    # Feeding unknown records to the bus with shacl_gate_enabled=True must NOT block.
    # Only status="failed" records block; "unknown" is the fail-safe pass-through.
    bus_result = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(*records, *shacl_records),
        shacl_gate_enabled=True,
    )
    assert bus_result["decision"] == "pass"
