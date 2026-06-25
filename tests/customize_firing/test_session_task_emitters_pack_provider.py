"""PR-F-LIFE4b production-wire test: the session-start LifecycleSessionControl
is surfaced via the bundled control_plane pack provider so the live runner
path (cli/real_runner + transport/gate5b_governance which both go through
build_default_plugin → build_control_plane_from_packs) picks it up.

Without this pack provider entry the control would only register through the
legacy/compat build_default_plane composition surface — authored
on_session_start rules would silently never fire on operator-facing
serve/REPL/child paths (the F-LIFE2 blocker lesson).

Mirror of tests/customize_firing/test_llm_call_hooks_firing.py's
``test_pack_provider_registers_control_when_master_flag_on``: pinning
both build_default_plugin (top-level surface) AND
build_control_plane_from_packs (inner seam) catches a regression that
bypasses the pack loader entirely.
"""

from __future__ import annotations

import pytest

from magi_agent.adk_bridge.lifecycle_session_control import (
    LifecycleSessionControl,
)


def _has_lifecycle_session_control(controls) -> bool:
    return any(isinstance(c, LifecycleSessionControl) for c in controls)


def test_pack_provider_registers_control_when_master_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_default_plugin (the live runner path) MUST surface the
    LifecycleSessionControl when MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED
    is ON — proving the pack.toml provider entry is wired into the pack
    loader (build_control_plane_from_packs), not just the legacy
    build_default_plane composition."""
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "1"
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")

    plugin = build_default_plugin(
        {
            "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED": "1",
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED": "1",
        }
    )
    assert _has_lifecycle_session_control(plugin._p._controls)


def test_pack_provider_inert_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-OFF byte-identical contract: no-arg build_default_plugin()
    MUST NOT register the F-LIFE4b control. Locks the strict default-OFF
    semantics on the live runner path."""
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    monkeypatch.delenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", raising=False
    )
    plugin = build_default_plugin({})
    assert not _has_lifecycle_session_control(plugin._p._controls)


def test_pack_loader_registers_control_when_master_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_control_plane_from_packs (the inner pack-loader seam that
    build_default_plugin delegates to) MUST surface the
    LifecycleSessionControl when the master flag is ON. Pinning the
    inner seam in addition to build_default_plugin catches a regression
    that bypasses the pack loader entirely."""
    from magi_agent.packs.registries import build_control_plane_from_packs

    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "1"
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")

    plane = build_control_plane_from_packs(
        os_environ={
            "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED": "1",
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED": "1",
        }
    )
    assert _has_lifecycle_session_control(plane._controls)


def test_pack_toml_lists_lifecycle_session_task_provider() -> None:
    """Defense-in-depth: the bundled pack.toml MUST declare the
    ``control_plane:lifecycle-session-task@1`` provider so the loader
    can discover it. A future PR that drops the entry would fail this
    test loudly even if the pack loader silently no-ops."""
    from importlib.resources import files

    pack_text = (
        files("magi_agent.firstparty.packs.control_plane_default")
        .joinpath("pack.toml")
        .read_text(encoding="utf-8")
    )
    assert "control_plane:lifecycle-session-task@1" in pack_text
    assert "provide_lifecycle_session_task_controls" in pack_text
