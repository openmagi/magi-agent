from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


PRIVATE_PAYLOAD = {
    "prompt": "child prompt with hidden reasoning",
    "toolArgs": {"cmd": "cat /workspace/private && echo sk-live-secret"},
    "toolResult": "raw result from /data/bots/bot-1/workspace",
    "Authorization": "Bearer live-token",
    "cookie": "sid=opaque",
}


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _assert_no_private_payload(value: object) -> None:
    dumped = _dump(value)
    for forbidden in (
        "child prompt",
        "hidden reasoning",
        "/workspace",
        "/data/bots",
        "sk-live-secret",
        "live-token",
        "opaque",
        "raw result",
    ):
        assert forbidden not in dumped


def test_request_shape_ledger_defaults_off_and_records_only_sanitized_refs() -> None:
    from magi_agent.runtime.request_ledger import (
        RequestLedgerConfig,
        RequestShapeLedger,
        RequestShapeLedgerEntry,
    )

    ledger = RequestShapeLedger()
    entry = RequestShapeLedgerEntry(
        turnId="turn-1",
        stage="model_input",
        modelInputRefs=("session://turn-1/user", "summary://compact-1"),
        toolRefs=("tool:FileRead", "tool://Search"),
        controlRefs=("control://approval-1",),
        validatorRefs=("validator:hard-safety:path",),
        checkpointRefs=("checkpoint:plan:approved",),
        evidenceRefs=("evidence://source/src-1",),
        budget={"maxContextEvents": 16, "droppedEvents": 2},
        compaction={"boundaryId": "compact-1", "summaryRef": "summary://compact-1"},
        rawPayload=PRIVATE_PAYLOAD,
    )

    result = ledger.record(entry, config=RequestLedgerConfig())

    assert result.status == "skipped"
    assert result.reason == "disabled"
    assert result.recorded is False
    assert ledger.entries == ()
    assert result.entry is None
    assert result.authority_flags.model_context_write_allowed is False
    assert result.authority_flags.tool_dispatch_allowed is False
    assert result.authority_flags.production_write_allowed is False
    assert result.authority_flags.user_visible_output_allowed is False

    enabled = ledger.record(entry, config=RequestLedgerConfig(enabled=True))

    assert enabled.status == "recorded"
    assert enabled.reason == "local_request_shape_recorded"
    assert enabled.recorded is True
    assert len(ledger.entries) == 1
    safe_entry = enabled.entry
    assert safe_entry is not None
    assert safe_entry.model_input_refs == ("session://turn-1/user", "summary://compact-1")
    assert safe_entry.tool_refs == ("tool:FileRead", "tool://Search")
    assert safe_entry.control_refs == ("control://approval-1",)
    assert safe_entry.validator_refs == ("validator:hard-safety:path",)
    assert safe_entry.checkpoint_refs == ("checkpoint:plan:approved",)
    assert safe_entry.evidence_refs == ("evidence://source/src-1",)
    assert safe_entry.redaction_count >= 1
    assert safe_entry.raw_payload is None
    assert safe_entry.public_preview["rawPayload"] == "[redacted]"
    assert safe_entry.public_preview["budget"] == {
        "droppedEvents": 2,
        "maxContextEvents": 16,
    }
    _assert_no_private_payload(enabled.model_dump(by_alias=True))


def test_request_shape_ledger_rejects_unsafe_refs_and_keeps_diagnostics() -> None:
    from magi_agent.runtime.request_ledger import (
        RequestLedgerConfig,
        RequestShapeLedger,
        RequestShapeLedgerEntry,
    )

    entry = RequestShapeLedgerEntry(
        turnId="turn-unsafe",
        stage="tool_request",
        modelInputRefs=("file:///workspace/private", "session://safe-turn"),
        toolRefs=("tool://SafeTool", "https://evil.test/tool"),
        controlRefs=("control://approval-1", "control://memory://private/root"),
        validatorRefs=("validator:hard-safety:path", "validator://sk-live-secret"),
        checkpointRefs=("checkpoint:workspace:dry-run", "/Users/kevin/private"),
        evidenceRefs=("evidence://safe/src-1", "evidence://file:///tmp/private"),
    )

    result = RequestShapeLedger().record(
        entry,
        config=RequestLedgerConfig(enabled=True),
    )

    assert result.recorded is True
    assert result.entry is not None
    assert result.entry.model_input_refs == ("session://safe-turn",)
    assert result.entry.tool_refs == ("tool://SafeTool",)
    assert result.entry.control_refs == ("control://approval-1",)
    assert result.entry.validator_refs == ("validator:hard-safety:path",)
    assert result.entry.checkpoint_refs == ("checkpoint:workspace:dry-run",)
    assert result.entry.evidence_refs == ("evidence://safe/src-1",)
    assert {
        "unsafe_model_input_ref_rejected",
        "unsafe_tool_ref_rejected",
        "unsafe_control_ref_rejected",
        "unsafe_validator_ref_rejected",
        "unsafe_checkpoint_ref_rejected",
        "unsafe_evidence_ref_rejected",
    }.issubset(set(result.diagnostics.reason_codes))
    _assert_no_private_payload(result.model_dump(by_alias=True))


def test_request_shape_ledger_rejects_private_home_and_kubelet_uri_refs() -> None:
    from magi_agent.runtime.request_ledger import (
        RequestLedgerConfig,
        RequestShapeLedger,
        RequestShapeLedgerEntry,
    )

    result = RequestShapeLedger().record(
        RequestShapeLedgerEntry(
            turnId="turn-private-uri",
            stage="evidence_attach",
            evidenceRefs=(
                "evidence://host/home/kevin/.ssh/id_rsa",
                "evidence://host/var/lib/kubelet/pods/x",
                "evidence://safe/src-1",
            ),
        ),
        config=RequestLedgerConfig(enabled=True),
    )
    dumped = result.model_dump(by_alias=True)

    assert result.entry is not None
    assert result.entry.evidence_refs == ("evidence://safe/src-1",)
    assert "unsafe_evidence_ref_rejected" in result.diagnostics.reason_codes
    assert "/home/kevin" not in str(dumped)
    assert "/var/lib/kubelet" not in str(dumped)


@pytest.mark.parametrize(
    ("recipe_policy", "validator_status", "expected_action"),
    (
        ("fail_closed", "blocked", "block"),
        ("fail_open_to_typescript", "blocked", "restore_typescript"),
        ("audit_only", "blocked", "audit"),
        ("fail_closed", "passed", "continue"),
    ),
)
def test_runtime_control_decision_models_fail_policy_without_side_effects(
    recipe_policy: str,
    validator_status: str,
    expected_action: str,
) -> None:
    from magi_agent.runtime.request_ledger import build_runtime_control_decision

    decision = build_runtime_control_decision(
        recipePolicy=recipe_policy,
        validatorStatus=validator_status,
        approvalStatus="not_required",
        evidenceRefs=("evidence://validator/path-1",),
    )

    assert decision.action == expected_action
    assert decision.evidence_refs == ("evidence://validator/path-1",)
    assert decision.authority_flags.model_context_write_allowed is False
    assert decision.authority_flags.tool_dispatch_allowed is False
    assert decision.authority_flags.production_write_allowed is False
    assert decision.authority_flags.route_activation_allowed is False


def test_approval_gate_result_records_pending_resolved_denied_and_timeout_states() -> None:
    from magi_agent.runtime.request_ledger import ApprovalGateResult

    pending = ApprovalGateResult(
        requestId="control-1",
        status="pending",
        controlRefs=("control://control-1",),
    )
    approved = pending.resolve(decision="approved", evidenceRefs=("evidence://approval-1",))
    denied = pending.resolve(decision="denied", evidenceRefs=("evidence://denial-1",))
    timed_out = pending.timeout()

    assert pending.execute_allowed is False
    assert approved.status == "approved"
    assert approved.execute_allowed is False
    assert denied.status == "denied"
    assert denied.execute_allowed is False
    assert timed_out.status == "timed_out"
    assert timed_out.execute_allowed is False
    assert approved.control_refs == ("control://control-1",)
    assert approved.evidence_refs == ("evidence://approval-1",)


def test_approval_gate_result_cannot_forge_execute_authority_or_private_ids() -> None:
    from magi_agent.runtime.request_ledger import ApprovalGateResult

    forged = ApprovalGateResult(
        requestId="tool-permission:turn-/workspace/private-sk-live-secret:FileWrite",
        status="approved",
        controlRefs=(
            "control://safe-approval",
            "control://memory://private/root",
        ),
        executeAllowed=True,
    )
    dumped = forged.model_dump(by_alias=True)

    assert forged.execute_allowed is False
    assert forged.request_id.startswith("control:")
    assert forged.control_refs == ("control://safe-approval",)
    assert "/workspace/private" not in str(dumped)
    assert "sk-live-secret" not in str(dumped)
    assert "memory://private" not in str(dumped)


def test_request_ledger_redacts_private_turn_ids() -> None:
    from magi_agent.runtime.request_ledger import (
        RequestLedgerConfig,
        RequestShapeLedger,
        RequestShapeLedgerEntry,
    )

    result = RequestShapeLedger().record(
        RequestShapeLedgerEntry(
            turnId="turn-/workspace/private-sk-live-secret",
            stage="model_input",
            modelInputRefs=("session://safe-turn",),
        ),
        config=RequestLedgerConfig(enabled=True),
    )
    dumped = result.model_dump(by_alias=True)

    assert result.entry is not None
    assert result.entry.turn_id == "turn:redacted"
    assert dumped["entry"]["turnId"] == "turn:redacted"
    assert "/workspace/private" not in str(dumped)
    assert "sk-live-secret" not in str(dumped)


def test_authority_flags_cannot_be_enabled_by_construct_copy_or_payload() -> None:
    from magi_agent.runtime.request_ledger import (
        RequestLedgerAuthorityFlags,
        RequestShapeLedgerResult,
    )

    flags = RequestLedgerAuthorityFlags.model_construct(
        modelContextWriteAllowed=True,
        toolDispatchAllowed=True,
        productionWriteAllowed=True,
        userVisibleOutputAllowed=True,
    )
    copied = flags.model_copy(update={"toolDispatchAllowed": True})

    assert flags.model_context_write_allowed is False
    assert flags.tool_dispatch_allowed is False
    assert flags.production_write_allowed is False
    assert flags.user_visible_output_allowed is False
    assert copied.tool_dispatch_allowed is False

    result = RequestShapeLedgerResult.model_validate(
        {
            "status": "recorded",
            "reason": "local_request_shape_recorded",
            "recorded": True,
            "entry": None,
            "authorityFlags": {
                "modelContextWriteAllowed": True,
                "toolDispatchAllowed": True,
                "productionWriteAllowed": True,
                "routeActivationAllowed": True,
                "userVisibleOutputAllowed": True,
            },
        }
    )
    assert result.authority_flags.model_context_write_allowed is False
    assert result.authority_flags.route_activation_allowed is False

    with pytest.raises(ValidationError):
        RequestLedgerAuthorityFlags.model_validate({"unexpectedAuthority": True})


def test_request_ledger_import_boundary_avoids_live_runtime_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

before = set(sys.modules)
module = importlib.import_module("magi_agent.runtime.request_ledger")
assert hasattr(module, "RequestShapeLedger")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "fastapi",
    "uvicorn",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
    "httpx",
    "requests",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.transport.chat",
    "magi_agent.transport.sse",
    "magi_agent.memory.adk_bridge",
    "magi_agent.workspace.adoption_boundary",
)
loaded = [
    name
    for name in set(sys.modules) - before
    if name in forbidden_exact
    or any(name.startswith(f"{prefix}.") for prefix in forbidden_exact)
]
if loaded:
    raise AssertionError(f"request ledger loaded forbidden modules: {loaded}")
""",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
