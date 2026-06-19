"""Read-only ``net`` tools auto-allow under the fail-closed default (A-1 handback).

PR #704 made the permission scope fail-closed: the legacy ``selected_full_toolhost``
preapproval no longer auto-allows every safety-passing tool. The handback bug is
that read-only ``net`` tools (WebSearch, WebFetch GET) then fall through to
``approval_required_reason`` and prompt on every call in ``default`` mode.

The fix: a ``net`` tool that is declared read-only (``parallel_safety="readonly"``)
must NOT require approval, while net-write / externally-mutating net tools and
write / execute / dangerous tools STILL require approval.
"""

from __future__ import annotations

import pytest

from magi_agent.plugins.manager import resolve_plugin_state
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.tools.catalog import core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.permission import ToolPermissionPolicy, approval_required_reason
from magi_agent.plugins.tool_projection import project_native_plugin_tool_manifests


def _projected_web_manifest(name: str) -> ToolManifest:
    state = resolve_plugin_state(native_plugin_manifests())
    manifests = {m.name: m for m in project_native_plugin_tool_manifests(state)}
    assert name in manifests, f"{name} not projected; available={sorted(manifests)[:10]}"
    return manifests[name]


def _core_manifest(name: str) -> ToolManifest:
    manifests = {m.name: m for m in core_tool_manifests()}
    assert name in manifests, f"{name} not in core catalog"
    return manifests[name]


def _net_side_effecting_manifest() -> ToolManifest:
    """A ``net`` tool that is NOT read-only and causes external side effects."""
    return ToolManifest(
        name="NetExternalWrite",
        description="A net tool that performs an external write/side-effect.",
        kind="native",
        source=ToolSource(kind="native-plugin", package="openmagi.test"),
        permission="net",
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=0,
        side_effect_class="external",
        parallel_safety="unsafe",
    )


# ---------------------------------------------------------------------------
# Unit: approval_required_reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["WebSearch", "WebFetch"])
def test_readonly_net_tool_does_not_require_approval(name: str) -> None:
    manifest = _projected_web_manifest(name)
    assert manifest.permission == "net"
    assert manifest.parallel_safety == "readonly", (
        f"{name} must carry the read-only signal at its manifest definition site"
    )
    assert approval_required_reason(manifest) is None


def test_local_read_tool_does_not_require_approval() -> None:
    assert approval_required_reason(_core_manifest("FileRead")) is None


def test_write_tool_still_requires_approval() -> None:
    assert approval_required_reason(_core_manifest("FileWrite")) is not None


def test_execute_tool_still_requires_approval() -> None:
    assert approval_required_reason(_core_manifest("Bash")) is not None


def test_dangerous_tool_still_requires_approval() -> None:
    dangerous = ToolManifest(
        name="DangerNet",
        description="A dangerous net tool.",
        kind="native",
        source=ToolSource(kind="native-plugin", package="openmagi.test"),
        permission="net",
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=0,
        dangerous=True,
        side_effect_class="local_process",
        parallel_safety="unsafe",
    )
    assert approval_required_reason(dangerous) is not None


def test_net_side_effecting_tool_still_requires_approval() -> None:
    manifest = _net_side_effecting_manifest()
    assert approval_required_reason(manifest) is not None


# ---------------------------------------------------------------------------
# Policy: decide() under a fail-closed default scope (no preapproval)
# ---------------------------------------------------------------------------


def _fail_closed_context() -> ToolContext:
    return ToolContext(
        bot_id="bot-1",
        turn_id="turn-1",
        workspace_root="/tmp/ws",
        permission_scope={"mode": "default", "source": "fail_closed"},
    )


def _decide(manifest: ToolManifest, args: dict[str, object]) -> str:
    policy = ToolPermissionPolicy()
    decision = policy.decide(manifest, args, _fail_closed_context(), mode="act")
    return decision.action


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("WebSearch", {"query": "magi agent"}),
        ("WebFetch", {"url": "https://example.com", "method": "GET"}),
    ],
)
def test_decide_readonly_net_allows_under_fail_closed(
    name: str, args: dict[str, object]
) -> None:
    assert _decide(_projected_web_manifest(name), args) == "allow"


def test_decide_file_read_allows_under_fail_closed() -> None:
    assert _decide(_core_manifest("FileRead"), {"path": "README.md"}) == "allow"


def test_decide_net_side_effecting_still_asks_under_fail_closed() -> None:
    assert _decide(_net_side_effecting_manifest(), {}) == "ask"
