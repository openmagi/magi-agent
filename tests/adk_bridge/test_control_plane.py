"""Tests for the ControlPlane / LoopControl abstraction (PR2, goose-parity).

Covers:
- Registration is chainable.
- ``_before_tool`` fan-out: first deny wins; rewrite mutates args; allow passes through.
- ``_after_tool`` fan-out: first non-None override wins.
- ``_before_model`` fan-out: all controls run (mutation accumulates); returns None.
- A ``LoopControl`` that only overrides one hook works via ``BaseLoopControl`` defaults.
- Registration guard: controls that override ``on_before_tool`` are rejected.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    ControlPlane,
    LoopControl,
    ToolDecision,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _AllowControl(BaseLoopControl):
    name = "allow_control"

    async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
        return ToolDecision(action="allow")


class _DenyControl(BaseLoopControl):
    name = "deny_control"
    deny_result = {"blocked": True, "reason": "deny_control fired"}

    async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
        return ToolDecision(action="deny", deny_result=self.deny_result)


class _RewriteControl(BaseLoopControl):
    name = "rewrite_control"

    async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
        return ToolDecision(action="rewrite", updated_args={"rewritten": True})


class _AfterToolOverrideControl(BaseLoopControl):
    name = "after_override"
    override = {"overridden": True}

    async def on_after_tool(self, *, tool, args, tool_context, result) -> dict | None:
        return self.override


class _AfterToolPassControl(BaseLoopControl):
    name = "after_pass"

    async def on_after_tool(self, *, tool, args, tool_context, result) -> dict | None:
        return None


class _BeforeModelMutateControl(BaseLoopControl):
    name = "before_model_mutate"
    key: str
    value: Any

    def __init__(self, key: str, value: Any) -> None:
        self.key = key
        self.value = value

    async def on_before_model(self, *, callback_context, llm_request) -> None:
        llm_request[self.key] = self.value


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _make_plane(*controls) -> ControlPlane:
    """Helper: build a ControlPlane by appending directly to _controls.

    Used for tests that exercise internal _before_tool dispatch semantics using
    fakes that override on_before_tool. These fakes are intentionally forbidden
    via register() (the registration guard prevents silent permission-gate bypass);
    here we bypass the guard to test the underlying fan-out mechanics in isolation.
    """
    plane = ControlPlane()
    for ctrl in controls:
        plane._controls.append(ctrl)
    return plane


def test_register_is_chainable() -> None:
    """register() is chainable for controls that do NOT override on_before_tool."""
    plane = ControlPlane()

    class _AfterOnly(BaseLoopControl):
        name = "after_only"

        async def on_after_tool(self, *, tool, args, tool_context, result) -> dict | None:
            return None

    result = plane.register(_AfterOnly()).register(_AfterOnly())
    assert result is plane
    assert len(plane._controls) == 2


def test_register_returns_self_allows_one_liner() -> None:
    """register() returns self — verified with a non-before_tool control."""

    class _ModelOnly(BaseLoopControl):
        name = "model_only"

        async def on_before_model(self, *, callback_context, llm_request) -> None:
            return None

    plane = ControlPlane().register(_ModelOnly())
    assert len(plane._controls) == 1


# ---------------------------------------------------------------------------
# _before_tool: first deny wins
# ---------------------------------------------------------------------------


def test_before_tool_first_deny_wins() -> None:
    deny = _DenyControl()
    plane = _make_plane(_AllowControl(), deny, _AllowControl())

    args = {"x": 1}
    result = _run(plane._before_tool(tool=None, args=args, tool_context=None))

    # Returns the deny_result from the first deny control.
    assert result == deny.deny_result


def test_before_tool_deny_short_circuits_later_controls() -> None:
    # After a deny, later controls must not run.
    called = []

    class _TrackedAllow(BaseLoopControl):
        name = "tracked"

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            called.append("after_deny")
            return None

    plane = _make_plane(_DenyControl(), _TrackedAllow())
    _run(plane._before_tool(tool=None, args={}, tool_context=None))

    assert "after_deny" not in called


def test_before_tool_all_allow_returns_none() -> None:
    plane = _make_plane(_AllowControl(), _AllowControl())
    result = _run(plane._before_tool(tool=None, args={}, tool_context=None))
    assert result is None


def test_before_tool_rewrite_mutates_args_and_continues() -> None:
    # A rewrite control mutates args in-place and execution continues (no short-circuit).
    called_after = []

    class _TrackedAfterRewrite(BaseLoopControl):
        name = "after_rewrite"

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            called_after.append(args.copy())
            return None

    args = {"original": True}
    plane = _make_plane(_RewriteControl(), _TrackedAfterRewrite())
    result = _run(plane._before_tool(tool=None, args=args, tool_context=None))

    # No deny -> returns None (proceed to tool).
    assert result is None
    # Args were mutated in place by the rewrite control.
    assert args == {"rewritten": True}
    # Subsequent control saw the rewritten args.
    assert called_after == [{"rewritten": True}]


def test_before_tool_empty_plane_returns_none() -> None:
    plane = ControlPlane()
    result = _run(plane._before_tool(tool=None, args={}, tool_context=None))
    assert result is None


# ---------------------------------------------------------------------------
# _after_tool: first non-None override wins
# ---------------------------------------------------------------------------


def test_after_tool_first_non_none_wins() -> None:
    class _SecondOverride(BaseLoopControl):
        name = "second"
        override = {"second": True}

        async def on_after_tool(self, *, tool, args, tool_context, result) -> dict | None:
            return self.override

    first = _AfterToolOverrideControl()
    plane = ControlPlane().register(first).register(_SecondOverride())

    out = _run(plane._after_tool(tool=None, args={}, tool_context=None, result={"orig": 1}))
    assert out == first.override


def test_after_tool_all_none_returns_none() -> None:
    plane = ControlPlane().register(_AfterToolPassControl()).register(_AfterToolPassControl())
    out = _run(plane._after_tool(tool=None, args={}, tool_context=None, result={"orig": 1}))
    assert out is None


def test_after_tool_empty_plane_returns_none() -> None:
    plane = ControlPlane()
    out = _run(plane._after_tool(tool=None, args={}, tool_context=None, result={}))
    assert out is None


# ---------------------------------------------------------------------------
# _before_model: all controls run; mutations accumulate; always returns None
# ---------------------------------------------------------------------------


def test_before_model_all_controls_run() -> None:
    request = {}
    plane = (
        ControlPlane()
        .register(_BeforeModelMutateControl("key_a", 1))
        .register(_BeforeModelMutateControl("key_b", 2))
    )

    result = _run(plane._before_model(callback_context=None, llm_request=request))

    assert result is None
    assert request == {"key_a": 1, "key_b": 2}


def test_before_model_mutations_accumulate() -> None:
    request = {"base": True}
    plane = (
        ControlPlane()
        .register(_BeforeModelMutateControl("first", "A"))
        .register(_BeforeModelMutateControl("second", "B"))
    )

    _run(plane._before_model(callback_context=None, llm_request=request))

    assert request["base"] is True
    assert request["first"] == "A"
    assert request["second"] == "B"


def test_before_model_empty_plane_returns_none() -> None:
    plane = ControlPlane()
    result = _run(plane._before_model(callback_context=None, llm_request={}))
    assert result is None


# ---------------------------------------------------------------------------
# BaseLoopControl: default no-ops
# ---------------------------------------------------------------------------


def test_base_loop_control_defaults_are_noop() -> None:
    class _MinimalControl(BaseLoopControl):
        name = "minimal"

    ctrl = _MinimalControl()

    # All default hooks return None (no-op / pass through).
    assert _run(ctrl.on_before_tool(tool=None, args={}, tool_context=None)) is None
    assert _run(ctrl.on_after_tool(tool=None, args={}, tool_context=None, result={})) is None
    assert _run(ctrl.on_before_model(callback_context=None, llm_request={})) is None


def test_partial_control_only_before_tool() -> None:
    """A control that only overrides on_before_tool still works in a plane.

    Uses _make_plane() to bypass register() guard (we are testing fan-out
    dispatch semantics, not the registration safety check).
    """

    class _OnlyBeforeTool(BaseLoopControl):
        name = "only_before"
        fired = False

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            _OnlyBeforeTool.fired = True
            return None

    ctrl = _OnlyBeforeTool()
    plane = _make_plane(ctrl)

    _run(plane._before_tool(tool=None, args={}, tool_context=None))
    _run(plane._after_tool(tool=None, args={}, tool_context=None, result={}))
    _run(plane._before_model(callback_context=None, llm_request={}))

    assert _OnlyBeforeTool.fired


# ---------------------------------------------------------------------------
# LoopControl Protocol conformance
# ---------------------------------------------------------------------------


def test_base_loop_control_satisfies_protocol() -> None:
    class _Concrete(BaseLoopControl):
        name = "concrete"

    assert isinstance(_Concrete(), LoopControl)


# ---------------------------------------------------------------------------
# ToolDecision dataclass
# ---------------------------------------------------------------------------


def test_tool_decision_defaults() -> None:
    d = ToolDecision()
    assert d.action == "allow"
    assert d.deny_result is None
    assert d.updated_args is None


def test_tool_decision_deny() -> None:
    d = ToolDecision(action="deny", deny_result={"err": "nope"})
    assert d.action == "deny"
    assert d.deny_result == {"err": "nope"}


def test_tool_decision_rewrite() -> None:
    d = ToolDecision(action="rewrite", updated_args={"new": "val"})
    assert d.action == "rewrite"
    assert d.updated_args == {"new": "val"}


# ---------------------------------------------------------------------------
# Registration guard: on_before_tool override detection
# ---------------------------------------------------------------------------


def test_register_raises_if_on_before_tool_overridden() -> None:
    """Registering a control that overrides on_before_tool must raise ValueError.

    This guard prevents a silent permission-gate bypass: ControlPlanePlugin runs
    at ADK plugin level (Step 1), but the permission gate is wired agent-level
    (Step 2). A plugin-level on_before_tool that returns deny/rewrite would
    short-circuit Step 2, bypassing the gate entirely.
    """

    class _ForbiddenDenyControl(BaseLoopControl):
        name = "forbidden_deny"

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            return ToolDecision(action="deny", deny_result={"blocked": True})

    plane = ControlPlane()
    with pytest.raises(ValueError, match="on_before_tool"):
        plane.register(_ForbiddenDenyControl())


def test_register_raises_if_on_before_tool_overridden_rewrite() -> None:
    """The guard also trips for rewrite-capable on_before_tool overrides."""

    class _ForbiddenRewriteControl(BaseLoopControl):
        name = "forbidden_rewrite"

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            return ToolDecision(action="rewrite", updated_args={"injected": True})

    plane = ControlPlane()
    with pytest.raises(ValueError, match="permission.gate"):
        plane.register(_ForbiddenRewriteControl())


def test_register_guard_error_mentions_control_name() -> None:
    """The ValueError must mention the control's name for easy diagnosis."""

    class _NamedForbidden(BaseLoopControl):
        name = "my_forbidden_control"

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            return None  # even returning None is an override of the base

    plane = ControlPlane()
    with pytest.raises(ValueError, match="my_forbidden_control"):
        plane.register(_NamedForbidden())


def test_register_guard_does_not_trip_for_base_class_default() -> None:
    """A control that does NOT override on_before_tool registers fine.

    Verifies the guard only checks for real overrides, not for classes that
    merely inherit the BaseLoopControl no-op.
    """

    class _OnlyAfterTool(BaseLoopControl):
        name = "only_after_tool"

        async def on_after_tool(self, *, tool, args, tool_context, result) -> dict | None:
            return {"patched": True}

    plane = ControlPlane()
    plane.register(_OnlyAfterTool())  # must not raise
    assert len(plane._controls) == 1


def test_register_guard_does_not_trip_for_only_before_model() -> None:
    """A control that only overrides on_before_model registers fine."""

    class _OnlyBeforeModel(BaseLoopControl):
        name = "only_before_model"

        async def on_before_model(self, *, callback_context, llm_request) -> None:
            llm_request["mutated"] = True

    plane = ControlPlane()
    plane.register(_OnlyBeforeModel())  # must not raise
    assert len(plane._controls) == 1


# ---------------------------------------------------------------------------
# 4 real default controls do not trip the guard
# ---------------------------------------------------------------------------


def test_four_default_controls_register_without_guard_trip() -> None:
    """All 4 real default LoopControl implementations must register without raising.

    This is the canonical regression test: if any default control were accidentally
    given an on_before_tool override it would trip the guard here before it could
    silently bypass the permission gate in production.

    The 4 controls under test:
    - _EditRetryLoopControl  (on_after_tool only)
    - _ResilienceLoopControl (on_after_tool only)
    - _CompactionLoopControl (on_before_model only)
    - MaxStepsBrakeControl   (on_before_model only)
    """
    from magi_agent.adk_bridge.control_plane import (
        MaxStepsBrakeControl,
        _CompactionLoopControl,
        _EditRetryLoopControl,
        _ResilienceLoopControl,
    )

    class _FakePlugin:
        name = "fake"

        async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
            return None

        async def before_model_callback(self, *, callback_context, llm_request):
            return None

    fake = _FakePlugin()

    plane = ControlPlane()
    # None of these should raise — they only use on_after_tool / on_before_model.
    plane.register(_EditRetryLoopControl(fake))
    plane.register(_ResilienceLoopControl(fake))
    plane.register(_CompactionLoopControl(fake))
    plane.register(MaxStepsBrakeControl(max_iterations=10, iteration=0))

    assert len(plane._controls) == 4
