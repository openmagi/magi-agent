from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from magi_agent.harness.presets import (
    BuiltinHarnessPreset,
    PresetHookContribution,
)


class HarnessVerifierStatus(StrEnum):
    STARTED = "started"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


_HARNESS_VERIFIER_STATUS_ADAPTER = TypeAdapter(HarnessVerifierStatus)


HarnessVerifierEventType = Literal[
    "harness.verifier.started",
    "harness.verifier.completed",
]
HarnessRuleViolationEventType = Literal["harness.rule.violation"]


class HarnessVerifierAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    event_id: str = Field(alias="eventId")
    event_type: HarnessVerifierEventType = Field(alias="eventType")
    ts: int
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    preset_key: str = Field(alias="presetKey")
    gate_name: str = Field(alias="gateName")
    verifier_name: str = Field(alias="verifierName")
    hook_name: str | None = Field(default=None, alias="hookName")
    phase: str | None = None
    hook_point: str | None = Field(default=None, alias="hookPoint")
    status: HarnessVerifierStatus
    blocking: bool | None = None
    fail_open: bool | None = Field(default=None, alias="failOpen")
    fail_closed: bool | None = Field(default=None, alias="failClosed")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    env_gates: tuple[str, ...] = Field(default=(), alias="envGates")
    config_gates: tuple[str, ...] = Field(default=(), alias="configGates")
    hard_safety: bool = Field(alias="hardSafety")
    security_critical: bool = Field(alias="securityCritical")
    default_on: bool = Field(alias="defaultOn")
    opt_out: bool = Field(alias="optOut")
    message: str | None = None
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")


class HarnessRuleViolationAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    event_id: str = Field(alias="eventId")
    event_type: HarnessRuleViolationEventType = Field(alias="eventType")
    ts: int
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    preset_key: str = Field(alias="presetKey")
    rule_name: str = Field(alias="ruleName")
    hook_name: str | None = Field(default=None, alias="hookName")
    phase: str | None = None
    hook_point: str | None = Field(default=None, alias="hookPoint")
    severity: str
    reason: str
    message: str
    blocking: bool | None = None
    fail_open: bool | None = Field(default=None, alias="failOpen")
    fail_closed: bool | None = Field(default=None, alias="failClosed")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    env_gates: tuple[str, ...] = Field(default=(), alias="envGates")
    config_gates: tuple[str, ...] = Field(default=(), alias="configGates")
    hard_safety: bool = Field(alias="hardSafety")
    security_critical: bool = Field(alias="securityCritical")
    default_on: bool = Field(alias="defaultOn")
    opt_out: bool = Field(alias="optOut")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")


def make_verifier_audit_event(
    *,
    preset: BuiltinHarnessPreset,
    gate_name: str,
    status: HarnessVerifierStatus | str,
    session_id: str,
    turn_id: str,
    ts: int,
    verifier_name: str | None = None,
    hook_name: str | None = None,
    phase: str | None = None,
    hook_point: str | None = None,
    duration_ms: int | None = None,
    message: str | None = None,
) -> HarnessVerifierAuditEvent:
    resolved_status = _HARNESS_VERIFIER_STATUS_ADAPTER.validate_python(status)
    resolved_hook_name = hook_name or _first_or_none(preset.contributed_hooks)
    resolved_hook_point = hook_point or _first_or_none(preset.hook_points)
    resolved_verifier_name = verifier_name or gate_name
    event_type: HarnessVerifierEventType = (
        "harness.verifier.started"
        if resolved_status is HarnessVerifierStatus.STARTED
        else "harness.verifier.completed"
    )

    event_id = _event_id(
        "verifier",
        event_type,
        str(ts),
        session_id,
        turn_id,
        preset.key,
        gate_name,
        resolved_verifier_name,
        resolved_status.value,
        phase or "",
        resolved_hook_point or "",
        str(duration_ms or ""),
    )

    return HarnessVerifierAuditEvent(
        event_id=event_id,
        event_type=event_type,
        ts=ts,
        session_id=session_id,
        turn_id=turn_id,
        preset_key=preset.key,
        gate_name=gate_name,
        verifier_name=resolved_verifier_name,
        hook_name=resolved_hook_name,
        phase=phase,
        hook_point=resolved_hook_point,
        status=resolved_status,
        blocking=preset.blocking,
        fail_open=preset.fail_open,
        fail_closed=_fail_closed_from_fail_open(preset.fail_open),
        timeout_ms=preset.timeout_ms,
        duration_ms=duration_ms,
        env_gates=tuple(preset.env_gates),
        config_gates=tuple(preset.config_gates),
        hard_safety=preset.hard_safety,
        security_critical=preset.security_critical,
        default_on=preset.default_on,
        opt_out=preset.opt_out,
        message=message,
        traffic_attached=False,
        execution_attached=False,
    )


def make_rule_violation_audit_event(
    *,
    preset: BuiltinHarnessPreset,
    reason: str,
    message: str,
    session_id: str,
    turn_id: str,
    ts: int,
    severity: str = "error",
    rule_name: str | None = None,
    hook_name: str | None = None,
    phase: str | None = None,
    hook_point: str | None = None,
    duration_ms: int | None = None,
) -> HarnessRuleViolationAuditEvent:
    contribution = _select_hook_contribution(preset, hook_name=hook_name)
    resolved_hook_name = hook_name or (contribution.hook if contribution else None)
    resolved_rule_name = rule_name or resolved_hook_name or preset.key
    resolved_hook_point = (
        hook_point
        or (_first_or_none(contribution.hook_points) if contribution else None)
        or _first_or_none(preset.hook_points)
    )
    blocking = contribution.blocking if contribution else preset.blocking
    fail_open = contribution.fail_open if contribution else preset.fail_open
    timeout_ms = contribution.timeout_ms if contribution else preset.timeout_ms
    env_gates = contribution.env_gates if contribution else preset.env_gates
    config_gates = contribution.config_gates if contribution else preset.config_gates

    event_id = _event_id(
        "rule-violation",
        "harness.rule.violation",
        str(ts),
        session_id,
        turn_id,
        preset.key,
        resolved_rule_name,
        severity,
        reason,
        phase or "",
        resolved_hook_point or "",
        str(duration_ms or ""),
    )

    return HarnessRuleViolationAuditEvent(
        event_id=event_id,
        event_type="harness.rule.violation",
        ts=ts,
        session_id=session_id,
        turn_id=turn_id,
        preset_key=preset.key,
        rule_name=resolved_rule_name,
        hook_name=resolved_hook_name,
        phase=phase,
        hook_point=resolved_hook_point,
        severity=severity,
        reason=reason,
        message=message,
        blocking=blocking,
        fail_open=fail_open,
        fail_closed=_fail_closed_from_fail_open(fail_open),
        timeout_ms=timeout_ms,
        duration_ms=duration_ms,
        env_gates=tuple(env_gates),
        config_gates=tuple(config_gates),
        hard_safety=preset.hard_safety,
        security_critical=preset.security_critical,
        default_on=preset.default_on,
        opt_out=preset.opt_out,
        traffic_attached=False,
        execution_attached=False,
    )


def _select_hook_contribution(
    preset: BuiltinHarnessPreset, *, hook_name: str | None
) -> PresetHookContribution | None:
    if not preset.hook_contributions:
        return None
    if hook_name is None:
        return preset.hook_contributions[0]
    for contribution in preset.hook_contributions:
        if contribution.hook == hook_name:
            return contribution
    return None


def _first_or_none(values: tuple[str, ...]) -> str | None:
    return values[0] if values else None


def _fail_closed_from_fail_open(fail_open: bool | None) -> bool | None:
    if fail_open is None:
        return None
    return not fail_open


def _event_id(*parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"harness-audit-{digest}"
