"""Mode-derived permission scope wiring in ``build_cli_tool_runtime`` (A-1 / P0.1).

A-1 flips the default: ``MAGI_PERMISSION_SCOPE_FROM_MODE`` is now ON when the env
var is ABSENT, so the CLI tool runtime derives the scope from the permission mode
instead of unconditionally stamping the legacy ``selected_full_toolhost`` scope.

- Absent flag (new default) -> mode-derived strict scope: a mutating tool under
  ``default`` mode gets NO ``selected_full_toolhost``/``bypass`` preapproval.
- ``acceptEdits`` still preapproves edit-class tools.
- A resolver error falls back to a *fail-closed* (no-preapproval) scope, never
  the legacy full-toolhost scope.
- The deprecated rollback hatch ``MAGI_PERMISSION_SCOPE_LEGACY_FULL_TOOLHOST``
  (default OFF) restores the byte-identical legacy stamp.
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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_FROM_MODE", raising=False)
    monkeypatch.delenv("MAGI_PERMISSION_SCOPE_LEGACY_FULL_TOOLHOST", raising=False)


@pytest.mark.parametrize("tool", ["Bash", "FileWrite", "BrowserTask", "WebFetch"])
@pytest.mark.parametrize("mode", ["default", "smartApprove", "acceptEdits"])
def test_default_on_no_over_preapproval_for_non_edit(tool: str, mode: str) -> None:
    """Absent flag (default-ON): no mutating/net/dangerous tool is over-preapproved
    under default/smartApprove, and acceptEdits only covers edit-class tools."""
    scope = _scope_for(tool, permission_mode=mode)
    is_edit_class = tool in {"FileWrite", "FileEdit", "Edit", "Write"}
    if mode == "acceptEdits" and is_edit_class:
        assert scope.get("mode") == "selected_full_toolhost"
    else:
        assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


def test_default_on_default_mode_drops_preapproval() -> None:
    scope = _scope_for("FileWrite", permission_mode="default")
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


def test_default_on_bypass_mode_yields_bypass_scope() -> None:
    scope = _scope_for("FileWrite", permission_mode="bypassPermissions")
    assert scope.get("mode") == "bypass"


def test_default_on_accept_edits_preapproves_edit_tool() -> None:
    scope = _scope_for("FileWrite", permission_mode="acceptEdits")
    assert scope.get("mode") == "selected_full_toolhost"


def test_resolver_error_falls_back_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising resolver must NOT collapse to the legacy full-toolhost scope."""

    def _boom(self: object, **_kw: object) -> dict[str, object]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "magi_agent.tools.permission_scope.PermissionScopeResolver.resolve",
        _boom,
    )
    scope = _scope_for("FileWrite", permission_mode="default")
    assert scope.get("source") == "fail_closed"
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


def test_rollback_hatch_restores_legacy_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_PERMISSION_SCOPE_LEGACY_FULL_TOOLHOST", "1")
    for mode in ("default", "acceptEdits", "bypassPermissions"):
        scope = _scope_for("FileWrite", permission_mode=mode)
        assert scope == {
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        }


def test_explicit_flag_off_without_hatch_still_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even an explicit MAGI_PERMISSION_SCOPE_FROM_MODE=0 must NOT re-open the
    legacy full-toolhost hole unless the explicit rollback hatch is set."""
    monkeypatch.setenv("MAGI_PERMISSION_SCOPE_FROM_MODE", "0")
    scope = _scope_for("FileWrite", permission_mode="default")
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}
