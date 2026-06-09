"""Tests for the PR-F2b headless stream-json projection, command dispatch, and
sink wiring.

Style: this package has no ``pytest-asyncio``; async code is driven via
``asyncio.run``. The engine is always a fake/stub driver — no model is hit.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import threading

import pytest

from magi_agent.cli.contracts import (
    Command,
    CommandSurface,
    ContentBlock,
    ControlRequest,
    EngineResult,
    LocalCommand,
    PermissionDecision,
    PromptCommand,
    RuntimeEvent,
    Terminal,
    Text,
)
from magi_agent.cli.headless import run_headless
from magi_agent.cli.permissions import RulesPermissionGate

BOTH = CommandSurface(tui=True, headless=True)
HEADLESS = CommandSurface(tui=False, headless=True)
TUI_ONLY = CommandSurface(tui=True, headless=False)


# ---------------------------------------------------------------------------
# Scripted fake driver: yields a fixed RuntimeEvent list then a terminal.
# ---------------------------------------------------------------------------
class ScriptedDriver:
    """Yields a caller-supplied RuntimeEvent script, then the terminal result.

    Optionally calls ``gate.check`` once (so the sink-wiring path is exercised)
    and records the prompt/turn_input it was driven with (so command dispatch
    can be asserted).
    """

    def __init__(
        self,
        events: list[RuntimeEvent],
        *,
        terminal: Terminal = Terminal.completed,
        error: str | None = None,
        ask_tool: str | None = None,
    ) -> None:
        self._events = events
        self._terminal = terminal
        self._error = error
        self._ask_tool = ask_tool
        self.seen_input: object | None = None
        self.gate_decision: PermissionDecision | None = None

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        self.seen_input = turn_input
        for event in self._events:
            yield event
        if self._ask_tool is not None and gate is not None:
            req = ControlRequest(
                requestId="req-1",
                turnId="turn-1",
                toolName=self._ask_tool,
                arguments={"cmd": "ls"},
                reason="needs approval",
            )
            self.gate_decision = await gate.check(req)
        yield EngineResult(  # type: ignore[misc]
            terminal=self._terminal,
            usage={"input_tokens": 1, "output_tokens": 2},
            cost_usd=0.0,
            error=self._error,
        )


def _objs(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line]


def _composio_leak_probe() -> str:
    return (
        "open https://connect.composio.dev/link/ln_secret "
        "connectedAccountId: acct_live_12345 "
        "x-composio-session: sess_123 "
        "Authorization: Bearer bearer_secret"
    )


def _split_composio_leak_events() -> list[RuntimeEvent]:
    parts = (
        "open https://connect.composio.dev/li",
        "nk/ln_secret connectedAcc",
        "ountId: acct_live_12345 x-compo",
        "sio-session: sess_123 Authorization: Bea",
        "rer bearer_secret",
    )
    return [
        RuntimeEvent(type="token", payload={"delta": part}, turn_id="t")
        for part in parts
    ]


def _partial_redaction_artifact_events() -> list[RuntimeEvent]:
    parts = (
        "open [redacted-composio-connect-url]",
        "nk/ln_secret connectedAccountId: [redacted-composio-id] ",
        "x-composio-session: [redacted-composio-secret] ",
        "Authorization: Bearer bea",
        "rer_secret",
    )
    return [
        RuntimeEvent(type="token", payload={"delta": part}, turn_id="t")
        for part in parts
    ]


def _adversarial_redaction_suffix_events() -> list[RuntimeEvent]:
    parts = (
        "[redacted-composio-connect-url]",
        "_secret_tail987654321 ",
        "Authorization: Bearer [redacted]",
        "defghijklmnopqrstuvwxyz0123456789",
    )
    return [
        RuntimeEvent(type="token", payload={"delta": part}, turn_id="t")
        for part in parts
    ]


def _adversarial_redaction_suffix_text() -> str:
    return (
        "[redacted-composio-connect-url]_secret_tail987654321 "
        "Authorization: Bearer [redacted]defghijklmnopqrstuvwxyz0123456789"
    )


def _adversarial_redaction_alpha_suffix_text() -> str:
    return (
        "[redacted-composio-connect-url]abcdefghijklmnopqrstuvwxyz "
        "Authorization: Bearer [redacted]abcdefghijklmnopqrstuvwxyz"
    )


def _adversarial_redaction_punct_suffix_text() -> str:
    return (
        "[redacted-composio-connect-url].abcdefghijklmnopqrstuvwxyz "
        "[redacted-composio-connect-url]-abcdefghijklmnopqrstuvwxyz "
        "[redacted-composio-connect-url]:abcdefghijklmnopqrstuvwxyz "
        "Authorization: Bearer [redacted].abcdefghijklmnopqrstuvwxyz "
        "Authorization: Bearer [redacted]-abcdefghijklmnopqrstuvwxyz "
        "Authorization: Bearer [redacted]:abcdefghijklmnopqrstuvwxyz"
    )


def _assert_composio_probe_redacted(body: str) -> None:
    assert "ln_secret" not in body
    assert "acct_live_12345" not in body
    assert "sess_123" not in body
    assert "bearer_secret" not in body
    assert "[redacted-composio-connect-url]" in body
    assert "[redacted-composio-id]" in body
    assert "[redacted-composio-secret]" in body


def _assert_split_composio_probe_redacted(body: str) -> None:
    _assert_composio_probe_redacted(body)
    assert "https://connect.composio.dev/li" not in body
    assert "nk/ln_secret" not in body
    assert "rer bearer_secret" not in body


def _assert_partial_redaction_artifact_safe(body: str) -> None:
    assert "ln_secret" not in body
    assert "bearer_secret" not in body
    assert "nk/ln_secret" not in body
    assert "rer_secret" not in body
    assert "[redacted-composio-output]" in body


def _assert_adversarial_redaction_suffix_safe(body: str) -> None:
    assert "_secret_tail987654321" not in body
    assert "defghijklmnopqrstuvwxyz0123456789" not in body
    assert "[redacted-composio-connect-url]_secret" not in body
    assert "Bearer [redacted]defgh" not in body
    assert "[redacted-composio-output]" in body


def _assert_adversarial_redaction_alpha_suffix_safe(body: str) -> None:
    assert "[redacted-composio-connect-url]abcdefghijklmnopqrstuvwxyz" not in body
    assert "Bearer [redacted]abcdefghijklmnopqrstuvwxyz" not in body
    assert "[redacted-composio-output]" in body


def _assert_adversarial_redaction_punct_suffix_safe(body: str) -> None:
    assert "[redacted-composio-connect-url].abcdefghijklmnopqrstuvwxyz" not in body
    assert "[redacted-composio-connect-url]-abcdefghijklmnopqrstuvwxyz" not in body
    assert "[redacted-composio-connect-url]:abcdefghijklmnopqrstuvwxyz" not in body
    assert "Bearer [redacted].abcdefghijklmnopqrstuvwxyz" not in body
    assert "Bearer [redacted]-abcdefghijklmnopqrstuvwxyz" not in body
    assert "Bearer [redacted]:abcdefghijklmnopqrstuvwxyz" not in body
    assert "[redacted-composio-output]" in body


# ---------------------------------------------------------------------------
# 1. Per-token consolidation: many token deltas -> ONE assistant frame.
# ---------------------------------------------------------------------------
def test_token_deltas_consolidate_to_one_assistant_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(type="token", payload={"delta": "Hello"}, turn_id="t"),
        RuntimeEvent(type="token", payload={"delta": " "}, turn_id="t"),
        RuntimeEvent(type="token", payload={"delta": "world"}, turn_id="t"),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )
    objs = _objs(buffer)
    assistant = [o for o in objs if o["type"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["message"]["content"] == "Hello world"


def test_stream_json_redacts_composio_assistant_and_result_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(type="token", payload={"delta": _composio_leak_probe()}, turn_id="t"),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    assistant_frames = [o for o in objs if o["type"] == "assistant"]
    result_frames = [o for o in objs if o["type"] == "result"]

    assert assistant_frames
    assert result_frames
    _assert_composio_probe_redacted(body)


def test_stream_json_redacts_split_composio_assistant_and_result_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver(_split_composio_leak_events()),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    assistant_frames = [o for o in objs if o["type"] == "assistant"]
    result_frames = [o for o in objs if o["type"] == "result"]

    assert assistant_frames
    assert result_frames
    _assert_split_composio_probe_redacted(body)


@pytest.mark.parametrize("include_partial", (False, True))
def test_stream_json_replaces_partial_composio_redaction_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    include_partial: bool,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=include_partial,
            driver=ScriptedDriver(_partial_redaction_artifact_events()),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    assistant_frames = [o for o in objs if o["type"] == "assistant"]
    result_frames = [o for o in objs if o["type"] == "result"]

    assert assistant_frames
    assert result_frames
    _assert_partial_redaction_artifact_safe(body)
    if include_partial:
        stream_events = [o for o in objs if o["type"] == "stream_event"]
        assert stream_events
        assert all(
            event["event"]["payload"].get("delta") == "[redacted]"
            for event in stream_events
        )


@pytest.mark.parametrize("include_partial", (False, True))
def test_stream_json_replaces_adversarial_composio_redaction_suffixes(
    monkeypatch: pytest.MonkeyPatch,
    include_partial: bool,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=include_partial,
            driver=ScriptedDriver(_adversarial_redaction_suffix_events()),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    assistant_frames = [o for o in objs if o["type"] == "assistant"]
    result_frames = [o for o in objs if o["type"] == "result"]

    assert assistant_frames
    assert result_frames
    _assert_adversarial_redaction_suffix_safe(body)
    if include_partial:
        stream_events = [o for o in objs if o["type"] == "stream_event"]
        assert stream_events
        assert all(
            event["event"]["payload"].get("delta") == "[redacted]"
            for event in stream_events
        )


@pytest.mark.parametrize("output", ("json", "text"))
def test_collect_output_redacts_composio_assistant_text(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(type="token", payload={"delta": _composio_leak_probe()}, turn_id="t"),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output=output,  # type: ignore[arg-type]
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )

    _assert_composio_probe_redacted(buffer.getvalue())


@pytest.mark.parametrize("output", ("json", "text"))
def test_collect_output_redacts_split_composio_assistant_text(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output=output,  # type: ignore[arg-type]
            driver=ScriptedDriver(_split_composio_leak_events()),
            stream=buffer,
        )
    )

    _assert_split_composio_probe_redacted(buffer.getvalue())


@pytest.mark.parametrize("output", ("json", "text"))
def test_collect_output_replaces_partial_composio_redaction_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output=output,  # type: ignore[arg-type]
            driver=ScriptedDriver(_partial_redaction_artifact_events()),
            stream=buffer,
        )
    )

    _assert_partial_redaction_artifact_safe(buffer.getvalue())


@pytest.mark.parametrize("output", ("json", "text"))
def test_collect_output_replaces_adversarial_composio_redaction_suffixes(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output=output,  # type: ignore[arg-type]
            driver=ScriptedDriver(_adversarial_redaction_suffix_events()),
            stream=buffer,
        )
    )

    _assert_adversarial_redaction_suffix_safe(buffer.getvalue())


def test_include_partial_redacts_split_composio_token_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=ScriptedDriver(_split_composio_leak_events()),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    stream_events = [o for o in _objs(buffer) if o["type"] == "stream_event"]

    assert stream_events
    assert all(
        event["event"]["payload"].get("delta") == "[redacted]"
        for event in stream_events
    )
    _assert_split_composio_probe_redacted(body)


@pytest.mark.parametrize("output", ("stream-json", "json"))
def test_result_errors_redact_composio_error_text(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output=output,  # type: ignore[arg-type]
            driver=ScriptedDriver(
                [],
                terminal=Terminal.error,
                error=_composio_leak_probe(),
            ),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    result_frames = [o for o in objs if o["type"] == "result"]

    assert result_frames
    assert result_frames[-1]["is_error"] is True
    _assert_composio_probe_redacted(body)


@pytest.mark.parametrize("output", ("stream-json", "json"))
def test_result_errors_replace_adversarial_composio_redaction_suffixes(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output=output,  # type: ignore[arg-type]
            driver=ScriptedDriver(
                [],
                terminal=Terminal.error,
                error=_adversarial_redaction_suffix_text(),
            ),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    result_frames = [o for o in objs if o["type"] == "result"]

    assert result_frames
    assert result_frames[-1]["is_error"] is True
    _assert_adversarial_redaction_suffix_safe(body)


# ---------------------------------------------------------------------------
# 2. A token run is FLUSHED by a non-token event (tool_start) and resumes.
# ---------------------------------------------------------------------------
def test_token_run_flushes_on_tool_then_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(type="token", payload={"delta": "before"}, turn_id="t"),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_start",
                "id": "call-1",
                "name": "Bash",
                "input_preview": '{"cmd":"ls"}',
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_end",
                "id": "call-1",
                "status": "ok",
                "output_preview": "a.txt",
            },
            turn_id="t",
        ),
        RuntimeEvent(type="token", payload={"delta": "after"}, turn_id="t"),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )
    objs = _objs(buffer)
    # Two text assistant frames (before / after) + one tool_use assistant frame.
    text_assistants = [
        o
        for o in objs
        if o["type"] == "assistant" and isinstance(o["message"]["content"], str)
    ]
    assert [o["message"]["content"] for o in text_assistants] == ["before", "after"]


# ---------------------------------------------------------------------------
# 3. tool_start -> assistant frame with a tool_use block (+ parent threading).
# ---------------------------------------------------------------------------
def test_tool_start_emits_assistant_tool_use_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_start",
                "id": "call-9",
                "name": "Bash",
                "input_preview": '{"cmd":"ls"}',
                "parentToolUseId": "parent-7",
            },
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )
    objs = _objs(buffer)
    tool_use_frames = [
        o
        for o in objs
        if o["type"] == "assistant"
        and isinstance(o["message"]["content"], list)
        and any(b.get("type") == "tool_use" for b in o["message"]["content"])
    ]
    assert len(tool_use_frames) == 1
    block = tool_use_frames[0]["message"]["content"][0]
    assert block["id"] == "call-9"
    assert block["name"] == "Bash"
    assert tool_use_frames[0]["parent_tool_use_id"] == "parent-7"


# ---------------------------------------------------------------------------
# 4. tool_end -> user frame with a tool_result block keyed by the same id.
# ---------------------------------------------------------------------------
def test_tool_end_emits_user_tool_result_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_end",
                "id": "call-9",
                "status": "ok",
                "output_preview": "result text",
            },
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )
    objs = _objs(buffer)
    user_frames = [o for o in objs if o["type"] == "user"]
    assert len(user_frames) == 1
    block = user_frames[0]["message"]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call-9"
    assert "result text" in json.dumps(block)


def test_stream_json_redacts_composio_tool_and_status_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_start",
                "id": "call-composio",
                "name": "Composio",
                "input_preview": (
                    '{"url":"https://connect.composio.dev/link/ln_secret",'
                    '"connectedAccountId":"acct_live_12345",'
                    '"headers":{"x-composio-session":"sess_123"}}'
                ),
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_end",
                "id": "call-composio",
                "status": "ok",
                "output_preview": (
                    "connectedAccountId: acct_live_12345 "
                    "x-composio-session: sess_123 "
                    "https://connect.composio.dev/link/ln_secret"
                ),
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="status",
            payload={
                "type": "turn_phase",
                "message": (
                    "connectedAccountId: acct_live_12345 "
                    "x-composio-session: sess_123 "
                    "https://connect.composio.dev/link/ln_secret"
                ),
            },
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    assert "ln_secret" not in body
    assert "acct_live_12345" not in body
    assert "sess_123" not in body
    assert "[redacted-composio-connect-url]" in body
    assert "[redacted-composio-id]" in body
    assert "[redacted-composio-secret]" in body


def test_stream_json_redacts_composio_structured_frame_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    artifact_text = _adversarial_redaction_suffix_text()
    events = [
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_start",
                "id": "call-composio",
                "name": "Composio",
                "input_preview": json.dumps({"probe": artifact_text}),
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_end",
                "id": "call-composio",
                "status": "ok",
                "output_preview": artifact_text,
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="status",
            payload={"type": "turn_phase", "message": artifact_text},
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    tool_use_frames = [
        o
        for o in objs
        if o["type"] == "assistant"
        and isinstance(o["message"]["content"], list)
        and any(b.get("type") == "tool_use" for b in o["message"]["content"])
    ]
    user_frames = [o for o in objs if o["type"] == "user"]
    status_frames = [
        o for o in objs if o["type"] == "system" and o["subtype"] != "init"
    ]
    stream_events = [o for o in objs if o["type"] == "stream_event"]

    assert tool_use_frames
    assert user_frames
    assert status_frames
    assert stream_events
    assert tool_use_frames[0]["message"]["content"][0]["input"]["probe"] == (
        "[redacted-composio-output]"
    )
    assert user_frames[0]["message"]["content"][0]["content"] == (
        "[redacted-composio-output]"
    )
    assert status_frames[0]["payload"]["message"] == "[redacted-composio-output]"
    _assert_adversarial_redaction_suffix_safe(json.dumps(stream_events))
    _assert_adversarial_redaction_suffix_safe(body)


def test_stream_json_redacts_composio_structured_frame_alpha_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    artifact_text = _adversarial_redaction_alpha_suffix_text()
    events = [
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_start",
                "id": "call-composio",
                "name": "Composio",
                "input_preview": json.dumps({"probe": artifact_text}),
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_end",
                "id": "call-composio",
                "status": "ok",
                "output_preview": artifact_text,
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="status",
            payload={"type": "turn_phase", "message": artifact_text},
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    tool_use_frames = [
        o
        for o in objs
        if o["type"] == "assistant"
        and isinstance(o["message"]["content"], list)
        and any(b.get("type") == "tool_use" for b in o["message"]["content"])
    ]
    user_frames = [o for o in objs if o["type"] == "user"]
    status_frames = [
        o for o in objs if o["type"] == "system" and o["subtype"] != "init"
    ]
    stream_events = [o for o in objs if o["type"] == "stream_event"]

    assert tool_use_frames
    assert user_frames
    assert status_frames
    assert stream_events
    assert tool_use_frames[0]["message"]["content"][0]["input"]["probe"] == (
        "[redacted-composio-output]"
    )
    assert user_frames[0]["message"]["content"][0]["content"] == (
        "[redacted-composio-output]"
    )
    assert status_frames[0]["payload"]["message"] == "[redacted-composio-output]"
    _assert_adversarial_redaction_alpha_suffix_safe(json.dumps(stream_events))
    _assert_adversarial_redaction_alpha_suffix_safe(body)


def test_stream_json_redacts_composio_structured_frame_punct_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    artifact_text = _adversarial_redaction_punct_suffix_text()
    events = [
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_start",
                "id": "call-composio",
                "name": "Composio",
                "input_preview": json.dumps({"probe": artifact_text}),
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_end",
                "id": "call-composio",
                "status": "ok",
                "output_preview": artifact_text,
            },
            turn_id="t",
        ),
        RuntimeEvent(
            type="status",
            payload={"type": "turn_phase", "message": artifact_text},
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )

    body = buffer.getvalue()
    objs = _objs(buffer)
    tool_use_frames = [
        o
        for o in objs
        if o["type"] == "assistant"
        and isinstance(o["message"]["content"], list)
        and any(b.get("type") == "tool_use" for b in o["message"]["content"])
    ]
    user_frames = [o for o in objs if o["type"] == "user"]
    status_frames = [
        o for o in objs if o["type"] == "system" and o["subtype"] != "init"
    ]
    stream_events = [o for o in objs if o["type"] == "stream_event"]

    assert tool_use_frames
    assert user_frames
    assert status_frames
    assert stream_events
    assert tool_use_frames[0]["message"]["content"][0]["input"]["probe"] == (
        "[redacted-composio-output]"
    )
    assert user_frames[0]["message"]["content"][0]["content"] == (
        "[redacted-composio-output]"
    )
    assert status_frames[0]["payload"]["message"] == "[redacted-composio-output]"
    _assert_adversarial_redaction_punct_suffix_safe(json.dumps(stream_events))
    _assert_adversarial_redaction_punct_suffix_safe(body)


# ---------------------------------------------------------------------------
# 5. status/artifact events -> SystemStatus frames.
# ---------------------------------------------------------------------------
def test_status_event_emits_system_status_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(
            type="status",
            payload={"type": "turn_phase", "phase": "executing", "label": "working"},
            turn_id="t",
        ),
        RuntimeEvent(
            type="artifact",
            payload={"type": "source_inspected", "source": {"sourceId": "s1"}},
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )
    objs = _objs(buffer)
    statuses = [o for o in objs if o["type"] == "system" and o["subtype"] != "init"]
    assert len(statuses) == 2
    assert all("payload" in o for o in statuses)


# ---------------------------------------------------------------------------
# 6. include_partial still emits raw stream_event frames for every event.
# ---------------------------------------------------------------------------
def test_include_partial_still_emits_stream_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    events = [
        RuntimeEvent(type="token", payload={"delta": "x"}, turn_id="t"),
        RuntimeEvent(
            type="tool",
            payload={"type": "tool_start", "id": "c1", "name": "Bash"},
            turn_id="t",
        ),
    ]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=ScriptedDriver(events),
            stream=buffer,
        )
    )
    objs = _objs(buffer)
    stream_events = [o for o in objs if o["type"] == "stream_event"]
    assert len(stream_events) == 2


# ---------------------------------------------------------------------------
# 7. SystemInit.model defaults to a sensible value (not "stub").
# ---------------------------------------------------------------------------
def test_system_init_model_is_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver([]),
            stream=buffer,
            model="claude-opus-4-8",
        )
    )
    objs = _objs(buffer)
    init = objs[0]
    assert init["subtype"] == "init"
    assert init["model"] == "claude-opus-4-8"


# ---------------------------------------------------------------------------
# Command dispatch — LocalCommand
# ---------------------------------------------------------------------------
class _Registry:
    def __init__(self, commands: list[Command]) -> None:
        self._commands = commands

    def lookup(self, name: str) -> Command | None:
        for c in self._commands:
            if getattr(c, "name", None) == name:
                return c
        return None

    def list_for(self, surface: CommandSurface) -> list[Command]:
        _ = surface
        return list(self._commands)


class _EchoLocal(LocalCommand):
    async def call(self, args, ctx):  # type: ignore[override]
        return Text(text=f"local ran: {args}")


class _EchoPrompt(PromptCommand):
    async def build_prompt(self, args, ctx):  # type: ignore[override]
        return [ContentBlock(type="text", text=f"prompt expanded: {args}")]


def test_local_command_runs_locally_no_engine_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "X"})])
    registry = _Registry([_EchoLocal(name="hello", surface=BOTH)])
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "/hello world",
            output="stream-json",
            driver=driver,
            commands=registry,
            stream=buffer,
        )
    )
    # Local command does NOT drive an engine turn.
    assert driver.seen_input is None
    assert code == 0
    objs = _objs(buffer)
    # The local result text must surface in a frame.
    assert any("local ran: world" in json.dumps(o) for o in objs)


def test_prompt_command_feeds_engine_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "ok"})])
    registry = _Registry([_EchoPrompt(name="ask", surface=BOTH)])
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "/ask something",
            output="stream-json",
            driver=driver,
            commands=registry,
            stream=buffer,
        )
    )
    assert code == 0
    # A PromptCommand drives a turn: the driver was invoked with the expanded
    # content blocks as the turn input.
    assert driver.seen_input is not None
    turn = driver.seen_input
    blocks = turn.get("content") if isinstance(turn, dict) else None
    assert blocks and any(
        getattr(b, "text", None) == "prompt expanded: something" for b in blocks
    )


def test_superpowers_runtime_flag_injects_bundled_skill_into_engine_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When configured ON, /superpowers must feed bundled instructions to the turn."""

    from magi_agent.cli.commands.discovery import build_registry

    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_SUPERPOWERS_RUNTIME_ENABLED", "1")

    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "ok"})])
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "/superpowers",
            output="stream-json",
            driver=driver,
            commands=build_registry(str(tmp_path)),
            stream=buffer,
        )
    )

    assert code == 0
    assert driver.seen_input is not None
    turn = driver.seen_input
    assert isinstance(turn, dict)
    prompt = turn.get("prompt")
    assert isinstance(prompt, str)
    assert "name: using-superpowers" in prompt
    assert "Using Skills" in prompt
    assert "/Users/" not in prompt


def test_superpowers_runtime_flag_off_keeps_local_ack_no_engine_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Explicit OFF keeps the existing intent-only /superpowers behavior."""

    from magi_agent.cli.commands.discovery import build_registry

    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_SUPERPOWERS_RUNTIME_ENABLED", "0")

    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "X"})])
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "/superpowers",
            output="stream-json",
            driver=driver,
            commands=build_registry(str(tmp_path)),
            stream=buffer,
        )
    )

    assert code == 0
    assert driver.seen_input is None
    assert "superpowers: command_intent" in buffer.getvalue()


def test_unknown_slash_command_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "X"})])
    registry = _Registry([])
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "/nope",
            output="stream-json",
            driver=driver,
            commands=registry,
            stream=buffer,
        )
    )
    # No engine turn ran; an error/unknown frame surfaced; nonzero exit.
    assert driver.seen_input is None
    assert code != 0
    objs = _objs(buffer)
    assert any("unknown" in json.dumps(o).lower() for o in objs)


def test_non_slash_prompt_runs_turn_as_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "hi"})])
    registry = _Registry([_EchoLocal(name="hello", surface=BOTH)])
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "just a prompt",
            output="stream-json",
            driver=driver,
            commands=registry,
            stream=buffer,
        )
    )
    assert code == 0
    # A non-slash prompt drives a turn with the raw prompt.
    assert driver.seen_input is not None
    assert driver.seen_input.get("prompt") == "just a prompt"


# ---------------------------------------------------------------------------
# Sink wiring — gate.ask emits a real control_request frame, answered inbound.
# ---------------------------------------------------------------------------
def test_sink_wired_to_gate_and_inbound_reader_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    # A rules gate with NO sinks of its own and an "ask" default verdict.
    gate = RulesPermissionGate()
    driver = ScriptedDriver([], ask_tool="Bash")

    # The inbound stream answers the control_request once it appears. We can't
    # know the request_id ahead of time, so the fake reader echoes an allow for
    # whatever request_id the sink emits — but run_headless reads inbound lines,
    # not us. Instead we feed a control_response whose request_id matches the
    # deterministic id the gate's callback mints. The gate here uses the SINK
    # race, and HeadlessSink emits a frame with req.request_id == "req-1".
    inbound = io.StringIO(
        json.dumps(
            {
                "type": "control_response",
                "request_id": "req-1",
                "response": {"decision": "allow"},
            }
        )
        + "\n"
    )
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "do it",
            output="stream-json",
            gate=gate,
            driver=driver,
            stream=buffer,
            input_stream=inbound,
        )
    )
    # The gate asked, the sink emitted a control_request, the inbound reader
    # delivered an allow -> the decision is allow.
    assert driver.gate_decision is not None
    assert driver.gate_decision.kind == "allow"
    objs = _objs(buffer)
    assert any(o["type"] == "control_request" for o in objs)


def test_no_input_stream_does_not_block_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    # No ask -> no sink wait. With input_stream=None we must not start a blocking
    # reader and the run completes promptly.
    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "x"})])
    gate = RulesPermissionGate()
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            gate=gate,
            driver=driver,
            stream=buffer,
            input_stream=None,
        )
    )
    assert code == 0


def test_gate_without_sink_falls_back_to_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    # No input_stream provided and the sink never gets an answer -> the gate's
    # race falls back to deny (we never auto-allow). The inbound reader is not
    # started, so the sink's ask future is cancelled on teardown -> deny.
    driver = ScriptedDriver([], ask_tool="Bash")
    gate = RulesPermissionGate()
    buffer = io.StringIO()

    # Feed an EMPTY inbound stream (EOF immediately): the reader runs but
    # delivers nothing, so the ask cannot be answered -> safe deny.
    inbound = io.StringIO("")
    asyncio.run(
        run_headless(
            "do it",
            output="stream-json",
            gate=gate,
            driver=driver,
            stream=buffer,
            input_stream=inbound,
        )
    )
    assert driver.gate_decision is not None
    assert driver.gate_decision.kind == "deny"


# ---------------------------------------------------------------------------
# FIX 1: a still-open / blocking inbound pipe must NEVER gate run/teardown.
# ---------------------------------------------------------------------------
class _BlockingStream:
    """A stream whose ``readline`` blocks indefinitely (until closed).

    Models a controller that keeps the stdin write-end open: ``readline`` never
    returns EOF. The daemon-thread reader (FIX 1) must not gate process exit on
    such a stream — i.e. ``run_headless`` returns promptly even though this
    reader thread is still blocked.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def readline(self) -> str:
        # Block forever (the daemon thread parks here); never returns.
        self._event.wait()
        return ""

    def unblock(self) -> None:
        self._event.set()


def test_blocking_inbound_pipe_does_not_hang_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    # The turn completes WITHOUT needing a permission answer; the inbound reader
    # is parked in a blocking readline the whole time. run_headless must still
    # return promptly (proving the daemon reader thread does not gate teardown).
    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "ok"})])
    gate = RulesPermissionGate()
    buffer = io.StringIO()
    blocking = _BlockingStream()

    async def _go() -> int:
        return await asyncio.wait_for(
            run_headless(
                "hi",
                output="stream-json",
                gate=gate,
                driver=driver,
                stream=buffer,
                input_stream=blocking,
            ),
            timeout=5,
        )

    try:
        code = asyncio.run(_go())
    finally:
        blocking.unblock()  # let the parked daemon thread fall through
    assert code == 0


def test_blocking_inbound_thread_is_daemon_and_does_not_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    # Snapshot the inbound daemon threads before/after: the reader thread must be
    # a daemon (so the interpreter never joins it on exit).
    driver = ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "x"})])
    gate = RulesPermissionGate()
    buffer = io.StringIO()
    blocking = _BlockingStream()

    async def _go() -> int:
        return await asyncio.wait_for(
            run_headless(
                "hi",
                output="stream-json",
                gate=gate,
                driver=driver,
                stream=buffer,
                input_stream=blocking,
            ),
            timeout=5,
        )

    try:
        asyncio.run(_go())
        inbound = [
            t for t in threading.enumerate() if t.name == "magi-cli-inbound"
        ]
        # The reader thread (still parked in readline) must be a daemon thread.
        assert inbound, "expected the inbound reader thread to still be alive"
        assert all(t.daemon for t in inbound)
    finally:
        blocking.unblock()


# ---------------------------------------------------------------------------
# Cold-start: a headless run does not import textual.
# ---------------------------------------------------------------------------
def test_headless_run_does_not_import_textual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    for key in list(sys.modules.keys()):
        if key == "textual" or key.startswith("textual."):
            del sys.modules[key]
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=ScriptedDriver([RuntimeEvent(type="token", payload={"delta": "x"})]),
            stream=buffer,
        )
    )
    leaked = [m for m in sys.modules if m == "textual" or m.startswith("textual.")]
    assert not leaked, f"textual leaked into headless path: {leaked}"
