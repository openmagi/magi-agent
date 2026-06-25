"""PR-F-EXEC1 production-wire test: the LifecycleShellCommandControl is
surfaced via the bundled control_plane pack provider so the live runner
path (cli/real_runner + transport/gate5b_governance which both go through
build_default_plugin → build_control_plane_from_packs) picks it up.

Without this pack provider entry the control would only register through
the legacy/compat build_default_plane composition surface — operator-
authored shell_command rules with a per-turn budget cap would silently
lose the cap on operator-facing serve/REPL/child paths (the F-LIFE2
blocker lesson).

Mirror of tests/customize_firing/test_session_task_emitters_pack_provider.py:
pinning both build_default_plugin (top-level surface) AND
build_control_plane_from_packs (inner seam) catches a regression that
bypasses the pack loader entirely.
"""

from __future__ import annotations

import pytest

from magi_agent.adk_bridge.lifecycle_shell_command_control import (
    LifecycleShellCommandControl,
)


def _has_lifecycle_shell_command_control(controls) -> bool:
    return any(isinstance(c, LifecycleShellCommandControl) for c in controls)


def test_pack_provider_registers_control_when_master_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_default_plugin (the live runner path) MUST surface the
    LifecycleShellCommandControl when MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED
    is ON — proving the pack.toml provider entry is wired into the pack
    loader (build_control_plane_from_packs), not just the legacy
    build_default_plane composition."""
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")

    plugin = build_default_plugin(
        {
            "MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED": "1",
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED": "1",
        }
    )
    assert _has_lifecycle_shell_command_control(plugin._p._controls)


def test_pack_provider_inert_when_master_flag_off() -> None:
    """Default-OFF byte-identical contract: no-arg build_default_plugin()
    MUST NOT register the F-EXEC1 control. Locks the strict default-OFF
    semantics on the live runner path."""
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    plugin = build_default_plugin({})
    assert not _has_lifecycle_shell_command_control(plugin._p._controls)


def test_pack_loader_registers_control_when_master_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_control_plane_from_packs (the inner pack-loader seam that
    build_default_plugin delegates to) MUST surface the
    LifecycleShellCommandControl when the master flag is ON. Pinning the
    inner seam in addition to build_default_plugin catches a regression
    that bypasses the pack loader entirely."""
    from magi_agent.packs.registries import build_control_plane_from_packs

    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")

    plane = build_control_plane_from_packs(
        os_environ={
            "MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED": "1",
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED": "1",
        }
    )
    assert _has_lifecycle_shell_command_control(plane._controls)


def test_pack_toml_lists_lifecycle_shell_command_provider() -> None:
    """Defense-in-depth: the bundled pack.toml MUST declare the
    ``control_plane:lifecycle-shell-command@1`` provider so the loader
    can discover it. A future PR that drops the entry would fail this
    test loudly even if the pack loader silently no-ops."""
    from importlib.resources import files

    pack_text = (
        files("magi_agent.firstparty.packs.control_plane_default")
        .joinpath("pack.toml")
        .read_text(encoding="utf-8")
    )
    assert "control_plane:lifecycle-shell-command@1" in pack_text
    assert "provide_lifecycle_shell_command_controls" in pack_text
