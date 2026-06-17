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
_RESEARCH_VALIDATOR_REF = "verifier:sourceOpened@1"
_RESEARCH_EVIDENCE_REF = "evidence:research:sourceQuote"


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


def _research_assembly() -> RunnerPolicyAssembly:
    """A POPULATED non-coding baseline: a research validator + evidence ref.

    Exercises the suppression scenario the empty baseline never could: when the
    model live-selects dev-coding on a no-mutation turn, the union must NOT drop
    these research obligations (the binding "additive union only" constraint).
    """
    return RunnerPolicyAssembly(
        modelProvider="local",
        modelLabel="local-dev",
        selectedPackIds=("openmagi.deep-research",),  # NOT dev-coding
        evidenceRequirements=(_RESEARCH_EVIDENCE_REF,),
        requiredValidators=(_RESEARCH_VALIDATOR_REF,),
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


# ---------------------------------------------------------------------------
# Issue-1 regression: POPULATED non-coding (research) baseline. The empty
# baseline above never exercised the suppression path; these do.
# ---------------------------------------------------------------------------


def test_research_baseline_live_coding_no_mutation_enforces_research_not_coding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row: research baseline + LIVE dev-coding + NO mutation.

    The live dev-coding selection must NOT suppress the gate (which would drop
    the research baseline). The gate stays applied and BLOCKS on the unmet
    research obligation — but the dev-coding test-evidence validator must NOT be
    listed (no code was mutated ⇒ nothing coding to verify).
    """
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "true")

    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda _turn: (),  # nothing observed ⇒ research unmet
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="research the market landscape",  # non-coding prompt
        harness_state=None,
        observed_public_refs=set(),
        coding_mutation_observed=False,
        live_selected_pack_ids=("openmagi.dev-coding",),
    )
    assert payload is not None, "Research baseline must keep the gate applied"
    assert payload["decision"] == "block", (
        f"Expected block on unmet research obligation, got: {payload['decision']}"
    )
    missing = list(payload["missingValidators"]) + list(payload["missingEvidence"])
    assert _RESEARCH_VALIDATOR_REF in payload["missingValidators"], (
        f"Research validator must still be enforced; missing={payload['missingValidators']}"
    )
    assert _RESEARCH_EVIDENCE_REF in payload["missingEvidence"], (
        f"Research evidence must still be enforced; missing={payload['missingEvidence']}"
    )
    assert _DEV_CODING_TEST_EVIDENCE_REF not in missing, (
        f"No-mutation turn must NOT require dev-coding validator; missing={missing}"
    )


def test_research_baseline_live_coding_with_mutation_requires_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row: research baseline + LIVE dev-coding + mutation.

    Blocks AND requires the dev-coding test-evidence validator (additive union):
    the research obligation is still enforced too.
    """
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "true")

    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda _turn: (),
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="implement the fix",  # coding prompt + mutation ⇒ gate applies
        harness_state=None,
        observed_public_refs=set(),
        coding_mutation_observed=True,
        live_selected_pack_ids=("openmagi.dev-coding",),
    )
    assert payload is not None
    assert payload["decision"] == "block", f"Expected block, got: {payload['decision']}"
    assert _DEV_CODING_TEST_EVIDENCE_REF in payload["missingValidators"], (
        f"Mutation turn must require dev-coding validator; missing={payload['missingValidators']}"
    )
    assert _RESEARCH_VALIDATOR_REF in payload["missingValidators"], (
        "Research baseline must remain enforced (additive union)"
    )


def test_research_baseline_no_live_selection_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row: research baseline + empty live selection → only research enforced.

    Confirms the OFF-path / empty-selection profile path is unchanged: blocks on
    research, never injects the dev-coding validator.
    """
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "true")

    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda _turn: (),
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="research the market landscape",
        harness_state=None,
        observed_public_refs=set(),
        coding_mutation_observed=False,
        live_selected_pack_ids=(),
    )
    assert payload is not None
    assert payload["decision"] == "block", "Research obligation unmet ⇒ block"
    assert _RESEARCH_VALIDATOR_REF in payload["missingValidators"]
    assert _DEV_CODING_TEST_EVIDENCE_REF not in payload["missingValidators"], (
        "Empty live selection must not inject dev-coding validator"
    )
