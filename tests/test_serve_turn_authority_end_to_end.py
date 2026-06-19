"""Combined authority gate for P0.1 + P0.2 (A-1 + A-8).

These two fixes jointly change serve/child default authority from *bypass /
full-toolhost* to *ask / no-preapproval*. Shipping one without the other leaves
a broken intermediate, so this end-to-end test asserts the composed default:

  A serve turn with NO explicit ``permission_mode`` resolves end-to-end to
  ask / no-preapproval — NOT ``bypassPermissions`` and NOT the legacy
  ``selected_full_toolhost`` scope — once BOTH changes are in.

No model / provider keys required (the runtime build is mocked / scope is read
off the assembled ToolContext).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from magi_agent.cli.tool_runtime import build_cli_tool_runtime
from magi_agent.runtime.governed_turn import _build_runtime
from magi_agent.runtime.turn_context import TurnContext


def _adk_ctx(tool_name: str) -> object:
    return SimpleNamespace(function_call=SimpleNamespace(name=tool_name, id="c1"))


def test_default_serve_turn_does_not_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """A-8 leg: a serve TurnContext with no explicit mode never bypasses."""
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_FROM_MODE", raising=False)
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_LEGACY_FULL_TOOLHOST", raising=False)
    ctx = TurnContext(prompt="serve", session_id="s", turn_id="t")
    with mock.patch("magi_agent.cli.wiring.build_headless_runtime") as build:
        _build_runtime(ctx)
    assert build.call_args.kwargs["permission_mode"] == "default"


def test_default_serve_turn_scope_is_not_legacy_full_toolhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-1 leg: with the flag ABSENT (new default-ON), a mutating tool under
    the default mode gets NO ``selected_full_toolhost`` preapproval scope."""
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_FROM_MODE", raising=False)
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_LEGACY_FULL_TOOLHOST", raising=False)
    runtime = build_cli_tool_runtime(workspace_root="/tmp/ws", permission_mode="default")
    scope = dict(runtime.tool_context_factory(_adk_ctx("FileWrite")).permission_scope)
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}
    assert scope.get("source") != "selected_full_toolhost"


def test_default_serve_turn_bash_reaches_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """The composed default lets the arbiter ``ask`` branch reach for a
    mutating non-edit tool (Bash) — proving over-preapproval is gone."""
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_FROM_MODE", raising=False)
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_LEGACY_FULL_TOOLHOST", raising=False)
    runtime = build_cli_tool_runtime(workspace_root="/tmp/ws", permission_mode="default")
    scope = dict(runtime.tool_context_factory(_adk_ctx("Bash")).permission_scope)
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}
