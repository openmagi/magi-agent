"""Tests for schema-invalid argument feedback (R3, hermes mechanism 1, returned-result path).

When the dispatcher returns ``errorCode == "tool_input_schema_invalid"``, the
model today only sees hashed/dropped path diagnostics ("a required field is
missing" — but never WHICH) and no retry instruction. The
``MagiSchemaFeedbackControl`` recomputes plain-text missing/unknown argument
names locally from the tool's enriched declaration (data the model already
legitimately sees: schema vocabulary + the args the model itself sent) and
appends hermes-style retry guidance, behind a default-OFF flag with an attempt
budget — without touching the redaction layer in
``magi_agent.tools.schema_validation``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.adk_bridge.control_plane import ControlPlane, build_default_plane
from magi_agent.adk_bridge.schema_feedback import (
    SCHEMA_FEEDBACK_CONTROL_NAME,
    SCHEMA_FEEDBACK_RESPONSE_TYPE,
    MagiSchemaFeedbackControl,
    build_schema_feedback_control,
)
from magi_agent.adk_bridge.tool_adapter import build_adk_tool_for_manifest
from magi_agent.runtime.openmagi_runtime import (
    _build_core_tool_registry,
    _build_default_plugin_state,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures: real manifest + real builder so the enriched declaration path is
# exercised end-to-end (same pattern as test_file_tool_arguments_schema.py).
# ---------------------------------------------------------------------------


class _FakeAdkToolContext:
    def __init__(self, invocation_id: str = "inv-1") -> None:
        self.invocation_id = invocation_id


def _build_file_edit_tool():
    registry = _build_core_tool_registry(_build_default_plugin_state())
    manifest = registry.resolve_registration("FileEdit").manifest
    dispatcher = ToolDispatcher(registry)
    return build_adk_tool_for_manifest(
        manifest,
        dispatcher,
        mode="act",
        tool_context_factory=lambda _adk: ToolContext(
            botId="test-bot", workspace_root="/tmp"
        ),
    )


def _real_schema_invalid_result(arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch real invalid args through the real dispatcher for the exact
    model_dump(by_alias=True) shape (dispatcher.py tool_input_schema_invalid)."""
    registry = _build_core_tool_registry(_build_default_plugin_state())
    dispatcher = ToolDispatcher(registry)
    result = _run(
        dispatcher.dispatch(
            "FileEdit",
            arguments,
            ToolContext(botId="test-bot", workspace_root="/tmp"),
            mode="act",
        )
    )
    dumped = result.model_dump(by_alias=True)
    assert dumped["errorCode"] == "tool_input_schema_invalid"
    return dumped


def _control(max_attempts: int = 2) -> MagiSchemaFeedbackControl:
    return MagiSchemaFeedbackControl(max_attempts=max_attempts)


# ---------------------------------------------------------------------------
# (a) Merged feedback dict: readable missing field names + retry guidance,
#     with ALL original keys preserved (merge-not-restructure).
# ---------------------------------------------------------------------------


def test_missing_required_field_is_named_and_original_keys_preserved() -> None:
    tool = _build_file_edit_tool()
    inner = {"old_text": "a", "new_text": "b"}  # 'path' missing
    result = _real_schema_invalid_result(inner)

    override = _run(
        _control().on_after_tool(
            tool=tool,
            args={"arguments": dict(inner)},
            tool_context=_FakeAdkToolContext(),
            result=result,
        )
    )

    assert override is not None
    assert override["response_type"] == SCHEMA_FEEDBACK_RESPONSE_TYPE
    # Readable diagnostics: the actual missing field name surfaces.
    assert override["schemaFeedback"]["missingRequired"] == ["path"]
    assert override["schemaFeedback"]["unknownArguments"] == []
    assert override["schemaFeedback"]["validArguments"] == [
        "new_text",
        "old_text",
        "path",
    ]
    # Hermes-style corrective guidance.
    guidance = override["retryGuidance"]
    assert "retry" in guidance.lower()
    assert "path" in guidance
    # Merge-not-restructure: every original key/value survives for telemetry.
    for key, value in result.items():
        assert override[key] == value
    assert override["status"] == "blocked"
    assert override["errorCode"] == "tool_input_schema_invalid"
    assert override["metadata"]["schemaValidation"]["valid"] is False
    # Attempt bookkeeping is model-visible.
    assert override["retry_attempt"] == 1
    assert override["max_attempts"] == 2


# ---------------------------------------------------------------------------
# (g) Unknown-argument typo case.
# ---------------------------------------------------------------------------


def test_unknown_argument_typo_is_named() -> None:
    tool = _build_file_edit_tool()
    inner = {"fil_path": "x", "old_text": "a", "new_text": "b"}  # typo'd 'path'
    result = _real_schema_invalid_result(inner)

    override = _run(
        _control().on_after_tool(
            tool=tool,
            args={"arguments": dict(inner)},
            tool_context=_FakeAdkToolContext(),
            result=result,
        )
    )

    assert override is not None
    assert override["schemaFeedback"]["unknownArguments"] == ["fil_path"]
    assert override["schemaFeedback"]["missingRequired"] == ["path"]
    assert "fil_path" in override["retryGuidance"]


# ---------------------------------------------------------------------------
# (b) Non-schema-invalid results pass through untouched (None).
# ---------------------------------------------------------------------------


def test_success_result_returns_none() -> None:
    tool = _build_file_edit_tool()
    override = _run(
        _control().on_after_tool(
            tool=tool,
            args={"arguments": {"path": "f", "old_text": "a", "new_text": "b"}},
            tool_context=_FakeAdkToolContext(),
            result={"status": "success", "output": "ok"},
        )
    )
    assert override is None


def test_other_error_codes_return_none() -> None:
    tool = _build_file_edit_tool()
    override = _run(
        _control().on_after_tool(
            tool=tool,
            args={"arguments": {}},
            tool_context=_FakeAdkToolContext(),
            result={
                "status": "error",
                "errorCode": "tool_not_found",
                "errorMessage": "tool not found",
            },
        )
    )
    assert override is None


def test_non_mapping_result_returns_none() -> None:
    tool = _build_file_edit_tool()
    override = _run(
        _control().on_after_tool(
            tool=tool,
            args={"arguments": {}},
            tool_context=_FakeAdkToolContext(),
            result="not a mapping",
        )
    )
    assert override is None


def test_marker_dicts_are_never_reprocessed() -> None:
    """Anti-recursion: any result already carrying a response_type marker
    (edit-retry reflection, our own feedback, etc.) is left alone."""
    tool = _build_file_edit_tool()
    inner = {"old_text": "a", "new_text": "b"}
    base = _real_schema_invalid_result(inner)

    edit_retry_marker = {
        "response_type": "MAGI_EDIT_RETRY_REFLECTION",
        "error_type": "edit_apply_failed",
        "reflection_guidance": "fix it",
    }
    own_marker = {**base, "response_type": SCHEMA_FEEDBACK_RESPONSE_TYPE}

    for marker_result in (edit_retry_marker, own_marker):
        override = _run(
            _control().on_after_tool(
                tool=tool,
                args={"arguments": dict(inner)},
                tool_context=_FakeAdkToolContext(),
                result=marker_result,
            )
        )
        assert override is None


# ---------------------------------------------------------------------------
# (c) + (d) Attempt budget per (invocation_id, tool_name).
# ---------------------------------------------------------------------------


def test_attempt_budget_exhausts_then_fresh_invocation_resets() -> None:
    tool = _build_file_edit_tool()
    inner = {"old_text": "a", "new_text": "b"}
    result = _real_schema_invalid_result(inner)
    control = _control(max_attempts=2)
    ctx = _FakeAdkToolContext("inv-budget")

    first = _run(
        control.on_after_tool(
            tool=tool, args={"arguments": dict(inner)}, tool_context=ctx, result=result
        )
    )
    second = _run(
        control.on_after_tool(
            tool=tool, args={"arguments": dict(inner)}, tool_context=ctx, result=result
        )
    )
    third = _run(
        control.on_after_tool(
            tool=tool, args={"arguments": dict(inner)}, tool_context=ctx, result=result
        )
    )
    assert first is not None and first["retry_attempt"] == 1
    assert second is not None and second["retry_attempt"] == 2
    assert third is None  # budget exhausted -> original result flows through

    # A different invocation gets a fresh budget.
    fresh = _run(
        control.on_after_tool(
            tool=tool,
            args={"arguments": dict(inner)},
            tool_context=_FakeAdkToolContext("inv-other"),
            result=result,
        )
    )
    assert fresh is not None and fresh["retry_attempt"] == 1


def test_after_run_callback_sweeps_invocation_counters() -> None:
    tool = _build_file_edit_tool()
    inner = {"old_text": "a", "new_text": "b"}
    result = _real_schema_invalid_result(inner)
    control = _control(max_attempts=1)
    ctx = _FakeAdkToolContext("inv-sweep")

    assert (
        _run(
            control.on_after_tool(
                tool=tool,
                args={"arguments": dict(inner)},
                tool_context=ctx,
                result=result,
            )
        )
        is not None
    )

    class _InvocationContext:
        invocation_id = "inv-sweep"

    _run(control.after_run_callback(invocation_context=_InvocationContext()))
    assert control._attempts == {}


# ---------------------------------------------------------------------------
# (e) Fail-open: declaration access failure -> None (original behavior).
# ---------------------------------------------------------------------------


def test_declaration_failure_fails_open() -> None:
    inner = {"old_text": "a", "new_text": "b"}
    result = _real_schema_invalid_result(inner)

    class _BrokenTool:
        name = "FileEdit"

        def _get_declaration(self):
            raise AttributeError("ADK private API changed")

    override = _run(
        _control().on_after_tool(
            tool=_BrokenTool(),
            args={"arguments": dict(inner)},
            tool_context=_FakeAdkToolContext(),
            result=result,
        )
    )
    assert override is None


# ---------------------------------------------------------------------------
# Env parsing (flag ownership in magi_agent.config.env, strict default-OFF).
# ---------------------------------------------------------------------------


def test_parse_tool_schema_feedback_env_defaults_off() -> None:
    from magi_agent.config.env import parse_tool_schema_feedback_env

    cfg = parse_tool_schema_feedback_env({})
    assert cfg.enabled is False
    assert cfg.max_attempts == 2


def test_parse_tool_schema_feedback_env_is_profile_independent() -> None:
    from magi_agent.config.env import parse_tool_schema_feedback_env

    assert (
        parse_tool_schema_feedback_env({"MAGI_RUNTIME_PROFILE": "full"}).enabled
        is False
    )
    assert (
        parse_tool_schema_feedback_env(
            {"MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED": "1", "MAGI_RUNTIME_PROFILE": "safe"}
        ).enabled
        is True
    )


def test_parse_tool_schema_feedback_env_budget() -> None:
    from magi_agent.config.env import RuntimeEnvError, parse_tool_schema_feedback_env

    cfg = parse_tool_schema_feedback_env(
        {
            "MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED": "true",
            "MAGI_TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS": "5",
        }
    )
    assert cfg.enabled is True
    assert cfg.max_attempts == 5

    with pytest.raises(RuntimeEnvError):
        parse_tool_schema_feedback_env(
            {"MAGI_TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS": "0"}
        )


def test_build_schema_feedback_control_disabled_returns_none() -> None:
    assert build_schema_feedback_control(enabled=False, max_attempts=2) is None
    control = build_schema_feedback_control(enabled=True, max_attempts=3)
    assert isinstance(control, MagiSchemaFeedbackControl)
    assert control.max_attempts == 3


def test_max_attempts_below_one_rejected() -> None:
    with pytest.raises(ValueError):
        MagiSchemaFeedbackControl(max_attempts=0)


# ---------------------------------------------------------------------------
# (f) build_default_plane flag gating + fan-out ordering.
# ---------------------------------------------------------------------------


def _control_names(plane: ControlPlane) -> list[str]:
    return [getattr(ctrl, "name", "") for ctrl in plane._controls]


def test_flag_off_registers_no_schema_feedback_control() -> None:
    plane = build_default_plane(os_environ={})
    assert SCHEMA_FEEDBACK_CONTROL_NAME not in _control_names(plane)


def test_flag_on_registers_after_edit_retry_and_resilience() -> None:
    plane = build_default_plane(
        os_environ={
            "MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED": "1",
            "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
            "MAGI_LOOP_GUARD_ENABLED": "1",
        }
    )
    names = _control_names(plane)
    assert SCHEMA_FEEDBACK_CONTROL_NAME in names
    idx = names.index(SCHEMA_FEEDBACK_CONTROL_NAME)
    edit_retry_idx = next(i for i, n in enumerate(names) if "edit_retry" in n)
    resilience_idx = next(i for i, n in enumerate(names) if "resilience" in n)
    # Fan-out is first-non-None: edit tools' schema failures keep going to
    # edit-retry first; loop-detector ordering unchanged.
    assert idx > edit_retry_idx
    assert idx > resilience_idx


def test_registered_control_is_discoverable_by_after_run_sweep() -> None:
    """_ExtendedControlPlanePlugin.after_run_callback sweeps via ctrl._plugin —
    the control must expose itself so its counters are swept per invocation."""
    plane = build_default_plane(
        os_environ={"MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED": "1"}
    )
    control = next(
        ctrl
        for ctrl in plane._controls
        if getattr(ctrl, "name", "") == SCHEMA_FEEDBACK_CONTROL_NAME
    )
    assert control._plugin is control
    assert callable(getattr(control._plugin, "after_run_callback", None))
