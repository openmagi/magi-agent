from __future__ import annotations

import asyncio
import json

from openmagi_core_agent.recipes.coding_subagents import (
    CodingSubagentConfig,
    CodingSubagentModeRequest,
    CodingSubagentRecipe,
)


class HarnessCodingFakeRunner:
    openmagi_local_fake_provider = True

    async def run_child(self, request: object) -> dict[str, object]:
        task_id = getattr(request, "task_id")
        return {
            "childExecutionId": f"child-{task_id}",
            "status": "completed",
            "summary": "Local fake coding child completed metadata-only work.",
            "evidenceRefs": ("evidence:pr19-review-1",),
            "artifactRefs": ("artifact:pr19-review-report",),
            "auditEventRefs": ("audit:pr19-review-planned",),
            "rawTranscript": "raw child transcript should stay private",
        }


def _request(mode: str) -> CodingSubagentModeRequest:
    return CodingSubagentModeRequest(
        mode=mode,
        parentExecutionId="parent-pr19-1",
        turnId="turn-pr19-1",
        taskId=f"pr19-{mode}-1",
        objective="Run the PR19 local fake coding subagent harness contract.",
        sessionId="session-pr19-1",
        workspaceRef="workspace:pr19",
    )


def test_pr19_coding_subagent_recipe_is_activation_blocked_local_fake_only() -> None:
    recipe = CodingSubagentRecipe(
        CodingSubagentConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=HarnessCodingFakeRunner(),
    )

    inspect = asyncio.run(recipe.run(_request("inspect")))
    review = asyncio.run(recipe.run(_request("code_review")))
    blocked_research = asyncio.run(recipe.run(_request("research")))

    inspect_projection = inspect.public_projection()
    review_projection = review.public_projection()
    rendered = json.dumps(
        {
            "inspect": inspect_projection,
            "review": review_projection,
            "research": blocked_research.public_projection(),
        },
        sort_keys=True,
    )

    assert inspect.status == "accepted"
    assert inspect_projection["toolScope"]["mutationIntentAllowed"] is False
    assert review.status == "accepted"
    assert review_projection["findings"][0]["evidenceRefs"]
    assert blocked_research.status == "blocked"
    assert blocked_research.reason_codes == ("research_mode_unavailable_for_coding_recipe",)
    assert inspect_projection["authorityFlags"]["liveChildRunnerEnabled"] is False
    assert review_projection["authorityFlags"]["workspaceMutationEnabled"] is False
    assert review_projection["child"]["authorityFlags"]["realChildRunnerExecuted"] is False
    assert "raw child transcript" not in rendered
