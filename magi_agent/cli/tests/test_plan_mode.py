"""Tests for plan-mode tool gating in the CLI wiring.

Plan mode must expose only read-only tools: the act-only mutating tools
(FileWrite / FileEdit / PatchApply / Bash) are excluded by the manifest
``modes=("act",)`` and the wiring just threads the selection.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from magi_agent.cli.tool_runtime import build_cli_adk_tools
from magi_agent.cli.wiring import build_headless_runtime, _build_first_party_adk_tools


# ---------------------------------------------------------------------------
# Hermetic provider resolution (xdist ``--dist loadfile`` isolation)
# ---------------------------------------------------------------------------
#
# The CLI-level tests below assert that ``_build_first_party_adk_tools`` (the
# real tool-build path) is reached when the runner is model-backed. Reaching it
# requires ``resolve_provider_config`` to return a non-``None`` config so the
# real ADK runner is built instead of the model-free stub.
#
# ``resolve_provider_config`` reads the *process* ``os.environ`` (and, via
# ``MAGI_CONFIG``, a ``config.toml``) when no explicit ``env`` is injected. A
# sibling test file co-scheduled on the same xdist worker that leaks (a) a
# ``MAGI_PROVIDER``/``MAGI_MODEL`` naming a keyless provider, or (b) a
# ``MAGI_CONFIG`` pointing at a ``config.toml`` whose ``[model] provider`` is a
# keyless provider, forces resolution down the early ``None`` path. The default
# runner then becomes the stub, ``_build_first_party_adk_tools`` is never
# called, and the ``captured`` spy dict stays ``{}`` (the observed failure:
# ``AssertionError: captured={}``).
#
# ``monkeypatch``/``CliRunner.invoke(env=...)`` do NOT undo those leaks: monkey-
# patch only restores keys the *test* set, and ``invoke``'s ``env`` MERGES into
# ``os.environ`` (a leaked explicit-provider signal still wins over the injected
# ``ANTHROPIC_API_KEY``). This autouse fixture snapshots + restores the whole
# ``os.environ`` for every test in this module and neutralizes the specific
# provider-resolution inputs, so the injected key is the sole provider signal
# regardless of which sibling ran first on the worker. The invariants under test
# (mode threading) are unchanged; only the ambient provider-resolution path is
# made deterministic.
@pytest.fixture(autouse=True)
def _hermetic_provider_resolution() -> "object":
    saved = dict(os.environ)
    # Clear the explicit-provider inputs so the injected ``ANTHROPIC_API_KEY``
    # is the only provider signal (config's default provider can't leak in).
    for key in ("MAGI_PROVIDER", "MAGI_MODEL"):
        os.environ.pop(key, None)
    # Point ``MAGI_CONFIG`` at a guaranteed-absent path so a leaked
    # ``config.toml`` (real ``~/.magi/config.toml`` or a sibling's temp file
    # naming a keyless provider) cannot flip resolution to the stub. A missing
    # file makes ``_load_config_file`` return ``{}`` → env-only resolution.
    os.environ["MAGI_CONFIG"] = os.path.join(
        os.sep, "nonexistent-magi-config-plan-mode", "config.toml"
    )
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)

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
