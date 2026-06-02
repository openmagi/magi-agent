from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from openmagi_core_agent.recipes.coding_subagents import (
    CodingSubagentConfig,
    CodingSubagentModeRequest,
    CodingSubagentRecipe,
    CodingSubagentToolScope,
)
from openmagi_core_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    workspace_content_digest,
)


class LocalCodingFakeRunner:
    openmagi_local_fake_provider = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.seen_allowed_tools: tuple[str, ...] = ()

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        metadata = getattr(request, "metadata")
        allowed_tools = metadata.get("allowedTools")
        self.seen_allowed_tools = tuple(allowed_tools) if isinstance(allowed_tools, tuple) else ()
        if self.fail:
            raise RuntimeError(
                "raw_child_transcript: /workspace/private.py PRIVATE_PAYLOAD_DO_NOT_PROJECT"
            )
        return {
            "childExecutionId": "child-review-1",
            "status": "completed",
            "summary": (
                "Found one issue.\n"
                "raw_child_transcript: /workspace/private.py\n"
                "Authorization: Bearer PRIVATE_PAYLOAD_DO_NOT_PROJECT"
            ),
            "evidenceRefs": ("evidence:review-finding-1",),
            "artifactRefs": ("artifact:review-report-1",),
            "auditEventRefs": ("audit:review-child-1",),
            "rawTranscript": "private transcript",
        }


def _enabled_recipe(
    *,
    fake: LocalCodingFakeRunner | None = None,
    read_ledger: ReadLedger | None = None,
    extra_tools: tuple[str, ...] = (),
) -> CodingSubagentRecipe:
    return CodingSubagentRecipe(
        CodingSubagentConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            additionalAllowedTools=extra_tools,
        ),
        child_runner=fake or LocalCodingFakeRunner(),
        read_ledger=read_ledger,
    )


def _request(mode: str, **metadata: object) -> CodingSubagentModeRequest:
    return CodingSubagentModeRequest(
        mode=mode,
        parentExecutionId="parent-coding-1",
        turnId="turn-coding-1",
        taskId=f"task-{mode}-1",
        objective="Review the local change without exposing raw child context.",
        sessionId="session-1",
        workspaceRef="workspace:repo-1",
        metadata=metadata,
    )


def _ledger_with_read(content: str = "alpha\n") -> tuple[ReadLedger, str]:
    ledger = ReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True))
    digest = workspace_content_digest(content)
    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:repo-1",
        path="src/app.py",
        digest=digest,
        size_bytes=len(content.encode("utf-8")),
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-coding-1",
        tool_use_id="read-1",
    )
    return ledger, digest


def test_inspect_mode_exposes_read_only_scope_and_cannot_mutate() -> None:
    fake = LocalCodingFakeRunner()
    recipe = _enabled_recipe(fake=fake, extra_tools=("FileWrite", "PatchApply", "Bash"))

    result = asyncio.run(recipe.run(_request("inspect")))
    projection = result.public_projection()

    assert fake.calls == 1
    assert fake.seen_allowed_tools == CodingSubagentToolScope.inspect().allowed_tools
    assert result.status == "accepted"
    assert result.tool_scope.mutation_intent_allowed is False
    assert projection["toolScope"]["allowedTools"] == list(
        CodingSubagentToolScope.inspect().allowed_tools
    )
    assert projection["toolScope"]["deniedTools"] == ["Bash", "FileWrite", "PatchApply"]
    assert projection["authorityFlags"]["workspaceMutationEnabled"] is False
    assert projection["authorityFlags"]["liveToolExecutionEnabled"] is False


def test_code_review_returns_findings_and_sanitized_child_evidence_refs() -> None:
    result = asyncio.run(_enabled_recipe().run(_request("code_review")))
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert result.status == "accepted"
    assert projection["mode"] == "code_review"
    assert projection["child"]["childEnvelope"]["childExecutionId"] == "child-review-1"
    assert projection["findings"][0]["findingRef"].startswith("finding:")
    assert projection["findings"][0]["evidenceRefs"] == projection["child"]["parentOutputRefs"][1:2]
    assert projection["child"]["authorityFlags"]["realChildRunnerExecuted"] is False
    assert "raw_child_transcript" not in rendered
    assert "private transcript" not in rendered
    assert "PRIVATE_PAYLOAD_DO_NOT_PROJECT" not in rendered
    assert "/workspace" not in rendered


def test_implement_local_requires_read_ledger_and_returns_approval_intent_without_mutation() -> None:
    missing_ledger = asyncio.run(
        _enabled_recipe().run(
            _request(
                "implement_local",
                mutationIntent={
                    "toolName": "FileEdit",
                    "path": "src/app.py",
                    "currentDigest": workspace_content_digest("alpha\n"),
                    "currentText": "alpha\n",
                    "oldString": "alpha",
                    "newString": "beta",
                },
            )
        )
    )
    ledger, digest = _ledger_with_read()
    with_read = asyncio.run(
        _enabled_recipe(read_ledger=ledger).run(
            _request(
                "implement_local",
                mutationIntent={
                    "toolName": "FileEdit",
                    "path": "src/app.py",
                    "currentDigest": digest,
                    "currentText": "alpha\n",
                    "oldString": "alpha",
                    "newString": "beta",
                    "explicitApproval": True,
                },
            )
        )
    )

    assert missing_ledger.status == "blocked"
    assert missing_ledger.reason_codes == ("read_ledger_required",)
    assert with_read.status == "approval_required"
    assert with_read.mutation_intent is not None
    assert with_read.mutation_intent.status == "approval_required"
    projection = with_read.public_projection()
    assert projection["mutationIntent"]["readLedger"]["status"] == "ok"
    assert projection["authorityFlags"]["workspaceMutationEnabled"] is False
    assert projection["authorityFlags"]["workspaceMutated"] is False
    assert projection["authorityFlags"]["productionAuthority"] is False
    assert projection["child"] is None


def test_implement_local_invalid_mutation_intent_blocks_without_raw_leakage() -> None:
    ledger, _digest = _ledger_with_read()
    result = asyncio.run(
        _enabled_recipe(read_ledger=ledger).run(
            _request(
                "implement_local",
                mutationIntent={
                    "toolName": "Bash",
                    "path": "/workspace/private.py",
                    "currentDigest": "not-a-digest",
                    "currentText": "PRIVATE_PAYLOAD_DO_NOT_PROJECT",
                    "oldString": "alpha",
                    "newString": "beta",
                    "explicitApproval": True,
                },
            )
        )
    )
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert result.status == "blocked"
    assert result.reason_codes == ("invalid_mutation_intent",)
    assert projection["mutationIntent"] is None
    assert projection["authorityFlags"]["workspaceMutationEnabled"] is False
    assert projection["authorityFlags"]["workspaceMutated"] is False
    assert "Bash" not in rendered
    assert "/workspace" not in rendered
    assert "PRIVATE_PAYLOAD_DO_NOT_PROJECT" not in rendered
    assert "not-a-digest" not in rendered


def test_research_and_background_modes_are_blocked_for_coding_recipe() -> None:
    recipe = _enabled_recipe()

    research = asyncio.run(recipe.run(_request("research")))
    background = asyncio.run(recipe.run(_request("inspect", delivery="background")))

    assert research.status == "blocked"
    assert research.reason_codes == ("research_mode_unavailable_for_coding_recipe",)
    assert background.status == "blocked"
    assert background.reason_codes == ("background_child_lifecycle_disabled",)


def test_child_failure_is_parent_visible_only_as_sanitized_metadata() -> None:
    result = asyncio.run(_enabled_recipe(fake=LocalCodingFakeRunner(fail=True)).run(_request("inspect")))
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert result.status == "error"
    assert projection["failureEvent"] == {
        "eventRef": result.failure_event_ref,
        "errorCode": "local_fake_child_runner_error",
    }
    assert "raw_child_transcript" not in rendered
    assert "PRIVATE_PAYLOAD_DO_NOT_PROJECT" not in rendered
    assert "/workspace" not in rendered


def test_disabled_defaults_and_forged_authority_flags_remain_inert() -> None:
    disabled = asyncio.run(CodingSubagentRecipe().run(_request("inspect")))
    forged = disabled.model_copy(
        update={
            "authorityFlags": {
                "recipeEnabled": True,
                "localFakeChildRunnerEnabled": True,
                "workspaceMutationEnabled": True,
                "workspaceMutated": True,
                "backgroundModeEnabled": True,
                "liveChildRunnerEnabled": True,
                "liveToolExecutionEnabled": True,
                "productionAuthority": True,
                "trafficAttached": True,
                "userVisibleActivation": True,
            }
        }
    )

    assert disabled.status == "disabled"
    assert disabled.reason_codes == ("coding_subagent_recipe_disabled",)
    assert forged.public_projection()["authorityFlags"] == {
        "recipeEnabled": False,
        "localFakeChildRunnerEnabled": False,
        "workspaceMutationEnabled": False,
        "workspaceMutated": False,
        "backgroundModeEnabled": False,
        "liveChildRunnerEnabled": False,
        "liveToolExecutionEnabled": False,
        "productionAuthority": False,
        "trafficAttached": False,
        "userVisibleActivation": False,
    }


def test_coding_subagent_recipe_import_boundary_has_no_live_runtime_surfaces() -> None:
    source = (
        Path(__file__).parents[2]
        / "openmagi_core_agent"
        / "recipes"
        / "coding_subagents.py"
    ).read_text(encoding="utf-8")
    for token in (
        "google.adk.runners",
        "subprocess",
        "ToolDispatcher",
        "FastAPI",
        "kubectl",
        "supabase",
        "stripe",
    ):
        assert token not in source

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.recipes.coding_subagents")
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
    raise AssertionError(f"coding subagent import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
