"""First-party memory strategy providers (no privilege, typed-ctx only)."""
from __future__ import annotations

from magi_agent.packs.context import MemoryStrategyProvideContext


def provide_compaction_denial(context: MemoryStrategyProvideContext) -> None:
    from magi_agent.harness.memory_compaction import _compaction_denial_reasons

    context.register("memory_strategy:compaction-denial@1", _compaction_denial_reasons)


def provide_recall_projection(context: MemoryStrategyProvideContext) -> None:
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallProjectionPolicy,
    )

    # ``latest_user_text`` is a required per-request field, so the strategy is
    # the CLASS itself: an addressable factory recall callers construct per
    # request (a user pack overrides this ref with its own factory).
    context.register("memory_strategy:recall-projection@1", MemoryRecallProjectionPolicy)


def provide_review_trigger(context: MemoryStrategyProvideContext) -> None:
    from magi_agent.harness.memory_review import should_run_review

    context.register("memory_strategy:review-trigger@1", should_run_review)
