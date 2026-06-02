from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.runtime.context_budget import (
    ContextBudgetPlanner,
    ContextBudgetRequest,
)


def test_budget_planner_uses_refs_instead_of_broad_context_for_cheap_model() -> None:
    planner = ContextBudgetPlanner.with_defaults()

    plan = planner.plan(
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="cheap",
            phase="final_answer_drafting",
            sourceRefs=tuple(f"source:web:{i}" for i in range(20)),
            summaryRefs=("summary:research:1",),
            memoryRefs=("memory-ref:alpha",),
            evidenceRefs=("evidence:web:src_1",),
            rawInputBytes=120_000,
        )
    )

    assert plan.strategy == "refs_only_with_chunk_summaries"
    assert plan.raw_context_included is False
    assert len(plan.included_refs) <= plan.max_refs
    assert "raw_context_too_large" in plan.reason_codes


def test_standard_and_sota_allow_more_refs_but_still_track_refs_not_raw() -> None:
    planner = ContextBudgetPlanner.with_defaults()

    standard = planner.plan(
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="standard",
            phase="final_answer_drafting",
            sourceRefs=tuple(f"source:web:{i}" for i in range(20)),
            summaryRefs=("summary:research:1",),
        )
    )
    sota = planner.plan(
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="sota",
            phase="final_answer_drafting",
            sourceRefs=tuple(f"source:web:{i}" for i in range(20)),
            summaryRefs=("summary:research:1",),
        )
    )

    assert standard.max_refs > 6
    assert sota.max_refs > standard.max_refs
    assert standard.raw_context_included is False
    assert sota.raw_context_included is False
    assert "refs_recorded" in standard.reason_codes
    assert "refs_recorded" in sota.reason_codes


def test_compaction_boundary_refs_are_prioritized_over_old_raw_context() -> None:
    plan = ContextBudgetPlanner.with_defaults().plan(
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="cheap",
            phase="final_answer_drafting",
            sourceRefs=("source:web:new",),
            summaryRefs=("summary:compact:boundary-1", "summary:research:new"),
            compactionBoundaryRefs=("summary:compact:boundary-1",),
            rawInputBytes=90_000,
        )
    )

    assert plan.ref_groups["compaction"] == ["summary:compact:boundary-1"]
    assert plan.raw_context_included is False
    assert "compaction_boundary_ref_used" in plan.reason_codes


def test_incognito_mode_excludes_memory_refs() -> None:
    plan = ContextBudgetPlanner.with_defaults().plan(
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="cheap",
            phase="final_answer_drafting",
            sourceRefs=("source:web:src_1",),
            memoryRefs=("memory-ref:private",),
            memoryMode="incognito",
        )
    )

    assert "memory-ref:private" not in plan.included_refs
    assert plan.ref_groups["memory"] == []
    assert "memory_refs_excluded_incognito" in plan.reason_codes


def test_ref_groups_track_source_summary_memory_and_evidence_separately() -> None:
    plan = ContextBudgetPlanner.with_defaults().plan(
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="standard",
            phase="source_extraction",
            sourceRefs=("source:web:1",),
            summaryRefs=("summary:web:1",),
            memoryRefs=("memory-ref:1",),
            evidenceRefs=("evidence:web:1",),
            toolResultRefs=("tool-result:1",),
            artifactRefs=("artifact:1",),
            controlRefs=("control:1",),
        )
    )

    assert plan.ref_groups == {
        "source": ["source:web:1"],
        "summary": ["summary:web:1"],
        "memory": ["memory-ref:1"],
        "evidence": ["evidence:web:1"],
        "tool_result": ["tool-result:1"],
        "artifact": ["artifact:1"],
        "control": ["control:1"],
        "compaction": [],
    }


@pytest.mark.parametrize(
    "bad_ref",
    (
        "/Users/kevin/private/source",
        "source:web:github_pat_unsafeToken12345",
        "source web with spaces",
        "https://example.com/raw",
    ),
)
def test_invalid_refs_are_rejected_or_redacted(bad_ref: str) -> None:
    with pytest.raises(ValidationError):
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="cheap",
            phase="final_answer_drafting",
            sourceRefs=(bad_ref,),
        )


def test_raw_text_inclusion_requires_explicit_local_policy_and_digest() -> None:
    plan = ContextBudgetPlanner.with_defaults().plan(
        ContextBudgetRequest(
            recipeIds=("openmagi.research",),
            modelTier="standard",
            phase="source_extraction",
            sourceRefs=("source:web:1",),
            rawInputBytes=256,
            allowRawContextForLocalTest=True,
            rawContextDigest="sha256:" + "a" * 64,
        )
    )

    projection = plan.public_projection()
    assert plan.raw_context_included is True
    assert projection["rawContextDigest"] == "sha256:" + "a" * 64
    assert "rawText" not in projection
