import pytest

from magi_agent.context.auto_compact import AutoCompactionEngine
from magi_agent.context.microcompact import MicrocompactEngine
from magi_agent.context.protected_tools import (
    PRUNE_PROTECTED_TOOLS,
    is_compaction_protected_tool_result,
)
from magi_agent.context.types import WarningLevel
from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest
from magi_agent.recipes.recipe_routing import (
    SELECTED_RECIPE_PACK_IDS_STATE_KEY,
    SELECT_RECIPE_TOOL_NAME,
    build_recipe_listing_section,
    build_recipe_tool_scope,
    project_recipe_route_decided_event,
    register_select_recipe_tool,
    select_recipe_handler,
    select_recipe_manifest,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry


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


def _manifest(
    pack_id: str,
    *,
    hard_safety: bool,
    when_to_use: str,
    granted_tool_names: tuple[str, ...] = (),
) -> RecipePackManifest:
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
        grantedToolNames=granted_tool_names,
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


# ---------------------------------------------------------------------------
# compaction protection — select_recipe result survives, others compacted.
#
# Mirrors the GA protection tests (tests/test_ga_progressive_disclosure.py): the
# decorative ``metadata["compactionProtected"]`` flag is NOT what the runtime
# reads — protection is decided by the tool NAME being in
# ``PRUNE_PROTECTED_TOOLS`` (context/protected_tools.py), exactly like the GA
# load tool. These tests run the REAL Tier-4 / Tier-5 engines.
# ---------------------------------------------------------------------------

async def _summarize(prompt: str) -> str:
    return "SUMMARY"


def _protected_select_recipe_msg(*, tool_use_id: str, chars: int = 60_000) -> dict:
    """A compaction message standing in for a real ``select_recipe`` ToolResult.

    The runtime serializes a :class:`ToolResult` carrying
    ``metadata["toolName"] == SELECT_RECIPE_TOOL_NAME``; the protection predicate
    keys on that name (via ``metadata.toolName``), so the message mirrors the
    metadata the handler emits.
    """
    return {
        "role": "tool",
        "tool_use_id": tool_use_id,
        "content": "y" * chars,
        "metadata": {"toolName": SELECT_RECIPE_TOOL_NAME},
    }


def _big_tool_result(*, name: str, tool_use_id: str, chars: int = 60_000) -> dict:
    return {
        "role": "tool",
        "name": name,
        "tool_use_id": tool_use_id,
        "content": "y" * chars,
    }


def test_select_recipe_registered_in_prune_protected_tools():
    # The real protection set — not the decorative metadata flag — must include
    # select_recipe, exactly like the GA load tool.
    assert SELECT_RECIPE_TOOL_NAME in PRUNE_PROTECTED_TOOLS


def test_real_handler_result_is_compaction_protected_by_predicate():
    # The ACTUAL handler result (its metadata.toolName) must be recognized by the
    # real runtime predicate — proving the protection is not decorative.
    registry = _routing_registry()
    result = select_recipe_handler(
        {"pack_id": "test.soft-full"}, _context(_StubAdkToolContext()), registry=registry
    )
    msg = {"role": "tool", "content": str(result.output), "metadata": dict(result.metadata)}
    assert is_compaction_protected_tool_result(msg) is True


def test_predicate_noop_for_non_select_recipe_result():
    other = {"role": "tool", "name": "test.soft-full", "content": "x" * 100}
    assert is_compaction_protected_tool_result(other) is False


@pytest.mark.asyncio
async def test_microcompact_protects_select_recipe_result():
    protected = _protected_select_recipe_msg(tool_use_id="t-protected")
    other = _big_tool_result(name="some_other_tool", tool_use_id="t-other")
    engine = MicrocompactEngine(classifier=_summarize)
    out, result = await engine.apply([protected, other], WarningLevel.HIGH)

    # The protected select_recipe body is unchanged (still the big content).
    assert out[0]["content"] == protected["content"]
    # The other big tool result is compacted to the summary.
    assert out[1]["content"] == "SUMMARY"
    assert result.messages_compacted == 1


@pytest.mark.asyncio
async def test_auto_compact_protects_select_recipe_result():
    protected_body = "z" * 5_000
    messages: list[dict] = []
    for i in range(6):
        messages.append({"role": "user", "content": f"user message {i}"})
        if i == 1:
            messages.append(
                {
                    "role": "tool",
                    "tool_use_id": "t-protected",
                    "content": protected_body,
                    "metadata": {"toolName": SELECT_RECIPE_TOOL_NAME},
                }
            )
        messages.append({"role": "assistant", "content": f"assistant reply {i}"})

    engine = AutoCompactionEngine(classifier=_summarize, keep_recent_turns=2)
    out, result = await engine.apply(messages, WarningLevel.CRITICAL)

    assert result.activated is True
    # The protected body survives compaction verbatim even though its turn was in
    # the OLD (summarized) region.
    serialized = "".join(
        m.get("content", "") if isinstance(m.get("content"), str) else "" for m in out
    )
    assert protected_body in serialized


# ---------------------------------------------------------------------------
# Half A — gated registration + dispatch wiring (register_select_recipe_tool).
#
# Flag OFF (default) → the tool is NEVER registered: absent from the manifest
# and not dispatchable, so the registry is byte-identical to before. Flag ON →
# the tool is registered + enabled and a dispatched call reaches the handler.
# ---------------------------------------------------------------------------

_FLAG_ENV = "MAGI_RECIPE_ROUTING_LLM_ENABLED"


def test_register_select_recipe_tool_noop_when_flag_off():
    registry = ToolRegistry()
    # explicit empty env mapping → flag OFF (independent of process env)
    registered = register_select_recipe_tool(registry, env={})
    assert registered is False
    # Not registered: absent from the manifest, not enabled, not dispatchable.
    assert registry.resolve_registration(SELECT_RECIPE_TOOL_NAME) is None
    assert registry.is_enabled(SELECT_RECIPE_TOOL_NAME) is False
    assert SELECT_RECIPE_TOOL_NAME not in {m.name for m in registry.list_all()}


def test_register_select_recipe_tool_registers_and_enables_when_flag_on():
    registry = ToolRegistry()
    registered = register_select_recipe_tool(registry, env={_FLAG_ENV: "1"})
    assert registered is True
    # Registered, enabled, and advertised in the manifest + act-mode listing.
    assert registry.is_enabled(SELECT_RECIPE_TOOL_NAME) is True
    assert SELECT_RECIPE_TOOL_NAME in {m.name for m in registry.list_all()}
    assert SELECT_RECIPE_TOOL_NAME in {
        m.name for m in registry.list_available(mode="act")
    }


def test_register_select_recipe_tool_is_idempotent_when_flag_on():
    registry = ToolRegistry()
    assert register_select_recipe_tool(registry, env={_FLAG_ENV: "1"}) is True
    # Second call must not raise "already registered"; it is a no-op.
    assert register_select_recipe_tool(registry, env={_FLAG_ENV: "1"}) is False
    assert registry.is_enabled(SELECT_RECIPE_TOOL_NAME) is True


def test_registered_select_recipe_handler_reaches_handler_and_returns_ok():
    registry = ToolRegistry()
    register_select_recipe_tool(
        registry,
        pack_registry=_routing_registry(),
        env={_FLAG_ENV: "1"},
    )
    registration = registry.resolve_registration(SELECT_RECIPE_TOOL_NAME)
    assert registration is not None and registration.handler is not None
    adk = _StubAdkToolContext()
    result = registration.handler({"pack_id": "test.soft-full"}, _context(adk))
    assert result.status == "ok"
    assert result.metadata.get("toolName") == SELECT_RECIPE_TOOL_NAME
    # The handler's accumulator side effect fired through the dispatched path.
    assert adk.state[SELECTED_RECIPE_PACK_IDS_STATE_KEY] == ("test.soft-full",)


def test_select_recipe_manifest_is_meta_readonly_disabled_by_default():
    manifest = select_recipe_manifest()
    assert manifest.name == SELECT_RECIPE_TOOL_NAME
    assert manifest.permission == "meta"
    assert manifest.dangerous is False
    assert manifest.mutates_workspace is False
    assert manifest.parallel_safety == "readonly"
    # The live flag — not the manifest default — is the activation authority.
    assert manifest.enabled_by_default is False


# ---------------------------------------------------------------------------
# recipe_route_decided advisory decision event (Task 9)
# ---------------------------------------------------------------------------


def _emitting_context(adk: object | None, sink: list[object]) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        adkToolContext=adk,
        emitControlEvent=lambda event: sink.append(event),
    )


def test_project_recipe_route_decided_event_carries_pack_id_and_status():
    event = project_recipe_route_decided_event(
        pack_id="test.soft-full", status="ok", selected_count=2
    )
    assert event["type"] == "recipe_route_decided"
    assert event["packId"] == "test.soft-full"
    assert event["status"] == "ok"
    assert event["selectedCount"] == 2


def test_select_handler_emits_recipe_route_decided_on_ok():
    registry = _routing_registry()
    sink: list[object] = []
    adk = _StubAdkToolContext()
    result = select_recipe_handler(
        {"pack_id": "test.soft-full"},
        _emitting_context(adk, sink),
        registry=registry,
    )
    assert result.status == "ok"
    events = [e for e in sink if isinstance(e, dict) and e.get("type") == "recipe_route_decided"]
    assert len(events) == 1
    assert events[0]["packId"] == "test.soft-full"
    assert events[0]["status"] == "ok"
    # Running count of accumulated selections (after this selection).
    assert events[0]["selectedCount"] == 1


def test_select_handler_emits_recipe_route_decided_on_error():
    registry = _routing_registry()
    sink: list[object] = []
    result = select_recipe_handler(
        {"pack_id": "does.not.exist"},
        _emitting_context(_StubAdkToolContext(), sink),
        registry=registry,
    )
    assert result.status == "error"
    events = [e for e in sink if isinstance(e, dict) and e.get("type") == "recipe_route_decided"]
    assert len(events) == 1
    assert events[0]["status"] == "error"


def test_select_handler_does_not_raise_when_no_emitter_present():
    # Fail-safe: no emit_control_event in scope -> selection still returns ok,
    # no event emitted, no exception.
    registry = _routing_registry()
    result = select_recipe_handler(
        {"pack_id": "test.soft-full"},
        _context(_StubAdkToolContext()),
        registry=registry,
    )
    assert result.status == "ok"


def test_select_handler_emit_failure_does_not_break_selection():
    # An emitter that raises must never change routing/selection behavior.
    registry = _routing_registry()

    def _boom(_event: object) -> None:
        raise RuntimeError("emit must never break selection")

    ctx = ToolContext(
        botId="test-bot",
        adkToolContext=_StubAdkToolContext(),
        emitControlEvent=_boom,
    )
    result = select_recipe_handler(
        {"pack_id": "test.soft-full"}, ctx, registry=registry
    )
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# Half B — recipe tool-scope map (build_recipe_tool_scope).
#
# A pure helper computing the recipe-exclusive tool model the per-call
# enforcement (HB-3) consumes: scoped tools (the union of granted_tool_names
# across non-hard_safety packs), the owning-pack map, and an is_allowed
# predicate (empty selection → allow; base-free → allow; scoped → union of the
# selected packs' granted tools).
# ---------------------------------------------------------------------------


def test_tool_scope_maps_scoped_tools_to_owning_packs():
    registry = PackRegistry((
        _manifest("t.a", hard_safety=False, when_to_use="a", granted_tool_names=("Alpha", "Shared")),
        _manifest("t.b", hard_safety=False, when_to_use="b", granted_tool_names=("Beta", "Shared")),
    ))
    scope = build_recipe_tool_scope(registry)
    assert scope.owning_packs["Alpha"] == ("t.a",)
    assert set(scope.owning_packs["Shared"]) == {"t.a", "t.b"}
    assert scope.scoped_tools == frozenset({"Alpha", "Beta", "Shared"})


def test_allowed_unions_granted_plus_base_free_and_activation():
    registry = PackRegistry((
        _manifest("t.a", hard_safety=False, when_to_use="a", granted_tool_names=("Alpha",)),
        _manifest("t.b", hard_safety=False, when_to_use="b", granted_tool_names=("Beta",)),
    ))
    scope = build_recipe_tool_scope(registry)
    assert scope.is_allowed("GeneralRead", selected_pack_ids=("t.a",)) is True   # base-free
    assert scope.is_allowed("Alpha", selected_pack_ids=("t.a",)) is True
    assert scope.is_allowed("Beta", selected_pack_ids=("t.a",)) is False         # other recipe's exclusive
    assert scope.is_allowed("Beta", selected_pack_ids=()) is True                # inactive pre-selection
    assert scope.is_allowed("Beta", selected_pack_ids=("t.a", "t.b")) is True    # multi-select union


def test_hard_safety_pack_granted_tools_do_not_scope():
    registry = PackRegistry((
        _manifest("t.hard", hard_safety=True, when_to_use="", granted_tool_names=("HardTool",)),
        _manifest("t.a", hard_safety=False, when_to_use="a", granted_tool_names=("Alpha",)),
    ))
    scope = build_recipe_tool_scope(registry)
    assert "HardTool" not in scope.scoped_tools          # hard pack doesn't scope
    assert scope.is_allowed("HardTool", selected_pack_ids=("t.a",)) is True  # base-free → allowed


# ---------------------------------------------------------------------------
# HB-4 — authored granted_tool_names are REAL ADK tool names.
#
# Recipe-scoped enforcement (HB-1..HB-3) compares against the ACTUAL registered
# ADK tool names available in a CLI turn (``tool.name``). This test asserts the
# authored grants on the routable first-party packs are (a) non-empty and (b) a
# subset of the canonical CLI tool-name set, so selecting a recipe scopes the
# model to tools that genuinely exist. A grant naming a tool that is never
# registered would be silently un-grantable — this test catches that.
# ---------------------------------------------------------------------------


def canonical_cli_tool_names() -> frozenset[str]:
    """The real ADK tool names a CLI turn can call, read from the tool catalog.

    Source of truth: the same registration paths assembled by
    :func:`magi_agent.cli.tool_runtime.build_cli_tool_runtime` /
    ``build_cli_adk_tools`` — the core tool catalog, the optional file/multimodal
    manifests, the gated single-tool name constants (BrowserTask, PythonExec,
    PersistentPython, select_recipe), and the key-gated direct web tools
    (web_search / web_fetch / research_fact). Names are read from the catalog
    rather than hardcoded so a renamed/added tool keeps this set in sync.
    """
    from magi_agent.browser.autonomous.tool import BROWSER_TOOL_NAME
    from magi_agent.context.recipe_routing_constants import SELECT_RECIPE_TOOL_NAME
    from magi_agent.tools.catalog import core_tool_manifests
    from magi_agent.tools.file_tool_manifests import (
        document_qa_manifest,
        file_tool_manifests,
    )
    from magi_agent.tools.persistent_python_toolhost import (
        PERSISTENT_PYTHON_TOOL_NAME,
    )
    from magi_agent.tools.python_exec import PYTHON_EXEC_TOOL_NAME

    names: set[str] = set()
    names.update(m.name for m in core_tool_manifests())
    names.update(m.name for m in file_tool_manifests())
    names.add(document_qa_manifest().name)
    names.update((
        BROWSER_TOOL_NAME,
        PYTHON_EXEC_TOOL_NAME,
        PERSISTENT_PYTHON_TOOL_NAME,
        SELECT_RECIPE_TOOL_NAME,
        # Direct web tools (build_web_search_tools) — key-gated FunctionTools
        # whose ``tool.name`` derives from the function __name__.
        "web_search",
        "web_fetch",
        "research_fact",
    ))
    return frozenset(names)


def test_authored_granted_tools_are_real_for_key_packs():
    registry = PackRegistry.with_first_party_packs()
    real = canonical_cli_tool_names()
    for pid in (
        "openmagi.office-automation",
        "openmagi.research",
        "openmagi.dev-coding",
    ):
        g = registry.get(pid).granted_tool_names
        assert g, f"{pid} has no granted_tool_names"
        assert set(g) <= real, f"{pid} grants unknown tool names: {set(g) - real}"


def test_all_routable_pack_grants_are_real_or_empty():
    """Every non-hard_safety pack's grants (if any) must be real tool names."""
    registry = PackRegistry.with_first_party_packs()
    real = canonical_cli_tool_names()
    for pack in registry.values():
        if pack.hard_safety:
            # Hard-safety packs must NOT scope tools.
            assert pack.granted_tool_names == ()
            continue
        unknown = set(pack.granted_tool_names) - real
        assert not unknown, f"{pack.pack_id} grants unknown tool names: {unknown}"


# ---------------------------------------------------------------------------
# HB-4B — recipe obligation scope map (build_recipe_obligation_scope).
#
# A pure helper computing the completion-gate obligations each routable pack
# imposes: validators + evidence refs, unioned over selected packs, sorted and
# deduped.
# ---------------------------------------------------------------------------


def test_obligation_scope_unions_selected_pack_refs():
    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry
    from magi_agent.recipes.recipe_routing import build_recipe_obligation_scope

    scope = build_recipe_obligation_scope(build_runtime_pack_registry())
    validators, evidence = scope.obligations_for(["openmagi.research"])
    # research pack authors 3 validators + evidence:inspected-source
    assert any(v.startswith("validator:research") for v in validators)
    assert "evidence:inspected-source" in evidence


def test_obligation_scope_dev_coding_special_case():
    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry
    from magi_agent.recipes.recipe_routing import build_recipe_obligation_scope

    scope = build_recipe_obligation_scope(build_runtime_pack_registry())
    validators, _ = scope.obligations_for(["openmagi.dev-coding"])
    assert "verifier:dev-coding:test-evidence" in validators


def test_obligation_scope_empty_selection_is_empty():
    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry
    from magi_agent.recipes.recipe_routing import build_recipe_obligation_scope

    scope = build_recipe_obligation_scope(build_runtime_pack_registry())
    assert scope.obligations_for([]) == ((), ())


def test_obligation_scope_unknown_pack_ignored():
    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry
    from magi_agent.recipes.recipe_routing import build_recipe_obligation_scope

    scope = build_recipe_obligation_scope(build_runtime_pack_registry())
    assert scope.obligations_for(["does.not.exist"]) == ((), ())


def test_obligation_scope_two_packs_union_is_additive():
    """M4: obligations_for(research + dev-coding) == union of each pack's obligations.

    Locks the additive-union property Task 2 relies on: selecting BOTH packs must
    produce a result containing every validator from the research pack AND the
    dev-coding test-evidence validator, plus every evidence ref from both packs.
    No validator or evidence from either single-pack call may be dropped.
    """
    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry
    from magi_agent.recipes.recipe_routing import build_recipe_obligation_scope

    scope = build_recipe_obligation_scope(build_runtime_pack_registry())
    research_validators, research_evidence = scope.obligations_for(["openmagi.research"])
    coding_validators, coding_evidence = scope.obligations_for(["openmagi.dev-coding"])
    union_validators, union_evidence = scope.obligations_for(
        ["openmagi.research", "openmagi.dev-coding"]
    )

    # Every single-pack validator must appear in the two-pack union.
    for v in research_validators:
        assert v in union_validators, f"research validator {v!r} missing from union"
    for v in coding_validators:
        assert v in union_validators, f"dev-coding validator {v!r} missing from union"

    # Every single-pack evidence ref must appear in the two-pack union.
    for e in research_evidence:
        assert e in union_evidence, f"research evidence {e!r} missing from union"
    for e in coding_evidence:
        assert e in union_evidence, f"dev-coding evidence {e!r} missing from union"

    # The union must be strictly at least as large as either single-pack result
    # (it cannot shrink obligations).
    assert len(union_validators) >= max(len(research_validators), len(coding_validators))
    assert len(union_evidence) >= max(len(research_evidence), len(coding_evidence))
