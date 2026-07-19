"""Regression: hosted no-op gate must honor the PermissionGate contract.

Live incident (canary 186bf3d7, 2026-07-19, 0.1.143): _NoOpGate.check
returned the bare str "allow"; the driver's before-tool callback reads
decision.kind, so EVERY tool call on the hosted governed path died with
AttributeError('str' object has no attribute 'kind') and the turn committed
as runner_error. Surfaced only after MAGI_HOSTED_GOVERNED_TURN_ENABLED went
fleet-default-ON (the legacy boundary path never attaches this callback).
"""
from __future__ import annotations

import asyncio

from magi_agent.engine.contracts import PermissionDecision
from magi_agent.engine.driver import MagiEngineDriver
from magi_agent.runtime.hosted_runtime import _HOSTED_NOOP_GATE


def test_noop_gate_returns_permission_decision() -> None:
    decision = asyncio.run(_HOSTED_NOOP_GATE.check(object()))
    assert isinstance(decision, PermissionDecision)
    assert decision.kind == "allow"
    assert decision.updated_input is None


def _run_callback(gate: object) -> object:
    cancel = asyncio.Event()
    callback = MagiEngineDriver._build_gate_before_tool(
        gate=gate, turn_id="t1", cancel=cancel
    )

    class _Tool:
        name = "SpawnAgent"

    return asyncio.run(callback(tool=_Tool(), args={"prompt": "x"}))


def test_gate_callback_allows_via_noop_gate() -> None:
    # End-to-end through the driver callback: the hosted gate must let the
    # tool run (None = proceed), not raise.
    assert _run_callback(_HOSTED_NOOP_GATE) is None


def test_gate_callback_normalizes_legacy_str_decisions() -> None:
    class _LegacyStrGate:
        async def check(self, *_a: object, **_k: object) -> str:
            return "allow"

    class _LegacyDenyGate:
        async def check(self, *_a: object, **_k: object) -> str:
            return "deny"

    assert _run_callback(_LegacyStrGate()) is None
    denied = _run_callback(_LegacyDenyGate())
    assert isinstance(denied, dict)
    assert denied.get("status") == "blocked"
