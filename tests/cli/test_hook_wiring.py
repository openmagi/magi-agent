"""Tests for the CLI HookBus → engine tool-callback bridge (cluster doc 11 PR2).

Scope: ``magi_agent.cli.hook_wiring``.

- ``build_user_hook_bus`` is gated by ``MAGI_USER_HOOKS_ENABLED`` (default OFF →
  None, byte-identical to today). When ON it loads the user + workspace
  ``settings.json`` hooks and constructs a single command-wired ``HookBus``.
- ``attach_hook_bus_tool_callbacks`` bridges the bus onto the ADK
  ``before_tool_callback`` / ``after_tool_callback`` of the runner's agent
  WITHOUT clobbering any pre-existing gate callback (prepended *after* the gate
  so a gate deny still short-circuits first).
- ``restore_hook_bus_tool_callbacks`` restores the original callbacks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from magi_agent.cli import hook_wiring
from magi_agent.hooks.bus import HookBus
from magi_agent.hooks.context import HookContext
from magi_agent.tools.manifest import ToolSource

_SOURCE = ToolSource(kind="builtin", package="test.fixtures")


# ---------------------------------------------------------------------------
# Minimal ADK-like stubs (agent owns the *_tool_callback attributes).
# ---------------------------------------------------------------------------


class _StubAgent:
    def __init__(self) -> None:
        self.before_tool_callback = None
        self.after_tool_callback = None


class _StubRunner:
    def __init__(self) -> None:
        self.agent = _StubAgent()


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _context() -> HookContext:
    return HookContext(bot_id="cli", session_id="s1", turn_id="t1")


def _write_settings(path: Path, block: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": block}), encoding="utf-8")


# ---------------------------------------------------------------------------
# env gate
# ---------------------------------------------------------------------------


def test_user_hooks_gate_off_when_disabled(monkeypatch):
    from magi_agent.config.env import is_user_hooks_enabled

    monkeypatch.setenv("MAGI_USER_HOOKS_ENABLED", "0")
    assert is_user_hooks_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_user_hooks_gate_strict_truthy(monkeypatch, value):
    from magi_agent.config.env import is_user_hooks_enabled

    monkeypatch.setenv("MAGI_USER_HOOKS_ENABLED", value)
    assert is_user_hooks_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_user_hooks_gate_falsy(monkeypatch, value):
    from magi_agent.config.env import is_user_hooks_enabled

    monkeypatch.setenv("MAGI_USER_HOOKS_ENABLED", value)
    assert is_user_hooks_enabled() is False


# ---------------------------------------------------------------------------
# build_user_hook_bus
# ---------------------------------------------------------------------------


def test_build_user_hook_bus_returns_none_when_gate_off(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGI_USER_HOOKS_ENABLED", raising=False)
    bus = hook_wiring.build_user_hook_bus(workspace_root=str(tmp_path))
    assert bus is None


def test_build_user_hook_bus_returns_none_when_no_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_USER_HOOKS_ENABLED", "true")
    # Point HOME at an empty dir and an empty workspace → no settings files.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    bus = hook_wiring.build_user_hook_bus(workspace_root=str(tmp_path / "ws"))
    assert bus is None


def test_build_user_hook_bus_loads_workspace_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_USER_HOOKS_ENABLED", "true")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    ws = tmp_path / "ws"
    _write_settings(
        ws / ".magi" / "settings.json",
        {"PreToolUse": [{"command": "/bin/true", "matcher": "Edit"}]},
    )
    bus = hook_wiring.build_user_hook_bus(workspace_root=str(ws))
    assert isinstance(bus, HookBus)


def test_build_user_hook_bus_merges_user_and_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_USER_HOOKS_ENABLED", "true")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write_settings(
        home / ".magi" / "settings.json",
        {"PreToolUse": [{"command": "/bin/true", "name": "user-hook"}]},
    )
    ws = tmp_path / "ws"
    _write_settings(
        ws / ".magi" / "settings.json",
        {"PostToolUse": [{"command": "/bin/true", "name": "ws-hook"}]},
    )
    bus = hook_wiring.build_user_hook_bus(workspace_root=str(ws))
    assert isinstance(bus, HookBus)
    # Both hooks must be present on the bus (user + workspace merged).
    names = {h.manifest.name for h in bus._hooks}
    assert "user-hook" in names
    assert "ws-hook" in names


# ---------------------------------------------------------------------------
# attach / restore
# ---------------------------------------------------------------------------


def test_attach_returns_none_when_bus_is_none():
    runner = _StubRunner()
    att = hook_wiring.attach_hook_bus_tool_callbacks(
        runner=runner, bus=None, hook_context=_context()
    )
    assert att is None
    assert runner.agent.before_tool_callback is None


def test_attach_returns_none_when_agentless():
    class _NoAgent:
        pass

    att = hook_wiring.attach_hook_bus_tool_callbacks(
        runner=_NoAgent(), bus=HookBus(), hook_context=_context()
    )
    assert att is None


def test_attach_prepends_after_existing_gate_callback():
    runner = _StubRunner()

    async def _gate_cb(*, tool, args, tool_context=None):  # pre-existing gate
        return None

    runner.agent.before_tool_callback = _gate_cb

    att = hook_wiring.attach_hook_bus_tool_callbacks(
        runner=runner, bus=HookBus(), hook_context=_context()
    )
    assert att is not None
    cbs = runner.agent.before_tool_callback
    assert isinstance(cbs, list)
    # Gate stays FIRST so a deny short-circuits before the hook bridge runs;
    # the hook-bus bridge is appended AFTER the gate.
    assert cbs[0] is _gate_cb
    assert len(cbs) == 2


def test_restore_returns_original_callbacks():
    runner = _StubRunner()

    async def _gate_cb(*, tool, args, tool_context=None):
        return None

    runner.agent.before_tool_callback = _gate_cb
    original_after = runner.agent.after_tool_callback

    att = hook_wiring.attach_hook_bus_tool_callbacks(
        runner=runner, bus=HookBus(), hook_context=_context()
    )
    hook_wiring.restore_hook_bus_tool_callbacks(att)
    assert runner.agent.before_tool_callback is _gate_cb
    assert runner.agent.after_tool_callback is original_after


def test_restore_none_is_noop():
    # Must not raise.
    hook_wiring.restore_hook_bus_tool_callbacks(None)


# ---------------------------------------------------------------------------
# bridge behaviour: a PreToolUse block hook DENIES the tool.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_tool_bridge_denies_on_block(tmp_path):
    from magi_agent.hooks.bus import RegisteredHook
    from magi_agent.hooks.manifest import HookManifest, HookPoint
    from magi_agent.hooks.result import HookResult

    def _blocking_handler(ctx):
        return HookResult(action="block", reason="nope")

    manifest = HookManifest(
        name="blocker",
        point=HookPoint.BEFORE_TOOL_USE,
        description="test",
        source=_SOURCE,
    )
    bus = HookBus(hooks=(RegisteredHook(manifest=manifest, handler=_blocking_handler),))
    runner = _StubRunner()
    hook_wiring.attach_hook_bus_tool_callbacks(
        runner=runner, bus=bus, hook_context=_context()
    )
    cbs = runner.agent.before_tool_callback
    assert isinstance(cbs, list)
    bridge = cbs[-1]
    result = await bridge(tool=_StubTool("Edit"), args={}, tool_context=None)
    # A block result must surface as a deny dict (skips the tool).
    assert isinstance(result, dict)
    assert result.get("status") == "blocked"


@pytest.mark.asyncio
async def test_before_tool_bridge_allows_on_continue():
    bus = HookBus()  # no hooks → continue
    runner = _StubRunner()
    hook_wiring.attach_hook_bus_tool_callbacks(
        runner=runner, bus=bus, hook_context=_context()
    )
    bridge = runner.agent.before_tool_callback[-1]
    result = await bridge(tool=_StubTool("Edit"), args={}, tool_context=None)
    assert result is None  # tool runs


@pytest.mark.asyncio
async def test_before_tool_bridge_fail_open_on_hook_throw(tmp_path):
    """Spec doc 11 PR2 test #4: a BEFORE_TOOL_USE hook that *raises* with
    ``failOpen=True`` must NOT block the tool — the turn continues.

    A raising handler is caught by the bus's fail-open path (the manifest's
    ``fail_open`` flag turns the failure into a ``continue`` result), so the
    bridge returns ``None`` (the tool runs) rather than a deny dict.
    """
    from magi_agent.hooks.bus import RegisteredHook
    from magi_agent.hooks.manifest import HookManifest, HookPoint

    def _raising_handler(ctx):
        raise RuntimeError("boom")

    manifest = HookManifest(
        name="thrower",
        point=HookPoint.BEFORE_TOOL_USE,
        description="test",
        source=_SOURCE,
        fail_open=True,
    )
    bus = HookBus(hooks=(RegisteredHook(manifest=manifest, handler=_raising_handler),))
    runner = _StubRunner()
    hook_wiring.attach_hook_bus_tool_callbacks(
        runner=runner, bus=bus, hook_context=_context()
    )
    bridge = runner.agent.before_tool_callback[-1]
    result = await bridge(tool=_StubTool("Edit"), args={}, tool_context=None)
    assert result is None  # fail-open: tool still runs, turn continues


@pytest.mark.asyncio
async def test_after_tool_bridge_runs_without_blocking():
    observed: list[str] = []

    from magi_agent.hooks.bus import RegisteredHook
    from magi_agent.hooks.manifest import HookManifest, HookPoint
    from magi_agent.hooks.result import HookResult

    def _observer(ctx):
        observed.append(ctx.turn_id or "")
        return HookResult(action="continue")

    manifest = HookManifest(
        name="post",
        point=HookPoint.AFTER_TOOL_USE,
        description="test",
        source=_SOURCE,
    )
    bus = HookBus(hooks=(RegisteredHook(manifest=manifest, handler=_observer),))
    runner = _StubRunner()
    hook_wiring.attach_hook_bus_tool_callbacks(
        runner=runner, bus=bus, hook_context=_context()
    )
    after_cb = runner.agent.after_tool_callback
    assert after_cb is not None
    bridge = after_cb if not isinstance(after_cb, list) else after_cb[-1]
    # after_tool_callback never blocks the tool result; returns None.
    out = await bridge(tool=_StubTool("Edit"), args={}, tool_context=None, tool_response={})
    assert out is None
    assert observed == ["t1"]
