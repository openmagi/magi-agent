"""The 3 main-side control-plane features are PACK-LOADED, not hardcoded.

FactsReplanControl (#510), the tool-synthesis nudge (#512), and the
loop-resilience pair (6b7cd40e: tool-exception reflection + schema feedback)
each get their OWN ``provides`` entry in the bundled control_plane pack —
discovered and loaded through the SAME loader a user ``~/.magi/packs``
control_plane pack uses (§1 no privilege):

* per-feature manifest entries with ``priority`` ordering — the nudge entry
  carries the highest priority so it registers LAST (edit-retry / resilience
  overrides win the first-non-None-wins after-tool fan-out);
* env-flag default-OFF semantics preserved exactly (provider impls read the
  same ``context.env`` gates as the legacy assembly);
* the monolithic default provider no longer registers them (no double-load);
* a user pack can override a SINGLE feature ref (last-wins) without touching
  the rest of the default plane;
* the pack-loaded plane matches ``build_default_plane``'s legacy composition
  byte-for-byte for the same env + collaborators.
"""

from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.adk_bridge.control_plane import (
    build_default_plane,
    build_default_plugin,
)
from magi_agent.adk_bridge.facts_replan_control import FACTS_REPLAN_CONTROL_NAME
from magi_agent.adk_bridge.schema_feedback import SCHEMA_FEEDBACK_CONTROL_NAME
from magi_agent.adk_bridge.tool_exception_reflection import (
    TOOL_EXCEPTION_REFLECTION_PLUGIN_NAME,
)
from magi_agent.adk_bridge.tool_synthesis_nudge import (
    TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME,
)
from magi_agent.packs.discovery import _bundled_firstparty_base
from magi_agent.packs.manifest import load_manifest_from_toml
from magi_agent.packs.registries import build_control_plane_from_packs

_PACK_TOML = (
    Path(magi_agent.__file__).parent
    / "firstparty"
    / "packs"
    / "control_plane_default"
    / "pack.toml"
)

_FRONTIER_LABEL = "anthropic/claude-sonnet-4-6"

_ALL_FEATURE_FLAGS = {
    "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "1",
    "MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED": "1",
    "MAGI_FACTS_REPLAN_ENABLED": "1",
    "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED": "1",
}

_FEATURE_REFS = {
    "control_plane:loop-resilience@1",
    "control_plane:facts-replan@1",
    "control_plane:tool-synthesis-nudge@1",
}

_FEATURE_CONTROL_NAMES = {
    TOOL_EXCEPTION_REFLECTION_PLUGIN_NAME,
    SCHEMA_FEEDBACK_CONTROL_NAME,
    FACTS_REPLAN_CONTROL_NAME,
    TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME,
}


def _names(plane) -> list[str]:
    return [getattr(c, "name", type(c).__name__) for c in plane._controls]


def _bundled_plane(env: dict[str, str], **kwargs):
    return build_control_plane_from_packs(
        bases=[_bundled_firstparty_base()], os_environ=env, **kwargs
    )


# --- manifest shape -----------------------------------------------------------------


def test_manifest_declares_one_entry_per_feature() -> None:
    manifest = load_manifest_from_toml(_PACK_TOML)
    refs = {e.ref for e in manifest.provides if e.type == "control_plane"}
    assert _FEATURE_REFS <= refs, refs
    assert "control_plane:default@1" in refs


def test_manifest_priority_puts_nudge_last() -> None:
    manifest = load_manifest_from_toml(_PACK_TOML)
    entries = {
        e.ref: e for e in manifest.provides if e.type == "control_plane"
    }
    nudge = entries["control_plane:tool-synthesis-nudge@1"]
    for ref, entry in entries.items():
        if ref == "control_plane:tool-synthesis-nudge@1":
            continue
        assert (entry.priority or 0) < (nudge.priority or 0), (
            f"{ref} must order before the nudge entry"
        )


# --- pack-loaded liveness + default-OFF ----------------------------------------------


def test_pack_path_loads_all_three_features_when_flagged(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    plane = _bundled_plane(
        dict(_ALL_FEATURE_FLAGS), tool_synthesis_model_label=_FRONTIER_LABEL
    )
    names = _names(plane)
    assert TOOL_EXCEPTION_REFLECTION_PLUGIN_NAME in names
    assert SCHEMA_FEEDBACK_CONTROL_NAME in names
    assert FACTS_REPLAN_CONTROL_NAME in names
    assert TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME in names
    assert names[-1] == TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME, "nudge registers LAST"


def test_pack_path_all_off_loads_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    # facts-replan + tool-synthesis-nudge are profile-aware default-ON (_pb), so
    # disable them explicitly; the other two features are strict default-OFF.
    plane = _bundled_plane(
        {
            "MAGI_FACTS_REPLAN_ENABLED": "0",
            "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED": "0",
        },
        tool_synthesis_model_label=_FRONTIER_LABEL,
    )
    assert not (_FEATURE_CONTROL_NAMES & set(_names(plane)))


def test_pack_path_matches_legacy_assembly_byte_for_byte(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    env = {
        **_ALL_FEATURE_FLAGS,
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
        "MAGI_LOOP_GUARD_ENABLED": "1",
        "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
        "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
    }
    legacy = build_default_plane(
        os_environ=env, tool_synthesis_model_label=_FRONTIER_LABEL
    )
    loaded = _bundled_plane(env, tool_synthesis_model_label=_FRONTIER_LABEL)
    assert [type(c).__name__ for c in loaded._controls] == [
        type(c).__name__ for c in legacy._controls
    ]
    assert _names(loaded) == _names(legacy)


def test_live_plugin_path_loads_features_and_keeps_nudge_last(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    plugin = build_default_plugin(
        os_environ={
            "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED": "1",
            "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
            "MAGI_FACTS_REPLAN_ENABLED": "1",
        },
        tool_synthesis_model_label=_FRONTIER_LABEL,
    )
    names = _names(plugin._p)
    assert FACTS_REPLAN_CONTROL_NAME in names
    assert names[-1] == TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME
    assert names.index(TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME) > next(
        i for i, n in enumerate(names) if "edit_retry" in n
    )


# --- §1: per-feature override through the identical loader ---------------------------


def test_user_pack_can_override_a_single_feature_ref(tmp_path, monkeypatch) -> None:
    """A user pack re-declaring ONLY ``control_plane:facts-replan@1`` (last-wins)
    replaces that provider without touching the rest of the default plane."""
    user_root = tmp_path / "packs"
    pack_dir = user_root / "user_facts_off"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(
        "def provide(ctx):\n    return None  # registers nothing\n"
    )
    (pack_dir / "pack.toml").write_text(
        'packId = "user.facts-replan-off"\n'
        'displayName = "user facts-replan override"\nversion = "0.0.1"\n\n'
        '[[provides]]\ntype = "control_plane"\n'
        'ref = "control_plane:facts-replan@1"\n'
        'impl = "user_facts_off.impl:provide"\n'
        'priority = 20\ngatePosition = "after"\n'
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))

    plane = build_control_plane_from_packs(
        bases=[_bundled_firstparty_base(), user_root],
        os_environ=dict(_ALL_FEATURE_FLAGS),
        tool_synthesis_model_label=_FRONTIER_LABEL,
    )
    names = _names(plane)
    assert FACTS_REPLAN_CONTROL_NAME not in names, "user override must win (last-wins)"
    # the other feature entries are untouched
    assert SCHEMA_FEEDBACK_CONTROL_NAME in names
    assert names[-1] == TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME
