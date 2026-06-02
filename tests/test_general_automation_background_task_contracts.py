from __future__ import annotations

import json
from pathlib import Path

from magi_agent.harness.general_automation.background_task_projection import (
    build_background_task_completion_projection,
)
from magi_agent.recipes.first_party.general_automation.background_task_contracts import (
    BackgroundTaskResumeRequest,
    background_task_long_running_tool_metadata,
    classify_background_task_resume_request,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = PYTHON_ROOT / "magi_agent" / "harness" / "general_automation"
RECIPE_DIR = (
    PYTHON_ROOT
    / "magi_agent"
    / "recipes"
    / "first_party"
    / "general_automation"
)


def _digest(char: str) -> str:
    return f"sha256:{char * 64}"


def _fragment(*parts: str) -> str:
    return "".join(parts)


def test_background_task_metadata_uses_disabled_long_running_tool_shape() -> None:
    metadata = background_task_long_running_tool_metadata()

    assert metadata["name"] == "BackgroundTaskResume"
    assert metadata["adkToolType"] == "LongRunningFunctionTool"
    assert metadata["enabledByDefault"] is False
    assert metadata["handlerAttached"] is False
    assert metadata["longRunningFunctionToolAttached"] is False
    assert metadata["sessionServiceAttached"] is False
    assert metadata["backgroundRunnerAttached"] is False
    assert set(metadata["inputSchema"]["required"]) == {
        "sessionRef",
        "taskRef",
        "checkpointRef",
        "resumeIntent",
    }


def test_resume_intent_requires_approval_and_never_executes_runner() -> None:
    pending = classify_background_task_resume_request(
        BackgroundTaskResumeRequest(
            sessionRef="session:daily",
            taskRef="task:daily-summary",
            checkpointRef="checkpoint:daily-summary",
            resumeIntent="resume",
        )
    )
    approved = classify_background_task_resume_request(
        BackgroundTaskResumeRequest(
            sessionRef="session:daily",
            taskRef="task:daily-summary",
            checkpointRef="checkpoint:daily-summary",
            resumeIntent="resume",
            approvalRef="approval:background-resume:sha256:"
            "1111111111111111111111111111111111111111111111111111111111111111",
        )
    )

    assert pending.status == "approval_required"
    assert pending.reason_codes == ("background_task_resume_approval_required",)
    assert pending.execution_allowed is False
    assert approved.status == "approval_recorded"
    assert approved.execution_allowed is False
    assert approved.resume_ref.startswith("resume:background-task:sha256:")
    public = approved.public_projection()
    assert public["adkBoundary"] == {
        "longRunningFunctionTool": "LongRunningFunctionTool",
        "functionToolName": "BackgroundTaskResume",
        "sessionService": "SessionService",
        "sessionRefsOnly": True,
    }
    assert public["authorityFlags"] == {
        "longRunningFunctionToolAttached": False,
        "sessionServiceAttached": False,
        "backgroundRunnerInvoked": False,
        "workspaceMutated": False,
        "channelDeliveryPerformed": False,
        "routeAttached": False,
    }


def test_status_lookup_is_session_metadata_only() -> None:
    decision = classify_background_task_resume_request(
        BackgroundTaskResumeRequest(
            sessionRef="session:daily",
            taskRef="task:daily-summary",
            checkpointRef="checkpoint:daily-summary",
            resumeIntent="status",
        )
    )

    assert decision.status == "metadata_recorded"
    assert decision.execution_allowed is False
    assert decision.reason_codes == ("background_task_session_status_metadata_only",)
    assert decision.public_projection()["resumeRef"].startswith(
        "resume:background-task:sha256:"
    )


def test_completion_projection_is_digest_and_ref_based_without_private_payloads() -> None:
    completion = build_background_task_completion_projection(
        sessionRef="session:daily",
        taskRef="task:daily-summary",
        completionStatus="completed",
        contentDigest=_digest("2"),
        outputRefs=(
            "artifact:background-summary:sha256:"
            "3333333333333333333333333333333333333333333333333333333333333333",
        ),
        summary="private account names and local-home markers",
    )

    public = completion.public_projection()
    rendered = json.dumps(public, sort_keys=True)
    assert public["completionRef"].startswith("completion:background-task:sha256:")
    assert public["sessionRef"] == "session:daily"
    assert public["taskRef"] == "task:daily-summary"
    assert public["contentDigest"] == _digest("2")
    assert public["summaryDigest"].startswith("sha256:")
    assert public["outputRefs"][0].startswith("artifact:background-summary:sha256:")
    assert public["adkBoundary"]["sessionService"] == "SessionService"
    assert public["adkBoundary"]["longRunningFunctionTool"] == "LongRunningFunctionTool"
    assert set(public["authorityFlags"].values()) == {False}
    assert "private account names" not in rendered
    assert "local-home" not in rendered


def test_background_task_contract_modules_do_not_touch_core_or_live_surfaces() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            HARNESS_DIR / "background_task_projection.py",
            RECIPE_DIR / "background_task_contracts.py",
        )
    )

    forbidden_fragments = (
        "google.adk",
        "LongRunningFunctionTool(",
        "SessionService(",
        "magi_agent.adk_bridge",
        "magi_agent.runtime",
        "magi_agent.transport",
        "magi_agent.routing",
        "magi_agent.harness.background_tasks",
        "magi_agent.evidence.child_runtime_envelope",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "playwright",
        "selenium",
        _fragment("sub", "process"),
        _fragment("import", "lib"),
        _fragment("__", "import", "__("),
        ".write_text(",
        "open(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
