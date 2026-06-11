"""Group D.3 — a pack connector's projected tools reach the LIVE tool registry's
mode-scoped offer list (shared with Group A) via ``project_connector_tools``."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.connector_projection import project_connector_tools
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_connector_tools_reach_the_live_tool_registry() -> None:
    registries, _ = load_into_registries([_FIRST_PARTY_ROOT / "connector_local_readonly"])
    project_connector_tools(registries)
    names = {m.name for m in registries.tools.list_available(mode="act")}
    assert "LocalSourceOpen" in names
