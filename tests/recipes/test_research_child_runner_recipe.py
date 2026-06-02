from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from openmagi_core_agent.recipes.research_child_runner import (
    ResearchChildRunnerConfig,
    ResearchChildRunnerRecipe,
    ResearchChildTaskSpec,
    ResearchSynthesisRequest,
)


WRITE_OR_LIVE_TOOLS = (
    "FileWrite",
    "PatchApply",
    "Bash",
    "BrowserClick",
    "BrowserNavigate",
    "MemoryWrite",
    "ChannelSend",
    "WebSearch",
    "WebFetch",
)


class LocalResearchFakeRunner:
    openmagi_local_fake_provider = True

    def __init__(self, *, fail_task_id: str | None = None, natural_language_only: bool = False) -> None:
        self.fail_task_id = fail_task_id
        self.natural_language_only = natural_language_only
        self.calls = 0
        self.seen_allowed_tools: dict[str, tuple[str, ...]] = {}
        self.seen_spawn_depths: dict[str, int] = {}

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        task_id = getattr(request, "task_id")
        metadata = getattr(request, "metadata")
        allowed_tools = metadata.get("allowedTools")
        spawn_depth = metadata.get("spawnDepth")
        self.seen_allowed_tools[task_id] = tuple(allowed_tools) if isinstance(allowed_tools, tuple) else ()
        self.seen_spawn_depths[task_id] = int(spawn_depth) if isinstance(spawn_depth, int) else -1
        if task_id == self.fail_task_id:
            raise RuntimeError(
                "raw_child_transcript: /workspace/private/transcript.json "
                "PRIVATE_PAYLOAD_DO_NOT_PROJECT"
            )
        output: dict[str, object] = {
            "childExecutionId": f"child-{task_id}",
            "status": "completed",
            "summary": (
                "Public child summary with source observations.\n"
                "Natural language citation: https://child.example/private-source\n"
                "raw_child_transcript: /Users/kevin/private/raw.json"
            ),
            "auditEventRefs": (f"audit:{task_id}-planned",),
            "rawTranscript": "private transcript",
            "toolLogs": "private tool logs",
            "hiddenReasoning": "private reasoning",
        }
        if not self.natural_language_only:
            output.update(
                {
                    "evidenceRefs": (f"evidence:{task_id}-source-inspection",),
                    "artifactRefs": (f"artifact:{task_id}-source-notes",),
                }
            )
        return output


def _enabled_recipe(fake: LocalResearchFakeRunner | None = None) -> ResearchChildRunnerRecipe:
    return ResearchChildRunnerRecipe(
        ResearchChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            additionalAllowedTools=WRITE_OR_LIVE_TOOLS,
        ),
        child_runner=fake or LocalResearchFakeRunner(),
    )


def _request(
    *,
    tasks: tuple[ResearchChildTaskSpec, ...] | None = None,
) -> ResearchSynthesisRequest:
    return ResearchSynthesisRequest(
        parentExecutionId="parent-research-1",
        turnId="turn-research-1",
        synthesisId="synthesis-research-1",
        objective="Synthesize child source inspection evidence without raw child context.",
        parentSourceRefs=("source:ledger:parent-src-1", "source:ledger:parent-src-2"),
        parentClaimRefs=("claim:research:parent-1",),
        tasks=tasks
        or (
            ResearchChildTaskSpec(
                taskId="child-market-1",
                childRole="explore",
                objective="Inspect parent-ledger sources for market claims.",
                sourceRefs=("source:ledger:parent-src-1",),
                claimRefs=("claim:research:market-1", "claim:research:market-2"),
                unsupportedClaimCount=1,
            ),
            ResearchChildTaskSpec(
                taskId="child-policy-1",
                childRole="verifier",
                objective="Verify policy claims against parent-ledger sources.",
                sourceRefs=("source:ledger:parent-src-2",),
                claimRefs=("claim:research:policy-1",),
                unsupportedClaimCount=0,
            ),
        ),
    )


def test_parallel_research_children_build_parent_synthesis_inputs_from_runtime_refs() -> None:
    fake = LocalResearchFakeRunner()
    result = asyncio.run(_enabled_recipe(fake).run(_request()))
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert result.status == "accepted"
    assert fake.calls == 2
    assert set(fake.seen_allowed_tools) == {"child-market-1", "child-policy-1"}
    for allowed_tools in fake.seen_allowed_tools.values():
        assert allowed_tools
        assert not set(allowed_tools).intersection(WRITE_OR_LIVE_TOOLS)
    assert fake.seen_spawn_depths == {"child-market-1": 1, "child-policy-1": 1}

    synthesis_inputs = projection["parentSynthesisInput"]["childInputs"]
    assert len(synthesis_inputs) == 2
    first = synthesis_inputs[0]
    assert first["taskId"] == "child-market-1"
    assert first["childRole"] == "explore"
    assert first["sourceRefs"] == ["source:ledger:parent-src-1"]
    assert first["claimRefs"] == ["claim:research:market-1", "claim:research:market-2"]
    assert first["unsupportedClaimCount"] == 1
    assert first["childStatus"] == "completed"
    assert first["publicChildSummary"] == "Public child summary with source observations."
    assert first["evidenceRefs"][0].startswith("evidence:")
    assert first["childRef"].startswith("child:")
    assert first["auditEventRefs"][0].startswith("audit:")
    assert first["childRef"] in projection["parentSynthesisInput"]["parentEvidenceRefs"]
    assert first["evidenceRefs"][0] in projection["parentSynthesisInput"]["parentEvidenceRefs"]

    assert projection["parentSynthesisInput"]["parentSourceRefs"] == [
        "source:ledger:parent-src-1",
        "source:ledger:parent-src-2",
    ]
    assert "https://child.example/private-source" not in rendered
    assert "raw_child_transcript" not in rendered
    assert "private transcript" not in rendered
    assert "private tool logs" not in rendered
    assert "private reasoning" not in rendered
    assert "PRIVATE_PAYLOAD_DO_NOT_PROJECT" not in rendered
    assert "/Users/kevin" not in rendered
    assert "/workspace" not in rendered


def test_natural_language_only_child_output_does_not_satisfy_evidence_contract() -> None:
    result = asyncio.run(
        _enabled_recipe(LocalResearchFakeRunner(natural_language_only=True)).run(_request())
    )
    projection = result.public_projection()

    assert result.status == "blocked"
    assert result.reason_codes == ("child_evidence_refs_required",)
    assert projection["parentSynthesisInput"]["childInputs"][0]["evidenceRefs"] == []
    assert projection["parentSynthesisInput"]["childInputs"][0]["childStatus"] == "blocked"


def test_child_failure_becomes_sanitized_synthesis_status_not_raw_error() -> None:
    result = asyncio.run(
        _enabled_recipe(LocalResearchFakeRunner(fail_task_id="child-policy-1")).run(_request())
    )
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)
    failed = projection["parentSynthesisInput"]["childInputs"][1]

    assert result.status == "partial"
    assert result.reason_codes == ("local_fake_child_runner_error",)
    assert failed["taskId"] == "child-policy-1"
    assert failed["childStatus"] == "error"
    assert failed["errorCode"] == "local_fake_child_runner_error"
    assert failed["publicChildSummary"] == ""
    assert "raw_child_transcript" not in rendered
    assert "PRIVATE_PAYLOAD_DO_NOT_PROJECT" not in rendered
    assert "/workspace" not in rendered


def test_child_summary_drops_raw_url_schemes_and_citation_text() -> None:
    class UrlSummaryFakeRunner(LocalResearchFakeRunner):
        async def run_child(self, request: object) -> dict[str, object]:
            output = await super().run_child(request)
            output["summary"] = (
                "Safe source-backed observation.\n"
                "s3://private-bucket/raw-source\n"
                "file://tmp/report.txt\n"
                "ftp://private.example/file\n"
                "x://private-resource/raw-source\n"
                "private.example/raw-citation\n"
                "Child citation: see source text"
            )
            return output

    result = asyncio.run(
        _enabled_recipe(UrlSummaryFakeRunner()).run(
            _request(tasks=(_request().tasks[0],)),
        )
    )
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert result.status == "accepted"
    child = projection["parentSynthesisInput"]["childInputs"][0]
    assert child["publicChildSummary"] == "Safe source-backed observation."
    assert "s3://" not in rendered
    assert "file://" not in rendered
    assert "ftp://" not in rendered
    assert "x://" not in rendered
    assert "private.example" not in rendered
    assert "citation" not in rendered.casefold()


def test_disabled_defaults_and_spawn_depth_block_without_child_call() -> None:
    fake = LocalResearchFakeRunner()
    disabled = asyncio.run(ResearchChildRunnerRecipe(child_runner=fake).run(_request()))
    too_deep = asyncio.run(
        _enabled_recipe(fake).run(
            _request(
                tasks=(
                    ResearchChildTaskSpec(
                        taskId="child-too-deep-1",
                        childRole="explore",
                        objective="Attempt an unsupported nested research child.",
                        sourceRefs=("source:ledger:parent-src-1",),
                        claimRefs=("claim:research:nested-1",),
                        spawnDepth=2,
                    ),
                )
            )
        )
    )

    assert disabled.status == "disabled"
    assert disabled.reason_codes == ("research_child_runner_recipe_disabled",)
    assert disabled.public_projection()["authorityFlags"]["localFakeChildRunnerEnabled"] is False
    assert fake.calls == 0
    assert too_deep.status == "blocked"
    assert too_deep.reason_codes == ("max_spawn_depth_exceeded",)
    assert fake.calls == 0


def test_parent_claim_refs_are_optional_but_child_claim_refs_are_projected() -> None:
    request = ResearchSynthesisRequest(
        parentExecutionId="parent-research-claims-1",
        turnId="turn-research-claims-1",
        synthesisId="synthesis-research-claims-1",
        objective="Synthesize source-backed child claim refs.",
        parentSourceRefs=("source:ledger:parent-src-1",),
        tasks=(
            ResearchChildTaskSpec(
                taskId="child-claims-1",
                childRole="explore",
                objective="Inspect source-backed child claims.",
                sourceRefs=("source:ledger:parent-src-1",),
                claimRefs=("claim:research:child-claims-1",),
            ),
        ),
    )

    result = asyncio.run(_enabled_recipe().run(request))
    projection = result.public_projection()

    assert result.status == "accepted"
    assert projection["parentSynthesisInput"]["parentClaimRefs"] == []
    assert projection["parentSynthesisInput"]["childInputs"][0]["claimRefs"] == [
        "claim:research:child-claims-1"
    ]


def test_research_child_runner_import_boundary_has_no_live_runtime_surfaces() -> None:
    source = (
        Path(__file__).parents[2]
        / "openmagi_core_agent"
        / "recipes"
        / "research_child_runner.py"
    ).read_text(encoding="utf-8")
    for token in (
        "google.adk.runners",
        "subprocess",
        "ToolDispatcher",
        "FastAPI",
        "kubectl",
        "supabase",
        "stripe",
        "WebSearch",
        "WebFetch",
        "Browser",
        "MemoryWrite",
        "ChannelSend",
        "FileWrite",
        "PatchApply",
    ):
        assert token not in source

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.recipes.research_child_runner")
forbidden = (
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.models",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.runtime.adk_turn_runner",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.tools.dispatcher",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"research child runner import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
