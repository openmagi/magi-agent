"""Typed payload schemas for ``HookResult(action='replace', value=...)``.

F-MUT prerequisite (audit 2026-06-24, extension #5). This module ONLY defines
shapes — it does not wire any consumer, so importing/using it from a hook
handler is a pure no-op at runtime today. Existing replace consumers continue
to read ``HookResult.value`` as a plain object; new consumers (F-MUT1/F-MUT2
PRs) will call :func:`coerce_replace_payload` at the projection site to
validate and substitute. Until a consumer ships per event, hooks that emit a
replace value remain functionally inert (matching today's audit-confirmed
behavior); the schemas here exist so the customize wizard's authoring kinds
can reference stable shape names without producing dead UI bindings.

See ``docs/architecture/hookbus-replace-contract-audit-2026-06-24.md`` for the
per-event matrix this schema set mirrors.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from magi_agent.hooks.manifest import HookPoint
from magi_agent.tools.result import ToolStatus


class BeforeToolUseReplace(BaseModel):
    """Replace tool ``arguments`` before dispatch (F-MUT1 headline kind)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    arguments: dict[str, object]


class AfterToolUseReplace(BaseModel):
    """Replace ``ToolResult`` fields after dispatch (F-MUT2 output-rewrite).

    ``status`` aligns with :data:`magi_agent.tools.result.ToolStatus` so the
    F-MUT2 facades projection (``result.model_copy(update={'status': ...})``,
    which bypasses pydantic validation) cannot land an out-of-vocabulary
    status. The earlier draft used ``Literal['ok','failed']`` which silently
    leaked ``'failed'`` into ToolResult.status (illegal per ToolStatus).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    result_text: str | None = None
    structured_data: dict[str, object] | None = None
    status: ToolStatus | None = None


class BeforeLlmCallReplace(BaseModel):
    """Replace LLM request fields before call (deferred until callback_adapter consumes)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    messages: list[dict[str, object]] | None = None
    system: list[str] | None = None
    tool_choice: dict[str, object] | None = None
    temperature: float | None = None
    model: str | None = None


class AfterLlmCallReplace(BaseModel):
    """Replace LLM response content after call (PII redactor / refusal scrubber)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str | list[dict[str, object]] | None = None
    tool_calls: list[dict[str, object]] | None = None


class BeforeTurnStartReplace(BaseModel):
    """Patch session state at turn start."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    state_patch: dict[str, object] | None = None
    session_vars: dict[str, object] | None = None


class AfterTurnEndReplace(BaseModel):
    """Rewrite outbound assistant text + queue follow-ups."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    assistant_text: str | None = None
    follow_up_messages: list[dict[str, object]] | None = None


class OnErrorReplace(BaseModel):
    """Recovery directive for model/tool errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    recovery: Literal["retry", "swallow", "rephrase"]
    synthetic_response: dict[str, object] | None = None
    backoff_ms: int = 0


# Lookup keyed by HookPoint. ``BEFORE_SYSTEM_PROMPT`` is intentionally absent:
# its consumer (``message_builder._apply_prompt_transform``) takes a bare
# ``list[str]``, which predates this typed-payload layer and stays as-is for
# back-compat (audit row "BEFORE_SYSTEM_PROMPT" — already F-MUT-ready).
REPLACE_PAYLOAD_BY_POINT: dict[HookPoint, type[BaseModel]] = {
    HookPoint.BEFORE_TOOL_USE: BeforeToolUseReplace,
    HookPoint.AFTER_TOOL_USE: AfterToolUseReplace,
    HookPoint.BEFORE_LLM_CALL: BeforeLlmCallReplace,
    HookPoint.AFTER_LLM_CALL: AfterLlmCallReplace,
    HookPoint.BEFORE_TURN_START: BeforeTurnStartReplace,
    HookPoint.AFTER_TURN_END: AfterTurnEndReplace,
    HookPoint.ON_ERROR: OnErrorReplace,
}


def coerce_replace_payload(point: HookPoint, value: object) -> BaseModel | None:
    """Validate ``value`` against the schema for ``point``; return None on mismatch.

    Mirrors the fail-safe-original semantics in
    ``magi_agent/runtime/message_builder.py:_apply_prompt_transform``: a
    malformed replace value is dropped silently (caller falls back to original
    payload), never raised. Returns ``None`` when:
        - ``point`` has no registered schema,
        - ``value`` is ``None``,
        - ``value`` is not a mapping,
        - ``value`` fails schema validation.
    """
    schema = REPLACE_PAYLOAD_BY_POINT.get(point)
    if schema is None or not isinstance(value, dict):
        return None
    try:
        return schema.model_validate(value)
    except Exception:
        return None


__all__ = [
    "AfterLlmCallReplace",
    "AfterToolUseReplace",
    "AfterTurnEndReplace",
    "BeforeLlmCallReplace",
    "BeforeToolUseReplace",
    "BeforeTurnStartReplace",
    "OnErrorReplace",
    "REPLACE_PAYLOAD_BY_POINT",
    "coerce_replace_payload",
]
