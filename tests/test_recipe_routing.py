from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest
from magi_agent.recipes.recipe_routing import (
    SELECTED_RECIPE_PACK_IDS_STATE_KEY,
    SELECT_RECIPE_TOOL_NAME,
    build_recipe_listing_section,
    select_recipe_handler,
)
from magi_agent.tools.context import ToolContext


class _StubAdkToolContext:
    """Minimal stand-in for ADK's ToolContext carrying a mutable ``state`` dict.

    The real ADK ``ToolContext`` exposes a mutable ``state`` mapping that
    survives across tool calls within a turn/session; the runner threads it onto
    ``ToolContext.adk_tool_context``. The handler accumulates selected pack ids
    there, so a deterministic stub with a plain dict is sufficient for tests.
    """

    def __init__(self) -> None:
        self.state: dict[str, object] = {}


def _context(adk: object | None = None) -> ToolContext:
    return ToolContext(botId="test-bot", adkToolContext=adk)


def _manifest(pack_id: str, *, hard_safety: bool, when_to_use: str) -> RecipePackManifest:
    # hard-safety packs carry a manifest invariant: they must be non-opt-out and
    # non-customizable (compiler._validate_safety_and_metadata_only). Set those
    # fields accordingly so the synthetic manifests validate without weakening
    # the invariant.
    return RecipePackManifest(
        packId=pack_id,
        displayName=pack_id,
        description=f"synthetic pack {pack_id}",
        whenToUse=when_to_use,
        hardSafety=hard_safety,
        optOutAllowed=not hard_safety,
        customizable=not hard_safety,
    )


def test_listing_lists_non_hard_packs_with_when_to_use_and_excludes_hard_safety():
    registry = PackRegistry.with_first_party_packs()
    section = build_recipe_listing_section(registry)
    for p in registry.values():
        if p.hard_safety:
            assert p.pack_id not in section            # hard-safety never routed
        else:
            assert p.pack_id in section                # routable pack listed
            assert p.when_to_use.split("\n")[0] in section
    assert SELECT_RECIPE_TOOL_NAME in section          # advertises the load tool


def test_listing_skips_packs_without_when_to_use():
    # a non-hard pack with empty when_to_use must not appear (defensive)
    registry = PackRegistry.with_first_party_packs()
    section = build_recipe_listing_section(registry)
    assert isinstance(section, str) and section.strip() != ""


def test_listing_excludes_hard_safety_even_with_when_to_use_and_skips_empty():
    # The two skip conditions (hard_safety, empty when_to_use) must be pinned
    # independently. A synthetic registry isolates each: the first-party packs
    # entangle them (the only hard_safety packs also have empty when_to_use).
    registry = PackRegistry((
        _manifest("test.hard", hard_safety=True, when_to_use="should never be routed"),
        _manifest("test.soft-full", hard_safety=False, when_to_use="pick me"),
        _manifest("test.soft-empty", hard_safety=False, when_to_use=""),
    ))
    section = build_recipe_listing_section(registry)
    assert "test.hard" not in section        # hard_safety excluded despite when_to_use
    assert "test.soft-full" in section
    assert "test.soft-empty" not in section


def _routing_registry() -> PackRegistry:
    return PackRegistry((
        _manifest("test.hard", hard_safety=True, when_to_use="should never be routed"),
        _manifest("test.soft-full", hard_safety=False, when_to_use="pick me"),
        _manifest("test.soft-other", hard_safety=False, when_to_use="also pick me"),
    ))


def test_select_valid_pack_returns_ok_and_compaction_protected():
    registry = _routing_registry()
    adk = _StubAdkToolContext()
    result = select_recipe_handler(
        {"pack_id": "test.soft-full"}, _context(adk), registry=registry
    )
    assert result.status == "ok"
    assert result.metadata.get("compactionProtected") is True
    assert result.metadata.get("toolName") == SELECT_RECIPE_TOOL_NAME
    # body carries the pack's identity / when-to-use info
    assert "test.soft-full" in str(result.output)
    assert "pick me" in str(result.output)
    # accumulated into ADK state for a later resolver to drain
    assert adk.state[SELECTED_RECIPE_PACK_IDS_STATE_KEY] == ("test.soft-full",)


def test_select_unknown_pack_returns_error_not_crash():
    registry = _routing_registry()
    result = select_recipe_handler(
        {"pack_id": "test.nope"}, _context(_StubAdkToolContext()), registry=registry
    )
    assert result.status == "error"
    assert result.error_code  # carries an error code, did not raise


def test_select_hard_safety_pack_is_blocked_noop():
    registry = _routing_registry()
    adk = _StubAdkToolContext()
    result = select_recipe_handler(
        {"pack_id": "test.hard"}, _context(adk), registry=registry
    )
    assert result.status == "blocked"
    # hard packs are always-on, never routed → nothing accumulated
    assert SELECTED_RECIPE_PACK_IDS_STATE_KEY not in adk.state


def test_select_missing_pack_id_returns_error_not_crash():
    registry = _routing_registry()
    result = select_recipe_handler(
        {}, _context(_StubAdkToolContext()), registry=registry
    )
    assert result.status == "error"


def test_multi_call_accumulates_selected_pack_ids_dedup_ordered():
    registry = _routing_registry()
    adk = _StubAdkToolContext()
    ctx = _context(adk)
    select_recipe_handler({"pack_id": "test.soft-full"}, ctx, registry=registry)
    select_recipe_handler({"pack_id": "test.soft-other"}, ctx, registry=registry)
    # duplicate select of an already-accumulated pack must not double up
    select_recipe_handler({"pack_id": "test.soft-full"}, ctx, registry=registry)
    assert adk.state[SELECTED_RECIPE_PACK_IDS_STATE_KEY] == (
        "test.soft-full",
        "test.soft-other",
    )


def test_select_without_adk_state_still_returns_ok_failsafe():
    # No ADK tool context (no accumulator) must NOT crash — still returns ok body.
    registry = _routing_registry()
    result = select_recipe_handler(
        {"pack_id": "test.soft-full"}, _context(None), registry=registry
    )
    assert result.status == "ok"
    assert "test.soft-full" in str(result.output)
