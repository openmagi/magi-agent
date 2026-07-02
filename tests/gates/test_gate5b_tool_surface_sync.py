"""Sync guard: the gate5b first-party serve tuple must not drift from the
live tool registry (N-06). The 5 dead names (ArtifactCreate/List/Read,
HealthStatus, ToolSearch) were advertised to the model and always failed
with ValueError("unsupported_tool"); this test makes that class of drift
impossible to reintroduce."""

from __future__ import annotations

from magi_agent.gates.gate5b_full_toolhost import (
    _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES,
)
from magi_agent.runtime.openmagi_runtime import (
    _build_core_tool_registry,
    _build_default_plugin_state,
)


def test_first_party_serve_tuple_is_subset_of_live_registry() -> None:
    # The exact registry construction the serve path uses
    # (openmagi_runtime.py:187), WITHOUT OpenMagiRuntime's customize.json
    # override pass, so the test is hermetic w.r.t. ~/.magi state.
    # Registry MEMBERSHIP (not handler presence) is the right strength:
    # AskUserQuestion/EnterPlanMode/ExitPlanMode are registered handler-less
    # by design (routed/gated elsewhere; dispatcher returns a structured
    # tool_handler_missing error, dispatcher.py:266).
    registry = _build_core_tool_registry(_build_default_plugin_state())
    dead = [
        name
        for name in _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES
        if registry.resolve(name) is None
    ]
    assert not dead, (
        "gate5b advertises tool names absent from the live registry "
        f"(calls always fail with unsupported_tool): {dead}"
    )


def test_known_live_names_stay_exposed() -> None:
    # Over-prune guard: representative live names must remain advertised.
    expected = {
        "WebSearch",
        "SpawnAgent",
        "DocumentWrite",
        "KnowledgeSearch",
        "ArtifactUpdate",
        "ArtifactDelete",
    }
    missing = expected - set(_GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES)
    assert not missing, f"live serve tools were over-pruned: {sorted(missing)}"


def test_removed_dead_names_stay_out() -> None:
    # Mirrors tools/tests/test_catalog_honest_manifests.py _REMOVED_TOOL_NAMES
    # on the gate5b serve surface (the honesty guard's blind spot, N-06).
    removed = {
        "ArtifactCreate",
        "ArtifactList",
        "ArtifactRead",
        "HealthStatus",
        "ToolSearch",
    }
    leaked = removed & set(_GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES)
    assert not leaked, f"dead tool names re-advertised on gate5b: {sorted(leaked)}"
