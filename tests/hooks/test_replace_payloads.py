"""Smoke tests for typed replace payloads (F-MUT prerequisite, default-OFF).

The payloads in :mod:`magi_agent.hooks.replace_payloads` only define shapes —
they MUST NOT be wired into any emitter yet. These tests assert (a) the typed
schemas accept the documented shape per HookPoint, (b) malformed values
fail-safe to ``None`` (matching ``_apply_prompt_transform`` semantics), and
(c) no current emitter imports ``coerce_replace_payload`` so production
behavior is byte-identical to today.
"""

from __future__ import annotations

import pathlib

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


class TestNoConsumerWiredYet:
    """Guard: shipping this module must not change runtime behavior.

    F-MUT1/F-MUT2 will add the actual ``coerce_replace_payload`` call sites in
    ``facades.py`` and ``adk_bridge/callback_adapter.py``. Until then, this
    test pins that the module is purely additive — no production code path
    references it, so emitting a replace value remains inert exactly as the
    audit documented.
    """

    def test_no_runtime_module_imports_coerce_replace_payload(self) -> None:
        repo_root = pathlib.Path(__file__).resolve().parents[2] / "magi_agent"
        offenders: list[str] = []
        for path in repo_root.rglob("*.py"):
            if path.name == "replace_payloads.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "coerce_replace_payload" in text or "REPLACE_PAYLOAD_BY_POINT" in text:
                offenders.append(str(path.relative_to(repo_root.parent)))
        assert offenders == [], (
            "Replace payloads must remain unwired in this PR; F-MUT1/F-MUT2 "
            f"will add consumers. Found references in: {offenders}"
        )
