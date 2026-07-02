"""N-30 lock: ``_STAGE_ORDER`` must mirror the ``HookPoint`` enum exactly.

``recipes/hook_composition._STAGE_ORDER`` assigns a deterministic sort rank to
every hook point so the composed contribution list is stable. When a new
``HookPoint`` member lands without a matching ``_STAGE_ORDER`` entry, the stage
silently falls through ``_stage_rank``'s rank-1000 fallback and sorts by name
only. This test pins the table to the enum so a future member cannot drift out
of the ordering unnoticed, with an explicit intentional-exclusion list (empty
today) as the single documented escape hatch.
"""

from __future__ import annotations

from magi_agent.hooks.manifest import HookPoint
from magi_agent.recipes.hook_composition import _STAGE_ORDER

#: Hook points deliberately left out of ``_STAGE_ORDER`` (none today). Any
#: addition here must carry an explicit rationale in review.
_INTENTIONAL_EXCLUSIONS: frozenset[str] = frozenset()


def test_stage_order_mirrors_hookpoint_exactly() -> None:
    assert set(_STAGE_ORDER) == {p.value for p in HookPoint} - _INTENTIONAL_EXCLUSIONS


def test_stage_order_ranks_are_unique() -> None:
    ranks = list(_STAGE_ORDER.values())
    assert len(ranks) == len(set(ranks)), (
        "Two hook points share a _STAGE_ORDER rank; the deterministic tiebreak "
        "would then depend on the stage name only."
    )


def test_new_lifecycle_stages_are_not_on_the_fallback_rank() -> None:
    lifecycle_stages = ("onSessionStart", "onTaskComplete", "onSessionEnd")
    for stage in lifecycle_stages:
        assert stage in _STAGE_ORDER
    assert all(_STAGE_ORDER[stage] < 1_000 for stage in lifecycle_stages)
