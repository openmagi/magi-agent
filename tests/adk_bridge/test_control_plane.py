"""Tests for the ControlPlane / LoopControl abstraction (PR2, goose-parity).

Covers:
- Registration is chainable.
- ``_before_tool`` fan-out: first deny wins; rewrite mutates args; allow passes through.
- ``_after_tool`` fan-out: first non-None override wins.
- ``_before_model`` fan-out: all controls run (mutation accumulates); returns None.
- A ``LoopControl`` that only overrides one hook works via ``BaseLoopControl`` defaults.
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


def test_register_is_chainable() -> None:
    plane = ControlPlane()
    result = plane.register(_AllowControl()).register(_DenyControl())
    assert result is plane
    assert len(plane._controls) == 2


def test_register_returns_self_allows_one_liner() -> None:
    plane = ControlPlane().register(_AllowControl())
    assert len(plane._controls) == 1


# ---------------------------------------------------------------------------
# _before_tool: first deny wins
# ---------------------------------------------------------------------------


def test_before_tool_first_deny_wins() -> None:
    deny = _DenyControl()
    plane = ControlPlane().register(_AllowControl()).register(deny).register(_AllowControl())

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

    plane = ControlPlane().register(_DenyControl()).register(_TrackedAllow())
    _run(plane._before_tool(tool=None, args={}, tool_context=None))

    assert "after_deny" not in called


def test_before_tool_all_allow_returns_none() -> None:
    plane = ControlPlane().register(_AllowControl()).register(_AllowControl())
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
    plane = ControlPlane().register(_RewriteControl()).register(_TrackedAfterRewrite())
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
    """A control that only overrides on_before_tool still works in a plane."""

    class _OnlyBeforeTool(BaseLoopControl):
        name = "only_before"
        fired = False

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            _OnlyBeforeTool.fired = True
            return None

    ctrl = _OnlyBeforeTool()
    plane = ControlPlane().register(ctrl)

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
