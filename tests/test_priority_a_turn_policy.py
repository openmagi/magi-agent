from __future__ import annotations

from copy import deepcopy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openmagi_core_agent.runtime.turn_policy import (
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
    ContextOverflowError,
    StopReasonHandlerState,
    classify_stop_reason,
    handle_stop_reason,
    is_context_overflow_error,
    sanitize_messages_for_llm,
)


class RecordingDeps:
    def __init__(self) -> None:
        self.audits: list[dict[str, Any]] = []
        self.unknowns: list[dict[str, Any]] = []

    def stage_audit_event(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        audit: dict[str, Any] = {"event": event}
        if data is not None:
            audit["data"] = data
        self.audits.append(audit)

    def log_unknown(self, raw: str | None, turn_id: str) -> None:
        self.unknowns.append({"raw": raw, "turn_id": turn_id})


def _state(recovery_attempt: int = 0, text_len: int = 0) -> StopReasonHandlerState:
    return StopReasonHandlerState(
        recovery_attempt=recovery_attempt,
        assistant_text_so_far_len=text_len,
    )


def test_classifies_canonical_stop_reasons_and_unknown_values() -> None:
    for raw in (
        "end_turn",
        "tool_use",
        "stop_sequence",
        "max_tokens",
        "refusal",
        "pause_turn",
    ):
        assert classify_stop_reason(raw) == raw

    assert classify_stop_reason(None) == "unknown"
    assert classify_stop_reason("future_wire_reason") == "unknown"


@pytest.mark.parametrize("stop_reason", ("end_turn", "stop_sequence"))
def test_end_turn_and_stop_sequence_finalise_without_audit(stop_reason: str) -> None:
    deps = RecordingDeps()
    state = _state()
    messages: list[dict[str, Any]] = []

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw=stop_reason,
        blocks=[{"type": "text", "text": "done"}],
        iteration=0,
        turn_id="turn_1",
        messages=messages,
    )

    assert decision.kind == "finalise"
    assert deps.audits == []
    assert messages == []
    assert state.recovery_attempt == 0


def test_refusal_stages_rule_check_violation_and_finalises() -> None:
    deps = RecordingDeps()

    decision = handle_stop_reason(
        deps,
        _state(),
        stop_reason_raw="refusal",
        blocks=[],
        iteration=2,
        turn_id="turn_1",
        messages=[],
    )

    assert decision.kind == "finalise"
    assert deps.audits == [
        {
            "event": "rule_check_violation",
            "data": {
                "reason": "model_refusal",
                "stop_reason": "refusal",
                "iteration": 2,
            },
        },
    ]


def test_unknown_stop_reason_logs_audits_and_finalises() -> None:
    deps = RecordingDeps()

    decision = handle_stop_reason(
        deps,
        _state(),
        stop_reason_raw="new_reason",
        blocks=[],
        iteration=4,
        turn_id="turn_unknown",
        messages=[],
    )

    assert decision.kind == "finalise"
    assert deps.unknowns == [{"raw": "new_reason", "turn_id": "turn_unknown"}]
    assert deps.audits == [
        {
            "event": "stop_reason_unknown",
            "data": {"raw": "new_reason", "iteration": 4},
        },
    ]


def test_tool_use_with_tool_blocks_returns_run_tools() -> None:
    deps = RecordingDeps()
    tool_block = {
        "type": "tool_use",
        "id": "tu_1",
        "name": "Bash",
        "input": {"cmd": "ls"},
    }

    decision = handle_stop_reason(
        deps,
        _state(),
        stop_reason_raw="tool_use",
        blocks=[{"type": "text", "text": "checking"}, tool_block],
        iteration=0,
        turn_id="turn_1",
        messages=[],
    )

    assert decision.kind == "run_tools"
    assert decision.tool_uses == [tool_block]
    assert deps.audits == []


def test_tool_use_without_tool_blocks_finalises_defensively() -> None:
    decision = handle_stop_reason(
        RecordingDeps(),
        _state(),
        stop_reason_raw="tool_use",
        blocks=[{"type": "text", "text": "no tool block"}],
        iteration=0,
        turn_id="turn_1",
        messages=[],
    )

    assert decision.kind == "finalise"


@pytest.mark.parametrize("stop_reason", ("max_tokens", "pause_turn"))
def test_output_recovery_within_cap_appends_filtered_blocks_and_continue(
    stop_reason: str,
) -> None:
    deps = RecordingDeps()
    state = _state(recovery_attempt=1, text_len=25)
    messages: list[dict[str, Any]] = []

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw=stop_reason,
        blocks=[{"type": "text", "text": "partial"}],
        iteration=3,
        turn_id="turn_1",
        messages=messages,
    )

    assert decision.kind == "recover"
    assert state.recovery_attempt == 2
    assert messages == [
        {"role": "assistant", "content": [{"type": "text", "text": "partial"}]},
        {"role": "user", "content": "Continue."},
    ]
    assert deps.audits == [
        {
            "event": "output_recovery",
            "data": {
                "iteration": 3,
                "recoveryAttempt": 2,
                "stop_reason": stop_reason,
            },
        },
    ]


def test_recovery_drops_unresolved_tool_use_blocks_and_audits_drop() -> None:
    deps = RecordingDeps()
    messages: list[dict[str, Any]] = []

    decision = handle_stop_reason(
        deps,
        _state(),
        stop_reason_raw="max_tokens",
        blocks=[
            {"type": "text", "text": "calling "},
            {"type": "tool_use", "id": "tu_partial", "name": "Bash", "input": {}},
        ],
        iteration=7,
        turn_id="turn_1",
        messages=messages,
    )

    assert decision.kind == "recover"
    assert messages == [
        {"role": "assistant", "content": [{"type": "text", "text": "calling "}]},
        {"role": "user", "content": "Continue."},
    ]
    assert deps.audits[0] == {
        "event": "output_recovery_drop_unresolved_tool_use",
        "data": {"dropped": 1, "iter": 7, "recoveryAttempt": 0},
    }
    assert deps.audits[1]["event"] == "output_recovery"


def test_recovery_with_only_tool_use_appends_only_continue() -> None:
    deps = RecordingDeps()
    messages: list[dict[str, Any]] = []

    decision = handle_stop_reason(
        deps,
        _state(),
        stop_reason_raw="pause_turn",
        blocks=[{"type": "tool_use", "id": "tu_partial", "name": "Bash", "input": {}}],
        iteration=0,
        turn_id="turn_1",
        messages=messages,
    )

    assert decision.kind == "recover"
    assert messages == [{"role": "user", "content": "Continue."}]
    assert deps.audits[0]["event"] == "output_recovery_drop_unresolved_tool_use"


def test_output_recovery_at_cap_audits_exhaustion_and_finalises() -> None:
    deps = RecordingDeps()
    state = _state(
        recovery_attempt=MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
        text_len=42,
    )
    messages: list[dict[str, Any]] = []

    decision = handle_stop_reason(
        deps,
        state,
        stop_reason_raw="max_tokens",
        blocks=[{"type": "text", "text": "partial"}],
        iteration=5,
        turn_id="turn_1",
        messages=messages,
    )

    assert decision.kind == "finalise"
    assert state.recovery_attempt == MAX_OUTPUT_TOKENS_RECOVERY_LIMIT
    assert messages == []
    assert deps.audits == [
        {
            "event": "output_recovery_exhausted",
            "data": {
                "finalLength": 42,
                "limit": MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
                "stop_reason": "max_tokens",
            },
        },
    ]


@pytest.mark.parametrize(
    "message",
    (
        "prompt is too long for this model",
        "max_tokens_exceeded: input is 250000 tokens",
        "context_length_exceeded",
        "request entity too large",
        "input payload is too large",
        "input is too long",
        "exceeds model context",
        "maximum context length reached",
    ),
)
def test_http_400_context_overflow_patterns_are_detected(message: str) -> None:
    assert is_context_overflow_error("http_400", message) is True


def test_any_http_413_is_context_overflow() -> None:
    assert is_context_overflow_error("http_413", "unrelated upstream text") is True


@pytest.mark.parametrize(
    ("code", "message"),
    (
        ("http_400", "invalid_api_key"),
        ("http_401", "prompt is too long"),
        ("http_500", "context_length_exceeded"),
        ("provider_error", "request entity too large"),
    ),
)
def test_context_overflow_rejects_unrelated_codes_and_messages(
    code: str,
    message: str,
) -> None:
    assert is_context_overflow_error(code, message) is False


def test_context_overflow_error_stores_http_code_and_upstream_message() -> None:
    err = ContextOverflowError("http_400", "prompt is too long")

    assert isinstance(err, Exception)
    assert err.http_code == "http_400"
    assert err.upstream_message == "prompt is too long"
    assert err.__class__.__name__ == "ContextOverflowError"
    assert str(err) == "Context overflow (http_400): prompt is too long"


def test_sanitizer_returns_new_list_without_mutating_input_or_content_arrays() -> None:
    content = [
        {"type": "text", "text": "I will help"},
        {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"cmd": "ls"}},
    ]
    messages = [
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": content},
        {"role": "user", "content": "next"},
    ]
    before = deepcopy(messages)

    result = sanitize_messages_for_llm(messages)

    assert result is not messages
    assert messages == before
    assert content == before[1]["content"]
    assert result == [
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": [{"type": "text", "text": "I will help"}]},
        {"role": "user", "content": "next"},
    ]


def test_sanitizer_strips_trailing_assistant_messages_including_block_content() -> None:
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow-up"},
        {"role": "assistant", "content": [{"type": "text", "text": "partial"}]},
        {"role": "assistant", "content": "second partial"},
    ]

    result = sanitize_messages_for_llm(messages)

    assert result == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow-up"},
    ]


def test_sanitizer_strips_orphaned_tool_use_and_removes_empty_assistant() -> None:
    messages = [
        {"role": "user", "content": "run something"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}},
                {"type": "tool_use", "id": "tu_2", "name": "Read", "input": {}},
            ],
        },
        {"role": "user", "content": "never mind"},
    ]

    result = sanitize_messages_for_llm(messages)

    assert result == [
        {"role": "user", "content": "run something"},
        {"role": "user", "content": "never mind"},
    ]


def test_sanitizer_strips_orphaned_and_duplicate_tool_results() -> None:
    messages = [
        {"role": "user", "content": "run something"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "while waiting"},
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "duplicate"},
                {"type": "tool_result", "tool_use_id": "tu_missing", "content": "stale"},
                {"type": "text", "text": "after result"},
            ],
        },
    ]

    result = sanitize_messages_for_llm(messages)

    assert result[-1]["content"] == [
        {"type": "text", "text": "while waiting"},
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
        {"type": "text", "text": "after result"},
    ]


def test_sanitizer_removes_user_message_that_becomes_empty_after_tool_result_strip() -> None:
    messages = [
        {"role": "assistant", "content": "no tool call"},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "orphan", "content": "stale"},
            ],
        },
    ]

    result = sanitize_messages_for_llm(messages)

    assert result == []


def test_sanitizer_preserves_matched_pair_and_mixed_tool_result_order() -> None:
    messages = [
        {"role": "user", "content": "run something"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "while waiting"},
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
                {"type": "text", "text": "new instruction"},
            ],
        },
    ]

    result = sanitize_messages_for_llm(messages)

    assert result == messages


def test_sanitizer_preserves_consecutive_user_messages_without_merging() -> None:
    messages = [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
    ]

    assert sanitize_messages_for_llm(messages) == messages


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_turn_policy_import_is_pure_local_metadata_only() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.runtime.turn_policy")
assert hasattr(module, "handle_stop_reason")
assert hasattr(module, "sanitize_messages_for_llm")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "fastapi",
    "starlette.routing",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.local_runner",
    "openmagi_core_agent.tools.dispatcher",
)
forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.children",
    "openmagi_core_agent.missions",
    "kubernetes",
    "supabase",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"turn_policy import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_turn_policy_source_forbids_runtime_side_effect_imports() -> None:
    root = Path(__file__).parents[1]
    module_path = root / "openmagi_core_agent" / "runtime" / "turn_policy.py"
    source = module_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "asyncio",
        "fastapi",
        "starlette",
        "kubernetes",
        "supabase",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.workspace",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.channels",
        "openmagi_core_agent.children",
        "openmagi_core_agent.missions",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "kubectl" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
