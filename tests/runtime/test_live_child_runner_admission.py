"""Track 17 PR2 — admit a vetted, model-backed child runner under a gate.

The local ADK turn runner only accepts a `LocalAdkReplayRunner` (fixture replay)
by design, so no real model can run a child turn. This adds a single vetted,
in-module live runner type (`LocalAdkLiveChildRunner`) that wraps a real ADK
runner and delegates `run_async`. It is a valid candidate type, but the turn
runner only *executes* it when `AdkTurnRunnerConfig.live_child_runner_allowed`
is set (the boundary derives that from `real_child_execution_pack_enabled`).

Invariants preserved: arbitrary `google.adk`/`google.genai` runners are still
rejected; the result stays `local_only` with `user_visible_output=None`; the
live runner cannot execute without the gate.
"""
from __future__ import annotations

import asyncio

import pytest
from google.genai import types

from magi_agent.runtime.adk_turn_runner import (
    AdkTurnRequest,
    AdkTurnRunner,
    AdkTurnRunnerConfig,
    LocalAdkLiveChildRunner,
    LocalAdkReplayRunner,
    LocalAdkTurnRunnerBoundary,
    _validate_local_runner_candidate,
)


class _FakeRealRunner:
    """Stand-in for a real ADK runner: an async-gen run_async yielding events."""

    def __init__(self, events: list[object]) -> None:
        self._events = tuple(events)
        self.calls: list[dict[str, object]] = []

    async def run_async(self, **kwargs: object):
        self.calls.append(kwargs)
        for event in self._events:
            yield event


def _request() -> AdkTurnRequest:
    return AdkTurnRequest(
        turnId="t",
        userId="u",
        sessionId="s",
        invocationId="i",
        newMessage=types.Content(role="user", parts=[types.Part(text="hi")]),
    )


def test_live_shim_is_a_valid_candidate():
    _validate_local_runner_candidate(LocalAdkLiveChildRunner(raw_runner=_FakeRealRunner([])))


def test_live_shim_requires_run_async():
    with pytest.raises(TypeError):
        LocalAdkLiveChildRunner(raw_runner=object())


def test_live_shim_run_async_delegates():
    shim = LocalAdkLiveChildRunner(raw_runner=_FakeRealRunner([{"type": "a"}, {"type": "b"}]))

    async def drain() -> list[object]:
        return [event async for event in shim.run_async(foo=1)]

    assert asyncio.run(drain()) == [{"type": "a"}, {"type": "b"}]


def test_config_live_flag_default_false():
    assert AdkTurnRunnerConfig(enabled=True).live_child_runner_allowed is False


def test_config_live_flag_settable_and_strict():
    assert AdkTurnRunnerConfig(enabled=True, liveChildRunnerAllowed=True).live_child_runner_allowed is True
    with pytest.raises(Exception):
        AdkTurnRunnerConfig(enabled=True, liveChildRunnerAllowed="yes")


def test_run_turn_rejects_live_runner_without_permission():
    boundary = LocalAdkTurnRunnerBoundary.for_live_child_runner(_FakeRealRunner([{"type": "x"}]))
    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=boundary,
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )
    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "live_child_runner_not_permitted"


def test_run_turn_drives_live_runner_with_permission():
    fake = _FakeRealRunner([{"type": "model_event", "sequence": 1}])
    boundary = LocalAdkTurnRunnerBoundary.for_live_child_runner(fake)
    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=boundary,
            config=AdkTurnRunnerConfig(
                enabled=True, timeoutSeconds=0.5, liveChildRunnerAllowed=True
            ),
        )
    )
    assert result.status == "succeeded"
    assert result.runner_invoked is True
    assert result.events == ({"type": "model_event", "sequence": 1},)
    # Enforcement invariants unchanged even for a real child.
    assert result.local_only is True
    assert result.user_visible_output is None
    assert fake.calls, "the real runner was actually driven"


def test_replay_runner_still_succeeds_without_live_flag():
    boundary = LocalAdkTurnRunnerBoundary.from_local_test_runner(
        LocalAdkReplayRunner(events=({"type": "e"},))
    )
    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=boundary,
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )
    assert result.status == "succeeded"
