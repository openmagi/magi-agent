"""D1: the CLI engine/runner must CONSUME the materialized phase route.

Today the recipe materializer assembles a ``PhaseRoutingPlan`` and the real
runner threads it into :class:`RunnerPolicyAssembly.phase_routing` as an opaque
``model_dump`` dict. Nothing reads it: the engine emits the whole assembly as
metadata but never distills a routing decision, and the pre-final evidence gate
ignores the route's verifier-escalation requirement.

These tests pin the gap (RED) and then the consumption (GREEN). The wiring is
deliberately limited to routing *hints/policy* (a distilled decision event +
gate remediation escalation). It does NOT change which model executes, which
tools are exposed, or grant any production authority.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.runtime.events import RuntimeEvent


# --------------------------------------------------------------------------- #
# Fakes mirroring magi_agent/cli/tests/test_runtime_policy_wiring.py
# --------------------------------------------------------------------------- #
class _NoopRunner:
    async def run_async(self, **kwargs: object) -> AsyncIterator[object]:
        if False:
            yield kwargs


class _FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, *, role: str, parts: list[object]) -> None:
        self.role = role
        self.parts = parts


class _FakeTypes:
    Content = _FakeContent
    Part = _FakePart


class _CapturedRunnerInput:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.harness_state = kwargs.get("harnessState")


class _FakeAdapter:
    def __init__(self, *, runner: object) -> None:
        self.runner = runner

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        if False:
            yield object()


class _FakeBridge:
    def __init__(self, *, live_compatible: bool) -> None:
        self.live_compatible = live_compatible

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> object:
        del adk_event, turn_id
        return type("Projection", (), {"agent_events": []})()


def _fake_engine_deps() -> dict[str, object]:
    return {
        "types": _FakeTypes,
        "OpenMagiEventBridge": _FakeBridge,
        "OpenMagiRunnerAdapter": _FakeAdapter,
        "RunnerTurnInput": _CapturedRunnerInput,
        "sanitize_agent_event": lambda event: event,
    }


def _phase_routing_dict(
    *,
    route_denied: bool = False,
    denial_reason: str | None = None,
    final_escalation: str = "bounded_stronger_verifier",
    final_verifier_tier: str | None = "sota",
) -> dict[str, object]:
    """A phaseRouting payload shaped like ``PhaseRoutingPlan.model_dump(by_alias=True)``."""

    return {
        "phaseRoutes": {
            "intent_classification": {
                "phase": "intent_classification",
                "provider": "anthropic",
                "model": "haiku",
                "tier": "cheap",
                "capabilities": [],
                "escalationPolicy": "none",
                "verifierTier": None,
                "routeDenied": False,
                "reasonCodes": [],
                "estimatedCostUsd": 0.002,
            },
            "final_verification": {
                "phase": "final_verification",
                "provider": "anthropic",
                "model": "haiku",
                "tier": "cheap",
                "capabilities": [],
                "escalationPolicy": final_escalation,
                "verifierTier": final_verifier_tier,
                "routeDenied": route_denied,
                "reasonCodes": ["phase:final_verification:denied"] if route_denied else [],
                "estimatedCostUsd": 0.006,
            },
        },
        "routeDenied": route_denied,
        "denialReason": denial_reason,
        "reasonCodes": [],
        "fallbackToTypeScript": route_denied,
        "fallbackReason": None,
        "maxSotaEscalations": 1,
        "estimatedCostUsd": 0.008,
        "budgetDecisions": {},
        "budgetLedger": {},
    }


def _assembly(
    *,
    phase_routing: dict[str, object] | None,
    missing_evidence_action: str = "audit",
) -> RunnerPolicyAssembly:
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/haiku",
        selectedPackIds=("openmagi.dev-coding",),
        evidenceRequirements=("evidence:git-diff",),
        requiredValidators=("verifier:dev-coding:test-evidence",),
        missingEvidenceAction=missing_evidence_action,
        repairPolicy={"action": missing_evidence_action, "source": "recipe-materializer"},
        attachmentFlags={"livePolicyCallbackAttached": True},
        phaseRouting=phase_routing or {},
    )


def _drive_events(driver: MagiEngineDriver, *, prompt: str) -> list[object]:
    async def _run() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={"prompt": prompt, "session_id": "s1", "turn_id": "t1"},
                cancel=asyncio.Event(),
            )
        ]

    return asyncio.run(_run())


# --------------------------------------------------------------------------- #
# 1. The assembly must expose a normalized, consumable routing decision.
# --------------------------------------------------------------------------- #
def test_assembly_exposes_normalized_phase_route_decision() -> None:
    assembly = _assembly(phase_routing=_phase_routing_dict())

    decision = assembly.phase_route_decision()

    assert decision is not None
    assert decision["routeDenied"] is False
    assert decision["maxSotaEscalations"] == 1
    assert decision["requiresStrongerVerifier"] is True
    assert decision["phaseModels"]["final_verification"] == {
        "provider": "anthropic",
        "model": "haiku",
        "tier": "cheap",
    }
    assert decision["escalationPolicies"]["final_verification"] == "bounded_stronger_verifier"
    assert decision["verifierTiers"]["final_verification"] == "sota"


def test_assembly_phase_route_decision_none_when_routing_absent() -> None:
    assembly = _assembly(phase_routing=None)

    assert assembly.phase_route_decision() is None


def test_assembly_phase_route_decision_reports_denial() -> None:
    assembly = _assembly(
        phase_routing=_phase_routing_dict(route_denied=True, denial_reason="budget_too_low")
    )

    decision = assembly.phase_route_decision()

    assert decision is not None
    assert decision["routeDenied"] is True
    assert decision["denialReason"] == "budget_too_low"
    assert decision["fallbackToTypeScript"] is True
    assert "final_verification" in decision["deniedPhases"]


# --------------------------------------------------------------------------- #
# 2. The engine must EMIT the distilled decision (consumed by all surfaces).
# --------------------------------------------------------------------------- #
def test_engine_emits_phase_route_decision_event_when_routing_present(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)
    driver = MagiEngineDriver(
        runner=_NoopRunner(),
        runner_policy_assembly=_assembly(phase_routing=_phase_routing_dict()),
    )

    items = _drive_events(driver, prompt="hello")
    events = [item for item in items if isinstance(item, RuntimeEvent)]

    route_events = [e for e in events if e.payload.get("type") == "phase_route_decision"]
    assert len(route_events) == 1
    payload = route_events[0].payload
    assert payload["requiresStrongerVerifier"] is True
    assert payload["phaseModels"]["intent_classification"]["tier"] == "cheap"
    assert payload["routeDenied"] is False
    # It must come AFTER the runner_policy_assembly status event.
    types_in_order = [e.payload.get("type") for e in events]
    assert types_in_order.index("runner_policy_assembly") < types_in_order.index(
        "phase_route_decision"
    )


def test_engine_omits_phase_route_decision_event_when_routing_absent(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)
    driver = MagiEngineDriver(
        runner=_NoopRunner(),
        runner_policy_assembly=_assembly(phase_routing=None),
    )

    items = _drive_events(driver, prompt="hello")
    events = [item for item in items if isinstance(item, RuntimeEvent)]

    assert [e for e in events if e.payload.get("type") == "phase_route_decision"] == []


# --------------------------------------------------------------------------- #
# 3. The pre-final gate must CONSUME the verifier-escalation routing decision.
# --------------------------------------------------------------------------- #
def test_pre_final_gate_escalates_remediation_for_stronger_verifier_route(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)
    # missing_evidence_action starts as the weak "audit"; the route demands a
    # bounded stronger verifier for final_verification, so an already-blocking
    # gate must escalate its remediation to "repair_required".
    driver = MagiEngineDriver(
        runner=_NoopRunner(),
        runner_policy_assembly=_assembly(
            phase_routing=_phase_routing_dict(),
            missing_evidence_action="audit",
        ),
    )

    items = _drive_events(driver, prompt="fix the bug in the patch")
    events = [item for item in items if isinstance(item, RuntimeEvent)]
    terminal = items[-1]

    gate = next(
        e.payload for e in events if e.payload.get("type") == "pre_final_evidence_gate"
    )
    assert gate["decision"] == "block"
    # phase route is surfaced in the gate and drives the escalation.
    assert gate["phaseRoute"]["requiresStrongerVerifier"] is True
    assert gate["phaseRouteEscalation"] is True
    assert gate["missingEvidenceAction"] == "repair_required"
    assert gate["repairDecision"]["type"] == "coding_repair_decision"

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error


def test_pre_final_gate_no_escalation_without_stronger_verifier_route(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)
    driver = MagiEngineDriver(
        runner=_NoopRunner(),
        runner_policy_assembly=_assembly(
            phase_routing=_phase_routing_dict(final_escalation="none", final_verifier_tier=None),
            missing_evidence_action="audit",
        ),
    )

    items = _drive_events(driver, prompt="fix the bug in the patch")
    events = [item for item in items if isinstance(item, RuntimeEvent)]

    gate = next(
        e.payload for e in events if e.payload.get("type") == "pre_final_evidence_gate"
    )
    assert gate["decision"] == "block"
    assert gate.get("phaseRouteEscalation", False) is False
    # remediation stays the materialized "audit" action (no stronger-verifier demand).
    assert gate["missingEvidenceAction"] == "audit"
