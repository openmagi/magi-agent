"""Honesty guard for the core tool catalog (doc 12 PR5 / B14).

The catalog must not advertise manifests that have no execution handler bound
anywhere in the runtime. ToolSearch, ArtifactCreate/Read/List and HealthStatus
were manifest-only declarations: the CLI registry silently filtered them out
(``registration.handler is not None``) so the model could never call them, yet
``docs/tools.md`` listed them as if they worked.

These tests pin the removal so the "advertise non-existent capability" drift
cannot recur, while asserting the genuinely routed tools stay advertised.
"""

from __future__ import annotations

from magi_agent.tools.catalog import core_tool_manifests


# Low-value, handler-less manifests removed in doc 12 PR5.
_REMOVED_TOOL_NAMES = frozenset(
    {
        "ToolSearch",
        "ArtifactCreate",
        "ArtifactRead",
        "ArtifactList",
        "HealthStatus",
    }
)

# Tools that remain in the catalog and either have handlers today or are
# explicitly routed/gated elsewhere. They must NOT be dropped by this PR.
_RETAINED_TOOL_NAMES = frozenset(
    {
        "FileRead",
        "FileWrite",
        "FileEdit",
        "PatchApply",
        "Glob",
        "Grep",
        "Bash",
        "TestRun",
        "GitDiff",
        "TodoWrite",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "Clock",
        "Calculation",
        "TaskList",
        "TaskGet",
        "TaskOutput",
        "CronList",
        "MemoryWrite",
        "InspectSelfEvidence",
    }
)


def _catalog_names() -> frozenset[str]:
    return frozenset(manifest.name for manifest in core_tool_manifests())


def test_removed_manifests_are_not_advertised() -> None:
    advertised = _catalog_names()
    for name in _REMOVED_TOOL_NAMES:
        assert name not in advertised, f"{name} should no longer be advertised by the catalog"


def test_retained_manifests_still_advertised() -> None:
    advertised = _catalog_names()
    missing = _RETAINED_TOOL_NAMES - advertised
    assert not missing, f"routed tools were dropped: {sorted(missing)}"


def test_catalog_count_matches_retained_set() -> None:
    # After removing the 5 handler-less manifests the catalog holds exactly the
    # retained set — no orphan declarations, no over-removal.
    assert _catalog_names() == _RETAINED_TOOL_NAMES
