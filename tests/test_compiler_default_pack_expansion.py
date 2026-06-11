"""Tests for ``MAGI_RECIPE_DEFAULT_PACKS_EXPANDED`` (doc 05 PR-2).

The expansion promotes a *safe* subset of first-party packs to be selected by
default (without an explicit task-profile selector) **only when the stage gate
is ON**.  When the gate is OFF the compiled snapshot must be byte-identical to
``origin/main`` — i.e. only the two ``hardSafety`` packs are default-selected.

The "safe" criterion (doc 05 §6 open-decision (1)) is:

    A pack may be auto-promoted to ``defaultEnabled`` iff it
      (a) is NOT a ``hardSafety`` pack (those are already default), AND
      (b) requires only read-only / idempotent tools (no mutating tool refs), AND
      (c) carries zero production-authority approval gates (every approval-gate
          metadata entry is ``metadata-only``), AND
      (d) declares no live dependency (no ``live_tool_refs`` /
          ``live_callback_refs`` / ``runner_route_refs`` and no provider-opt-in
          approval gate).

Nothing with side-effects/authority (coding/web-live/channel/scheduler/office/
artifact/spreadsheet/browser/memory-write) may be auto-enabled.
"""

from __future__ import annotations

from collections.abc import Mapping

from magi_agent.config.env import parse_recipe_default_packs_expanded
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
    ProfileResolver,
    RecipePackManifest,
    SAFE_DEFAULT_PACK_EXPANSION_IDS,
)

# ``hardSafety`` packs that are already ``defaultEnabled`` on origin/main.
_BASELINE_DEFAULT_PACK_IDS = (
    "openmagi.context-safety",
    "openmagi.evidence",
)


def _minimal_request() -> ProfileResolutionRequest:
    """A request with no task-profile / explicit selection.

    Only ``defaultEnabled`` (and ``hardSafety``) packs may be selected here, so
    it isolates the default-pack-expansion behaviour.
    """

    return ProfileResolutionRequest(
        userProfile={},
        workspacePolicy={},
        taskProfile={},
        recipePackConfig={},
        runtimeContext={"channel": "fixture", "currentDate": "2026-06-09"},
    )


# --------------------------------------------------------------------------- #
# Gate parser (default-OFF)                                                    #
# --------------------------------------------------------------------------- #


def test_default_packs_expanded_flag_is_default_off() -> None:
    assert parse_recipe_default_packs_expanded({}) is False


def test_default_packs_expanded_flag_honours_truthy_values() -> None:
    for value in ("1", "true", "yes", "on", "TRUE", " On "):
        assert parse_recipe_default_packs_expanded({"MAGI_RECIPE_DEFAULT_PACKS_EXPANDED": value}) is True
    for value in ("0", "false", "no", "off", "", "garbage"):
        assert parse_recipe_default_packs_expanded({"MAGI_RECIPE_DEFAULT_PACKS_EXPANDED": value}) is False


# --------------------------------------------------------------------------- #
# The expansion set must match the documented safe criteria                   #
# --------------------------------------------------------------------------- #


# Approval-gate refs that name a real side-effect / external / live / execution
# / lifecycle-control authority. ``metadata-only`` callback/validator/approval
# *metadata* markers are inert across the whole catalog and do not discriminate;
# the semantic approval-gate-ref name does.
#
# NB: methodology *guard* gates (e.g. ``plan-execution`` /
# ``git-worktree-isolation`` / ``live-behavior``) are deny-by-default approval
# guards a methodology pack offers, not authority it wields, so they are NOT
# treated as disqualifying. The disqualifying markers are concrete
# side-effect / external-provider / mutation / lifecycle-control authorities;
# packs whose *dependencies* wield such authority are caught by the transitive
# dependency check below (e.g. autopilot -> dev-coding tool refs).
_AUTHORITY_APPROVAL_MARKERS = (
    "mutation",
    "write-or-send",
    "external-send",
    "external-action",
    "external-source-use",
    "provider-opt-in",
    "channel-send",
    "cancel-retry-resume",
    "resume-or-notify",
    "tracked-change",
)


def _pack_self_is_safe_for_default(pack: RecipePackManifest) -> bool:
    if pack.hard_safety:
        return False  # (a) already default; not part of the expansion set

    # (b) read-only / idempotent only — a pure methodology metadata pack
    # declares no tool refs at all. Any pack that lists tools (even read tools
    # like ``file.read``) bundles them with mutating siblings / authority
    # gates, so we require the strictest no-tool form for default promotion.
    if pack.tool_refs:
        return False

    # (d) no live dependency surfaces.
    if pack.live_tool_refs or pack.live_callback_refs or pack.runner_route_refs:
        return False

    # (c)+(d) no production-authority / external / live / lifecycle-control
    # approval gate.
    for ref in pack.approval_gate_refs:
        lowered = ref.lower()
        if any(marker in lowered for marker in _AUTHORITY_APPROVAL_MARKERS):
            return False

    # Distinguish *methodology metadata* packs (which contribute runner-policy
    # validators/approval-guards and are useful as an always-on baseline) from
    # bare profile-scoped *instruction-injection* packs (e.g. learning-usage /
    # discovery) whose static prompt text is intentionally opt-in via a task
    # selector. Only the former are promoted to default.
    if not (pack.validator_refs or pack.approval_gate_refs):
        return False

    return True


def _pack_is_safe_for_default(
    pack: RecipePackManifest,
    registry: PackRegistry,
) -> bool:
    """Mirror of doc 05 §6 open-decision (1) safe criteria, computed purely
    from manifest fields so the test is independent of the implementation's
    hard-coded id list. A pack is safe only if it AND every pack it transitively
    depends on are self-safe (a dependency that wields authority would be pulled
    in by promoting the dependent)."""

    seen: set[str] = set()

    def _check(pack_id: str) -> bool:
        if pack_id in seen:
            return True
        seen.add(pack_id)
        candidate = registry.get(pack_id)
        if not _pack_self_is_safe_for_default(candidate):
            return False
        return all(_check(dep) for dep in candidate.depends_on_pack_ids)

    return _check(pack.pack_id)


def test_expansion_set_matches_safe_criteria_exactly() -> None:
    registry = PackRegistry.with_first_party_packs()
    expected_safe = {
        pack.pack_id
        for pack in registry.values()
        if _pack_is_safe_for_default(pack, registry)
    }
    assert set(SAFE_DEFAULT_PACK_EXPANSION_IDS) == expected_safe
    # Sanity: at least one pack is promoted and none is a baseline/hardSafety pack.
    assert SAFE_DEFAULT_PACK_EXPANSION_IDS
    assert not (set(SAFE_DEFAULT_PACK_EXPANSION_IDS) & set(_BASELINE_DEFAULT_PACK_IDS))


def test_no_side_effect_or_authority_pack_is_promoted() -> None:
    registry = PackRegistry.with_first_party_packs()
    forbidden = {
        "openmagi.dev-coding",
        "openmagi.lightweight-scripting",
        "openmagi.office-automation",
        "openmagi.spreadsheet-automation",
        "openmagi.browser-automation",
        "openmagi.artifact-delivery",
        "openmagi.channel-delivery",
        "openmagi.scheduled-work",
        "openmagi.missions",
        "openmagi.memory-agentmemory",
        "openmagi.web-acquisition",
        "openmagi.research",
        "openmagi.autopilot",
    }
    # Every forbidden id is a real registered pack and is NOT promoted.
    for pack_id in forbidden:
        assert pack_id in registry.pack_ids
        assert pack_id not in SAFE_DEFAULT_PACK_EXPANSION_IDS


# --------------------------------------------------------------------------- #
# Gate OFF => byte-identical snapshot (regression guard)                       #
# --------------------------------------------------------------------------- #


def _compile_minimal(env: Mapping[str, str]) -> tuple[str, ...]:
    registry = PackRegistry.with_first_party_packs()
    compiler = AgentRecipeCompiler(registry)
    snapshot = compiler.compile(_minimal_request(), env=env)
    return snapshot.selected_pack_ids


def test_gate_off_keeps_baseline_default_selection(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_RECIPE_DEFAULT_PACKS_EXPANDED", raising=False)
    assert _compile_minimal({}) == _BASELINE_DEFAULT_PACK_IDS


def test_gate_off_snapshot_id_is_unchanged() -> None:
    registry = PackRegistry.with_first_party_packs()
    compiler = AgentRecipeCompiler(registry)
    off_snapshot = compiler.compile(_minimal_request(), env={})
    assert off_snapshot.selected_pack_ids == _BASELINE_DEFAULT_PACK_IDS
    # snapshot id is derived from selected_pack_ids; recompute to confirm parity.
    from magi_agent.recipes.compiler import build_recipe_snapshot_id

    assert off_snapshot.snapshot_id == build_recipe_snapshot_id(_BASELINE_DEFAULT_PACK_IDS)


# --------------------------------------------------------------------------- #
# Gate ON => promoted packs become default-selected                           #
# --------------------------------------------------------------------------- #


def test_gate_on_adds_safe_packs_to_default_selection() -> None:
    selected = _compile_minimal({"MAGI_RECIPE_DEFAULT_PACKS_EXPANDED": "1"})
    # Baseline hardSafety packs still present.
    for pack_id in _BASELINE_DEFAULT_PACK_IDS:
        assert pack_id in selected
    # Every safe-expansion pack is now selected even with no task selector.
    for pack_id in SAFE_DEFAULT_PACK_EXPANSION_IDS:
        assert pack_id in selected
    # No forbidden side-effect pack leaked in.
    assert "openmagi.dev-coding" not in selected
    assert "openmagi.channel-delivery" not in selected
    assert "openmagi.scheduled-work" not in selected


def test_gate_on_is_superset_of_gate_off() -> None:
    off = set(_compile_minimal({}))
    on = set(_compile_minimal({"MAGI_RECIPE_DEFAULT_PACKS_EXPANDED": "1"}))
    assert off <= on
    assert on - off == set(SAFE_DEFAULT_PACK_EXPANSION_IDS)


def test_resolver_default_expansion_respects_opt_out(monkeypatch) -> None:
    """A promoted pack must still honour explicit disable (opt-out)."""

    registry = PackRegistry.with_first_party_packs()
    resolver = ProfileResolver(registry)
    promoted = SAFE_DEFAULT_PACK_EXPANSION_IDS[0]
    request = ProfileResolutionRequest(
        userProfile={},
        workspacePolicy={},
        taskProfile={},
        recipePackConfig={"packs": {"disable": [promoted]}},
        runtimeContext={"channel": "fixture", "currentDate": "2026-06-09"},
    )
    resolved = resolver.resolve(
        request, env={"MAGI_RECIPE_DEFAULT_PACKS_EXPANDED": "1"}
    )
    # opt-out only honoured if the pack allows it; agent-methodology does.
    pack = registry.get(promoted)
    if pack.opt_out_allowed:
        assert promoted not in resolved.selected_pack_ids
