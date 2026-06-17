"""Task 2 — completion gate unions live-selected recipe obligations.

Drives ``MagiEngineDriver._pre_final_gate_payload`` directly (sync, no ADK,
no model keys) with a baseline assembly that has NO coding obligation:
  (a) With flag ON + live_selected_pack_ids=("openmagi.dev-coding",) and
      coding_mutation_observed=True → gate BLOCKS; missingValidators includes
      the dev-coding test-evidence validator.
  (b) With live_selected_pack_ids=() → payload unchanged (no new block).

Pattern mirrors test_user_validator_enforces_end_to_end.py.
"""
from __future__ import annotations

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly


_DEV_CODING_TEST_EVIDENCE_REF = "verifier:dev-coding:test-evidence"


def _non_coding_assembly() -> RunnerPolicyAssembly:
    """A baseline assembly with a generic (non-dev-coding) pack — gate always applies."""
    return RunnerPolicyAssembly(
        modelProvider="local",
        modelLabel="local-dev",
        selectedPackIds=("openmagi.general-automation",),  # NOT dev-coding
        evidenceRequirements=(),
        requiredValidators=(),
        missingEvidenceAction="audit",
    )


def test_live_coding_recipe_blocks_when_mutation_observed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag ON + live dev-coding selection + file mutation → gate must block."""
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "true")

    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_non_coding_assembly(),
        evidence_collector=lambda _turn: (),  # nothing observed
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="implement the feature",
        harness_state=None,
        observed_public_refs=set(),
        coding_mutation_observed=True,
        live_selected_pack_ids=("openmagi.dev-coding",),
    )
    assert payload is not None, "Gate should apply when dev-coding is live-selected + mutation observed"
    assert payload["decision"] == "block", f"Expected block, got: {payload['decision']}"
    assert _DEV_CODING_TEST_EVIDENCE_REF in payload["missingValidators"], (
        f"Expected {_DEV_CODING_TEST_EVIDENCE_REF!r} in missingValidators, got: {payload['missingValidators']}"
    )


def test_live_empty_selection_payload_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag ON but live_selected_pack_ids=() → payload identical to baseline (no block)."""
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "true")

    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_non_coding_assembly(),
        evidence_collector=lambda _turn: (),
    )
    # With no live selection and a non-coding assembly, the gate applies
    # (selectedPackIds != dev-coding → _pre_final_gate_applies returns True)
    # but neither missing_evidence nor missing_validators should block (both empty).
    payload_empty = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="please produce a final answer",
        harness_state=None,
        observed_public_refs=set(),
        live_selected_pack_ids=(),
    )
    # Non-coding pack with no requirements → should pass (or None if gate doesn't apply)
    # The assembly has no required_validators/evidence_requirements → decision = pass
    if payload_empty is not None:
        assert payload_empty["decision"] == "pass", (
            f"Empty live selection should not block; got: {payload_empty['decision']}"
        )
        assert _DEV_CODING_TEST_EVIDENCE_REF not in payload_empty.get("missingValidators", []), (
            "Empty live selection must not inject dev-coding validator"
        )


def test_flag_off_no_live_selection_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag OFF → live_selected_pack_ids treated as () → gate unchanged from baseline."""
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "false")

    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_non_coding_assembly(),
        evidence_collector=lambda _turn: (),
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="implement the feature",
        harness_state=None,
        observed_public_refs=set(),
        coding_mutation_observed=True,
        live_selected_pack_ids=("openmagi.dev-coding",),  # should be ignored when flag OFF
    )
    # With flag OFF the live selection is NOT applied. The non-coding assembly has
    # no required_validators, so the gate should pass (not block).
    if payload is not None:
        assert payload["decision"] == "pass", (
            f"Flag OFF: live selection must not inject obligations; got: {payload['decision']}"
        )
