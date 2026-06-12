"""BUG 4 — S-C controls sharing one PerInvocationState must not collide.

Multiple S-C control_plane controls (edit-retry, schema-feedback,
tool-exception) receive the SAME runtime-owned
:class:`~magi_agent.packs.context.PerInvocationState` and keyed their
per-invocation attempt counters by ``(scope_key, tool_name)`` only. When two of
them handle the SAME tool in the SAME invocation (their flags both ON), they
read and increment each other's counter under the identical
``(invocation_id, tool_name)`` key — consuming each other's ``max_attempts``
budget and failing closed early.

Realistic scenario: ``MAGI_EDIT_RETRY_REFLECTION_ENABLED`` and
``MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED`` are both on; a ``FileEdit`` call fails
schema validation. Both the edit-retry control and the schema-feedback control
observe the same ``FileEdit`` failure in the same invocation. Each must keep its
OWN independent attempt counter; incrementing one must not advance the other.
"""
from __future__ import annotations

from types import SimpleNamespace

from magi_agent.adk_bridge.edit_retry_reflection import MagiEditRetryReflectionPlugin
from magi_agent.adk_bridge.schema_feedback import MagiSchemaFeedbackControl
from magi_agent.adk_bridge.tool_exception_reflection import (
    MagiToolExceptionReflectionPlugin,
)
from magi_agent.packs.context import PerInvocationState


class _FakeCtx:
    def __init__(self, invocation_id: str = "inv-collision") -> None:
        self.invocation_id = invocation_id


class _FakeArgumentsSchema:
    def __init__(self) -> None:
        self.required = ["path"]
        self.properties = {"path": object(), "old_text": object(), "new_text": object()}


class _FakeDeclaration:
    def __init__(self) -> None:
        self.parameters = SimpleNamespace(
            properties={"arguments": _FakeArgumentsSchema()}
        )


class _FileEditTool:
    """A FileEdit tool that also exposes the enriched declaration schema-feedback
    reads — so BOTH edit-retry and schema-feedback can process it."""

    name = "FileEdit"

    def _get_declaration(self) -> _FakeDeclaration:
        return _FakeDeclaration()


_SCHEMA_INVALID_RESULT = {
    "status": "blocked",
    "errorCode": "tool_input_schema_invalid",
    "errorMessage": "input does not match schema",
}


def test_edit_retry_and_schema_feedback_do_not_share_counter() -> None:
    """One shared state, two controls, same FileEdit, same invocation: each keeps
    its OWN attempt counter."""
    shared = PerInvocationState()
    edit_retry = MagiEditRetryReflectionPlugin(max_attempts=3)
    schema_feedback = MagiSchemaFeedbackControl(max_attempts=3)
    tool = _FileEditTool()
    ctx = _FakeCtx("inv-collision")

    # Edit-retry observes a FileEdit failure first.
    edit_retry.reflect_with_state(
        state=shared,
        tool=tool,
        tool_args={"old_text": "a", "new_text": "b"},
        tool_context=ctx,
        reason="old_text_not_found",
    )
    # Then schema-feedback observes its OWN FileEdit schema failure.
    schema_feedback.feedback_with_state(
        state=shared,
        tool=tool,
        args={"arguments": {"old_text": "a", "new_text": "b"}},
        tool_context=ctx,
        result=dict(_SCHEMA_INVALID_RESULT),
    )

    # Each control must see attempt==1 on its OWN namespace, not 2 from sharing a
    # single (invocation_id, "FileEdit") key.
    edit_again = edit_retry.reflect_with_state(
        state=shared,
        tool=tool,
        tool_args={"old_text": "a", "new_text": "b"},
        tool_context=ctx,
        reason="old_text_not_found",
    )
    schema_again = schema_feedback.feedback_with_state(
        state=shared,
        tool=tool,
        args={"arguments": {"old_text": "a", "new_text": "b"}},
        tool_context=ctx,
        result=dict(_SCHEMA_INVALID_RESULT),
    )

    # If the counters collided, each control's 2nd observation would already be
    # its 3rd increment (1 own + 1 from the other + 1 now) — still under the
    # budget of 3 here, but the retry_attempt value would be wrong. Assert the
    # exact independent attempt numbers.
    assert edit_again is not None
    assert edit_again["retry_attempt"] == 2, edit_again["retry_attempt"]
    assert schema_again is not None
    assert schema_again["retry_attempt"] == 2, schema_again["retry_attempt"]


class _BashTool:
    """A generic Bash tool exposing the enriched declaration schema-feedback reads
    AND that can raise (so tool-exception handles it too) — both controls key on
    the same tool name "Bash"."""

    name = "Bash"

    def _get_declaration(self) -> _FakeDeclaration:
        return _FakeDeclaration()


def test_schema_feedback_and_tool_exception_do_not_share_counter() -> None:
    """schema-feedback (returned schema-invalid path) and tool-exception (raise
    path) both act on the SAME generic tool "Bash" in one invocation. They must
    keep independent counters rather than sharing a single (inv, "Bash") key."""
    shared = PerInvocationState()
    schema_feedback = MagiSchemaFeedbackControl(max_attempts=2)
    tool_exc = MagiToolExceptionReflectionPlugin(max_attempts=2)
    tool = _BashTool()
    ctx = _FakeCtx("inv-2")

    # tool-exception observes a Bash raise.
    tool_exc.reflect_with_state(
        state=shared,
        tool=tool,
        tool_args={"command": "ls"},
        tool_context=ctx,
        error=ValueError("boom"),
    )
    # schema-feedback observes a Bash schema-invalid return.
    schema_feedback.feedback_with_state(
        state=shared,
        tool=tool,
        args={"arguments": {"old_text": "a", "new_text": "b"}},
        tool_context=ctx,
        result=dict(_SCHEMA_INVALID_RESULT),
    )

    # tool-exception's Bash counter must be untouched by schema-feedback's Bash
    # increment: a second Bash exception is still attempt==2 (under budget 2),
    # NOT a fail-closed 3rd from a shared counter.
    second = tool_exc.reflect_with_state(
        state=shared,
        tool=tool,
        tool_args={"command": "ls"},
        tool_context=ctx,
        error=ValueError("boom"),
    )
    assert second is not None
    assert second["retry_attempt"] == 2, second["retry_attempt"]
