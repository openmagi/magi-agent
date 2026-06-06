from __future__ import annotations

from magi_agent.recipes.first_party.discovery import (
    DISCOVERY_INSTRUCTION_REF,
    DISCOVERY_PACK_ID,
    build_discovery_pack,
)


def test_pack_is_metadata_only_and_selected_by_discovery_profile() -> None:
    pack = build_discovery_pack()
    assert pack.pack_id == DISCOVERY_PACK_ID
    assert "discovery" in pack.task_profile_selectors
    assert DISCOVERY_INSTRUCTION_REF in pack.instruction_refs


def test_pack_carries_no_live_refs() -> None:
    pack = build_discovery_pack()
    # Mirror learning_usage: instruction/audit metadata only — no tool/callback
    # live refs.
    assert getattr(pack, "tool_refs", ()) == ()
    assert getattr(pack, "callback_refs", ()) == ()
