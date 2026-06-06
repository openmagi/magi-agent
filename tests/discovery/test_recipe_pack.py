from __future__ import annotations

import json

from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)
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


def test_discovery_pack_is_registered_first_party_but_default_off() -> None:
    """The discovery pack is part of the first-party registry but OFF.

    Mirrors ``test_learning_pack_is_registered_first_party_but_default_off`` —
    proves the lazy ``_build_discovery_pack()`` registration in
    ``_first_party_packs`` actually lands the pack in the shared registry.
    """
    registry = PackRegistry.with_first_party_packs()
    assert DISCOVERY_PACK_ID in registry.pack_ids
    pack = registry.get(DISCOVERY_PACK_ID)
    assert pack.default_enabled is False
    assert pack.hard_safety is False


def test_compiled_snapshot_off_does_not_contain_discovery_ref() -> None:
    """OFF (default) — no task profile selecting discovery → ref absent.

    The discovery pack is registered first-party, but without a task profile
    that selects it the compiled snapshot must NOT contain the instruction ref
    and the discovery pack must NOT be selected.
    """
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())
    request = ProfileResolutionRequest(taskProfile={"taskType": "general"})
    snapshot = compiler.compile(request)

    rendered = json.dumps(
        snapshot.model_dump(by_alias=True, mode="json"), sort_keys=True
    )
    assert DISCOVERY_PACK_ID not in snapshot.selected_pack_ids
    assert DISCOVERY_INSTRUCTION_REF not in snapshot.instruction_refs
    assert DISCOVERY_INSTRUCTION_REF not in rendered


def test_compiled_snapshot_on_includes_instruction_ref_when_selected() -> None:
    """END-TO-END — a ``discovery`` task profile resolves the pack from the
    REGISTRY (not by calling the builder directly) and injects its ref."""
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())
    request = ProfileResolutionRequest(taskProfile={"taskType": "discovery"})
    snapshot = compiler.compile(request)

    assert DISCOVERY_PACK_ID in snapshot.selected_pack_ids
    assert DISCOVERY_INSTRUCTION_REF in snapshot.instruction_refs
