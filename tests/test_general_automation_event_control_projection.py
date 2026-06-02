from __future__ import annotations

import json
from pathlib import Path

from magi_agent.harness.general_automation.control_projection import (
    GeneralAutomationControlProjectionRequest,
    build_general_automation_control_projection,
)
from magi_agent.harness.general_automation.event_projection import (
    GeneralAutomationEventProjectionRequest,
    build_general_automation_event_projection,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = PYTHON_ROOT / "magi_agent" / "harness" / "general_automation"


def _digest(char: str) -> str:
    return f"sha256:{char * 64}"


def _fragment(*parts: str) -> str:
    return "".join(parts)


def test_control_projection_covers_required_public_states_as_refs_only() -> None:
    states = (
        "approval_required",
        "blocked",
        "resume_ready",
        "artifact_recorded",
        "source_recorded",
        "verifier_state",
    )
    projections = tuple(
        build_general_automation_control_projection(
            GeneralAutomationControlProjectionRequest(
                controlType=state,
                subjectRef=f"tool:{state}",
                policyRef="policy:general-automation",
                payloadDigest=_digest("1"),
                evidenceRefs=(f"evidence:{state}",),
                reasonCodes=(f"{state}_metadata_only",),
                metadata={"privateMarker": "account names and local-home markers"},
            )
        )
        for state in states
    )

    rendered = json.dumps(
        [projection.public_projection() for projection in projections],
        sort_keys=True,
    )
    assert [projection.control_type for projection in projections] == list(states)
    assert all(
        projection.control_ref.startswith("control:general-automation:sha256:")
        for projection in projections
    )
    assert all(
        projection.public_projection()["payloadDigest"] == _digest("1")
        for projection in projections
    )
    assert all(
        set(projection.public_projection()["authorityFlags"].values()) == {False}
        for projection in projections
    )
    assert "account names" not in rendered
    assert "local-home" not in rendered
    assert "privateMarker" not in rendered


def test_approval_blocked_and_resume_projections_do_not_allow_execution() -> None:
    approval = build_general_automation_control_projection(
        GeneralAutomationControlProjectionRequest(
            controlType="approval_required",
            subjectRef="tool:write-file",
            policyRef="policy:approval",
            payloadDigest=_digest("2"),
            approvalRef="approval:write-file:sha256:"
            "3333333333333333333333333333333333333333333333333333333333333333",
            reasonCodes=("approval_required",),
        )
    )
    blocked = build_general_automation_control_projection(
        GeneralAutomationControlProjectionRequest(
            controlType="blocked",
            subjectRef="tool:write-file",
            policyRef="policy:approval",
            payloadDigest=_digest("2"),
            reasonCodes=("policy_blocked",),
        )
    )
    resume = build_general_automation_control_projection(
        GeneralAutomationControlProjectionRequest(
            controlType="resume_ready",
            subjectRef="tool:write-file",
            policyRef="policy:approval",
            payloadDigest=_digest("2"),
            resumeRef="resume:write-file:sha256:"
            "4444444444444444444444444444444444444444444444444444444444444444",
            reasonCodes=("resume_metadata_recorded",),
        )
    )

    for projection in (approval, blocked, resume):
        public = projection.public_projection()
        assert public["executionAllowed"] is False
        assert public["authorityFlags"]["toolDispatchEnabled"] is False
        assert public["authorityFlags"]["approvalBypassed"] is False
        assert public["adkBoundary"] == {
            "callbackEventVocabulary": "ADK callback",
            "controlProjectionOnly": True,
        }


def test_event_projection_uses_callback_vocabulary_and_control_refs() -> None:
    control = build_general_automation_control_projection(
        GeneralAutomationControlProjectionRequest(
            controlType="approval_required",
            subjectRef="tool:write-file",
            policyRef="policy:approval",
            payloadDigest=_digest("5"),
            reasonCodes=("approval_required",),
        )
    )
    event = build_general_automation_event_projection(
        GeneralAutomationEventProjectionRequest(
            eventType="callback.before_tool",
            callbackName="before_tool_callback",
            controlRef=control.control_ref,
            subjectRef="tool:write-file",
            observedAt="2026-05-27T16:00:00Z",
            payloadDigest=_digest("5"),
        )
    )

    public = event.public_projection()
    assert public["eventRef"].startswith("event:general-automation:sha256:")
    assert public["eventType"] == "callback.before_tool"
    assert public["callbackName"] == "before_tool_callback"
    assert public["controlRef"] == control.control_ref
    assert public["adkBoundary"] == {
        "callbackEventVocabulary": "ADK callback",
        "eventProjectionOnly": True,
    }
    assert public["authorityFlags"] == {
        "callbackAttached": False,
        "toolDispatchEnabled": False,
        "artifactServiceAttached": False,
        "sourceProviderCalled": False,
        "routeAttached": False,
    }


def test_event_projection_accepts_artifact_source_and_verifier_refs_without_payloads() -> None:
    events = tuple(
        build_general_automation_event_projection(
            GeneralAutomationEventProjectionRequest(
                eventType=event_type,
                callbackName="after_tool_callback",
                controlRef=f"control:{event_type.replace('.', '-')}",
                subjectRef=subject_ref,
                observedAt="2026-05-27T16:00:00Z",
                payloadDigest=_digest("6"),
                evidenceRefs=(subject_ref,),
                metadata={"privateMarker": "account names and local-home markers"},
            )
        )
        for event_type, subject_ref in (
            (
                "artifact.recorded",
                "artifact:report:sha256:"
                "7777777777777777777777777777777777777777777777777777777777777777",
            ),
            (
                "source.recorded",
                "source:web:sha256:"
                "8888888888888888888888888888888888888888888888888888888888888888",
            ),
            (
                "verifier.completed",
                "verifier:benchmark:sha256:"
                "9999999999999999999999999999999999999999999999999999999999999999",
            ),
        )
    )

    rendered = json.dumps([event.public_projection() for event in events], sort_keys=True)
    assert [event.event_type for event in events] == [
        "artifact.recorded",
        "source.recorded",
        "verifier.completed",
    ]
    assert all(event.public_projection()["payloadDigest"] == _digest("6") for event in events)
    assert "account names" not in rendered
    assert "local-home" not in rendered
    assert "privateMarker" not in rendered


def test_general_automation_event_control_modules_do_not_touch_core_or_live_surfaces() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            HARNESS_DIR / "control_projection.py",
            HARNESS_DIR / "event_projection.py",
        )
    )

    forbidden_fragments = (
        "google.adk",
        "magi_agent.adk_bridge",
        "magi_agent.runtime",
        "magi_agent.transport",
        "magi_agent.routing",
        "magi_agent.tools.dispatcher",
        "magi_agent.tools.registry",
        "magi_agent.tools.permission",
        "magi_agent.tools.result",
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
