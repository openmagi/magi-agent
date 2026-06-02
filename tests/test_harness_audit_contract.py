from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.harness.audit import (
    HarnessRuleViolationAuditEvent,
    HarnessVerifierAuditEvent,
    make_rule_violation_audit_event,
    make_verifier_audit_event,
)
from magi_agent.harness.presets import builtin_preset_by_key


def test_verifier_audit_event_alias_serialization_uses_stable_started_type() -> None:
    event = make_verifier_audit_event(
        preset=builtin_preset_by_key("benchmark-verifier"),
        gate_name="benchmark-verifier",
        status="started",
        session_id="session-1",
        turn_id="turn-1",
        ts=1_710_000_000_000,
        phase="before_commit",
        duration_ms=25,
    )

    dumped = event.model_dump(by_alias=True)

    assert isinstance(event, HarnessVerifierAuditEvent)
    assert dumped["eventId"] == make_verifier_audit_event(
        preset=builtin_preset_by_key("benchmark-verifier"),
        gate_name="benchmark-verifier",
        status="started",
        session_id="session-1",
        turn_id="turn-1",
        ts=1_710_000_000_000,
        phase="before_commit",
        duration_ms=25,
    ).event_id
    assert dumped["eventType"] == "harness.verifier.started"
    assert dumped["ts"] == 1_710_000_000_000
    assert dumped["sessionId"] == "session-1"
    assert dumped["turnId"] == "turn-1"
    assert dumped["presetKey"] == "benchmark-verifier"
    assert dumped["gateName"] == "benchmark-verifier"
    assert dumped["verifierName"] == "benchmark-verifier"
    assert dumped["phase"] == "before_commit"
    assert dumped["hookPoint"] == "beforeCommit"
    assert dumped["status"] == "started"
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_benchmark_verifier_audit_event_surfaces_814_metadata() -> None:
    event = make_verifier_audit_event(
        preset=builtin_preset_by_key("benchmark-verifier"),
        gate_name="benchmark-verifier",
        status="passed",
        session_id="session-814",
        turn_id="turn-814",
        ts=1,
    )
    dumped = event.model_dump(by_alias=True)

    assert dumped["eventType"] == "harness.verifier.completed"
    assert dumped["presetKey"] == "benchmark-verifier"
    assert dumped["gateName"] == "benchmark-verifier"
    assert dumped["verifierName"] == "benchmark-verifier"
    assert dumped["envGates"] == ("MAGI_PRESET_VERIFIERS",)
    assert dumped["configGates"] == ()
    assert dumped["blocking"] is True
    assert dumped["failOpen"] is True
    assert dumped["failClosed"] is False
    assert dumped["timeoutMs"] == 65_000
    assert dumped["hardSafety"] is False
    assert dumped["securityCritical"] is False
    assert dumped["defaultOn"] is True
    assert dumped["optOut"] is True


def test_security_rule_violation_event_surfaces_hard_safety_non_opt_out_metadata() -> None:
    event = make_rule_violation_audit_event(
        preset=builtin_preset_by_key("secret-exposure"),
        reason="secret_literal_detected",
        message="Final answer attempted to expose a credential.",
        session_id="session-sec",
        turn_id="turn-sec",
        ts=2,
        severity="critical",
    )
    dumped = event.model_dump(by_alias=True)

    assert isinstance(event, HarnessRuleViolationAuditEvent)
    assert dumped["eventType"] == "harness.rule.violation"
    assert dumped["presetKey"] == "secret-exposure"
    assert dumped["ruleName"] == "builtin:secret-exposure-gate"
    assert dumped["hookName"] == "builtin:secret-exposure-gate"
    assert dumped["hookPoint"] == "beforeCommit"
    assert dumped["severity"] == "critical"
    assert dumped["reason"] == "secret_literal_detected"
    assert dumped["message"] == "Final answer attempted to expose a credential."
    assert dumped["hardSafety"] is True
    assert dumped["securityCritical"] is True
    assert dumped["defaultOn"] is True
    assert dumped["optOut"] is False
    assert dumped["blocking"] is True
    assert dumped["failOpen"] is True
    assert dumped["failClosed"] is False
    assert dumped["timeoutMs"] == 500
    assert dumped["envGates"] == ("CORE_AGENT_SECRET_EXPOSURE",)
    assert dumped["configGates"] == ()
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_path_escape_rule_violation_uses_named_before_commit_hook_metadata() -> None:
    event = make_rule_violation_audit_event(
        preset=builtin_preset_by_key("path-escape"),
        hook_name="builtin:resource-boundary-before-commit",
        reason="path_escape_detected",
        message="Commit attempted to reference a path outside the workspace.",
        session_id="session-path",
        turn_id="turn-path",
        ts=3,
    )
    dumped = event.model_dump(by_alias=True)

    assert dumped["presetKey"] == "path-escape"
    assert dumped["ruleName"] == "builtin:resource-boundary-before-commit"
    assert dumped["hookName"] == "builtin:resource-boundary-before-commit"
    assert dumped["hookPoint"] == "beforeCommit"
    assert dumped["failOpen"] is True
    assert dumped["failClosed"] is False
    assert dumped["timeoutMs"] == 2_000
    assert dumped["blocking"] is True


def test_verifier_audit_event_rejects_unknown_status_as_validation_error() -> None:
    with pytest.raises(ValidationError):
        make_verifier_audit_event(
            preset=builtin_preset_by_key("benchmark-verifier"),
            gate_name="benchmark-verifier",
            status="unknown-status",
            session_id="session-invalid",
            turn_id="turn-invalid",
            ts=3,
        )


def test_helpers_preserve_caller_supplied_status_reason_and_do_not_infer_results() -> None:
    verifier = make_verifier_audit_event(
        preset=builtin_preset_by_key("benchmark-verifier"),
        gate_name="benchmark-verifier",
        status="failed",
        session_id="session-status",
        turn_id="turn-status",
        ts=3,
        message="caller observed verifier mismatch",
    )
    skipped = make_verifier_audit_event(
        preset=builtin_preset_by_key("benchmark-verifier"),
        gate_name="benchmark-verifier",
        status="skipped",
        session_id="session-status",
        turn_id="turn-status",
        ts=3,
        message="caller skipped verifier",
    )
    rule = make_rule_violation_audit_event(
        preset=builtin_preset_by_key("secret-exposure"),
        reason="caller_reason",
        message="caller supplied message",
        session_id="session-status",
        turn_id="turn-status",
        ts=3,
        severity="warning",
    )

    assert verifier.status == "failed"
    assert verifier.message == "caller observed verifier mismatch"
    assert skipped.status == "skipped"
    assert skipped.event_type == "harness.verifier.completed"
    assert rule.reason == "caller_reason"
    assert rule.message == "caller supplied message"
    assert rule.severity == "warning"


def test_models_are_immutable_and_events_are_defensive_metadata_copies() -> None:
    source = builtin_preset_by_key("benchmark-verifier")
    event = make_verifier_audit_event(
        preset=source,
        gate_name="benchmark-verifier",
        status="error",
        session_id="session-copy",
        turn_id="turn-copy",
        ts=4,
    )

    assert event.env_gates == ("MAGI_PRESET_VERIFIERS",)
    assert isinstance(event.env_gates, tuple)
    assert isinstance(event.config_gates, tuple)

    with pytest.raises(ValidationError):
        event.env_gates = ("MUTATED",)

    custom_source = source.model_copy(update={"env_gates": ("CUSTOM_GATE",)})
    custom_event = make_verifier_audit_event(
        preset=custom_source,
        gate_name="benchmark-verifier",
        status="error",
        session_id="session-copy",
        turn_id="turn-copy",
        ts=4,
    )

    assert custom_event.env_gates == ("CUSTOM_GATE",)
    assert builtin_preset_by_key("benchmark-verifier").env_gates == (
        "MAGI_PRESET_VERIFIERS",
    )


def test_audit_module_import_does_not_load_runtime_adk_or_writer_boundaries() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.audit")
forbidden_prefixes = (
    "google.adk",
)
forbidden_modules = (
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.hooks.bus",
    "magi_agent.tools.dispatcher",
    "magi_agent.runtime.transcript",
    "magi_agent.runtime.openmagi_runtime",
)
loaded = [
    module
    for module in sys.modules
    if module.startswith(forbidden_prefixes) or module in forbidden_modules
]
if loaded:
    raise AssertionError(f"audit import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
