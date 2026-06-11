"""End-to-end headless tool-permission approval tests.

These tests prove the HEADLESS approval flow at the ``run_headless`` seam — the
last unwired surface of the CLI permission gate. The gate + engine seam is
already covered by ``test_engine_gate.py`` and the ``HeadlessSink`` in isolation
by ``test_permissions.py``; here we drive the WHOLE headless path
(``run_headless`` -> ``RulesPermissionGate`` -> ``HeadlessSink`` ->
``control_request`` frame on stdout / ``control_response`` on stdin) so the
Claude-Code-style ``control_request`` / ``control_response`` protocol is proven
observable on the wire.

A ``GateAwareRunner`` (reused from ``test_engine_gate.py``) emits a scripted
``function_call`` and drives the ADK ``before_tool_callback`` loop, so a gated
tool reaches the gate's ``ask`` path WITHOUT any real model / network.

The inbound ``control_response`` is pre-loaded into the ``input_stream`` so the
daemon reader stashes it (HeadlessSink ``_early``) and the awaiting ``ask``
consumes it deterministically — no sleeps, no flaky timing.

Style: sync tests driving async via ``asyncio.run(...)`` (matches the rest of
the cli test suite; no pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import io
import json
import os

import pytest

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import run_headless
from magi_agent.cli.permissions import RulesEngine, RulesPermissionGate
from magi_agent.cli.tests.test_engine_gate import GateAwareRunner

# The gate stamps the request id as ``{turn_id}:{tool_name}:{seq}`` (see
# engine._build_gate_before_tool). ``run_headless`` passes ``{"prompt": ...}``
# with no session/turn id, so the engine defaults turn_id to ``cli-turn``.
_TURN_ID = "cli-turn"


def _request_id(tool_name: str, seq: int = 1) -> str:
    return f"{_TURN_ID}:{tool_name}:{seq}"


def _control_response_line(request_id: str, decision: str) -> str:
    return (
        json.dumps(
            {
                "type": "control_response",
                "request_id": request_id,
                "response": {"decision": decision},
            }
        )
        + "\n"
    )


def _bare_gate() -> RulesPermissionGate:
    """A gate with NO rules and NO sinks: every tool hits the ``ask`` path.

    ``run_headless`` (stream-json) attaches a ``HeadlessSink`` to this empty
    ``sinks`` list when an ``ask`` can actually be resolved.
    """

    return RulesPermissionGate(rules=RulesEngine())


def _run_stream_json(
    *,
    runner: GateAwareRunner,
    gate: RulesPermissionGate,
    input_stream: io.StringIO | None = None,
    permission_mode: str = "default",
) -> tuple[int, list[dict]]:
    """Drive ``run_headless`` in stream-json mode; return (exit_code, frames)."""

    out = io.StringIO()
    driver = MagiEngineDriver(runner=runner)

    prev = os.environ.get("MAGI_CLI_ENABLED")
    os.environ["MAGI_CLI_ENABLED"] = "1"
    try:
        code = asyncio.run(
            asyncio.wait_for(
                run_headless(
                    "go",
                    output="stream-json",
                    gate=gate,
                    driver=driver,
                    permission_mode=permission_mode,  # type: ignore[arg-type]
                    stream=out,
                    input_stream=input_stream,
                ),
                timeout=5.0,
            )
        )
    finally:
        if prev is None:
            os.environ.pop("MAGI_CLI_ENABLED", None)
        else:
            os.environ["MAGI_CLI_ENABLED"] = prev

    frames = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, frames


def _run_text(
    *,
    runner: GateAwareRunner,
    gate: RulesPermissionGate,
    permission_mode: str = "default",
) -> tuple[int, str]:
    """Drive ``run_headless`` in text mode; return (exit_code, output)."""

    out = io.StringIO()
    driver = MagiEngineDriver(runner=runner)

    prev = os.environ.get("MAGI_CLI_ENABLED")
    os.environ["MAGI_CLI_ENABLED"] = "1"
    try:
        code = asyncio.run(
            run_headless(
                "go",
                output="text",
                gate=gate,
                driver=driver,
                permission_mode=permission_mode,  # type: ignore[arg-type]
                stream=out,
            )
        )
    finally:
        if prev is None:
            os.environ.pop("MAGI_CLI_ENABLED", None)
        else:
            os.environ["MAGI_CLI_ENABLED"] = prev

    return code, out.getvalue()


def _control_requests(frames: list[dict]) -> list[dict]:
    return [f for f in frames if f.get("type") == "control_request"]


# ---------------------------------------------------------------------------
# control_request emission
# ---------------------------------------------------------------------------
def test_headless_ask_emits_control_request() -> None:
    """A gated tool with no rule emits exactly ONE control_request frame with
    the tool name + input."""

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls -la"})
    # Pre-load a matching allow response so the ask registers (and the frame is
    # deterministically emitted) before the channel reaches EOF. The point of
    # THIS test is the OUTBOUND control_request shape; allow/deny outcomes are
    # asserted by the dedicated tests below.
    inbound = io.StringIO(_control_response_line(_request_id("Bash"), "allow"))
    code, frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        input_stream=inbound,
    )

    requests = _control_requests(frames)
    assert len(requests) == 1
    req = requests[0]
    assert req["request_id"] == _request_id("Bash")
    assert req["request"]["tool_name"] == "Bash"
    assert req["request"]["arguments"] == {"cmd": "ls -la"}
    assert code == 0


# ---------------------------------------------------------------------------
# control_response allow / deny
# ---------------------------------------------------------------------------
def test_headless_control_response_allow_runs_tool() -> None:
    """An inbound control_response{decision:allow} lets the gated tool run."""

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    inbound = io.StringIO(_control_response_line(_request_id("Bash"), "allow"))

    code, frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        input_stream=inbound,
    )

    assert len(_control_requests(frames)) == 1
    assert runner.executed == [{"cmd": "ls"}]
    assert runner.blocked == []
    assert code == 0


def test_headless_control_response_deny_skips_tool() -> None:
    """An inbound control_response{decision:deny} blocks the gated tool."""

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "rm -rf /"})
    inbound = io.StringIO(_control_response_line(_request_id("Bash"), "deny"))

    code, frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        input_stream=inbound,
    )

    assert len(_control_requests(frames)) == 1
    assert runner.executed == []
    assert len(runner.blocked) == 1
    blocked = runner.blocked[0]
    assert blocked["status"] == "blocked"
    assert blocked["error"] == "permission_denied"
    assert code == 0


# ---------------------------------------------------------------------------
# EOF safe-deny
# ---------------------------------------------------------------------------
def test_headless_eof_safe_denies() -> None:
    """EOF on the inbound channel (no answer) fails the ask CLOSED (deny)."""

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    code, _frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        input_stream=io.StringIO(""),  # immediate EOF, never an answer
    )

    # EOF / no answer resolves the ask to a safe deny — NEVER an auto-allow —
    # so the tool does NOT run. (Whether the outbound frame raced out before the
    # EOF close is intentionally not asserted: the SAFETY guarantee is the deny,
    # not the frame. The frame shape is proven by the allow/deny tests where a
    # response is present.)
    assert runner.executed == []
    assert len(runner.blocked) == 1
    blocked = runner.blocked[0]
    assert blocked["status"] == "blocked"
    assert blocked["error"] == "permission_denied"
    assert code == 0


# ---------------------------------------------------------------------------
# permission modes
# ---------------------------------------------------------------------------
def test_bypass_permissions_mode_auto_allows() -> None:
    """bypassPermissions auto-allows with NO control_request frame emitted."""

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    code, frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        # No input_stream needed: bypass resolves without inbound data, so the
        # sink is attached and auto-allows.
        permission_mode="bypassPermissions",
    )

    assert _control_requests(frames) == []  # NO frame in bypass mode
    assert runner.executed == [{"cmd": "ls"}]
    assert runner.blocked == []
    assert code == 0


def test_accept_edits_mode_auto_allows_edit_tool_no_frame() -> None:
    """acceptEdits auto-allows an edit-class tool with NO frame; the tool runs."""

    runner = GateAwareRunner(tool_name="Write", tool_args={"path": "a.txt"})
    code, frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        permission_mode="acceptEdits",
    )

    assert _control_requests(frames) == []  # edit-class tool: no frame
    assert runner.executed == [{"path": "a.txt"}]
    assert runner.blocked == []
    assert code == 0


def test_accept_edits_text_mode_auto_allows_edit_tool_without_inbound() -> None:
    """One-shot text output has no inbound approver, but acceptEdits still allows edits."""

    runner = GateAwareRunner(tool_name="Write", tool_args={"path": "a.txt"})
    code, output = _run_text(
        runner=runner,
        gate=_bare_gate(),
        permission_mode="acceptEdits",
    )

    assert runner.executed == [{"path": "a.txt"}]
    assert runner.blocked == []
    assert code == 0
    assert "permission_denied" not in output


def test_accept_edits_mode_non_edit_tool_still_prompts() -> None:
    """acceptEdits does NOT auto-allow a non-edit tool: it still prompts and
    honors the inbound answer."""

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    inbound = io.StringIO(_control_response_line(_request_id("Bash"), "allow"))
    code, frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        input_stream=inbound,
        permission_mode="acceptEdits",
    )

    assert len(_control_requests(frames)) == 1  # non-edit tool DID prompt
    assert runner.executed == [{"cmd": "ls"}]
    assert code == 0


def test_accept_edits_stream_json_non_edit_without_inbound_denies_without_hanging() -> None:
    """No inbound approver means non-edit tools fail closed instead of blocking."""

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    code, frames = _run_stream_json(
        runner=runner,
        gate=_bare_gate(),
        permission_mode="acceptEdits",
    )

    assert _control_requests(frames) == []
    assert runner.executed == []
    assert runner.blocked == [
        {
            "status": "blocked",
            "error": "permission_denied",
            "tool": "Bash",
        }
    ]
    assert code == 0


# ---------------------------------------------------------------------------
# Preserve existing behavior: a plain prompt with NO gated tool is unchanged.
# ---------------------------------------------------------------------------
def test_headless_no_tool_no_control_request() -> None:
    """A turn that calls no tool emits NO control_request frame (no regression)."""

    from magi_agent.cli.headless import StubEngineDriver

    out = io.StringIO()
    prev = os.environ.get("MAGI_CLI_ENABLED")
    os.environ["MAGI_CLI_ENABLED"] = "1"
    try:
        code = asyncio.run(
            run_headless(
                "hello",
                output="stream-json",
                gate=_bare_gate(),
                driver=StubEngineDriver(text="hi"),
                stream=out,
                input_stream=io.StringIO(""),
            )
        )
    finally:
        if prev is None:
            os.environ.pop("MAGI_CLI_ENABLED", None)
        else:
            os.environ["MAGI_CLI_ENABLED"] = prev

    frames = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert _control_requests(frames) == []
    # The assistant text still flows through unchanged.
    assistant = [f for f in frames if f.get("type") == "assistant"]
    assert any("hi" in str(f.get("message", {})) for f in assistant)
    assert code == 0
