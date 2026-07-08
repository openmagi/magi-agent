"""First-party ``deep_solve`` pack (U4): manifest + catalog projection.

The pack carries **catalog metadata only** for the ``DeepSolve`` tool — the
runtime handler lives in ``magi_agent/plugins/native/deep_solve.py`` and is
registered via ``plugins/native_catalog.py`` (U3), exactly like SpawnAgent
(design D2/B2). Mirrors ``tests/firstparty/test_tools_persistent_python_pack.py``.

Hermetic, no network.
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.manifest import load_manifest_from_toml
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_PACK = _FIRST_PARTY_ROOT / "deep_solve"
_PACK_ID = "openmagi.deep-solve"
_TOOL_NAME = "DeepSolve"


# --------------------------------------------------------------------------- #
# 1. pack.toml validates against packs/manifest.py (static, no impl import)
# --------------------------------------------------------------------------- #


def test_pack_manifest_validates() -> None:
    manifest = load_manifest_from_toml(_PACK / "pack.toml")
    assert manifest.pack_id == _PACK_ID
    assert manifest.display_name == "Deep Solve"
    assert manifest.version == "1.0.0"
    entries = {(entry.type, entry.ref) for entry in manifest.provides}
    assert ("tool", _TOOL_NAME) in entries
    # Tool entries carry a code impl (module:symbol), never a spec file.
    tool_entry = next(e for e in manifest.provides if e.type == "tool")
    assert tool_entry.impl is not None
    assert ":" in tool_entry.impl
    assert tool_entry.spec is None


# --------------------------------------------------------------------------- #
# 2. Pack loader registers the DeepSolve ToolManifest (metadata only)
# --------------------------------------------------------------------------- #


def test_pack_registers_deep_solve_manifest() -> None:
    registries, report = load_into_registries([_PACK])
    assert _TOOL_NAME in report.registered
    manifest = registries.tools.resolve(_TOOL_NAME)
    assert manifest is not None
    assert manifest.name == _TOOL_NAME
    assert manifest.permission == "execute"
    # Input schema mirrors the handler's argument surface.
    schema = manifest.input_schema
    assert schema["required"] == ["problem"]
    for key in ("problem", "test_command", "domain", "consecutive_clean_passes"):
        assert key in schema["properties"]


def test_pack_offered_in_act_mode() -> None:
    registries, _ = load_into_registries([_PACK])
    act_names = {m.name for m in registries.tools.list_available(mode="act")}
    assert _TOOL_NAME in act_names


# --------------------------------------------------------------------------- #
# 3. Live catalog projection includes openmagi.deep-solve
# --------------------------------------------------------------------------- #


def test_live_catalog_discovers_deep_solve() -> None:
    from magi_agent.packs.catalog_build import resolve_live_catalog

    live = resolve_live_catalog()
    assert _TOOL_NAME in live.tool_refs
