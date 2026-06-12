"""Group A.3 — a pack-registered tool reaches the LIVE mode-scoped offer list
(``ToolRegistry.list_available``), not just the catalog. Same real-ABI wiring."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_pack_registered_tool_is_offered_in_act_and_plan_modes() -> None:
    registries, _ = load_into_registries([_FIRST_PARTY_ROOT / "tools_clock"])
    act_names = {m.name for m in registries.tools.list_available(mode="act")}
    plan_names = {m.name for m in registries.tools.list_available(mode="plan")}
    assert "Clock" in act_names
    assert "Clock" in plan_names
