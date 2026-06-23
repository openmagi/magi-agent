"""C1 decomposition boundary guard (the SS1 guard's source of truth).

The C1 worked examples migrated EXACTLY Clock/Calculation/FileEdit into the
workspace-tools pack and the memory-mode/permission-preflight policies into the
gates-policy pack; the remaining 8 legacy ``_handle`` branches follow the
repeating template in
``magi_agent/firstparty/packs/workspace_tools_default/MIGRATION.md``.
This test pins the boundary so any further migration MUST update the tracking
doc and these sets together — and proves the SS1 reach of the seam: a user pack
can already override a migrated handler AND supply a handler for a
not-yet-migrated tool (the handler-first lookup beats the legacy branch).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import magi_agent
from magi_agent.packs.manifest import load_manifest_from_toml

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"

#: Tools whose _handle branch has been moved into the workspace-tools pack.
MIGRATED_WORKSPACE_TOOLS = frozenset({"Clock", "Calculation", "FileEdit"})

#: Legacy _handle branches still in the kernel, awaiting the MIGRATION.md
#: template. Shrink this set as each tool moves (and update MIGRATION.md).
PENDING_WORKSPACE_TOOLS = frozenset(
    {"FileRead", "Glob", "Grep", "FileWrite", "PatchApply", "Bash", "TestRun", "GitDiff", "ListCredentials"}
)


def test_boundary_matches_bundled_pack() -> None:
    from magi_agent.packs.registries import build_tool_host_runtime_from_packs

    handlers, policies = build_tool_host_runtime_from_packs(bases=[_FIRST_PARTY_ROOT])
    assert set(handlers) == MIGRATED_WORKSPACE_TOOLS, (
        "workspace-tools pack drifted from the documented C1 boundary — update "
        "MIGRATED_WORKSPACE_TOOLS/PENDING_WORKSPACE_TOOLS and MIGRATION.md together"
    )
    assert not (set(handlers) & PENDING_WORKSPACE_TOOLS)
    assert len(policies) == 2  # memory-mode + permission-preflight


def test_boundary_covers_all_legacy_tool_names() -> None:
    from magi_agent.gates.gate5b_full_toolhost import (
        _GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES,
    )

    legacy = set(_GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES) | {"TestRun", "GitDiff"}
    assert MIGRATED_WORKSPACE_TOOLS | PENDING_WORKSPACE_TOOLS == legacy


def test_policy_pack_declares_exactly_the_two_moved_policies() -> None:
    manifest = load_manifest_from_toml(
        _FIRST_PARTY_ROOT / "gates_policy_default" / "pack.toml"
    )
    refs = {entry.ref for entry in manifest.provides}
    assert refs == {"gate5b:memory-mode@1", "gate5b:permission-preflight@1"}
    for entry in manifest.provides:
        assert entry.type == "control_plane"
        assert entry.phase == "tool_host"
        assert entry.gate_position == "before"


def _write_user_pack(root: Path) -> None:
    pack_dir = root / "my-workspace-tools"
    pack_dir.mkdir(parents=True)
    (pack_dir / "user_ws_impl.py").write_text(
        "def provide_clock_override(ctx):\n"
        "    if ctx.register_workspace_handler is not None:\n"
        "        ctx.register_workspace_handler(\n"
        "            'Clock', lambda args, view: {'nowMs': 1, 'viaUserPack': True})\n"
        "def provide_bash_handler(ctx):\n"
        "    if ctx.register_workspace_handler is not None:\n"
        "        ctx.register_workspace_handler(\n"
        "            'Bash', lambda args, view: {'exitCode': 0, 'viaUserPack': True})\n"
    )
    (pack_dir / "pack.toml").write_text(
        'packId = "user.my-workspace-tools"\n'
        'displayName = "user workspace tools"\n'
        'version = "0.0.1"\n'
        "\n"
        "[[provides]]\n"
        'type = "tool"\n'
        'ref = "workspace:Clock@1"\n'
        'impl = "user_ws_impl:provide_clock_override"\n'
        "\n"
        "[[provides]]\n"
        'type = "tool"\n'
        'ref = "workspace:Bash@1"\n'
        'impl = "user_ws_impl:provide_bash_handler"\n'
    )


def test_user_pack_overrides_migrated_and_pending_handlers(tmp_path: Path, monkeypatch):
    """SS1: through the IDENTICAL loader path a user pack (a) replaces the
    first-party Clock handler (same ref, last-wins) and (b) binds a handler for
    a PENDING tool (Bash) which the handler-first lookup prefers over the
    legacy branch."""
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )
    from magi_agent.packs.registries import build_tool_host_runtime_from_packs

    user_root = tmp_path / "user_packs"
    _write_user_pack(user_root)
    monkeypatch.syspath_prepend(str(user_root / "my-workspace-tools"))

    handlers, _policies = build_tool_host_runtime_from_packs(
        bases=[_FIRST_PARTY_ROOT, user_root]
    )
    host = Gate5BFullToolHost(
        config=Gate5BFullToolHostConfig.model_validate(
            {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
             "environment": "local", "environmentAllowlist": ["local"],
             "maxToolCallsPerTurn": 8}
        ),
        workspace_root=tmp_path,
        exposed_tool_names=("Clock", "Bash"),
        now_ms=lambda: 1_700_000_000_000,
        workspace_handlers=handlers,
    )
    clock = asyncio.run(host.dispatch("Clock", {}, request_digest="r", tool_call_id="c1"))
    assert clock.status == "ok"
    assert clock.output_preview == {"nowMs": 1, "viaUserPack": True}
    bash = asyncio.run(
        host.dispatch("Bash", {"command": "printf hi"},
                      request_digest="r", tool_call_id="c2")
    )
    assert bash.status == "ok"
    assert bash.output_preview == {"exitCode": 0, "viaUserPack": True}


def test_disabling_firstparty_gates_packs_removes_them(tmp_path: Path, monkeypatch):
    """SS1 REMOVE: config.toml [packs] disable drops both C1 packs through the
    shipped removal convention. The host then dual-loads the legacy in-module
    enforcement (defense in depth until the C1.5 template completes), so
    removal never strands a live host without memory-mode policy."""
    from magi_agent.packs.registries import build_tool_host_runtime_from_packs

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[packs]\ndisable = ["open' 'magi.workspace-tools-default", '
        '"open' 'magi.gates-policy-default"]\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))
    handlers, policies = build_tool_host_runtime_from_packs(bases=[_FIRST_PARTY_ROOT])
    assert handlers == {}
    assert policies == ()
