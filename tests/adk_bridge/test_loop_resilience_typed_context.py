"""Phase-6 typed-context migration of the loop-resilience controls (6b7cd40e).

The two loop-resilience mechanisms — generic tool-exception reflection
(``on_tool_error`` raise path) and schema-invalid argument feedback
(``on_after_tool`` returned-result path) — move their per-invocation attempt
counters out of plugin-private dicts into the runtime-owned S-C
:class:`~magi_agent.packs.context.PerInvocationState`, and gain ``apply_*``
typed-context entry points (the P5 pattern; same shape as
``_EditRetryLoopControl.apply_after_tool`` / ``_ResilienceLoopControl``):

* the decision body is factored into a ``*_with_state(state=..., ...)`` pure
  decision over a supplied :class:`PerInvocationState`;
* the ADK callbacks delegate to it with the plugin's own default state
  (behavior byte-identical — main's feature tests stay green unchanged);
* the LoopControl adapters expose ``apply_tool_error`` / ``apply_after_tool``
  reading ``ctx.per_invocation`` with a default-state fallback, so a
  dispatcher-owned shared state replaces plugin-private mutability with no
  privileged access;
* the legacy ``._attempts`` mapping survives as a live write-through view with
  the same LRU/sweep semantics as the other S-C migrations.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from magi_agent.adk_bridge.control_plane import (
    _ToolExceptionReflectionLoopControl,
)
from magi_agent.adk_bridge.edit_retry_reflection import scoped_state_name
from magi_agent.adk_bridge.schema_feedback import (
    SCHEMA_FEEDBACK_RESPONSE_TYPE,
    SCHEMA_FEEDBACK_STATE_NAMESPACE,
    MagiSchemaFeedbackControl,
)
from magi_agent.adk_bridge.tool_exception_reflection import (
    TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE,
    TOOL_EXCEPTION_STATE_NAMESPACE,
    MagiToolExceptionReflectionPlugin,
)
from magi_agent.packs.context import ControlPlaneContext, PerInvocationState

# The S-C controls namespace their PerInvocationState scalar key by control
# identity so they never collide on a shared state; these direct-state assertions
# read the namespaced name.
_TE = lambda tool: scoped_state_name(TOOL_EXCEPTION_STATE_NAMESPACE, tool)  # noqa: E731
_SF = lambda tool: scoped_state_name(SCHEMA_FEEDBACK_STATE_NAMESPACE, tool)  # noqa: E731


def _run(coro):
    return asyncio.run(coro)


class _FakeTool:
    def __init__(self, name: str = "Bash") -> None:
        self.name = name


class _FakeCtx:
    def __init__(self, invocation_id: str = "inv-typed") -> None:
        self.invocation_id = invocation_id


# A minimal enriched-declaration fake matching what
# ``tool_adapter._enrich_arguments_schema`` produces (the only attributes the
# schema-feedback decision body reads).
class _FakeArgumentsSchema:
    def __init__(self) -> None:
        self.required = ["path"]
        self.properties = {"path": object(), "old_text": object(), "new_text": object()}


class _FakeDeclaration:
    def __init__(self) -> None:
        self.parameters = SimpleNamespace(properties={"arguments": _FakeArgumentsSchema()})


class _FakeSchemaTool:
    name = "FileEdit"

    def _get_declaration(self) -> _FakeDeclaration:
        return _FakeDeclaration()


_SCHEMA_INVALID_RESULT = {
    "status": "blocked",
    "errorCode": "tool_input_schema_invalid",
    "errorMessage": "input does not match schema",
}


# ---------------------------------------------------------------------------
# Tool-exception reflection (raise path) — S-C state + apply_tool_error
# ---------------------------------------------------------------------------


def test_tool_exception_reflect_with_state_uses_supplied_state() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=1)
    shared = PerInvocationState()

    first = plugin.reflect_with_state(
        state=shared,
        tool=_FakeTool("Bash"),
        tool_args={"command": "ls"},
        tool_context=_FakeCtx("inv-1"),
        error=ValueError("boom"),
    )
    second = plugin.reflect_with_state(
        state=shared,
        tool=_FakeTool("Bash"),
        tool_args={"command": "ls"},
        tool_context=_FakeCtx("inv-1"),
        error=ValueError("boom"),
    )

    assert first is not None
    assert first["response_type"] == TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE
    assert first["retry_attempt"] == 1
    assert second is None, "budget lives on the SUPPLIED state"
    # the counter lives on the supplied state, not on the plugin default
    assert shared.get_scoped("inv-1", _TE("Bash"), default=0) == 2
    assert plugin._default_state.get_scoped("inv-1", _TE("Bash"), default=0) == 0


def test_tool_exception_adapter_apply_tool_error_reads_ctx_per_invocation() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=2)
    control = _ToolExceptionReflectionLoopControl(plugin)
    shared = PerInvocationState()
    ctx = ControlPlaneContext.minimal(per_invocation=shared)

    result = _run(
        control.apply_tool_error(
            ctx,
            tool=_FakeTool("Bash"),
            tool_args={"command": "ls"},
            tool_context=_FakeCtx("inv-apply"),
            error=ValueError("exploded"),
        )
    )

    assert result is not None
    assert result["retry_attempt"] == 1
    assert shared.get_scoped("inv-apply", _TE("Bash"), default=0) == 1
    assert plugin._default_state.get_scoped("inv-apply", _TE("Bash"), default=0) == 0


def test_tool_exception_adapter_apply_falls_back_to_default_state() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=2)
    control = _ToolExceptionReflectionLoopControl(plugin)

    result = _run(
        control.apply_tool_error(
            ControlPlaneContext.minimal(),
            tool=_FakeTool("Bash"),
            tool_args={"command": "ls"},
            tool_context=_FakeCtx("inv-fallback"),
            error=ValueError("exploded"),
        )
    )

    assert result is not None
    assert plugin._default_state.get_scoped("inv-fallback", _TE("Bash"), default=0) == 1


def test_tool_exception_apply_hard_skips_edit_tools() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=5)
    control = _ToolExceptionReflectionLoopControl(plugin)
    shared = PerInvocationState()
    ctx = ControlPlaneContext.minimal(per_invocation=shared)

    for tool_name in ("FileEdit", "PatchApply"):
        assert (
            _run(
                control.apply_tool_error(
                    ctx,
                    tool=_FakeTool(tool_name),
                    tool_args={},
                    tool_context=_FakeCtx("inv-edit"),
                    error=ValueError("old_text_not_found"),
                )
            )
            is None
        )
    assert shared.get_scoped("inv-edit", _TE("FileEdit"), default=0) == 0


def test_tool_exception_legacy_attempts_view_and_sweep() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=5)

    _run(
        plugin.on_tool_error_callback(
            tool=_FakeTool("Bash"),
            tool_args={},
            tool_context=_FakeCtx("inv-sweep"),
            error=ValueError("x"),
        )
    )
    # legacy mapping surface is a live view over the runtime-owned state
    assert plugin._attempts[("inv-sweep", "Bash")] == 1

    _run(
        plugin.after_run_callback(
            invocation_context=SimpleNamespace(invocation_id="inv-sweep")
        )
    )
    assert plugin._attempts == {}
    assert plugin._default_state.get_scoped("inv-sweep", _TE("Bash"), default=0) == 0


# ---------------------------------------------------------------------------
# Schema feedback (returned-result path) — S-C state + apply_after_tool
# ---------------------------------------------------------------------------


def test_schema_feedback_with_state_uses_supplied_state() -> None:
    control = MagiSchemaFeedbackControl(max_attempts=1)
    shared = PerInvocationState()

    first = control.feedback_with_state(
        state=shared,
        tool=_FakeSchemaTool(),
        args={"arguments": {"old_text": "a", "new_text": "b"}},
        tool_context=_FakeCtx("inv-s1"),
        result=dict(_SCHEMA_INVALID_RESULT),
    )
    second = control.feedback_with_state(
        state=shared,
        tool=_FakeSchemaTool(),
        args={"arguments": {"old_text": "a", "new_text": "b"}},
        tool_context=_FakeCtx("inv-s1"),
        result=dict(_SCHEMA_INVALID_RESULT),
    )

    assert first is not None
    assert first["response_type"] == SCHEMA_FEEDBACK_RESPONSE_TYPE
    assert first["schemaFeedback"]["missingRequired"] == ["path"]
    assert second is None, "budget lives on the SUPPLIED state"
    assert shared.get_scoped("inv-s1", _SF("FileEdit"), default=0) == 2
    assert control._default_state.get_scoped("inv-s1", _SF("FileEdit"), default=0) == 0


def test_schema_feedback_apply_after_tool_reads_ctx_per_invocation() -> None:
    control = MagiSchemaFeedbackControl(max_attempts=2)
    shared = PerInvocationState()
    ctx = ControlPlaneContext.minimal(per_invocation=shared)

    override = _run(
        control.apply_after_tool(
            ctx,
            tool=_FakeSchemaTool(),
            args={"arguments": {"old_text": "a", "new_text": "b"}},
            tool_context=_FakeCtx("inv-s2"),
            result=dict(_SCHEMA_INVALID_RESULT),
        )
    )

    assert override is not None
    assert override["retry_attempt"] == 1
    # merge-not-restructure preserved through the typed-context path
    assert override["errorCode"] == "tool_input_schema_invalid"
    assert shared.get_scoped("inv-s2", _SF("FileEdit"), default=0) == 1
    assert control._default_state.get_scoped("inv-s2", _SF("FileEdit"), default=0) == 0


def test_schema_feedback_apply_falls_back_to_default_state() -> None:
    control = MagiSchemaFeedbackControl(max_attempts=2)

    override = _run(
        control.apply_after_tool(
            ControlPlaneContext.minimal(),
            tool=_FakeSchemaTool(),
            args={"arguments": {"old_text": "a", "new_text": "b"}},
            tool_context=_FakeCtx("inv-s3"),
            result=dict(_SCHEMA_INVALID_RESULT),
        )
    )

    assert override is not None
    assert control._default_state.get_scoped("inv-s3", _SF("FileEdit"), default=0) == 1


def test_schema_feedback_legacy_attempts_view_and_sweep() -> None:
    control = MagiSchemaFeedbackControl(max_attempts=5)

    _run(
        control.on_after_tool(
            tool=_FakeSchemaTool(),
            args={"arguments": {"old_text": "a", "new_text": "b"}},
            tool_context=_FakeCtx("inv-s4"),
            result=dict(_SCHEMA_INVALID_RESULT),
        )
    )
    assert control._attempts[("inv-s4", "FileEdit")] == 1

    _run(
        control.after_run_callback(
            invocation_context=SimpleNamespace(invocation_id="inv-s4")
        )
    )
    assert control._attempts == {}
