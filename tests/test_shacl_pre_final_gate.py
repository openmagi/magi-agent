"""Task 1.3 — SHACL consume-side pre-final gate (default-OFF).

Tests exercise the ``shacl_gate_enabled`` parameter of
``execute_pre_final_verifier_bus`` and the ``MAGI_SHACL_VERIFIER_ENABLED`` flag.

Key contracts:
- ``shacl_gate_enabled=False`` (default) → behavior byte-identical to before.
- Only records with ``type == "custom:ShaclConstraintCheck"`` AND top-level
  ``EvidenceRecord.status == "failed"`` cause a block.
- ``status == "unknown"`` (fail-safe) NEVER causes a block.
- ``status == "ok"`` NEVER causes a block.
- No shacl records present → no regression on non-shacl turns.
- ``MAGI_SHACL_VERIFIER_ENABLED`` default reads False.
"""

from __future__ import annotations

import os

import pytest

from magi_agent.config.flags import flag_bool
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shacl_record(status: str, rule_id: str = "test-rule-1") -> EvidenceRecord:
    """Construct a ShaclConstraintCheck EvidenceRecord with the given status.

    Top-level ``status`` is set directly (unlike DocumentCoverage which uses
    ``fields["status"]`` via the tool-result path). The SHACL producer sets
    top-level status="failed" on a constraint violation.
    """
    return EvidenceRecord(
        type="custom:ShaclConstraintCheck",
        status=status,  # type: ignore[arg-type]
        observedAt=1,
        source=EvidenceSource(kind="verifier", verifierName="shacl_verifier"),
        fields={
            "ruleId": rule_id,
            "conforms": status != "failed",
        },
    )


def _non_shacl_record() -> EvidenceRecord:
    return EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=1,
        source=EvidenceSource(kind="tool_trace"),
        fields={"command": "pytest", "exitCode": 0},
    )


# ---------------------------------------------------------------------------
# Test 1 — gate disabled + failed shacl record → decision unchanged (not block)
# ---------------------------------------------------------------------------


def test_gate_disabled_failed_record_does_not_block() -> None:
    """OFF=no effect: a failed ShaclConstraintCheck record is audit-only when gate is off."""
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_shacl_record("failed"),),
        shacl_gate_enabled=False,
    )

    assert bus["decision"] == "pass"
    assert not any(
        result.get("verifierId") == "shacl-constraint-verifier" for result in bus["results"]
    )


# ---------------------------------------------------------------------------
# Test 2 — gate enabled + failed shacl record → block + result names the rule
# ---------------------------------------------------------------------------


def test_gate_enabled_failed_record_blocks_and_names_rule() -> None:
    """ON + failed ShaclConstraintCheck → decision=block, result identifies the rule."""
    rule_id = "max-amount-constraint"
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_shacl_record("failed", rule_id=rule_id),),
        shacl_gate_enabled=True,
    )

    assert bus["decision"] == "block"
    assert bus["failedShaclConstraints"] >= 1
    shacl_results = [
        r for r in bus["results"] if r.get("verifierId") == "shacl-constraint-verifier"
    ]
    assert shacl_results, "Expected a shacl-constraint-verifier result"
    assert shacl_results[0]["status"] == "failed"
    # The result must identify the failing rule somewhere in the public summary or
    # retry message so operators know which constraint fired.
    combined = (
        (shacl_results[0].get("publicSummary") or "")
        + (shacl_results[0].get("retryMessage") or "")
    )
    assert rule_id in combined


# ---------------------------------------------------------------------------
# Test 3 — gate enabled + only "ok" shacl records → not block
# ---------------------------------------------------------------------------


def test_gate_enabled_ok_records_do_not_block() -> None:
    """ok status records must never cause a block even with the gate on."""
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_shacl_record("ok"),),
        shacl_gate_enabled=True,
    )

    assert bus["decision"] == "pass"
    assert bus["failedShaclConstraints"] == 0


# ---------------------------------------------------------------------------
# Test 4 — gate enabled + only "unknown" (fail-safe) records → not block
# ---------------------------------------------------------------------------


def test_gate_enabled_unknown_fail_safe_records_do_not_block() -> None:
    """Fail-safe (unknown) records MUST NEVER block — only 'failed' blocks."""
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_shacl_record("unknown"),),
        shacl_gate_enabled=True,
    )

    assert bus["decision"] == "pass"
    assert bus["failedShaclConstraints"] == 0


# ---------------------------------------------------------------------------
# Test 5 — gate enabled + no shacl records → existing behavior unchanged
# ---------------------------------------------------------------------------


def test_gate_enabled_no_shacl_records_passes_without_regression() -> None:
    """Non-shacl turn with gate enabled must pass (no regression)."""
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_non_shacl_record(),),
        shacl_gate_enabled=True,
    )

    assert bus["decision"] == "pass"
    assert bus["failedShaclConstraints"] == 0


# ---------------------------------------------------------------------------
# Test 6 — MAGI_SHACL_VERIFIER_ENABLED default reads False
# ---------------------------------------------------------------------------


def test_magi_shacl_verifier_enabled_default_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAGI_SHACL_VERIFIER_ENABLED must default to False (strict opt-in, default-OFF)."""
    monkeypatch.delenv("MAGI_SHACL_VERIFIER_ENABLED", raising=False)
    assert flag_bool("MAGI_SHACL_VERIFIER_ENABLED", env={}) is False
    # Also confirm it's a registered bool flag (not profile_bool) so it has a
    # flat False default regardless of MAGI_RUNTIME_PROFILE.
    from magi_agent.config.flags import get_flag

    spec = get_flag("MAGI_SHACL_VERIFIER_ENABLED")
    assert spec.kind == "bool"
    assert spec.default is False
