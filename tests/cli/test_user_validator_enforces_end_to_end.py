"""Task 3.5 — capstone: a USER validator's ref actually enforces at the live gate.

Drives the real ``MagiEngineDriver._pre_final_gate_payload`` (fake-model, no keys):
the user validator ref is routed into ``required_validators`` via the SAME merge
helper production uses (Task 3.3). Assert:
  (a) tool emits nothing  -> gate ``block`` (ref in missingValidators);
  (b) tool emits the ref  -> gate ``pass``  (ref folded into observed refs).

Adapted to the real ABI: the validator ref uses the live public-ref prefix
``verifier:`` (``validator:`` is NOT recognized by ``harness/verifier_bus``), and a
non-dev-coding pack id makes ``_pre_final_gate_applies`` return True every turn.
"""
from __future__ import annotations

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.cli.real_runner import _merge_pack_validator_refs

_USER_REF = "verifier:userQuote@1"


def _assembly_with_user_validator() -> RunnerPolicyAssembly:
    required_validators = _merge_pack_validator_refs((), (_USER_REF,))
    return RunnerPolicyAssembly(
        modelProvider="local",
        modelLabel="local-dev",
        selectedPackIds=("user.quote",),  # non-dev-coding -> gate applies every turn
        evidenceRequirements=(),
        requiredValidators=required_validators,
        missingEvidenceAction="audit",
    )


def _tool_record_emitting(ref: str) -> dict[str, object]:
    # A tool-trace mapping; verifier_bus._collect_public_refs recurses into the
    # mapping values and folds any verifier:-prefixed string into matchedRefs.
    return {
        "type": "ToolResult",
        "status": "ok",
        "metadata": {"validatorRefs": [ref], "evidenceRefs": []},
    }


def test_user_validator_blocks_when_not_observed() -> None:
    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_assembly_with_user_validator(),
        evidence_collector=lambda _turn: (),  # tool emitted nothing
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="please produce a final answer",
        harness_state=None,
        observed_public_refs=set(),
    )
    assert payload is not None
    assert payload["decision"] == "block"
    assert _USER_REF in payload["missingValidators"]


def test_user_validator_passes_when_tool_emits_ref() -> None:
    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_assembly_with_user_validator(),
        evidence_collector=lambda _turn: (_tool_record_emitting(_USER_REF),),
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="please produce a final answer",
        harness_state=None,
        observed_public_refs=set(),
    )
    assert payload is not None
    assert payload["decision"] == "pass"
    assert _USER_REF not in payload["missingValidators"]
