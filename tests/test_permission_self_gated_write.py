"""Self-gated WRITE tools auto-allow under the fail-closed default (A-1 handback).

PR #704 made the permission scope fail-closed: the legacy ``selected_full_toolhost``
preapproval no longer auto-allows every safety-passing tool. The symmetric WRITE-side
handback is that the local-CLI tool ``MemoryWrite`` then falls through to
``approval_required_reason`` and prompts on every call in ``act`` mode under the
``default`` scope -- even though its blast radius is bounded (own memory files,
declarative-only) and it is already double-gated (real persistence requires
``MAGI_MEMORY_WRITE_ENABLED=1`` AND an injected provider).

The fix: a tool that carries its own enforcement gate may declare the
``self-gated`` tag. Such a tool auto-allows ONLY when it is a *narrow* write:
``permission == "write"`` and ``dangerous is False`` and the permission is NOT
``execute``/``net``. The exemption can NEVER be abused by an execute/net/dangerous
tool that mis-declares the tag.
"""

from __future__ import annotations

import pytest

from magi_agent.tools.catalog import core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.permission import (
    SELF_GATED_TAG,
    ToolPermissionPolicy,
    approval_required_reason,
    is_self_gated_write_tool,
)


def _core_manifest(name: str) -> ToolManifest:
    manifests = {m.name: m for m in core_tool_manifests()}
    assert name in manifests, f"{name} not in core catalog"
    return manifests[name]


def _tagged(
    *,
    name: str,
    permission: str,
    dangerous: bool = False,
    tags: tuple[str, ...] = (SELF_GATED_TAG,),
    side_effect_class: str | None = None,
    parallel_safety: str = "unsafe",
) -> ToolManifest:
    mutates = permission == "write"
    if side_effect_class is None:
        side_effect_class = "local_workspace" if mutates else "local_process"
    return ToolManifest(
        name=name,
        description="fixture tool for self-gated predicate tests",
        kind="native",
        source=ToolSource(kind="native-plugin", package="openmagi.test"),
        permission=permission,  # type: ignore[arg-type]
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=0,
        dangerous=dangerous,
        mutates_workspace=mutates,
        side_effect_class=side_effect_class,  # type: ignore[arg-type]
        parallel_safety=parallel_safety,  # type: ignore[arg-type]
        tags=tags,
        available_in_modes=("act",),
    )


# ---------------------------------------------------------------------------
# Unit: predicate + approval_required_reason
# ---------------------------------------------------------------------------


def test_memory_write_carries_self_gated_tag() -> None:
    manifest = _core_manifest("MemoryWrite")
    assert SELF_GATED_TAG in manifest.tags
    assert manifest.permission == "write"
    assert manifest.dangerous is False


def test_self_gated_write_tool_does_not_require_approval() -> None:
    manifest = _core_manifest("MemoryWrite")
    assert is_self_gated_write_tool(manifest) is True
    assert approval_required_reason(manifest) is None


def test_self_gated_execute_tool_still_requires_approval() -> None:
    manifest = _tagged(name="SelfGatedExec", permission="execute")
    assert is_self_gated_write_tool(manifest) is False
    assert approval_required_reason(manifest) is not None


def test_self_gated_net_tool_still_requires_approval() -> None:
    manifest = _tagged(
        name="SelfGatedNet",
        permission="net",
        side_effect_class="external",
    )
    assert is_self_gated_write_tool(manifest) is False
    assert approval_required_reason(manifest) is not None


def test_self_gated_dangerous_write_tool_still_requires_approval() -> None:
    manifest = _tagged(name="SelfGatedDanger", permission="write", dangerous=True)
    assert is_self_gated_write_tool(manifest) is False
    assert approval_required_reason(manifest) is not None


def test_plain_write_tool_without_tag_still_requires_approval() -> None:
    assert approval_required_reason(_core_manifest("FileWrite")) is not None


# ---------------------------------------------------------------------------
# Policy: decide() under a fail-closed default scope (no preapproval), act mode
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


def test_decide_memory_write_allows_under_fail_closed() -> None:
    assert _decide(_core_manifest("MemoryWrite"), {"fact": "user prefers dark mode"}) == "allow"


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("FileWrite", {"path": "notes.md"}),
        ("FileEdit", {"path": "notes.md"}),
        ("Bash", {"command": "echo hi && rm -f x"}),
    ],
)
def test_decide_generic_write_execute_still_asks_under_fail_closed(
    name: str, args: dict[str, object]
) -> None:
    assert _decide(_core_manifest(name), args) == "ask"


def test_decide_self_gated_execute_fixture_still_asks() -> None:
    assert _decide(_tagged(name="SelfGatedExec", permission="execute"), {}) == "ask"


def test_decide_self_gated_net_fixture_still_asks() -> None:
    manifest = _tagged(
        name="SelfGatedNet",
        permission="net",
        side_effect_class="external",
    )
    assert _decide(manifest, {}) == "ask"


def test_decide_self_gated_dangerous_fixture_still_asks() -> None:
    manifest = _tagged(name="SelfGatedDanger", permission="write", dangerous=True)
    assert _decide(manifest, {}) == "ask"
