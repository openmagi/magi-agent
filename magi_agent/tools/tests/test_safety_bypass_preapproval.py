"""bypassPermissions must preapprove workspace mutation + complex shell.

Regression guard for the pinned CLI headless write no-op: under an explicit
``bypass`` scope (``--permission-mode bypassPermissions``) the runtime safety
arbiter previously returned ``ask`` for FileEdit / FileWrite / PatchApply / Bash,
because only ``selected_full_toolhost`` scope was treated as a mutation/shell
preapproval. ``permission.decide`` honors safety's ``ask`` before the
``bypass_preapproved`` rescue, so ``bypassPermissions`` could not actually bypass
workspace-write / complex-shell approvals. Hard-safety denies still deny under
bypass — those run before any preapproval and are asserted here too.
"""

from __future__ import annotations

from magi_agent.gates.gate5b_full_toolhost import _legacy_tool_manifest
from magi_agent.tools.context import ToolContext
from magi_agent.tools.safety import RuntimePermissionArbiter

_BYPASS = {"mode": "bypass", "source": "bypass"}
_DEFAULT = {"mode": "default", "source": "builtin"}


def _ctx(scope: dict[str, object]) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        turnId="test-turn",
        workspaceRoot="/tmp/ws",
        permissionScope=scope,
    )


def _decide(tool: str, args: dict[str, object], scope: dict[str, object]):
    return RuntimePermissionArbiter().decide(
        _legacy_tool_manifest(tool), args, _ctx(scope), mode="act"
    )


# --- bypass preapproves workspace mutation --------------------------------


def test_file_edit_bypass_preapproved() -> None:
    d = _decide(
        "FileEdit",
        {"path": "mod.py", "old_text": "a - b", "new_text": "a + b"},
        _BYPASS,
    )
    assert d.action == "allow", d.metadata.get("reasonCodes")
    assert d.metadata["securityPrecheck"] == "passed"


def test_file_write_bypass_preapproved() -> None:
    d = _decide("FileWrite", {"path": "new.txt", "content": "hi"}, _BYPASS)
    assert d.action == "allow", d.metadata.get("reasonCodes")


def test_patch_apply_bypass_preapproved() -> None:
    patch = "--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-a - b\n+a + b\n"
    d = _decide("PatchApply", {"path": "mod.py", "patch": patch}, _BYPASS)
    assert d.action == "allow", d.metadata.get("reasonCodes")


def test_bash_complex_command_bypass_preapproved() -> None:
    # A complex (non-readonly) shell command that under default/full-toolhost
    # asks: under bypass it must be preapproved.
    d = _decide("Bash", {"command": "echo hello && echo world > out.txt"}, _BYPASS)
    assert d.action == "allow", d.metadata.get("reasonCodes")


# --- guards: bypass never loosens the default posture or hard-safety -------


def test_file_edit_default_still_asks() -> None:
    d = _decide(
        "FileEdit",
        {"path": "mod.py", "old_text": "a - b", "new_text": "a + b"},
        _DEFAULT,
    )
    assert d.action == "ask"
    assert "workspace_mutation_requires_approval" in d.metadata["reasonCodes"]


def test_bash_destructive_still_denied_under_bypass() -> None:
    d = _decide("Bash", {"command": "rm -rf /"}, _BYPASS)
    assert d.action == "deny"
    # Hard-safety survives bypass with the explicit marker.
    assert "bypass_denied_hard_safety" in d.metadata["reasonCodes"]
