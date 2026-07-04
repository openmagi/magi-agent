"""Tests for plan-mode tool gating in the CLI wiring.

Plan mode must expose only read-only tools: the act-only mutating tools
(FileWrite / FileEdit / PatchApply / Bash) are excluded by the manifest
``modes=("act",)`` and the wiring just threads the selection.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from typer.testing import CliRunner

from magi_agent.cli.tool_runtime import build_cli_adk_tools
from magi_agent.cli.wiring import build_headless_runtime, _build_first_party_adk_tools

_MUTATING = {"FileWrite", "FileEdit", "PatchApply", "Bash"}
_FORBIDDEN_PLAN_TOOLHOST_TOOLS = {
    "AgentMemoryRemember",
    "ArtifactDelete",
    "ArtifactUpdate",
    "Browser",
    "CommitCheckpoint",
    "CronCreate",
    "CronDelete",
    "CronUpdate",
    "DocumentWrite",
    "ExternalSourceCache",
    "FileDeliver",
    "FileSend",
    "KnowledgeSearch",
    "KnowledgeWrite",
    "SocialBrowser",
    "SpawnAgent",
    "SpawnWorktreeApply",
    "SpreadsheetWrite",
    "TaskBoard",
    "TaskStop",
    "TaskWait",
    "WebFetch",
    "WebSearch",
    "knowledge-search",
    "knowledge-write",
    "web-search",
    "web_search",
}


def _names(tools: list[object]) -> set[str]:
    return {getattr(tool, "name", None) for tool in tools}


# ---------------------------------------------------------------------------
# tool_runtime.build_cli_adk_tools level
# ---------------------------------------------------------------------------

def test_plan_mode_excludes_mutating_tools(tmp_path) -> None:
    names = _names(build_cli_adk_tools(workspace_root=str(tmp_path), mode="plan"))
    assert names.isdisjoint(_MUTATING), f"plan mode exposed mutating tools: {names & _MUTATING}"
    # Read-only tools remain available.
    assert {"FileRead", "Glob", "Grep"}.issubset(names)


def test_act_mode_includes_mutating_tools(tmp_path) -> None:
    names = _names(build_cli_adk_tools(workspace_root=str(tmp_path), mode="act"))
    assert _MUTATING.issubset(names), f"act mode missing mutating tools: {_MUTATING - names}"


# ---------------------------------------------------------------------------
# wiring._build_first_party_adk_tools level (the ACTIVE tool path)
# ---------------------------------------------------------------------------

def test_first_party_plan_mode_excludes_mutating_tools(tmp_path) -> None:
    names = _names(
        _build_first_party_adk_tools(cwd=str(tmp_path), session_id="s", mode="plan")
    )
    assert names.isdisjoint(_MUTATING), f"plan mode exposed mutating tools: {names & _MUTATING}"
    assert "FileRead" in names


def test_first_party_plan_mode_excludes_toolhost_side_effects(tmp_path) -> None:
    names = _names(
        _build_first_party_adk_tools(cwd=str(tmp_path), session_id="s", mode="plan")
    )
    leaked = names & _FORBIDDEN_PLAN_TOOLHOST_TOOLS
    assert not leaked, f"plan mode exposed side-effect/external tools: {sorted(leaked)}"


def test_first_party_act_mode_includes_mutating_tools(tmp_path) -> None:
    names = _names(
        _build_first_party_adk_tools(cwd=str(tmp_path), session_id="s", mode="act")
    )
    assert _MUTATING.issubset(names), f"act mode missing mutating tools: {_MUTATING - names}"


def test_headless_plan_mode_does_not_attach_composio(monkeypatch, tmp_path) -> None:
    def fail_build(_config):  # type: ignore[no-untyped-def]
        raise AssertionError("plan mode must not build Composio MCP toolsets")

    def fail_attach(_runner, _bundle):  # type: ignore[no-untyped-def]
        raise AssertionError("plan mode must not attach Composio MCP toolsets")

    monkeypatch.setattr("magi_agent.cli.wiring.build_composio_toolset_bundle", fail_build)
    monkeypatch.setattr("magi_agent.cli.wiring.attach_composio_toolsets_to_runner", fail_attach)

    runtime = build_headless_runtime(cwd=str(tmp_path), runner=object(), mode="plan")

    assert runtime.composio.active is False
    assert runtime.composio.reason == "plan_mode"
    assert runtime.mcp_servers == ()


# ---------------------------------------------------------------------------
# CLI-level: --mode plan reaches the tool-build with mode="plan"
# ---------------------------------------------------------------------------

def test_cli_mode_plan_reaches_tool_build(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    # Hermetic provider resolution: a sibling test that leaks a
    # ``MAGI_PROVIDER``/``MAGI_MODEL`` naming a keyless provider would force
    # ``resolve_provider_config`` down the early ``None`` path, so the default
    # runner is a stub and ``_build_first_party_adk_tools`` is never reached
    # (captured stays ``{}``). Clear them so the injected ``ANTHROPIC_API_KEY``
    # is the sole provider signal, making the spy fire regardless of worker.
    monkeypatch.delenv("MAGI_PROVIDER", raising=False)
    monkeypatch.delenv("MAGI_MODEL", raising=False)

    captured: dict[str, object] = {}

    real = _build_first_party_adk_tools

    def spy(*, cwd, session_id, mode="act", **kwargs):  # type: ignore[no-untyped-def]
        captured["mode"] = mode
        return real(cwd=cwd, session_id=session_id, mode=mode, **kwargs)

    async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
        return 0

    from magi_agent.cli.app import app

    runner = CliRunner()
    with patch("magi_agent.cli.wiring._build_first_party_adk_tools", spy), \
         patch("magi_agent.cli.app.run_headless", fake_headless):
        result = runner.invoke(
            app,
            ["--mode", "plan", "-p", "hello"],
            env={
                "ANTHROPIC_API_KEY": "sk-x",
                "MAGI_CLI_ENABLED": "1",
                "MAGI_CLI_SESSION_DIR": str(tmp_path),
            },
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert captured.get("mode") == "plan", f"captured={captured}, output={result.output}"


def test_cli_default_mode_is_act(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    # Hermetic provider resolution: a sibling test that leaks a
    # ``MAGI_PROVIDER``/``MAGI_MODEL`` naming a keyless provider would force
    # ``resolve_provider_config`` down the early ``None`` path, so the default
    # runner is a stub and ``_build_first_party_adk_tools`` is never reached
    # (captured stays ``{}``). Clear them so the injected ``ANTHROPIC_API_KEY``
    # is the sole provider signal, making the spy fire regardless of worker.
    monkeypatch.delenv("MAGI_PROVIDER", raising=False)
    monkeypatch.delenv("MAGI_MODEL", raising=False)

    captured: dict[str, object] = {}

    real = _build_first_party_adk_tools

    def spy(*, cwd, session_id, mode="act", **kwargs):  # type: ignore[no-untyped-def]
        captured["mode"] = mode
        return real(cwd=cwd, session_id=session_id, mode=mode, **kwargs)

    async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
        return 0

    from magi_agent.cli.app import app

    runner = CliRunner()
    with patch("magi_agent.cli.wiring._build_first_party_adk_tools", spy), \
         patch("magi_agent.cli.app.run_headless", fake_headless):
        result = runner.invoke(
            app,
            ["-p", "hello"],
            env={
                "ANTHROPIC_API_KEY": "sk-x",
                "MAGI_CLI_ENABLED": "1",
                "MAGI_CLI_SESSION_DIR": str(tmp_path),
            },
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert captured.get("mode") == "act", f"captured={captured}, output={result.output}"
