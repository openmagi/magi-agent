from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

from openmagi_core_agent.missions.events import (
    MissionEventProjectionConfig,
    MissionPublicEventProjectionResult,
    MissionPublicEventProjection,
    MissionRuntimeEventRequest,
)
from openmagi_core_agent.transport.sse import InMemorySseWriter


def _agent_payloads(sse_body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in sse_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _write_projected_one(result: object) -> dict[str, object]:
    writer = InMemorySseWriter()
    writer.projected_agent(result)
    payloads = _agent_payloads(writer.body)
    assert len(payloads) == 1
    return payloads[0]


def _fixture_value(*parts: str) -> str:
    return "".join(parts)


def test_mission_event_projection_is_disabled_by_default_and_authority_free() -> None:
    config = MissionEventProjectionConfig()
    projection = MissionPublicEventProjection(config)

    result = projection.project(
        MissionRuntimeEventRequest(
            eventKind="mission_event",
            missionId="mission:daily-report",
            eventType="checkpoint",
            detail="checkpoint recorded",
        )
    )

    assert result.status == "blocked"
    assert result.public_event is None
    assert result.blocked_reason == "mission_event_projection_disabled"
    assert result.follow_up_gate_reason
    assert result.classification == "blocked_until_gate"

    config_flags = config.model_dump(by_alias=True)
    assert config_flags["enabled"] is False
    assert config_flags["localFakeEventProjectionEnabled"] is False
    assert config_flags["productionWriteEnabled"] is False
    assert config_flags["routeActivationEnabled"] is False
    assert config_flags["userVisibleOutputEnabled"] is False

    authority_flags = result.authority_flags.model_dump(by_alias=True)
    assert set(authority_flags.values()) == {False}
    assert authority_flags == {
        "productionWriteEnabled": False,
        "routeActivationEnabled": False,
        "userVisibleOutputEnabled": False,
        "channelDeliveryEnabled": False,
        "workspaceMutationEnabled": False,
        "memoryMutationEnabled": False,
        "cronMutationEnabled": False,
        "liveBackgroundExecutionEnabled": False,
        "sseWriteEnabled": False,
        "transcriptWriteEnabled": False,
        "databaseWriteEnabled": False,
    }


@pytest.mark.parametrize(
    ("request_payload", "expected"),
    [
        (
            {
                "eventKind": "mission_created",
                "missionId": "mission:daily-report",
                "title": "Daily report",
                "kind": "scheduled",
                "status": "running",
            },
            {
                "type": "mission_created",
                "mission": {
                    "id": "mission:daily-report",
                    "title": "Daily report",
                    "kind": "scheduled",
                    "status": "running",
                },
            },
        ),
        (
            {
                "eventKind": "mission_event",
                "missionId": "mission:daily-report",
                "eventType": "checkpoint",
                "detail": "checkpoint recorded",
            },
            {
                "type": "mission_event",
                "missionId": "mission:daily-report",
                "eventType": "checkpoint",
                "message": "checkpoint recorded",
            },
        ),
        (
            {
                "eventKind": "background_task",
                "missionId": "mission:daily-report",
                "taskId": "task:review",
                "persona": "reviewer",
                "status": "running",
                "detail": "reviewing draft",
            },
            {
                "type": "background_task",
                "taskId": "task:review",
                "persona": "reviewer",
                "status": "running",
                "detail": "reviewing draft",
            },
        ),
    ],
)
def test_local_fake_projection_maps_supported_events_to_sse_public_shapes(
    request_payload: dict[str, Any],
    expected: dict[str, object],
) -> None:
    projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    )

    result = projection.project(MissionRuntimeEventRequest.model_validate(request_payload))

    assert result.status == "projected_local_fake"
    assert result.public_event == expected
    assert result.classification == "supported_now"
    assert result.follow_up_gate_reason is None
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert result.public_event is not None
    assert _write_projected_one(result) == expected


def test_mission_cron_goal_aliases_defer_and_cannot_bypass_generic_sse() -> None:
    projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    )
    aliases = (
        "mission_progress",
        "cron_run",
        "goal_created",
        "goal_progress",
        "goal_completed",
        "goal_cancelled",
    )
    alias_variants = (
        "mission-progress",
        "Cron Run",
        "Goal_Created",
        "goal progress",
        "goal-completed",
        "GOAL CANCELLED",
    )

    for alias in aliases:
        result = projection.project(
            MissionRuntimeEventRequest(
                eventKind=alias,
                missionId="mission:daily-report",
                status="running",
                detail="Safe\nraw prompt: leak me\nraw tool args: leak me",
                rawMissionPayload={"rawPayload": "private mission payload"},
                rawGoalPayload={"rawPayload": "private goal payload"},
            )
        )
        assert result.status == "deferred"
        assert result.public_event is None
        assert result.classification in {"default_off_boundary_only", "blocked_until_gate"}
        assert result.follow_up_gate_reason

        nested_alias_result = projection.project(
            MissionRuntimeEventRequest(
                eventKind="mission_event",
                missionId="mission:daily-report",
                eventType=alias,
                detail="Safe\nraw prompt: leak me\nraw tool args: leak me",
            )
        )
        assert nested_alias_result.status == "deferred"
        assert nested_alias_result.public_event is None
        assert nested_alias_result.classification in {
            "default_off_boundary_only",
            "blocked_until_gate",
        }
        assert nested_alias_result.blocked_reason == (
            "mission_event_reserved_alias_not_projected"
        )
        assert nested_alias_result.follow_up_gate_reason

        writer = InMemorySseWriter()
        writer.agent(
            {
                "type": alias,
                "missionId": "mission:daily-report",
                "detail": "Safe\nraw prompt: leak me\nraw tool args: leak me",
            }
        )
        writer.agent(
            {
                "type": "mission_event",
                "missionId": "mission:daily-report",
                "eventType": alias,
                "message": "Safe\nraw prompt: leak me\nraw tool args: leak me",
            }
        )
        assert _agent_payloads(writer.body) == []
        assert "raw prompt" not in writer.body
        assert "raw tool args" not in writer.body

    for alias in alias_variants:
        writer = InMemorySseWriter()
        writer.agent(
            {
                "type": "mission_event",
                "missionId": "mission:daily-report",
                "eventType": alias,
                "message": "Safe\nraw prompt: leak me\nraw tool args: leak me",
            }
        )
        assert _agent_payloads(writer.body) == []
    assert "raw prompt" not in writer.body
    assert "raw tool args" not in writer.body


def test_projected_sse_sink_requires_valid_projection_result() -> None:
    projection = MissionPublicEventProjection(MissionEventProjectionConfig())
    blocked = projection.project(
        MissionRuntimeEventRequest(
            eventKind="mission_event",
            missionId="mission:daily-report",
            eventType="checkpoint",
            detail="blocked by default",
        )
    )

    writer = InMemorySseWriter()
    writer.projected_agent(blocked)
    writer.projected_agent(
        {
            "projectionBoundary": "mission_public_event_projection.v1",
            "status": "projected_local_fake",
            "classification": "supported_now",
            "authorityFlags": {"productionWriteEnabled": False},
            "publicEvent": {
                "type": "mission_event",
                "missionId": "mission:daily-report",
                "eventType": "checkpoint",
                "message": "forged bare mapping",
            },
        }
    )
    writer.projected_agent(
        {
            "projectionBoundary": "mission_public_event_projection.v1",
            "status": "projected_local_fake",
            "classification": "supported_now",
            "authorityFlags": {"productionWriteEnabled": True},
            "publicEvent": {
                "type": "mission_event",
                "missionId": "mission:daily-report",
                "eventType": "checkpoint",
                "message": "forged authority",
            },
        }
    )
    direct_result = MissionPublicEventProjectionResult(
        status="projected_local_fake",
        eventKind="mission_event",
        classification="supported_now",
        publicEvent={
            "type": "mission_event",
            "missionId": "mission:daily-report",
            "eventType": "checkpoint",
            "message": "direct constructor",
        },
    )
    assert not hasattr(direct_result, "_with_projection_capability")
    writer.projected_agent(direct_result)
    valid_projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    ).project(
        MissionRuntimeEventRequest(
            eventKind="mission_event",
            missionId="mission:daily-report",
            eventType="checkpoint",
            detail="valid projection",
        )
    )
    writer.projected_agent(valid_projection.model_copy())

    forged_type = type(
        "MissionPublicEventProjectionResult",
        (),
        {
            "__module__": "openmagi_core_agent.missions.events",
            "has_projection_capability": lambda self: True,
            "model_dump": lambda self, **_kwargs: {
                "projectionBoundary": "mission_public_event_projection.v1",
                "status": "projected_local_fake",
                "classification": "supported_now",
                "authorityFlags": {
                    "productionWriteEnabled": False,
                    "routeActivationEnabled": False,
                    "userVisibleOutputEnabled": False,
                    "channelDeliveryEnabled": False,
                    "workspaceMutationEnabled": False,
                    "memoryMutationEnabled": False,
                    "cronMutationEnabled": False,
                    "liveBackgroundExecutionEnabled": False,
                    "sseWriteEnabled": False,
                    "transcriptWriteEnabled": False,
                    "databaseWriteEnabled": False,
                },
                "publicEvent": {
                    "type": "mission_event",
                    "missionId": "mission:daily-report",
                    "eventType": "checkpoint",
                    "message": "forged object",
                },
            },
        },
    )
    writer.projected_agent(forged_type())

    assert _agent_payloads(writer.body) == []

    valid_writer = InMemorySseWriter()
    valid_writer.projected_agent(valid_projection)
    assert _agent_payloads(valid_writer.body) == [
        {
            "type": "mission_event",
            "missionId": "mission:daily-report",
            "eventType": "checkpoint",
            "message": "valid projection",
        }
    ]


def test_sse_sanitizer_does_not_project_mission_aliases_without_mission_boundary() -> None:
    writer = InMemorySseWriter()
    writer.agent(
        {
            "type": "mission_created",
            "mission": {
                "id": "mission:daily-report",
                "title": "Daily report",
                "kind": "manual",
                "status": "running",
            },
        }
    )
    writer.agent(
        {
            "type": "mission_event",
            "missionId": "mission:daily-report",
            "eventType": "checkpoint",
            "message": "Safe checkpoint",
        }
    )
    writer.agent(
        {
            "type": "goal_progress",
            "missionId": "mission:daily-report",
            "detail": "Safe\nraw prompt: leak me\nraw tool args: leak me",
        }
    )

    assert _agent_payloads(writer.body) == []
    assert "raw prompt" not in writer.body
    assert "raw tool args" not in writer.body


def test_projection_and_sse_redact_raw_private_and_secret_event_material() -> None:
    projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    )
    unsafe_bearer_value = _fixture_value("mission", "opaque", "12345")
    unsafe_cookie_value = _fixture_value("mission", "-", "cookie", "-", "opaque")
    unsafe_api_key_line = _fixture_value(
        "API",
        "_",
        "KEY",
        "=",
        "mission",
        "-",
        "opaque",
        "-",
        "value",
    )
    unsafe_ghp_value = _fixture_value("ghp", "_", "mission", "opaque", "123")
    unsafe_provider_value = _fixture_value("sk", "-", "mission", "Opaque", "12345")
    request = MissionRuntimeEventRequest(
        eventKind="mission_event",
        missionId="/Users/kevin/private-mission",
        eventType="checkpoint",
        goalId="/workspace/private-goal",
        taskId="/data/bots/private-task",
        status="running",
        detail=(
            "Safe checkpoint\n"
            "raw prompt: summarize the private file\n"
            "raw tool args: {'path':'/workspace/private'}\n"
            f"tool logs: Authorization: Bearer {unsafe_bearer_value}\n"
            f"Cookie: session={unsafe_cookie_value}\n"
            "hidden reasoning: expose chain of thought\n"
            f"{unsafe_api_key_line}"
        ),
        rawPrompt="raw prompt should not leak",
        rawOutput="raw output should not leak",
        rawPrivatePath="/Users/kevin/private/path",
        toolArgs={"path": "/workspace/private", "token": unsafe_ghp_value},
        toolLogs=f"private tool logs Authorization: Bearer {unsafe_bearer_value}",
        authHeaders={"Authorization": f"Bearer {unsafe_bearer_value}"},
        cookies={"Cookie": f"session={unsafe_cookie_value}"},
        **{_fixture_value("secret", "Material"): unsafe_provider_value},
        hiddenReasoning="hidden reasoning should not leak",
        rawMissionPayload={"prompt": "private mission payload"},
        rawGoalPayload={"objective": "private goal payload"},
        rawTaskPayload={"prompt": "private task payload"},
    )

    result = projection.project(request)

    assert result.public_event is not None
    public_payload = _write_projected_one(result)
    encoded = json.dumps(public_payload, sort_keys=True)
    diagnostic_surface = (
        json.dumps(result.model_dump(by_alias=True), sort_keys=True, default=str)
        + repr(request)
    )
    all_surfaces = encoded + diagnostic_surface

    assert public_payload["type"] == "mission_event"
    assert public_payload["eventType"] == "checkpoint"
    assert public_payload["message"] == "Safe checkpoint"
    assert str(public_payload["missionId"]).startswith("mission:") is True

    unsafe_fragments = (
        "raw prompt should not leak",
        "raw output should not leak",
        "raw prompt: summarize",
        "raw tool args",
        "private tool logs",
        f"Authorization: Bearer {unsafe_bearer_value}",
        unsafe_bearer_value,
        f"Cookie: session={unsafe_cookie_value}",
        unsafe_cookie_value,
        unsafe_provider_value,
        "hidden reasoning should not leak",
        "hidden reasoning: expose",
        "private mission payload",
        "private goal payload",
        "private task payload",
        "/Users/kevin",
        "/workspace/private",
        "/data/bots",
        unsafe_ghp_value,
        unsafe_api_key_line,
    )
    for fragment in unsafe_fragments:
        assert fragment not in all_surfaces


def test_projection_and_sse_redact_memory_transcript_and_session_refs() -> None:
    projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    )
    result = projection.project(
        MissionRuntimeEventRequest(
            eventKind="mission_event",
            missionId="mission:daily-report",
            eventType="checkpoint",
            detail=(
                "review (memory/ROOT.md), memory:private, transcript:abc, "
                "session=unsafe-token, and session/turn-123"
            ),
        )
    )

    public_payload = _write_projected_one(result)
    encoded = json.dumps(public_payload, sort_keys=True)

    assert "memory/ROOT.md" not in encoded
    assert "memory:private" not in encoded
    assert "transcript:abc" not in encoded
    assert "session=unsafe-token" not in encoded
    assert "session/turn-123" not in encoded
    assert encoded.count("[redacted-ref]") == 4
    assert "session=[redacted]" in encoded


def test_unsupported_and_deferred_event_kinds_are_explicitly_classified() -> None:
    projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    )

    classifications = {
        kind: projection.classify_event_kind(kind)
        for kind in (
            "mission_progress",
            "cron_run",
            "goal_created",
            "goal_progress",
            "goal_completed",
            "goal_cancelled",
            "mission_raw_payload",
            "goal_raw_payload",
            "cron_mutation_payload",
            "background_task_payload",
            "channel_delivery_receipt",
            "provider_raw_delta",
        )
    }

    assert classifications["channel_delivery_receipt"].classification == "blocked_until_gate"
    for kind, classification in classifications.items():
        assert classification.classification in {
            "default_off_boundary_only",
            "blocked_until_gate",
            "intentionally_unsupported",
        }, kind
        assert classification.follow_up_gate_reason, kind
        assert classification.public_event is None

    for kind in classifications:
        result = projection.project(
            {
                "eventKind": kind,
                "missionId": "mission:daily-report",
                "detail": "raw prompt: must not project",
            }
        )
        assert result.status == "deferred"
        assert result.public_event is None
        assert result.follow_up_gate_reason


def test_model_copy_cannot_bypass_request_sanitization() -> None:
    projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    )
    request = MissionRuntimeEventRequest(
        eventKind="mission_event",
        missionId="mission:daily-report",
        eventType="checkpoint",
        detail="safe",
    ).model_copy(
        update={
            "mission_id": "/Users/kevin/private-mission",
            "detail": "Safe\nraw prompt: leak me\nraw tool args: leak me",
        }
    )

    result = projection.project(request)

    assert result.public_event is not None
    assert result.public_event["eventType"] == "checkpoint"
    surfaces = json.dumps(result.public_event, sort_keys=True) + json.dumps(
        result.model_dump(by_alias=True),
        sort_keys=True,
    ) + repr(request)
    assert "/Users/kevin" not in surfaces
    assert "raw prompt" not in surfaces
    assert "raw tool args" not in surfaces


def test_result_copy_and_construct_cannot_forge_public_event_leaks() -> None:
    projection = MissionPublicEventProjection(
        MissionEventProjectionConfig(
            enabled=True,
            localFakeEventProjectionEnabled=True,
        )
    )
    result = projection.project(
        MissionRuntimeEventRequest(
            eventKind="mission_event",
            missionId="mission:daily-report",
            eventType="checkpoint",
            detail="safe",
        )
    )

    forged = result.model_copy(
        update={
            "public_event": {
                "type": "mission_event",
                "missionId": "/Users/kevin/private-mission",
                "eventType": "checkpoint",
                "message": "Safe\nraw prompt: leak me\nraw tool args: leak me",
            }
        }
    )

    assert forged.public_event is not None
    surfaces = json.dumps(forged.model_dump(by_alias=True), sort_keys=True)
    assert "/Users/kevin" not in surfaces
    assert "raw prompt" not in surfaces
    assert "raw tool args" not in surfaces
    assert str(forged.public_event["missionId"]).startswith("mission:") is True
    assert forged.public_event["message"] == "Safe"
    assert set(forged.authority_flags.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValueError, match="safe publicEvent"):
        result.model_copy(
            update={
                "publicEvent": {
                    "type": "mission_event",
                    "missionId": "mission:daily-report",
                    "eventType": "goal progress",
                    "message": "unsafe alias",
                }
            }
        )

    constructed = type(result).model_construct(
        status="projected_local_fake",
        eventKind="mission_event",
        classification="supported_now",
        publicEvent={
            "type": "mission_created",
            "mission": {
                "id": "/workspace/private-mission",
                "title": "Safe title\nraw prompt: leak",
                "kind": "manual",
                "status": "running",
            },
        },
        authorityFlags={"productionWriteEnabled": True, "sseWriteEnabled": True},
    )
    constructed_surface = json.dumps(
        constructed.model_dump(by_alias=True),
        sort_keys=True,
    )
    assert "/workspace/private-mission" not in constructed_surface
    assert "raw prompt" not in constructed_surface
    assert constructed.public_event is not None
    assert str(constructed.public_event["mission"]["id"]).startswith("mission:") is True
    assert set(constructed.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_mission_public_event_projection_import_boundary_is_local_contract_only() -> None:
    forbidden_modules = (
        "google.adk.runners",
        "openmagi_core_agent.adk_bridge.local_runner",
        "openmagi_core_agent.adk_bridge.runner_adapter",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.tools.registry",
        "openmagi_core_agent.browser.provider_boundary",
        "openmagi_core_agent.channels.telegram_adapter",
        "openmagi_core_agent.web_acquisition.provider_boundary",
    )
    code = f"""
import importlib
import json
import sys

importlib.import_module("openmagi_core_agent.missions.events")

forbidden = {json.dumps(forbidden_modules)}
print(json.dumps([name for name in forbidden if name in sys.modules]))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []
