"""Default-mode read-only auto-allow (CC/OpenCode parity).

Regression guard for the bug where the CLI permission gate sent EVERY tool to
``ask`` in the default mode, so in headless (no responder) all tools — even
read-only ``FileRead``/``Glob``/``Grep`` — were safe-denied and the agent
reported "tools seem restricted". The manifest-first SmartApprove classifier
must be wired by default so read-only tools auto-allow while mutating/dangerous
tools still fall through to ``ask``.
"""

from __future__ import annotations

import pytest

from magi_agent.cli.contracts import ControlRequest
from magi_agent.cli.wiring import build_headless_runtime


def _req(tool: str, **args: object) -> ControlRequest:
    return ControlRequest(
        requestId="r1", turnId="t1", toolName=tool, arguments=args, reason="test"
    )


def test_default_mode_wires_readonly_classifier(tmp_path) -> None:
    rt = build_headless_runtime(
        cwd=str(tmp_path), permission_mode="default", session_id="s1"
    )
    assert rt.gate._smart_approve is not None  # noqa: SLF001


@pytest.mark.asyncio
async def test_default_mode_auto_allows_read_only_tools(tmp_path) -> None:
    rt = build_headless_runtime(
        cwd=str(tmp_path), permission_mode="default", session_id="s1"
    )
    for tool in ("FileRead", "Glob", "Grep"):
        decision = await rt.gate.check(_req(tool, path="x"))
        assert decision.kind == "allow", f"{tool} should auto-allow (read-only)"


@pytest.mark.asyncio
async def test_default_mode_does_not_auto_allow_mutating_tools(tmp_path) -> None:
    # No sink is wired in default headless, so a non-read-only tool that misses
    # the rules falls through ``ask`` to a SAFE deny (never silently allowed).
    rt = build_headless_runtime(
        cwd=str(tmp_path), permission_mode="default", session_id="s1"
    )
    for tool in ("FileWrite", "FileEdit", "Bash"):
        decision = await rt.gate.check(_req(tool, path="x", command="rm -rf /"))
        assert decision.kind != "allow", f"{tool} must NOT auto-allow"
