"""Local-serve tool context must carry an ``emit_agent_event`` callable so
``SpawnAgent`` child lifecycle events (``child_started`` / ``child_progress``
/ ``child_completed`` / ``child_failed``) reach the dashboard's Work pane.

Pre-fix, ``build_cli_tool_runtime`` never set ``emit_agent_event`` on the
ToolContext it produced, so every child-lifecycle emission inside
``magi_agent/plugins/native/subagents.py`` (lines 70, 80, 100, 110, 120, 411)
no-op'd via the guard at line 53:

    if not callable(emitter):
        return

That's why Kevin's 0.1.66 SOTA-spawn run showed "1 AGENTS" (Main only) on the
right panel even though the body confirmed three subagents had spawned. The
only place ``emitAgentEvent`` was wired upstream was the HOSTED gate5b path
(``magi_agent/gates/gate5b_full_toolhost.py:1584``) — local serve dropped
every child event on the floor.

These tests pin the new wiring contract.
"""
from __future__ import annotations

from collections.abc import Mapping

import pytest

from magi_agent.cli.tool_runtime import build_cli_tool_runtime


def test_factory_default_leaves_emit_agent_event_unset(tmp_path) -> None:
    # Back-compat: callers that don't pass ``agent_event_emitter`` keep the
    # historical (None) behavior. Removes any risk of accidental leakage on
    # paths that haven't opted in yet.
    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    ctx = runtime.tool_context_factory(adk_tool_context=None)
    assert ctx.emit_agent_event is None


def test_factory_threads_emitter_to_tool_context(tmp_path) -> None:
    captured: list[Mapping[str, object]] = []

    def _emit(event: Mapping[str, object]) -> None:
        captured.append(dict(event))

    runtime = build_cli_tool_runtime(
        workspace_root=str(tmp_path),
        agent_event_emitter=_emit,
    )
    ctx = runtime.tool_context_factory(adk_tool_context=None)
    assert callable(ctx.emit_agent_event)

    # The emitter must be the SAME callable — SpawnAgent's _emit_agent_event
    # invokes it directly (not a wrapper) so we cannot lose identity here.
    ctx.emit_agent_event({"type": "child_started", "taskId": "child-1"})
    ctx.emit_agent_event({"type": "child_progress", "taskId": "child-1", "detail": "running"})
    assert len(captured) == 2
    assert captured[0]["type"] == "child_started"
    assert captured[1]["type"] == "child_progress"


def test_subagents_emit_helper_now_fires_via_threaded_emitter(tmp_path) -> None:
    # End-to-end inside the boundary of this PR: prove that the SpawnAgent
    # emit helper (the one whose silent no-op was the bug) actually pushes
    # an event when the factory is wired. Avoids the full SpawnAgent boundary
    # (which needs ADK / a real child runner) by calling the helper directly.
    import asyncio

    from magi_agent.plugins.native.subagents import _emit_agent_event

    captured: list[Mapping[str, object]] = []

    def _emit(event: Mapping[str, object]) -> None:
        captured.append(dict(event))

    runtime = build_cli_tool_runtime(
        workspace_root=str(tmp_path),
        agent_event_emitter=_emit,
    )
    ctx = runtime.tool_context_factory(adk_tool_context=None)

    asyncio.run(_emit_agent_event(ctx, {"type": "child_started", "taskId": "x"}))
    assert captured == [{"type": "child_started", "taskId": "x"}]


@pytest.mark.asyncio
async def test_chat_route_drains_pending_agent_events_into_sse(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # End-to-end: the chat-route's _local_adk_chat_sse creates a deque-shaped
    # emitter, threads it down to build_headless_runtime → build_cli_tool_runtime,
    # and drains pending agent events into the SSE stream alongside engine
    # events. We drive a fake engine + fake build_headless_runtime that records
    # the emitter argument and pushes one child event onto it.

    import magi_agent.cli.wiring as wiring
    from magi_agent.cli.contracts import EngineResult, Terminal
    from magi_agent.transport import chat as chat_mod
    from types import SimpleNamespace

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    captured_emitter: dict[str, object] = {}

    class _FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
            # Fire one out-of-band agent event during the turn (simulates
            # SpawnAgent's child_started emission). The chat route must drain
            # the pending deque INTO the SSE stream before yielding the next
            # engine event.
            emitter = captured_emitter.get("emit")
            if callable(emitter):
                emitter({"type": "child_started", "taskId": "child-1", "detail": "spawned"})  # type: ignore[misc]
            yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def _fake_build(**kwargs: object) -> SimpleNamespace:
        captured_emitter["emit"] = kwargs.get("agent_event_emitter")
        return SimpleNamespace(engine=_FakeEngine(), gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )

    runtime = SimpleNamespace(config=SimpleNamespace(model="anthropic/claude"))
    chunks: list[str] = []
    async for chunk in chat_mod._local_adk_chat_sse(
        runtime, {"sessionId": "s", "turnId": "t"}, "spawn please"
    ):
        chunks.append(chunk)
    payload = "".join(chunks)
    # The emitter MUST have been threaded — otherwise build_headless_runtime
    # received no agent_event_emitter kwarg.
    assert captured_emitter.get("emit") is not None, payload
    # And the child_started event MUST have surfaced on the SSE stream so the
    # dashboard's Work pane can render the subagent card.
    assert "child_started" in payload, payload
    assert "child-1" in payload, payload
