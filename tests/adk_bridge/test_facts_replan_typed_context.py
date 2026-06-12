"""Phase-6 typed-context migration of FactsReplanControl (#510).

The P5 pattern (template: ``MaxStepsBrakeControl.apply_before_model``; delegate
shape: ``GaConstraintReinjectionControl.on_before_model``): the ADK hook does
the privileged part — resolving the active ``(session_id, turn_id)`` from the
callback context's session/event tree — and delegates the decision body to the
typed-context entry ``apply_before_model(ctx, *, llm_request, session_id,
turn_id)``. The control reads no seam capability off the context (like the
max-steps brake): the survey decision needs only the pre-resolved turn identity
and the outgoing request, so a user pack authoring an equivalent control
receives the same context and the same pre-resolved identity.

Note: the per-(session, turn) counters intentionally stay control-private
(NOT ``ctx.per_invocation``): their lifecycle is per *logical turn* with a
config-bounded FIFO — a clear-on-after_run sweep would reset the consolidation
budget across goal-nudge/continuation re-invocations of the same turn.
Behavior is byte-identical; main's feature tests pass unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from magi_agent.adk_bridge.facts_replan_control import FactsReplanControl
from magi_agent.packs.context import ControlPlaneContext
from magi_agent.runtime.facts_replan import (
    FactsReplanConfig,
    build_survey_message,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeSession:
    def __init__(self, session_id: str):
        self.id = session_id


class _FakeCallbackContext:
    def __init__(self, session_id: str = "session-1", invocation_id: str = "turn-1"):
        self.session = _FakeSession(session_id)
        self.invocation_id = invocation_id


def _control(*, interval: int = 4, max_surveys: int = 5) -> FactsReplanControl:
    return FactsReplanControl(
        FactsReplanConfig(interval=interval, max_surveys_per_turn=max_surveys)
    )


def _apply(control: FactsReplanControl, request: Any, *, session_id: str = "s1",
           turn_id: str = "t1") -> None:
    _run(
        control.apply_before_model(
            ControlPlaneContext.minimal(),
            llm_request=request,
            session_id=session_id,
            turn_id=turn_id,
        )
    )


def test_apply_before_model_injects_on_schedule() -> None:
    ctrl = _control(interval=4, max_surveys=5)
    request: dict[str, Any] = {"contents": []}

    counts = []
    for _ in range(9):
        _apply(ctrl, request)
        counts.append(len(request["contents"]))

    # Byte-identical to the legacy hook: calls 1-4 nothing, call 5 one, 9 two.
    assert counts == [0, 0, 0, 0, 1, 1, 1, 1, 2]
    expected = build_survey_message(steps_so_far=4, survey_index=1, max_surveys=5)
    assert request["contents"][0] == {"role": "user", "content": expected}


def test_apply_keys_state_by_resolved_identity() -> None:
    ctrl = _control(interval=4)
    req_a: dict[str, Any] = {"contents": []}
    req_b: dict[str, Any] = {"contents": []}

    for _ in range(5):
        _apply(ctrl, req_a, session_id="s1", turn_id="turn-a")
    for _ in range(4):
        _apply(ctrl, req_b, session_id="s2", turn_id="turn-b")

    assert len(req_a["contents"]) == 1 and req_b["contents"] == []
    assert ("s1", "turn-a") in ctrl._turns
    assert ("s2", "turn-b") in ctrl._turns


def test_apply_fail_soft_builder_raises(monkeypatch) -> None:
    import magi_agent.adk_bridge.facts_replan_control as mod

    def _boom(**_kwargs: Any) -> str:
        raise RuntimeError("survey builder broke")

    monkeypatch.setattr(mod, "build_survey_message", _boom)
    ctrl = _control(interval=4)
    request: dict[str, Any] = {"contents": []}

    for _ in range(5):
        assert (
            _run(
                ctrl.apply_before_model(
                    ControlPlaneContext.minimal(),
                    llm_request=request,
                    session_id="s1",
                    turn_id="t1",
                )
            )
            is None
        )
    assert request["contents"] == []


def test_hook_delegates_to_apply_with_resolved_identity(monkeypatch) -> None:
    ctrl = _control(interval=1)
    seen: list[tuple[str, str]] = []

    async def _spy(ctx: Any, *, llm_request: Any, session_id: str, turn_id: str) -> None:
        _ = (ctx, llm_request)
        seen.append((session_id, turn_id))
        return None

    monkeypatch.setattr(ctrl, "apply_before_model", _spy)
    _run(
        ctrl.on_before_model(
            callback_context=_FakeCallbackContext("sess-9", "turn-9"),
            llm_request={"contents": []},
        )
    )
    assert seen == [("sess-9", "turn-9")]


def test_hook_unresolvable_identity_never_reaches_apply(monkeypatch) -> None:
    ctrl = _control(interval=1)
    called = False

    async def _spy(ctx: Any, **_kwargs: Any) -> None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(ctrl, "apply_before_model", _spy)

    class _NoSessionContext:
        invocation_id = "turn-1"

    request: dict[str, Any] = {"contents": []}
    _run(ctrl.on_before_model(callback_context=_NoSessionContext(), llm_request=request))
    assert called is False
    assert request["contents"] == []
    assert len(ctrl._turns) == 0
