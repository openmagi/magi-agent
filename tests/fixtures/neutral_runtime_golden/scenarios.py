"""Four scenario drivers — one per hard control-plane seam.

Each driver assembles the relevant control(s) using the SAME construction as the
existing test that already exercises that seam, wraps the plane (or extended
plugin) with the tap, drives the trigger, and returns the recorded+normalized
trace. Trigger setups are copied from:

* loop guard  -> tests/test_resilience_plugin_wiring.py
* compaction  -> tests/adk_bridge/test_context_compaction_plugin.py
* edit retry  -> tests/adk_bridge/test_extended_plugin_on_tool_error.py
* GA constraint -> tests/test_ga_constraint_control.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from tests.fixtures.neutral_runtime_golden.recorder import (
    ControlPlaneRecorder,
    normalize_trace,
)
from tests.fixtures.neutral_runtime_golden.tap import (
    recording_plane,
    recording_tool_error,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# S-C resilience loop-guard — fires at after_tool (hard stop replaces result).
# Copied from tests/test_resilience_plugin_wiring.py.
# ---------------------------------------------------------------------------


def run_loop_guard_scenario() -> list[dict[str, Any]]:
    from magi_agent.adk_bridge.control_plane import (
        ControlPlane,
        _ResilienceLoopControl,
    )
    from magi_agent.adk_bridge.resilience_plugin import build_resilience_plugin

    class _Tool:
        name = "Search"

    class _Ctx:
        invocation_id = "inv-loop-guard"

    plugin = build_resilience_plugin(
        loop_guard_enabled=True,
        loop_guard_soft_threshold=3,
        loop_guard_hard_threshold=5,
        error_recovery_enabled=False,
    )
    assert plugin is not None
    plane = ControlPlane()
    plane.register(_ResilienceLoopControl(plugin))

    rec = ControlPlaneRecorder()
    recording_plane(plane, rec)

    async def drive() -> None:
        # Five identical consecutive after_tool calls -> soft nudges then a hard stop.
        for _ in range(5):
            await plane._after_tool(
                tool=_Tool(),
                args={"query": "same"},
                tool_context=_Ctx(),
                result={"status": "ok", "results": ["a", "b"]},
            )

    _run(drive())
    return normalize_trace(rec.events)


# ---------------------------------------------------------------------------
# S-D context compaction — fires at before_model (contents trimmed to tail).
# Copied from tests/adk_bridge/test_context_compaction_plugin.py.
# ---------------------------------------------------------------------------


def run_compaction_scenario() -> list[dict[str, Any]]:
    from google.genai import types

    from magi_agent.adk_bridge.context_compaction import (
        build_context_compaction_plugin,
    )
    from magi_agent.adk_bridge.control_plane import (
        ControlPlane,
        _CompactionLoopControl,
    )
    from google.adk.models import LlmRequest

    def _content(index: int, text: str) -> types.Content:
        return types.Content(
            role="user" if index % 2 == 0 else "model",
            parts=[types.Part(text=text)],
        )

    def _big_request(count: int, *, chars: int = 1600) -> LlmRequest:
        req = LlmRequest()
        req.contents = [_content(i, "x" * chars) for i in range(count)]
        return req

    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=2_000, tail_events=16
    )
    assert plugin is not None
    plane = ControlPlane()
    plane.register(_CompactionLoopControl(plugin))

    rec = ControlPlaneRecorder()
    recording_plane(plane, rec)

    req = _big_request(40)

    async def drive() -> None:
        await plane._before_model(callback_context=None, llm_request=req)

    _run(drive())
    return normalize_trace(rec.events)


# ---------------------------------------------------------------------------
# S-C edit-retry — fires at the plugin-level on_tool_error_callback (raise path).
# Copied from tests/adk_bridge/test_extended_plugin_on_tool_error.py.
# ---------------------------------------------------------------------------


def run_edit_retry_scenario() -> list[dict[str, Any]]:
    from magi_agent.adk_bridge.control_plane import (
        ControlPlane,
        _EditRetryLoopControl,
        _ExtendedControlPlanePlugin,
    )
    from magi_agent.adk_bridge.edit_retry_reflection import (
        MagiEditRetryReflectionPlugin,
    )

    class _FakeTool:
        def __init__(self, name: str = "FileEdit") -> None:
            self.name = name

    class _FakeCtx:
        def __init__(self, invocation_id: str = "inv-edit-retry") -> None:
            self.invocation_id = invocation_id

    edit_retry_plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    plane = ControlPlane()
    plane.register(_EditRetryLoopControl(edit_retry_plugin))
    extended = _ExtendedControlPlanePlugin(plane, resilience_plugin=None)

    rec = ControlPlaneRecorder()
    recording_tool_error(extended, rec)

    async def drive() -> None:
        await extended.on_tool_error_callback(
            tool=_FakeTool("FileEdit"),
            tool_args={"path": "src/app.py", "oldText": "old", "newText": "new"},
            tool_context=_FakeCtx("inv-edit-retry"),
            error=ValueError("old_text_not_found"),
        )

    _run(drive())
    return normalize_trace(rec.events)


# ---------------------------------------------------------------------------
# S-A GA constraint reinjection — fires at on_before_model (reminder appended).
# Copied from tests/test_ga_constraint_control.py.
# ---------------------------------------------------------------------------


def run_ga_constraint_scenario() -> list[dict[str, Any]]:
    from magi_agent.adk_bridge.control_plane import (
        ControlPlane,
        GaConstraintReinjectionControl,
    )
    from magi_agent.harness.general_automation.live_gate import (
        GeneralAutomationReceiptLedgerStore,
    )
    from magi_agent.harness.general_automation.task_completion import (
        RequiredDeliverableEvidence,
    )
    from magi_agent.evidence.ledger import EvidenceLedger

    session_id = "session-1"
    turn_id = "turn-1"

    class _FakeConfig:
        def __init__(self, tools=None):
            self.tools = list(tools or [])

    class _FakeLlmRequest:
        def __init__(self, contents=None, tools=None):
            self.contents = list(contents or [])
            self.config = _FakeConfig(tools=list(tools or []))

    class _FakeSession:
        def __init__(self, sid: str):
            self.id = sid

    class _FakeCallbackContext:
        def __init__(self, sid: str, invocation_id: str):
            self.session = _FakeSession(sid)
            self.invocation_id = invocation_id

    def _ledger() -> EvidenceLedger:
        return EvidenceLedger(
            ledgerId=f"ledger-{session_id}-{turn_id}",
            sessionId=session_id,
            turnId=turn_id,
            runOn="main",
            agentRole="general",
            spawnDepth=0,
            sourceKind="tool_trace",
            producerSurface="tool_host",
        )

    store = GeneralAutomationReceiptLedgerStore()
    # Seed a ledger for this turn that does NOT carry the owed artifact ref.
    store._ledgers[(session_id, turn_id)] = _ledger()

    ctrl = GaConstraintReinjectionControl(
        receipts=store,
        contract_required=RequiredDeliverableEvidence(requires_artifact_ref=True),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    plane = ControlPlane()
    plane.register(ctrl)

    rec = ControlPlaneRecorder()
    recording_plane(plane, rec)

    request = _FakeLlmRequest(
        contents=[{"role": "user", "content": "go"}],
        tools=[{"type": "function", "name": "Read"}],
    )

    async def drive() -> None:
        await plane._before_model(
            callback_context=_FakeCallbackContext(session_id, turn_id),
            llm_request=request,
        )

    _run(drive())
    return normalize_trace(rec.events)
