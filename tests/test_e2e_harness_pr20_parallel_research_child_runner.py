from __future__ import annotations

import asyncio
import json

from magi_agent.recipes.research_child_runner import (
    ResearchChildRunnerConfig,
    ResearchChildRunnerRecipe,
    ResearchChildTaskSpec,
    ResearchSynthesisRequest,
)


class HarnessResearchFakeRunner:
    openmagi_local_fake_provider = True

    async def run_child(self, request: object) -> dict[str, object]:
        task_id = getattr(request, "task_id")
        return {
            "childExecutionId": f"child-{task_id}",
            "status": "completed",
            "summary": (
                "Local fake research child completed source inspection.\n"
                "child natural citation: https://child.example/not-parent-ledger\n"
                "hidden_reasoning: do not project"
            ),
            "evidenceRefs": (f"evidence:{task_id}-source-1",),
            "artifactRefs": (f"artifact:{task_id}-notes",),
            "auditEventRefs": (f"audit:{task_id}-planned",),
            "rawTranscript": "raw child transcript should stay private",
        }


def test_pr20_parallel_research_child_runner_is_activation_blocked_local_fake_only() -> None:
    recipe = ResearchChildRunnerRecipe(
        ResearchChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=HarnessResearchFakeRunner(),
    )
    result = asyncio.run(
        recipe.run(
            ResearchSynthesisRequest(
                parentExecutionId="parent-pr20-1",
                turnId="turn-pr20-1",
                synthesisId="synthesis-pr20-1",
                objective="Build a parent synthesis input from local fake research children.",
                parentSourceRefs=("source:ledger:pr20-parent-1",),
                parentClaimRefs=("claim:research:pr20-parent-1",),
                tasks=(
                    ResearchChildTaskSpec(
                        taskId="pr20-child-1",
                        childRole="explore",
                        objective="Inspect local source ledger refs.",
                        sourceRefs=("source:ledger:pr20-parent-1",),
                        claimRefs=("claim:research:pr20-child-1",),
                    ),
                    ResearchChildTaskSpec(
                        taskId="pr20-child-2",
                        childRole="verifier",
                        objective="Verify source-sensitive claims.",
                        sourceRefs=("source:ledger:pr20-parent-1",),
                        claimRefs=("claim:research:pr20-child-2",),
                    ),
                ),
            )
        )
    )
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)
    child_inputs = projection["parentSynthesisInput"]["childInputs"]

    assert result.status == "accepted"
    assert [child["taskId"] for child in child_inputs] == ["pr20-child-1", "pr20-child-2"]
    assert child_inputs[0]["childRole"] == "explore"
    assert child_inputs[1]["childRole"] == "verifier"
    assert child_inputs[0]["sourceRefs"] == ["source:ledger:pr20-parent-1"]
    assert child_inputs[0]["claimRefs"] == ["claim:research:pr20-child-1"]
    assert child_inputs[0]["childStatus"] == "completed"
    assert child_inputs[0]["unsupportedClaimCount"] == 0
    assert child_inputs[0]["childRef"].startswith("child:")
    assert child_inputs[0]["evidenceRefs"][0].startswith("evidence:")
    assert projection["authorityFlags"]["liveChildRunnerEnabled"] is False
    assert projection["authorityFlags"]["liveToolExecutionEnabled"] is False
    assert projection["authorityFlags"]["productionAuthority"] is False
    assert projection["authorityFlags"]["userVisibleActivation"] is False
    assert "https://child.example/not-parent-ledger" not in rendered
    assert "raw child transcript" not in rendered
    assert "hidden_reasoning" not in rendered
