"""Group E.3 — a pack-provided harness's components reach the LIVE resolved preset
state via ``apply_harness_pack`` (the resolved-state seam)."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.packs.harness_projection import apply_harness_pack
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_pack_harness_components_reach_resolved_state() -> None:
    registries, _ = load_into_registries([_FIRST_PARTY_ROOT / "harness_coding_lean"])
    pack = registries.harnesses.resolve("harness:coding-lean@1")
    state = build_default_resolved_harness_state(agent_role="coding")
    updated = apply_harness_pack(state, slot="coding", pack=pack)
    assert "FileEdit" in updated.coding.components["tools"]
    assert updated.coding.enabled is True
