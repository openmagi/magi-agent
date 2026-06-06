"""Tests for SmartApprove integration in RulesPermissionGate.

TDD — written before the implementation.
Covers:
  - Explicit deny is NEVER overridden by classifier
  - Rule-miss ask + classifier True -> allow
  - Rule-miss ask + classifier False -> falls to _race
  - smart_approve=None -> identical behavior to today (byte-identical)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.cli.contracts import (
    ControlRequest,
    PermissionDecision,
    PermissionUpdate,
)
from magi_agent.cli.permissions import RulesEngine, RulesPermissionGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(
    tool_name: str = "TestTool",
    arguments: dict | None = None,
) -> ControlRequest:
    return ControlRequest(
        requestId=f"req:{tool_name}",
        turnId="turn-1",
        toolName=tool_name,
        arguments=arguments or {},
        reason="tool_use",
    )


class _AlwaysAllowClassifier:
    """Fake classifier that always says read-only (returns True)."""

    async def classify(self, req: ControlRequest) -> bool:
        return True


class _AlwaysDenyClassifier:
    """Fake classifier that always says NOT read-only (returns False)."""

    async def classify(self, req: ControlRequest) -> bool:
        return False


class _RecordingClassifier:
    """Records calls and returns a configurable verdict."""

    def __init__(self, verdict: bool = True) -> None:
        self.verdict = verdict
        self.calls: list[ControlRequest] = []

    async def classify(self, req: ControlRequest) -> bool:
        self.calls.append(req)
        return self.verdict


class _CapturingSink:
    """Fake PromptSink that resolves with a configurable decision."""

    def __init__(self, decision: PermissionDecision | None = None) -> None:
        self._decision = decision or PermissionDecision(kind="deny")
        self.calls: list[ControlRequest] = []

    async def ask(self, req: ControlRequest) -> PermissionDecision:
        self.calls.append(req)
        return self._decision


# ---------------------------------------------------------------------------
# smart_approve=None -> identical to today (no classifier consulted)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_smart_approve_none_allows_with_allow_rule() -> None:
    """With smart_approve=None, allow rule -> allow (unchanged behavior)."""
    rules = RulesEngine(
        default_rules=[PermissionUpdate(tool="TestTool", matcher="*", decision="allow")]
    )
    gate = RulesPermissionGate(rules=rules)  # smart_approve defaults to None
    decision = await gate.check(_req("TestTool"))

    assert decision.kind == "allow"


@pytest.mark.asyncio
async def test_smart_approve_none_denies_with_deny_rule() -> None:
    """With smart_approve=None, deny rule -> deny (unchanged behavior)."""
    rules = RulesEngine(
        default_rules=[PermissionUpdate(tool="DenyTool", matcher="*", decision="deny")]
    )
    gate = RulesPermissionGate(rules=rules)
    decision = await gate.check(_req("DenyTool"))

    assert decision.kind == "deny"


@pytest.mark.asyncio
async def test_smart_approve_none_rule_miss_falls_to_race_no_sinks() -> None:
    """With smart_approve=None, rule-miss -> race with no sinks -> deny (unchanged)."""
    gate = RulesPermissionGate(rules=RulesEngine())  # no rules, no sinks, smart_approve=None
    decision = await gate.check(_req("UnknownTool"))

    assert decision.kind == "deny"


# ---------------------------------------------------------------------------
# Explicit deny is NEVER overridden
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_deny_rule_is_never_overridden_by_classifier() -> None:
    """An explicit deny rule must not be recovered by SmartApprove."""
    rules = RulesEngine(
        default_rules=[PermissionUpdate(tool="DangerTool", matcher="*", decision="deny")]
    )
    classifier = _AlwaysAllowClassifier()  # would return True if consulted
    gate = RulesPermissionGate(rules=rules, smart_approve=classifier)
    decision = await gate.check(_req("DangerTool"))

    assert decision.kind == "deny"


@pytest.mark.asyncio
async def test_explicit_deny_rule_classifier_never_called() -> None:
    """Classifier is NOT called when there is an explicit deny rule."""
    rules = RulesEngine(
        default_rules=[PermissionUpdate(tool="DangerTool", matcher="*", decision="deny")]
    )
    recording = _RecordingClassifier(verdict=True)
    gate = RulesPermissionGate(rules=rules, smart_approve=recording)
    await gate.check(_req("DangerTool"))

    assert len(recording.calls) == 0  # NEVER consulted on deny


# ---------------------------------------------------------------------------
# Rule-miss ask + classifier True -> allow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rule_miss_with_classifier_true_returns_allow() -> None:
    """Rule miss (no rule for tool) + classifier True -> allow, no sink needed."""
    gate = RulesPermissionGate(
        rules=RulesEngine(),  # no rules
        smart_approve=_AlwaysAllowClassifier(),
    )
    decision = await gate.check(_req("UnknownReadOnlyTool"))

    assert decision.kind == "allow"


@pytest.mark.asyncio
async def test_rule_miss_with_classifier_true_skips_race() -> None:
    """Classifier True -> allow returned BEFORE the sink race is invoked."""
    sink = _CapturingSink(PermissionDecision(kind="deny"))
    gate = RulesPermissionGate(
        rules=RulesEngine(),
        sinks=[sink],
        smart_approve=_AlwaysAllowClassifier(),
    )
    decision = await gate.check(_req("UnknownReadOnlyTool"))

    assert decision.kind == "allow"
    assert len(sink.calls) == 0  # sink was NOT asked


# ---------------------------------------------------------------------------
# Rule-miss ask + classifier False -> falls to _race
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rule_miss_with_classifier_false_falls_to_race() -> None:
    """Rule miss + classifier False -> falls to sink race (normal ask path)."""
    sink = _CapturingSink(PermissionDecision(kind="allow"))
    gate = RulesPermissionGate(
        rules=RulesEngine(),
        sinks=[sink],
        smart_approve=_AlwaysDenyClassifier(),
    )
    decision = await gate.check(_req("UnknownMutatingTool"))

    assert decision.kind == "allow"  # sink resolved it
    assert len(sink.calls) == 1  # sink WAS asked


@pytest.mark.asyncio
async def test_rule_miss_with_classifier_false_no_sinks_denies() -> None:
    """Rule miss + classifier False + no sinks -> safe deny."""
    gate = RulesPermissionGate(
        rules=RulesEngine(),
        sinks=[],  # no sinks
        smart_approve=_AlwaysDenyClassifier(),
    )
    decision = await gate.check(_req("UnknownMutatingTool"))

    assert decision.kind == "deny"


# ---------------------------------------------------------------------------
# smart_approve=None vs smart_approve=classifier: byte-identical for allow/deny
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_allow_rule_smart_approve_classifier_not_consulted() -> None:
    """An explicit allow rule -> classifier is NOT consulted even with smart_approve set."""
    recording = _RecordingClassifier(verdict=False)
    rules = RulesEngine(
        default_rules=[PermissionUpdate(tool="SafeTool", matcher="*", decision="allow")]
    )
    gate = RulesPermissionGate(rules=rules, smart_approve=recording)
    decision = await gate.check(_req("SafeTool"))

    assert decision.kind == "allow"
    assert len(recording.calls) == 0


@pytest.mark.asyncio
async def test_smart_approve_default_is_none() -> None:
    """RulesPermissionGate default constructor has smart_approve=None."""
    gate = RulesPermissionGate()
    # Confirm no smart_approve by checking attribute
    assert gate._smart_approve is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# RulesPermissionGate constructor accepts smart_approve keyword
# ---------------------------------------------------------------------------

def test_rules_permission_gate_accepts_smart_approve_kwarg() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    classifier = ReadOnlyClassifier()
    # Should not raise
    gate = RulesPermissionGate(smart_approve=classifier)
    assert gate._smart_approve is classifier  # noqa: SLF001
