"""Tests for ``PermissionScopeResolver`` (cluster 09 PR1).

The CLI tool runtime historically stamped a hardcoded
``permission_scope={"mode": "selected_full_toolhost", ...}`` onto every
``ToolContext``. That unconditional stamp routed write/execute/net/dangerous/
mutating tools through ``selected_full_toolhost_preapproved`` (allow) and so the
``RuntimePermissionArbiter`` "ask" branch was never reached on the local CLI.

PR1 turns that stamp into a real *mode-derived* scope:

- ``default`` / ``smartApprove`` -> no preapproval scope (arbiter ``ask`` reaches)
- ``acceptEdits``                -> preapproval scope ONLY for edit-class tools
- ``bypassPermissions``          -> ``bypass`` scope (hard-safety still enforced)

These tests are the RED contract for the resolver plus an integration assertion
that a default-mode FileWrite now reaches ``ask`` through ``ToolPermissionPolicy``
(it was an ``allow`` under the legacy stamp).
"""

from __future__ import annotations

import pytest

from magi_agent.tools import ToolSource
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import RuntimeMode, ToolManifest
from magi_agent.tools.permission import ToolPermissionPolicy
from magi_agent.tools.permission_scope import PermissionScopeResolver, fail_closed_scope


def make_manifest(
    name: str,
    *,
    permission: str = "read",
    modes: tuple[RuntimeMode, ...] = ("plan", "act"),
    dangerous: bool = False,
    mutates_workspace: bool = False,
    tags: tuple[str, ...] = (),
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind="core",
        source=ToolSource(kind="builtin", package="tests.tools"),
        permission=permission,  # type: ignore[arg-type]
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=120_000,
        available_in_modes=modes,
        dangerous=dangerous,
        mutates_workspace=mutates_workspace,
        tags=tags,
        enabled_by_default=True,
    )


FILE_WRITE = make_manifest("FileWrite", permission="write", mutates_workspace=True)
FILE_EDIT = make_manifest("FileEdit", permission="write", mutates_workspace=True)
BASH = make_manifest("Bash", permission="execute", mutates_workspace=True)
FILE_READ = make_manifest("FileRead", permission="read")


# --------------------------------------------------------------------------- #
# Resolver unit contract                                                       #
# --------------------------------------------------------------------------- #


def test_default_mode_yields_no_preapproval_scope() -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode="default", manifest=FILE_WRITE)
    # No selected_full_toolhost / bypass preapproval -> arbiter ask can reach.
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


def test_smart_approve_mode_yields_no_preapproval_scope() -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode="smartApprove", manifest=FILE_WRITE)
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


def test_bypass_mode_yields_bypass_scope() -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode="bypassPermissions", manifest=FILE_WRITE)
    assert scope.get("mode") == "bypass"
    assert scope.get("source") == "bypass"


def test_accept_edits_preapproves_edit_class_tools() -> None:
    resolver = PermissionScopeResolver()
    for manifest in (FILE_WRITE, FILE_EDIT):
        scope = resolver.resolve(permission_mode="acceptEdits", manifest=manifest)
        assert scope.get("mode") == "selected_full_toolhost"
        assert scope.get("source") == "selected_full_toolhost"


def test_accept_edits_does_not_preapprove_non_edit_tools() -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode="acceptEdits", manifest=BASH)
    # Bash mutates the workspace but is NOT an edit-class tool, so acceptEdits
    # must not preapprove it.
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


def test_unknown_mode_is_treated_as_default() -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode="totally-unknown", manifest=FILE_WRITE)
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}


# --------------------------------------------------------------------------- #
# Integration: the resolved scope drives the real policy decision              #
# --------------------------------------------------------------------------- #


def _context_with_scope(scope: dict[str, object]) -> ToolContext:
    return ToolContext(
        bot_id="magi-cli",
        user_id="cli",
        session_id="s",
        session_key="s",
        turn_id="t",
        workspace_root="/tmp/ws",
        workspace_ref="local-cli-workspace",
        channel="cli",
        permission_scope=scope,
    )


def _decide(scope: dict[str, object], manifest: ToolManifest) -> str:
    policy = ToolPermissionPolicy()
    decision = policy.decide(
        manifest,
        {"path": "out.txt", "content": "x"},
        _context_with_scope(scope),
        mode="act",
    )
    return decision.action


def test_default_scope_lets_file_write_reach_ask() -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode="default", manifest=FILE_WRITE)
    assert _decide(scope, FILE_WRITE) == "ask"


def test_legacy_stamp_preapproves_file_write_to_allow() -> None:
    # Regression guard: the legacy unconditional stamp (flag OFF behavior) still
    # preapproves FileWrite to allow, proving the resolver's default mode is the
    # behavior change, not an unrelated drift.
    legacy_scope = {"mode": "selected_full_toolhost", "source": "selected_full_toolhost"}
    assert _decide(legacy_scope, FILE_WRITE) == "allow"


def test_accept_edits_scope_preapproves_file_write_to_allow() -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode="acceptEdits", manifest=FILE_WRITE)
    assert _decide(scope, FILE_WRITE) == "allow"


@pytest.mark.parametrize("mode", ["default", "smartApprove"])
def test_non_edit_modes_route_bash_to_ask(mode: str) -> None:
    resolver = PermissionScopeResolver()
    scope = resolver.resolve(permission_mode=mode, manifest=BASH)
    assert _decide(scope, BASH) == "ask"


# --------------------------------------------------------------------------- #
# fail_closed_scope (A-1 / P0.1): resolver-error fallback is least-privilege   #
# --------------------------------------------------------------------------- #


def test_fail_closed_scope_shape() -> None:
    assert fail_closed_scope("resolver_error") == {
        "mode": "default",
        "source": "fail_closed",
        "scopeResolution": "resolver_error",
    }


def test_fail_closed_scope_is_not_preapproval() -> None:
    scope = fail_closed_scope("x")
    # Must never resolve to selected_full_toolhost / bypass preapproval.
    assert scope.get("mode") not in {"selected_full_toolhost", "bypass"}
    assert scope.get("source") != "selected_full_toolhost"


def test_fail_closed_scope_routes_file_write_to_ask() -> None:
    # A fail-closed scope drives the real policy to ask (not allow).
    assert _decide(fail_closed_scope("resolver_error"), FILE_WRITE) == "ask"
