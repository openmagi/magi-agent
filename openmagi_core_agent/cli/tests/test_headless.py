from __future__ import annotations

import asyncio
import io
import json

import pytest

from openmagi_core_agent.cli.contracts import (
    ControlRequest,
    EngineResult,
    NullPermissionGate,
    Terminal,
)
from openmagi_core_agent.cli.headless import StubEngineDriver, _cli_enabled, drain, run_headless


def _make_control_request() -> ControlRequest:
    return ControlRequest(
        requestId="req-1",
        turnId="turn-1",
        toolName="Bash",
        arguments={"cmd": "ls"},
        reason="needs approval",
    )


def test_cli_disabled_returns_2_and_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicitly set to a falsy token to exercise the disabled path (default is now ON).
    monkeypatch.setenv("MAGI_CLI_ENABLED", "0")
    buffer = io.StringIO()
    code = asyncio.run(run_headless("hi", output="stream-json", stream=buffer))
    assert code == 2
    assert buffer.getvalue() == ""


def test_text_mode_prints_only_final_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hi",
            output="text",
            driver=StubEngineDriver(text="final answer"),
            stream=buffer,
        )
    )
    assert code == 0
    out = buffer.getvalue()
    # Text mode emits exactly the final result text + newline — no NDJSON frames.
    assert out == "final answer\n"


def test_json_mode_single_result_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "true")
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hi",
            output="json",
            driver=StubEngineDriver(text="answer"),
            stream=buffer,
        )
    )
    assert code == 0
    lines = [line for line in buffer.getvalue().splitlines() if line]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["type"] == "result"
    assert obj["subtype"] == "success"
    assert obj["result"] == "answer"
    assert obj["is_error"] is False


def test_stream_json_mode_valid_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=StubEngineDriver(text="answer"),
            stream=buffer,
        )
    )
    assert code == 0
    lines = [line for line in buffer.getvalue().splitlines() if line]
    objs = [json.loads(line) for line in lines]
    # Every line is valid JSON.
    assert len(objs) >= 3
    # init line is first.
    assert objs[0]["type"] == "system"
    assert objs[0]["subtype"] == "init"
    # Exactly one result line.
    result_lines = [o for o in objs if o["type"] == "result"]
    assert len(result_lines) == 1
    assert result_lines[-1] is objs[-1]
    # An assistant frame carrying the text exists.
    assistant = [o for o in objs if o["type"] == "assistant"]
    assert any(o["message"].get("content") == "answer" for o in assistant)


def test_stream_json_include_partial_emits_stream_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=StubEngineDriver(text="answer"),
            stream=buffer,
        )
    )
    objs = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assert any(o["type"] == "stream_event" for o in objs)


def test_error_terminal_returns_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hi",
            output="json",
            driver=StubEngineDriver(terminal=Terminal.error, error="boom"),
            stream=buffer,
        )
    )
    assert code == 1
    obj = json.loads(buffer.getvalue().strip())
    assert obj["is_error"] is True
    assert obj["subtype"] == "error_during_execution"
    assert obj["errors"] == ["boom"]


def test_max_turns_terminal_subtype(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hi",
            output="json",
            driver=StubEngineDriver(terminal=Terminal.max_turns),
            stream=buffer,
        )
    )
    assert code == 1
    obj = json.loads(buffer.getvalue().strip())
    assert obj["subtype"] == "error_max_turns"
    assert obj["is_error"] is True


def test_drain_retrieves_terminal_result() -> None:
    async def run() -> None:
        gen = StubEngineDriver(text="x").run_turn_stream(
            None, {}, cancel=asyncio.Event()
        )
        events, terminal = await drain(gen)
        assert isinstance(terminal, EngineResult)
        assert terminal.terminal == Terminal.completed
        # Only RuntimeEvents collected (no EngineResult in the list).
        assert all(not isinstance(e, EngineResult) for e in events)
        assert any(e.type == "token" for e in events)

    asyncio.run(run())


def test_drain_synthesizes_terminal_when_missing() -> None:
    async def empty_gen():  # type: ignore[no-untyped-def]
        if False:
            yield  # pragma: no cover

    async def run() -> None:
        events, terminal = await drain(empty_gen())
        assert events == []
        assert terminal.terminal == Terminal.error
        assert terminal.error == "engine_driver_yielded_no_terminal_result"

    asyncio.run(run())


def test_null_permission_gate_ask_denies() -> None:
    async def run() -> None:
        gate = NullPermissionGate()
        decision = await gate.check(_make_control_request())
        assert decision.kind == "deny"

    asyncio.run(run())


def test_null_permission_gate_allow_in_test() -> None:
    async def run() -> None:
        gate = NullPermissionGate(allow_in_test=True)
        decision = await gate.check(_make_control_request())
        assert decision.kind == "allow"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# _cli_enabled: default-ON semantics (Track 18 Stream F PR-F2a)
# ---------------------------------------------------------------------------

def test_cli_enabled_default_on_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED unset → default-ON (True)."""
    monkeypatch.delenv("MAGI_CLI_ENABLED", raising=False)
    assert _cli_enabled() is True


def test_cli_enabled_true_for_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED='' (empty) → enabled (not a falsy token)."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "")
    assert _cli_enabled() is True


def test_cli_enabled_false_for_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED=0 → disabled."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "0")
    assert _cli_enabled() is False


def test_cli_enabled_false_for_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED=false → disabled."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "false")
    assert _cli_enabled() is False


def test_cli_enabled_false_for_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED=no → disabled."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "no")
    assert _cli_enabled() is False


def test_cli_enabled_false_for_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED=off → disabled."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "off")
    assert _cli_enabled() is False


def test_cli_enabled_false_for_off_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED=OFF → disabled (case-insensitive)."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "OFF")
    assert _cli_enabled() is False


def test_cli_enabled_true_for_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_CLI_ENABLED=1 → enabled."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    assert _cli_enabled() is True


def test_cli_disabled_false_token_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly disabled via MAGI_CLI_ENABLED=false → run_headless returns 2."""
    monkeypatch.setenv("MAGI_CLI_ENABLED", "false")
    buffer = io.StringIO()
    code = asyncio.run(run_headless("hi", output="stream-json", stream=buffer))
    assert code == 2
    assert buffer.getvalue() == ""
