"""Flag-gated wiring of the mode-derived permission scope (cluster 09 PR1).

``build_cli_tool_runtime`` previously stamped a hardcoded
``selected_full_toolhost`` scope onto every ``ToolContext``. PR1 routes that
through ``PermissionScopeResolver`` ONLY when ``MAGI_PERMISSION_SCOPE_FROM_MODE``
is ON; OFF must stay byte-identical (regression guard).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from magi_agent.cli.tool_runtime import build_cli_tool_runtime


def _adk_ctx(tool_name: str) -> object:
    return SimpleNamespace(function_call=SimpleNamespace(name=tool_name, id="call-1"))


def _scope_for(tool_name: str, *, permission_mode: str) -> dict[str, object]:
    runtime = build_cli_tool_runtime(
        workspace_root="/tmp/ws",
        permission_mode=permission_mode,
    )
    ctx = runtime.tool_context_factory(_adk_ctx(tool_name))
    return dict(ctx.permission_scope)  # type: ignore[arg-type]


def test_flag_off_keeps_legacy_full_toolhost_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_FROM_MODE", raising=False)
    for mode in ("default", "acceptEdits", "bypassPermissions"):
        scope = _scope_for("FileWrite", permission_mode=mode)
        assert scope == {
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        }


def test_flag_on_default_mode_drops_preapproval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_PERMISSION_SCOPE_FROM_MODE", "1")
    scope = _scope_for("FileWrite", permission_mode="default")
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


def test_flag_on_bypass_mode_yields_bypass_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_PERMISSION_SCOPE_FROM_MODE", "1")
    scope = _scope_for("FileWrite", permission_mode="bypassPermissions")
    assert scope.get("mode") == "bypass"


def test_flag_on_accept_edits_preapproves_edit_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_PERMISSION_SCOPE_FROM_MODE", "1")
    # FileWrite is a registered core edit-class tool -> preapproval scope.
    scope = _scope_for("FileWrite", permission_mode="acceptEdits")
    assert scope.get("mode") == "selected_full_toolhost"


def test_flag_on_unknown_tool_falls_back_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_PERMISSION_SCOPE_FROM_MODE", "1")
    # No manifest for an unknown tool -> strict default scope (no preapproval).
    scope = _scope_for("TotallyUnknownTool", permission_mode="default")
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}
