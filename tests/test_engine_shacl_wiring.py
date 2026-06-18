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
6. real violation (amount=4200 > maxInclusive 3000) → status="failed" AND bus decision="block".
7. MAGI_CUSTOMIZE_VERIFICATION_ENABLED OFF → wiring reads no store, produces no block.
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


# ---------------------------------------------------------------------------
# Test 6 — real violation → status="failed" AND bus decision="block"
# (Finding 2: genuine end-to-end integration test with a real violating record)
# ---------------------------------------------------------------------------

# Reuse the shape from test_shacl_verifier.py: sh:maxInclusive 3000 on magi:field_amount.
# An EvidenceRecord with fields={"amount": 4200} violates this shape.
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


def _violating_evidence_record() -> EvidenceRecord:
    """An EvidenceRecord with amount=4200 that violates sh:maxInclusive 3000."""
    return EvidenceRecord(
        type="Calculation",
        status="ok",
        observedAt=1_718_000_000,
        source=EvidenceSource(kind="verifier"),
        fields={"amount": 4200},
    )


def test_real_violation_status_failed_and_bus_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 2 — genuine integration test:
    A real violating record (amount=4200 > maxInclusive 3000) passed through
    _run_shacl_rules_for_turn must produce a record with status='failed', and
    feeding that into execute_pre_final_verifier_bus with shacl_gate_enabled=True
    must produce decision='block'.
    """
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")

    policy = _policy_with_shacl_rule(_SHAPE_AMOUNT_MAX_3000, rule_id="amount-max-rule")
    records = (_violating_evidence_record(),)

    shacl_records = _run_shacl_rules_for_turn(
        policy,
        records,
        enabled=True,
        observed_at=1_718_000_000,
    )

    # Must produce exactly one SHACL record
    assert len(shacl_records) == 1, f"Expected 1 shacl record, got {len(shacl_records)}"
    shacl_record = shacl_records[0]

    # The record must report status="failed" (real violation, not unknown)
    assert hasattr(shacl_record, "status"), "shacl_record must have a 'status' attribute"
    assert shacl_record.status == "failed", (
        f"Expected status='failed' for amount=4200 vs maxInclusive 3000, "
        f"got status={shacl_record.status!r}"
    )

    # Feeding the violation into the bus with shacl_gate_enabled=True must block
    bus_result = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(*records, *shacl_records),
        shacl_gate_enabled=True,
    )
    assert bus_result["decision"] == "block", (
        f"Expected decision='block' when a shacl_record with status='failed' is present, "
        f"got decision={bus_result['decision']!r}"
    )


# ---------------------------------------------------------------------------
# Test 7 — MAGI_CUSTOMIZE_VERIFICATION_ENABLED OFF → no store load, no block
# (Finding 1: the dual-gate guard added in the engine wiring)
# ---------------------------------------------------------------------------

# Import the engine-level helper that loads the SHACL policy.
# This is extracted from _pre_final_gate_payload for testability (TDD: RED until implemented).
from magi_agent.cli.engine import _load_shacl_policy_if_enabled  # noqa: E402  # TDD: RED F1


def test_customize_verification_disabled_prevents_store_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 1 — when MAGI_CUSTOMIZE_VERIFICATION_ENABLED is OFF, the engine wiring
    must NOT load the store and must NOT produce any SHACL block, even if
    MAGI_SHACL_VERIFIER_ENABLED is ON.

    Calls _load_shacl_policy_if_enabled (the extracted engine helper) directly with:
    (a) MAGI_SHACL_VERIFIER_ENABLED=1 (shacl flag ON)
    (b) MAGI_CUSTOMIZE_VERIFICATION_ENABLED=0 (customize gate OFF)
    (c) load_overrides patched to raise → assert it is never invoked
    (d) Assert returned (shacl_enabled, policy) == (False, None) — no store load occurred
    (e) Assert _run_shacl_rules_for_turn with enabled=False returns () → no block
    """
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")

    # Patch load_overrides to explode if called — it must NOT be called
    import magi_agent.customize.store as _store_mod

    def _must_not_be_called(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "load_overrides was called even though MAGI_CUSTOMIZE_VERIFICATION_ENABLED is OFF. "
            "The engine wiring must gate on BOTH flags before reading the store."
        )

    monkeypatch.setattr(_store_mod, "load_overrides", _must_not_be_called)

    # Call the extracted engine wiring helper directly.
    # With MAGI_CUSTOMIZE_VERIFICATION_ENABLED=0, it must return (False, None)
    # WITHOUT calling load_overrides.
    shacl_enabled, _shacl_policy = _load_shacl_policy_if_enabled()

    assert shacl_enabled is False, (
        f"Finding 1 FAIL: shacl_enabled must be False when MAGI_CUSTOMIZE_VERIFICATION_ENABLED=0, "
        f"got shacl_enabled={shacl_enabled!r}. The engine wiring must gate on BOTH flags."
    )
    assert _shacl_policy is None, (
        f"Finding 1 FAIL: policy must be None when customize gate is OFF, got {_shacl_policy!r}"
    )

    # With shacl_enabled=False, the helper returns () → no SHACL records → no block
    records = (_violating_evidence_record(),)
    shacl_records = _run_shacl_rules_for_turn(
        _shacl_policy,
        records,
        enabled=shacl_enabled,
        observed_at=1_718_000_000,
    )
    assert shacl_records == (), (
        f"Expected () when MAGI_CUSTOMIZE_VERIFICATION_ENABLED is OFF, "
        f"got {shacl_records!r}"
    )

    # Bus sees shacl_gate_enabled=False → no block even with a violating record
    bus_result = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=records,
        shacl_gate_enabled=False,
    )
    assert bus_result["decision"] == "pass", (
        f"Expected decision='pass' when shacl_gate_enabled=False (customize gate OFF), "
        f"got decision={bus_result['decision']!r}"
    )


def test_both_flags_on_enables_store_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 1 (positive path) — when BOTH flags are ON, _load_shacl_policy_if_enabled
    returns (True, policy) and load_overrides IS called (the positive case).
    Uses an empty overrides dict (no rules) so no actual SHACL validation occurs.
    """
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")

    # Patch load_overrides to return empty overrides (no SHACL rules — safe)
    import magi_agent.customize.store as _store_mod

    call_count = [0]

    def _empty_overrides() -> dict:  # type: ignore[return]
        call_count[0] += 1
        return {}

    monkeypatch.setattr(_store_mod, "load_overrides", _empty_overrides)

    shacl_enabled, policy = _load_shacl_policy_if_enabled()

    assert shacl_enabled is True, (
        f"Finding 1 positive FAIL: shacl_enabled must be True when both flags are ON, "
        f"got {shacl_enabled!r}"
    )
    assert call_count[0] == 1, (
        f"load_overrides must be called exactly once when both flags are ON, "
        f"called {call_count[0]} times"
    )
    assert policy is not None, "policy must not be None when both flags are ON"
