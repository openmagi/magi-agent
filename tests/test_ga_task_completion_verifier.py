"""Track 19 PR3 — task-completion verifier gates the ``general`` finalise path.

The completion verifier is deterministic and flag-gated. For the ``general``
role with ``MAGI_GA_LIVE_ENABLED`` ON, a would-be finalise whose active
contract declared required deliverable evidence (artifact / snapshot refs) is
routed to *repair* (re-enter the loop with a synthetic "you still owe X"
message) instead of finalising, unless the evidence is present in the ledger.

For any non-general role, flag-OFF, or a contract with no required deliverable
evidence, the gate is inert and finalise proceeds byte-identically to today.
"""
from __future__ import annotations

from typing import Any

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.harness.general_automation.task_completion import (
    ENFORCE_SNAPSHOT_REQUIREMENT,
    RequiredDeliverableEvidence,
    TaskCompletionVerdict,
    TaskCompletionVerifier,
    completion_repair_decision,
    required_deliverable_evidence_for_contract,
)
from magi_agent.harness.verifier_bus import (
    build_default_verifier_bus_metadata,
)
from magi_agent.recipes.first_party.general_automation.spreadsheet_contracts import (
    get_spreadsheet_operation_contract,
)
from magi_agent.runtime.turn_policy import (
    StopReasonHandlerState,
    handle_stop_reason,
)


COMPLETION_REPAIR_LIMIT = 3


class RecordingDeps:
    def __init__(self) -> None:
        self.audits: list[dict[str, Any]] = []
        self.unknowns: list[dict[str, Any]] = []

    def stage_audit_event(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        audit: dict[str, Any] = {"event": event}
        if data is not None:
            audit["data"] = data
        self.audits.append(audit)

    def log_unknown(self, raw: str | None, turn_id: str) -> None:
        self.unknowns.append({"raw": raw, "turn_id": turn_id})


def _ledger() -> EvidenceLedger:
    return EvidenceLedger(
        ledgerId="ledger-session-1-turn-1",
        sessionId="session-1",
        turnId="turn-1",
        runOn="main",
        agentRole="general",
        spawnDepth=0,
        sourceKind="tool_trace",
        producerSurface="tool_host",
    )


def _required_both() -> RequiredDeliverableEvidence:
    return RequiredDeliverableEvidence(
        requires_artifact_ref=True,
        requires_snapshot_ref=True,
    )


# ---------------------------------------------------------------------------
# Required-evidence derivation from contracts
# ---------------------------------------------------------------------------


def test_required_evidence_derived_from_spreadsheet_write_contract() -> None:
    contract = get_spreadsheet_operation_contract("spreadsheet.write")
    required = required_deliverable_evidence_for_contract(contract)
    assert required.requires_artifact_ref is True
    assert required.requires_snapshot_ref is True
    assert required.is_empty() is False


def test_required_evidence_empty_for_read_only_contract() -> None:
    contract = get_spreadsheet_operation_contract("spreadsheet.read")
    required = required_deliverable_evidence_for_contract(contract)
    assert required.requires_artifact_ref is False
    assert required.requires_snapshot_ref is False
    assert required.is_empty() is True


# ---------------------------------------------------------------------------
# Verifier verdicts (deterministic, on the real ledger)
# ---------------------------------------------------------------------------


def test_verifier_passes_when_required_refs_present() -> None:
    ledger = _ledger().append_artifact_ref(
        "artifact:spreadsheet:out",
        metadata={"snapshotRef": "snapshot:spreadsheet:src"},
    )
    verdict = TaskCompletionVerifier().evaluate(ledger, _required_both())
    assert verdict.status == "pass"
    assert verdict.missing == ()
    assert verdict.repair_message is None


def test_verifier_fails_with_repair_naming_missing_items() -> None:
    ledger = _ledger()
    verdict = TaskCompletionVerifier().evaluate(ledger, _required_both())
    assert verdict.status == "fail"
    assert verdict.action == "repair"
    assert "artifactRef" in verdict.missing
    # snapshotRef is only in missing when ENFORCE_SNAPSHOT_REQUIREMENT is True
    if ENFORCE_SNAPSHOT_REQUIREMENT:
        assert "snapshotRef" in verdict.missing
        assert verdict.repair_message is not None
        assert "snapshotRef" in verdict.repair_message
    assert verdict.repair_message is not None
    assert "artifactRef" in verdict.repair_message


def test_verifier_fails_when_only_snapshot_missing() -> None:
    ledger = _ledger().append_artifact_ref("artifact:spreadsheet:out")
    verdict = TaskCompletionVerifier().evaluate(ledger, _required_both())
    if ENFORCE_SNAPSHOT_REQUIREMENT:
        assert verdict.status == "fail"
        assert verdict.missing == ("snapshotRef",)
    else:
        # artifact is present and snapshot is not enforced → pass
        assert verdict.status == "pass"
        assert verdict.missing == ()


def test_verifier_inert_when_no_required_evidence() -> None:
    ledger = _ledger()
    required = RequiredDeliverableEvidence()
    verdict = TaskCompletionVerifier().evaluate(ledger, required)
    assert verdict.status == "pass"
    assert verdict.missing == ()


# ---------------------------------------------------------------------------
# Finalise-path wiring (a) pass → finalise
# ---------------------------------------------------------------------------


def test_general_flag_on_with_evidence_finalises() -> None:
    ledger = _ledger().append_artifact_ref(
        "artifact:spreadsheet:out",
        metadata={"snapshotRef": "snapshot:spreadsheet:src"},
    )
    deps = RecordingDeps()
    state = StopReasonHandlerState()
    messages: list[dict[str, Any]] = []

    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw="end_turn",
        blocks=[{"type": "text", "text": "done"}],
        iteration=4,
        turn_id="turn-1",
        messages=messages,
        completion_gate=gate,
    )

    assert decision.kind == "finalise"
    assert messages == []


# ---------------------------------------------------------------------------
# (b) missing → recover with synthetic "still owe" message
# ---------------------------------------------------------------------------


def test_general_flag_on_missing_evidence_recovers_with_synthetic_message() -> None:
    ledger = _ledger()
    deps = RecordingDeps()
    state = StopReasonHandlerState()
    messages: list[dict[str, Any]] = []

    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw="end_turn",
        blocks=[{"type": "text", "text": "all done"}],
        iteration=2,
        turn_id="turn-1",
        messages=messages,
        completion_gate=gate,
    )

    assert decision.kind == "recover"
    assert messages[-1]["role"] == "user"
    assert "still owe" in messages[-1]["content"]
    assert "artifactRef" in messages[-1]["content"]
    assert state.completion_repair_attempt == 1
    assert any(audit["event"] == "ga_completion_repair" for audit in deps.audits)


def test_completion_recover_preserves_assistant_text_block() -> None:
    ledger = _ledger()
    deps = RecordingDeps()
    state = StopReasonHandlerState()
    messages: list[dict[str, Any]] = []

    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )

    handle_stop_reason(
        deps,
        state,
        stop_reason_raw="end_turn",
        blocks=[{"type": "text", "text": "partial answer"}],
        iteration=1,
        turn_id="turn-1",
        messages=messages,
        completion_gate=gate,
    )

    assert messages[0] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "partial answer"}],
    }
    assert messages[-1]["role"] == "user"


# ---------------------------------------------------------------------------
# (c) bounded repair attempts → finalise with audit
# ---------------------------------------------------------------------------


def test_completion_repair_bounded_then_finalises_with_audit() -> None:
    ledger = _ledger()
    deps = RecordingDeps()
    state = StopReasonHandlerState(
        completion_repair_attempt=COMPLETION_REPAIR_LIMIT,
    )
    messages: list[dict[str, Any]] = []

    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw="end_turn",
        blocks=[{"type": "text", "text": "giving up"}],
        iteration=9,
        turn_id="turn-1",
        messages=messages,
        completion_gate=gate,
    )

    assert decision.kind == "finalise"
    assert messages == []
    assert state.completion_repair_attempt == COMPLETION_REPAIR_LIMIT
    assert any(
        audit["event"] == "ga_completion_repair_exhausted" for audit in deps.audits
    )


# ---------------------------------------------------------------------------
# (d) non-general or flag-OFF → gate inert, finalise unchanged
# ---------------------------------------------------------------------------


def test_non_general_role_gate_inert() -> None:
    ledger = _ledger()
    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="coding",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    assert gate is None


def test_flag_off_gate_inert() -> None:
    ledger = _ledger()
    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "0"},
    )
    assert gate is None


def test_flag_off_finalise_unchanged() -> None:
    ledger = _ledger()
    deps = RecordingDeps()
    state = StopReasonHandlerState()
    messages: list[dict[str, Any]] = []

    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "0"},
    )

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw="end_turn",
        blocks=[{"type": "text", "text": "done"}],
        iteration=0,
        turn_id="turn-1",
        messages=messages,
        completion_gate=gate,
    )

    assert decision.kind == "finalise"
    assert messages == []
    assert deps.audits == []


def test_no_completion_gate_finalise_unchanged() -> None:
    deps = RecordingDeps()
    state = StopReasonHandlerState()
    messages: list[dict[str, Any]] = []

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw="end_turn",
        blocks=[{"type": "text", "text": "done"}],
        iteration=0,
        turn_id="turn-1",
        messages=messages,
    )

    assert decision.kind == "finalise"
    assert messages == []
    assert deps.audits == []


# ---------------------------------------------------------------------------
# (e) contract with no required evidence → gate inert
# ---------------------------------------------------------------------------


def test_general_flag_on_no_required_evidence_finalises() -> None:
    ledger = _ledger()
    gate = completion_repair_decision(
        ledger=ledger,
        required=RequiredDeliverableEvidence(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    assert gate is None


# ---------------------------------------------------------------------------
# Verifier-bus registration (deterministic, default-OFF, ordering preserved)
# ---------------------------------------------------------------------------


def test_completion_verifier_registered_in_bus_default_off() -> None:
    bus = build_default_verifier_bus_metadata()
    by_id = {verifier.verifier_id: verifier for verifier in bus.verifiers}
    verifier = by_id["ga-task-completion"]
    assert verifier.stage == "task_plan_completion"
    assert verifier.phase == "deterministic"
    assert verifier.default_enabled is False
    assert verifier.disabled is True
    assert "repair" not in [a for a in verifier.failure_routing.actions]


def test_completion_verifier_does_not_disturb_hard_safety_ordering() -> None:
    bus = build_default_verifier_bus_metadata()
    by_id = {verifier.verifier_id: verifier for verifier in bus.verifiers}
    hard_safety = by_id["security-policy-hard-safety"]
    assert hard_safety.stage == "security_policy"
    assert hard_safety.priority == 60
    assert hard_safety.hard_safety is True
    assert hard_safety.disabled is False


# ---------------------------------------------------------------------------
# I3 — realistic EvidenceLedger population (artifact via nested metadata)
# ---------------------------------------------------------------------------


def test_verifier_passes_with_real_runtime_ledger_entry() -> None:
    """The live spreadsheet.write flow writes artifactRef inside
    metadata["localArtifactReceipt"]["artifactRef"]; no snapshotRef is
    produced yet (ENFORCE_SNAPSHOT_REQUIREMENT=False). The verifier must PASS
    because _collect_keys recurses into nested metadata and finds artifactRef,
    and snapshot enforcement is deferred.
    """
    assert ENFORCE_SNAPSHOT_REQUIREMENT is False, (
        "This test documents the current behaviour with snapshot enforcement OFF. "
        "Update it when flipping ENFORCE_SNAPSHOT_REQUIREMENT to True."
    )
    ledger = _ledger()
    # Populate ledger the way the real runtime does for a spreadsheet.write:
    # artifactRef is nested under localArtifactReceipt in metadata.
    ledger = ledger.append_artifact_ref(
        "artifact:spreadsheet:out",
        metadata={"localArtifactReceipt": {"artifactRef": "artifact:spreadsheet:out"}},
    )
    # _required_both() includes requires_snapshot_ref=True but with enforcement
    # OFF the verifier should not demand it.
    verdict = TaskCompletionVerifier().evaluate(ledger, _required_both())
    assert verdict.status == "pass", (
        f"Expected pass with real runtime ledger entry; missing={verdict.missing}"
    )
    assert verdict.missing == ()


def test_gate_passes_with_real_runtime_ledger_no_snapshot() -> None:
    """End-to-end: with flag ON and a ledger populated as the real runtime
    does (artifact ref via nested metadata, no snapshotRef), the gate returns
    None (pass) so handle_stop_reason finalises normally.
    """
    assert ENFORCE_SNAPSHOT_REQUIREMENT is False
    ledger = _ledger()
    ledger = ledger.append_artifact_ref(
        "artifact:spreadsheet:out",
        metadata={"localArtifactReceipt": {"artifactRef": "artifact:spreadsheet:out"}},
    )
    gate = completion_repair_decision(
        ledger=ledger,
        required=_required_both(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    # Gate must be None (pass) because the artifact requirement is satisfied
    # and snapshot enforcement is OFF.
    assert gate is None


# ---------------------------------------------------------------------------
# M1 — TaskCompletionVerdict invariant
# ---------------------------------------------------------------------------


def test_verdict_invariant_fail_action_pass_raises() -> None:
    """status='fail' with action='pass' violates the invariant."""
    import pytest

    with pytest.raises(ValueError, match="invariant"):
        TaskCompletionVerdict(status="fail", action="pass")


def test_verdict_invariant_pass_action_repair_raises() -> None:
    """status='pass' with action='repair' violates the invariant."""
    import pytest

    with pytest.raises(ValueError, match="invariant"):
        TaskCompletionVerdict(status="pass", action="repair")


def test_verdict_invariant_valid_combinations() -> None:
    """Both valid combinations (pass/pass and fail/repair) must construct OK."""
    v1 = TaskCompletionVerdict(status="pass")
    assert v1.status == "pass" and v1.action == "pass"

    v2 = TaskCompletionVerdict(
        status="fail",
        missing=("artifactRef",),
        action="repair",
        repair_message="owe artifactRef",
    )
    assert v2.status == "fail" and v2.action == "repair"


# ---------------------------------------------------------------------------
# M5 — stop_sequence triggers the completion gate (not only end_turn)
# ---------------------------------------------------------------------------


def test_completion_gate_fires_on_stop_sequence_missing_artifact() -> None:
    """The gate should recover on stop_reason='stop_sequence' when the
    artifact is missing — the same as end_turn.
    """
    ledger = _ledger()
    deps = RecordingDeps()
    state = StopReasonHandlerState()
    messages: list[dict[str, Any]] = []

    gate = completion_repair_decision(
        ledger=ledger,
        required=RequiredDeliverableEvidence(requires_artifact_ref=True),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    assert gate is not None, "Gate must be active when artifact is missing"

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw="stop_sequence",
        blocks=[{"type": "text", "text": "model stopped on sequence"}],
        iteration=1,
        turn_id="turn-1",
        messages=messages,
        completion_gate=gate,
    )

    assert decision.kind == "recover"
    assert messages[-1]["role"] == "user"
    assert "still owe" in messages[-1]["content"]
    assert "artifactRef" in messages[-1]["content"]
    assert state.completion_repair_attempt == 1
    assert any(audit["event"] == "ga_completion_repair" for audit in deps.audits)
