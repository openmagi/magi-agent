from __future__ import annotations

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
async def test_test_run_uses_300s_verification_timeout(tmp_path):
    # The TestRun handler must apply the manifest 300s timeout, not the 5s Bash
    # command_timeout_ms. We assert the timeout passed to subprocess.run is 300.
    import magi_agent.gates.gate5b_full_toolhost as mod

    captured: dict[str, float] = {}
    real_run = mod.subprocess.run

    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["timeout"] = kwargs.get("timeout")
        return real_run(["true"], capture_output=True, text=True)

    mod.subprocess.run = fake_run  # type: ignore[assignment]
    try:
        host = _host(tmp_path)
        await _run(host, "echo hi")
    finally:
        mod.subprocess.run = real_run  # type: ignore[assignment]

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
