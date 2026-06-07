from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    ControlPlane,
    ControlPlanePlugin,
    SELF_REVIEW_AFTER_TURN_CONTROL_NAME,
    SelfReviewAfterTurnControl,
    build_default_plane,
)
from magi_agent.harness.self_review import (
    REVIEW_DISABLED_TOOLSETS,
    ReviewCandidate,
    SelfReviewConfig,
)
from magi_agent.hooks.manifest import HookPoint
from magi_agent.runtime.fork_runner import ChildResult, ForkCacheShareEvidence


_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _FakeAgent:
    name = "magi_cli_agent"
    instruction = "System prompt used for self-review fork cache sharing."


class _FakeSession:
    def __init__(self, events: list[Event]) -> None:
        self.id = "sess-after-turn"
        self.events = events


class _FakeCallbackContext:
    def __init__(self, events: list[Event], invocation_id: str = "turn-after") -> None:
        self.invocation_id = invocation_id
        self.session = _FakeSession(events)


class _FakeCandidateSink:
    def __init__(self) -> None:
        self.received: list[ReviewCandidate] = []

    def receive(self, candidate: ReviewCandidate) -> None:
        self.received.append(candidate)


class _FakeForkRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fork(
        self,
        *,
        parent_turn_id: str,
        system_prompt_blocks: list[dict[str, Any]],
        parent_assistant_message: dict[str, Any],
        child_directives: list[str],
        disabled_toolsets: tuple[str, ...] = (),
    ) -> tuple[list[ChildResult], ForkCacheShareEvidence]:
        self.calls.append(
            {
                "parent_turn_id": parent_turn_id,
                "system_prompt_blocks": system_prompt_blocks,
                "parent_assistant_message": parent_assistant_message,
                "child_directives": child_directives,
                "disabled_toolsets": disabled_toolsets,
            }
        )
        return (
            [
                ChildResult(
                    directive=child_directives[0],
                    status="ok",
                    output='{"kind":"memory","proposal":"Remember after-turn wiring.","confidence":0.9}',
                )
            ],
            ForkCacheShareEvidence(
                parentTurnId=parent_turn_id,
                childCount=len(child_directives),
                sharedPrefixFingerprint="fake-fp",
                disabledToolsets=disabled_toolsets,
                status="ok",
                elapsedMs=0.1,
            ),
        )


def _model_event(*, turn_id: str = "turn-after", text: str = "Task completed.") -> Event:
    return Event(
        author="magi_cli_agent",
        invocationId=turn_id,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def test_after_agent_callback_fans_out_to_plane_controls() -> None:
    class _Recorder(BaseLoopControl):
        name = "after_agent_recorder"

        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        async def on_after_agent(
            self,
            *,
            agent: object,
            callback_context: object,
        ) -> None:
            self.calls.append((agent, callback_context))

    recorder = _Recorder()
    plugin = ControlPlanePlugin(ControlPlane().register(recorder))
    context = _FakeCallbackContext([_model_event()])
    agent = _FakeAgent()

    result = _run(plugin.after_agent_callback(agent=agent, callback_context=context))

    assert result is None
    assert recorder.calls == [(agent, context)]


def test_default_plane_registers_self_review_only_when_enabled() -> None:
    disabled = build_default_plane(os_environ={"MAGI_RUNTIME_PROFILE": "safe"})
    enabled = build_default_plane(
        os_environ={"MAGI_RUNTIME_PROFILE": "safe", "MAGI_SELF_REVIEW_ENABLED": "1"}
    )
    enabled_control = next(
        control
        for control in enabled._controls
        if control.name == SELF_REVIEW_AFTER_TURN_CONTROL_NAME
    )

    assert SELF_REVIEW_AFTER_TURN_CONTROL_NAME not in {
        control.name for control in disabled._controls
    }
    assert enabled_control.manifest.point is HookPoint.AFTER_TURN_END
    assert enabled_control.manifest.blocking is False
    assert enabled_control.manifest.fail_open is True


def test_self_review_after_turn_schedules_shadow_hook_with_restricted_tools() -> None:
    scheduled: list[Coroutine[Any, Any, None]] = []
    runner = _FakeForkRunner()
    sink = _FakeCandidateSink()
    control = SelfReviewAfterTurnControl(
        fork_runner=runner,
        candidate_sink=sink,
        config=SelfReviewConfig(enabled=True, shadow=True),
        now=_NOW,
        scheduler=scheduled.append,
    )
    plugin = ControlPlanePlugin(ControlPlane().register(control))

    result = _run(
        plugin.after_agent_callback(
            agent=_FakeAgent(),
            callback_context=_FakeCallbackContext([_model_event()]),
        )
    )

    assert result is None
    assert runner.calls == []
    assert len(scheduled) == 1

    _run(scheduled[0])

    assert runner.calls[0]["parent_turn_id"] == "turn-after"
    assert runner.calls[0]["disabled_toolsets"] == REVIEW_DISABLED_TOOLSETS
    assert runner.calls[0]["system_prompt_blocks"] == [
        {"type": "text", "text": _FakeAgent.instruction}
    ]
    assert runner.calls[0]["parent_assistant_message"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Task completed."}],
    }
    assert len(sink.received) == 1
    assert sink.received[0].mode == "shadow"
    assert sink.received[0].acted is False


def test_self_review_after_turn_missing_context_fails_open_without_scheduling() -> None:
    scheduled: list[Coroutine[Any, Any, None]] = []
    control = SelfReviewAfterTurnControl(
        fork_runner=_FakeForkRunner(),
        candidate_sink=_FakeCandidateSink(),
        config=SelfReviewConfig(enabled=True, shadow=True),
        now=_NOW,
        scheduler=scheduled.append,
    )
    plugin = ControlPlanePlugin(ControlPlane().register(control))

    result = _run(
        plugin.after_agent_callback(
            agent=_FakeAgent(),
            callback_context=_FakeCallbackContext([]),
        )
    )

    assert result is None
    assert scheduled == []
