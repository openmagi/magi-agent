from __future__ import annotations

import time
from pathlib import Path

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolHost,
    Gate5BFullToolHostConfig,
)


def _host(workspace: Path) -> Gate5BFullToolHost:
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "maxToolCallsPerTurn": 8,
        }
    )
    return Gate5BFullToolHost(
        config=config,
        workspace_root=workspace,
        exposed_tool_names=("TestRun",),
        now_ms=lambda: 0,
    )


def _bash_host(workspace: Path) -> Gate5BFullToolHost:
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "maxToolCallsPerTurn": 8,
            "commandTimeoutMs": 250,
            "maxPerToolOutputBytes": 512,
        }
    )
    return Gate5BFullToolHost(
        config=config,
        workspace_root=workspace,
        exposed_tool_names=("Bash",),
        now_ms=lambda: 0,
    )


async def _run(host: Gate5BFullToolHost, command: str) -> dict:
    return await host._handle("TestRun", {"command": command}, tool_call_id="test-run")


@pytest.mark.asyncio
async def test_test_run_executes_command_and_returns_exit_code(tmp_path):
    host = _host(tmp_path)
    out = await _run(host, "echo verify-ok")
    assert out["exitCode"] == 0
    assert "verify-ok" in out["stdout"]
    assert out["stdoutDigest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_test_run_non_zero_exit_is_preserved(tmp_path):
    host = _host(tmp_path)
    out = await _run(host, "exit 3")
    assert out["exitCode"] == 3


@pytest.mark.asyncio
async def test_test_run_empty_command_rejected(tmp_path):
    host = _host(tmp_path)
    with pytest.raises(ValueError, match="empty_command"):
        await _run(host, "   ")


@pytest.mark.asyncio
async def test_bash_timeout_bounds_infinite_output_before_redaction(tmp_path, monkeypatch):
    import magi_agent.gates.gate5b_full_toolhost as mod

    redaction_input_lengths: list[int] = []
    real_redact = mod._redact

    def guarded_redact(value: str) -> str:
        redaction_input_lengths.append(len(value))
        assert len(value.encode("utf-8")) <= 2048
        return real_redact(value)

    monkeypatch.setattr(mod, "_redact", guarded_redact)
    host = _bash_host(tmp_path)
    started = time.monotonic()

    out = await host._handle(
        "Bash",
        {
            "command": (
                "i=0; while :; do "
                "printf 'HEAD-%07d\\n' \"$i\"; "
                "i=$((i + 1)); "
                "done"
            )
        },
        tool_call_id="bash-timeout",
    )

    assert time.monotonic() - started < 3.0
    assert out["timedOut"] is True
    assert out["exitCode"] is None
    stdout = out["stdout"]
    assert "HEAD-0000000" in stdout
    assert "output truncated" in stdout
    assert "HEAD-" in stdout.split("output truncated", 1)[1]
    assert redaction_input_lengths
    assert max(redaction_input_lengths) <= 2048


@pytest.mark.asyncio
async def test_test_run_uses_300s_verification_timeout(tmp_path, monkeypatch):
    # The TestRun handler must apply the manifest 300s timeout, not the 5s Bash
    # command_timeout_ms.
    captured: dict[str, float] = {}

    async def fake_run_shell_command(
        self: Gate5BFullToolHost,
        raw_command: str,
        *,
        timeout_s: float,
    ) -> dict[str, object]:
        captured["timeout"] = timeout_s
        return {
            "exitCode": 0,
            "stdout": raw_command,
            "stderr": "",
            "stdoutDigest": "sha256:test",
            "stderrDigest": "sha256:test",
        }

    monkeypatch.setattr(
        Gate5BFullToolHost, "_run_shell_command_async", fake_run_shell_command
    )
    host = _host(tmp_path)
    await _run(host, "echo hi")

    assert captured["timeout"] == 300.0


@pytest.mark.asyncio
async def test_test_run_dispatch_allowlisted(tmp_path):
    host = _host(tmp_path)
    outcome = await host.dispatch(
        "TestRun",
        {"command": "echo ok"},
        request_digest="req",
        tool_call_id="call-1",
    )
    assert outcome.status == "ok"
