"""Smoke tests for typed replace payloads.

The payloads in :mod:`magi_agent.hooks.replace_payloads` define typed shapes
consumed by F-MUT1+ wiring. These tests assert (a) the typed schemas accept
the documented shape per HookPoint and (b) malformed values fail-safe to
``None`` (matching ``_apply_prompt_transform`` semantics).
"""

from __future__ import annotations

import pytest

from magi_agent.hooks.manifest import HookPoint
from magi_agent.hooks.replace_payloads import (
    AfterToolUseReplace,
    BeforeToolUseReplace,
    OnErrorReplace,
    REPLACE_PAYLOAD_BY_POINT,
    coerce_replace_payload,
)


class TestReplacePayloadShapes:
    def test_before_tool_use_accepts_arguments_dict(self) -> None:
        payload = coerce_replace_payload(
            HookPoint.BEFORE_TOOL_USE, {"arguments": {"path": "/tmp/x"}}
        )
        assert isinstance(payload, BeforeToolUseReplace)
        assert payload.arguments == {"path": "/tmp/x"}

    def test_after_tool_use_accepts_partial_fields(self) -> None:
        payload = coerce_replace_payload(
            HookPoint.AFTER_TOOL_USE, {"result_text": "redacted", "status": "ok"}
        )
        assert isinstance(payload, AfterToolUseReplace)
        assert payload.result_text == "redacted"
        assert payload.status == "ok"
        assert payload.structured_data is None

    def test_on_error_requires_recovery(self) -> None:
        payload = coerce_replace_payload(
            HookPoint.ON_ERROR, {"recovery": "retry", "backoff_ms": 250}
        )
        assert isinstance(payload, OnErrorReplace)
        assert payload.recovery == "retry"
        assert payload.backoff_ms == 250

    def test_all_seven_emitter_events_have_schemas(self) -> None:
        # Audit confirms 7 events with real emitters need replace consumers
        # (BEFORE_SYSTEM_PROMPT stays on list[str] for back-compat).
        expected = {
            HookPoint.BEFORE_TOOL_USE,
            HookPoint.AFTER_TOOL_USE,
            HookPoint.BEFORE_LLM_CALL,
            HookPoint.AFTER_LLM_CALL,
            HookPoint.BEFORE_TURN_START,
            HookPoint.AFTER_TURN_END,
            HookPoint.ON_ERROR,
        }
        assert set(REPLACE_PAYLOAD_BY_POINT.keys()) == expected


class TestReplacePayloadFailSafe:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            "string",
            42,
            [],
            {"arguments": "not-a-dict"},  # type mismatch
            {"unknown_field": 1},  # extra="forbid"
        ],
    )
    def test_malformed_before_tool_use_returns_none(self, value: object) -> None:
        assert coerce_replace_payload(HookPoint.BEFORE_TOOL_USE, value) is None

    def test_event_without_schema_returns_none(self) -> None:
        # BEFORE_SYSTEM_PROMPT is intentionally absent from the table.
        assert (
            coerce_replace_payload(HookPoint.BEFORE_SYSTEM_PROMPT, {"sections": ["a"]})
            is None
        )

    def test_invalid_recovery_literal_returns_none(self) -> None:
        assert (
            coerce_replace_payload(HookPoint.ON_ERROR, {"recovery": "nuke-from-orbit"})
            is None
        )
