"""C1 dispatch-policy pack: memory-mode + permission-preflight as removable
``control_plane`` entries with ``phase="tool_host"`` (ctx-callable convention).

``build_tool_host_runtime_from_packs`` loads them for the gate5b host;
``build_control_plane_from_packs`` must SKIP them (they are not LoopControl
providers). Bases are passed explicitly for hermeticity.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import magi_agent

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def _runtime():
    from magi_agent.packs.registries import build_tool_host_runtime_from_packs

    return build_tool_host_runtime_from_packs(bases=[_FIRST_PARTY_ROOT])


def _policies():
    _handlers, policies = _runtime()
    return policies


def test_bundled_tool_host_runtime_loads_handlers_and_policies():
    handlers, policies = _runtime()
    # the C1.3/C1.4 worked-example handlers arrive through the same loader
    assert set(handlers) >= {"Clock", "Calculation", "FileEdit"}
    # memory-mode + permission-preflight policies, ordered by priority
    assert len(policies) >= 2


def test_memory_mode_policy_blocks_protected_write_via_policy_path(tmp_path: Path):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    host = Gate5BFullToolHost(
        config=Gate5BFullToolHostConfig.model_validate(
            {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
             "environment": "local", "environmentAllowlist": ["local"],
             "maxToolCallsPerTurn": 8}
        ),
        workspace_root=tmp_path,
        exposed_tool_names=("FileWrite",),
        now_ms=lambda: 1_700_000_000_000,
        memory_mode="read_only",
        dispatch_policies=_policies(),
    )
    # Target path copied from tests/gates/test_gate5b_full_toolhost_memory_mode.py.
    outcome = asyncio.run(
        host.dispatch(
            "FileWrite",
            {"path": "MEMORY.md", "content": "x"},
            request_digest="r", tool_call_id="c",
        )
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"
    assert not (tmp_path / "MEMORY.md").exists()


def test_permission_preflight_policy_blocks_workspace_escape(tmp_path: Path):
    """The C1.0 golden showed the escape block is produced by the permission
    preflight (reason path_escapes_workspace) — the pack policy must reproduce
    it exactly."""
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    host = Gate5BFullToolHost(
        config=Gate5BFullToolHostConfig.model_validate(
            {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
             "environment": "local", "environmentAllowlist": ["local"],
             "maxToolCallsPerTurn": 8}
        ),
        workspace_root=tmp_path,
        exposed_tool_names=("FileRead",),
        now_ms=lambda: 1_700_000_000_000,
        dispatch_policies=_policies(),
    )
    outcome = asyncio.run(
        host.dispatch(
            "FileRead", {"path": "../escape.txt"},
            request_digest="r", tool_call_id="c",
        )
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "path_escapes_workspace"


def test_loop_plane_assembly_skips_tool_host_entries():
    """build_control_plane_from_packs must NOT register phase='tool_host' impls
    as LoopControls (they are ctx-callables, not providers — invoking one with a
    ControlPlaneProvideContext would crash the assembly)."""
    from magi_agent.packs.registries import build_control_plane_from_packs

    plane = build_control_plane_from_packs(bases=[_FIRST_PARTY_ROOT])
    for control in plane._controls:
        assert "gate5b" not in type(control).__name__.lower()
