"""U6 hosted-transport goal-mode wiring.

The composer Goal-mission toggle rides the chat body as ``goalMode``. U6 wires
it into the hosted streaming transport so the governed turn (and the self-host
streaming route's local-engine branch) publishes the mission-intensity
ContextVar and, when the toggle is on, the mission ``GoalLoopPolicy`` -- exactly
mirroring the local chat route (``chat_routes_local.py``).

Two dispatch chokepoints are covered:

* ``run_gate5b_user_visible_chat_response`` (gate5b serving entry) -- the single
  point that dispatches the governed turn for BOTH the streaming
  ``/v1/chat/stream`` gate5b branch (awaited inside ``asyncio.create_task``) and
  the ``/v1/chat/completions`` route. Setting the ContextVars at this scope keeps
  them live for the driver in every gate5b path.
* the ``/v1/chat/stream`` local-engine branch -- active immediately for self-host
  users of the streaming route.

The A-5 test locks in the ``asyncio.create_task`` context-copy semantics the
gate5b streaming path relies on (the governed turn runs inside a task created by
``_drive_selected_gate5b_stream``; the local branch's driver runs inside the
detach pump task).

``goalMode`` absent keeps the ContextVars at their defaults -> byte-identical to
pre-U6 behavior.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.runtime.goal_loop_policy import build_goal_loop_policy_from_request
from magi_agent.runtime.per_turn_goal_intensity import (
    current_per_turn_goal_mission,
    set_per_turn_goal_mission,
)
from magi_agent.runtime.per_turn_goal_loop_context import (
    current_per_turn_goal_loop_policy,
    set_per_turn_goal_loop_policy,
)

def _mission_policy(objective: str) -> object:
    """Build a real mission GoalLoopPolicy (all required frozen fields set)."""
    return build_goal_loop_policy_from_request(
        goal_mode_requested=True,
        objective=objective,
        env={"MAGI_GOAL_LOOP_ENABLED": "1"},
    )


_GOAL_ENV_KEYS = (
    "MAGI_GOAL_LOOP_ENABLED",
    "MAGI_GOAL_LOOP_MAX_TURNS",
    "MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS",
    "MAGI_RUNTIME_PROFILE",
    "MAGI_STREAMING_CHAT",
)


@pytest.fixture(autouse=True)
def _isolate_goal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Goal-loop flags are profile-aware; a polluted shell silently flips test
    # semantics. Clear them and let each test set explicitly.
    for name in _GOAL_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# A-5: ContextVars survive the asyncio.create_task boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_contextvars_visible_across_create_task_boundary() -> None:
    """A var set BEFORE ``create_task`` is visible inside the created task.

    This is the mechanism the gate5b streaming path and the local detach pump
    depend on: the transport sets the goal ContextVars, then a task is created
    whose copied context carries them into the governed turn / driver.
    """
    policy = _mission_policy("cross-task objective")
    mission_token = set_per_turn_goal_mission(True)
    policy_token = set_per_turn_goal_loop_policy(policy)
    try:
        seen: dict[str, object] = {}

        async def _reader() -> None:
            seen["mission"] = current_per_turn_goal_mission()
            seen["policy"] = current_per_turn_goal_loop_policy()

        # create_task snapshots the CURRENT context at creation time.
        task = asyncio.create_task(_reader())
        await task

        assert seen["mission"] is True
        assert seen["policy"] is policy
    finally:
        from magi_agent.runtime.per_turn_goal_intensity import (
            reset_per_turn_goal_mission,
        )
        from magi_agent.runtime.per_turn_goal_loop_context import (
            reset_per_turn_goal_loop_policy,
        )

        reset_per_turn_goal_loop_policy(policy_token)
        reset_per_turn_goal_mission(mission_token)


@pytest.mark.asyncio
async def test_goal_contextvars_visible_across_nested_create_task() -> None:
    """A var set INSIDE a created task is visible to a task it nests.

    Mirrors the gate5b streaming chokepoint: ``_drive_selected_gate5b_stream``
    create_tasks ``run_gate5b`` which sets the vars, then the governed turn runs
    (possibly under further create_tasks) and must observe them.
    """
    policy = _mission_policy("nested objective")
    seen: dict[str, object] = {}

    async def _outer() -> None:
        from magi_agent.runtime.per_turn_goal_intensity import (
            reset_per_turn_goal_mission,
        )
        from magi_agent.runtime.per_turn_goal_loop_context import (
            reset_per_turn_goal_loop_policy,
        )

        m_tok = set_per_turn_goal_mission(True)
        p_tok = set_per_turn_goal_loop_policy(policy)
        try:

            async def _inner() -> None:
                seen["mission"] = current_per_turn_goal_mission()
                seen["policy"] = current_per_turn_goal_loop_policy()

            inner = asyncio.create_task(_inner())
            await inner
        finally:
            reset_per_turn_goal_loop_policy(p_tok)
            reset_per_turn_goal_mission(m_tok)

    await asyncio.create_task(_outer())

    assert seen["mission"] is True
    assert seen["policy"] is policy
    # The nested set/reset never leaked into the outermost (test) context.
    assert current_per_turn_goal_mission() is False
    assert current_per_turn_goal_loop_policy() is None


# ---------------------------------------------------------------------------
# gate5b serving entry: run_gate5b_user_visible_chat_response
# ---------------------------------------------------------------------------


def _fake_request() -> SimpleNamespace:
    return SimpleNamespace(headers={})


async def _run_gate5b(payload: object, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Drive the U6 wrapper with the heavy inner faked; capture the live vars."""
    from magi_agent.transport import gate5b_serving

    captured: dict[str, object] = {}

    async def _fake_inner(runtime, payload, *, request, public_event_sink=None):  # noqa: ANN001
        # This runs at the exact scope the governed turn / driver runs at.
        captured["mission"] = current_per_turn_goal_mission()
        captured["policy"] = current_per_turn_goal_loop_policy()
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        gate5b_serving,
        "_run_gate5b_user_visible_chat_response_inner",
        _fake_inner,
    )
    await gate5b_serving.run_gate5b_user_visible_chat_response(
        SimpleNamespace(config=SimpleNamespace(bot_id="b", user_id="u")),
        payload,
        request=_fake_request(),
    )
    return captured


@pytest.mark.asyncio
async def test_gate5b_goal_mode_true_publishes_during_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
    payload = {
        "sessionId": "s",
        "goalMode": True,
        "messages": [{"role": "user", "content": "Multi-step hosted report"}],
    }
    captured = await _run_gate5b(payload, monkeypatch)

    assert captured["mission"] is True
    policy = captured["policy"]
    assert policy is not None
    assert getattr(policy, "enabled", False) is True
    assert getattr(policy, "objective", "") == "Multi-step hosted report"

    # Reset happened in the wrapper finally.
    assert current_per_turn_goal_mission() is False
    assert current_per_turn_goal_loop_policy() is None


@pytest.mark.asyncio
async def test_gate5b_goal_mode_absent_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
    payload = {
        "sessionId": "s",
        "messages": [{"role": "user", "content": "hi"}],
    }
    captured = await _run_gate5b(payload, monkeypatch)

    # Ambient path unaffected: mission False, no mission policy published.
    assert captured["mission"] is False
    assert captured["policy"] is None


@pytest.mark.asyncio
async def test_gate5b_goal_mode_true_but_master_flag_off_publishes_no_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The master kill-switch gates the mission POLICY; the raw toggle still
    # rides the intensity ContextVar (mirrors chat_routes_local exactly).
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "0")
    payload = {
        "sessionId": "s",
        "goalMode": True,
        "messages": [{"role": "user", "content": "Multi-step hosted report"}],
    }
    captured = await _run_gate5b(payload, monkeypatch)

    assert captured["mission"] is True
    assert captured["policy"] is None


# ---------------------------------------------------------------------------
# local-engine branch of /v1/chat/stream (active for self-host streaming users)
# ---------------------------------------------------------------------------


def _make_local_app(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]):
    from fastapi import FastAPI

    from magi_agent.transport.streaming_chat_route import (
        register_streaming_chat_routes,
    )

    class _RecordingEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
            # Runs inside the detach pump task; the goal ContextVars must have
            # propagated across that task boundary from the local-engine wiring.
            captured["mission"] = current_per_turn_goal_mission()
            captured["policy"] = current_per_turn_goal_loop_policy()
            yield EngineResult(
                terminal=Terminal.completed, session_id="s-local", turn_id="t-local"
            )

    def _builder(session_id, sink, model_override=None, *, agent_event_emitter=None):  # noqa: ANN001
        return _RecordingEngine(), None

    app = FastAPI(title="u6-local")
    rt = SimpleNamespace(
        config=SimpleNamespace(
            gateway_token="test-token", model="anthropic/claude"
        )
    )
    register_streaming_chat_routes(app, rt, engine_builder=_builder)
    return app


@pytest.mark.asyncio
async def test_local_engine_branch_wires_goal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")

    captured: dict[str, object] = {}
    client = TestClient(_make_local_app(monkeypatch, captured))
    response = client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer test-token"},
        json={
            "sessionId": "s-local",
            "turnId": "t-local",
            "goalMode": True,
            "messages": [{"role": "user", "content": "Do the 3-step task"}],
        },
    )
    assert response.status_code == 200, response.text

    assert captured.get("mission") is True
    policy = captured.get("policy")
    assert policy is not None
    assert getattr(policy, "objective", "") == "Do the 3-step task"

    # Reset in the finally: after the turn, the process-level context is clean.
    assert current_per_turn_goal_mission() is False
    assert current_per_turn_goal_loop_policy() is None


@pytest.mark.asyncio
async def test_local_engine_branch_goal_mode_absent_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")

    captured: dict[str, object] = {}
    client = TestClient(_make_local_app(monkeypatch, captured))
    response = client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer test-token"},
        json={
            "sessionId": "s-local",
            "turnId": "t-local",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200, response.text

    assert captured.get("mission") is False
    assert captured.get("policy") is None
