import importlib
import json
import sys
from pathlib import Path

from magi_agent.runtime.control import (
    CONTROL_EVENT_TYPES,
    ControlEventLedger,
    ControlEventBase,
    ControlRequestCancelledEvent,
    ControlRequestCreatedEvent,
    ControlRequestResolvedEvent,
    ControlRequestRecord,
    ControlRequestTimedOutEvent,
    PermissionDecisionControlEvent,
    make_transcript_reference,
)


LEAKY_CONTROL_INPUT = {
    "prompt": "child prompt with account details",
    "logs": ["stdout secret", "stderr secret"],
    "log": "single log secret",
    "stdout": "stdout secret",
    "stderr": "stderr secret",
    "output": {"nested": "output secret"},
    "Authorization": "Bearer live-token",
    "api_key": "sk-project-openai-secret",
    "serviceRoleKey": "supabase-service-role-secret",
    "serviceRoleKEY": "supabase-service-role-secret-uppercase",
    "service-role-key": "supabase-service-role-secret-hyphen",
    "apiKey": "camel-api-key-secret",
    "secretKey": "camel-secret-key-secret",
    "privateKey": "camel-private-key-secret",
    "authToken": "camel-auth-token-secret",
    "sessionCookie": "camel-cookie-secret",
    "clientCredential": "camel-credential-secret",
    "path": "/Users/kevin/Desktop/private/repo/.env",
    "workspacePath": "/workspace/project/.env",
}
LEAKY_CONTROL_TEXT = {
    "child prompt with account details",
    "stdout secret",
    "stderr secret",
    "single log secret",
    "output secret",
    "live-token",
    "sk-project-openai-secret",
    "supabase-service-role-secret",
    "supabase-service-role-secret-uppercase",
    "supabase-service-role-secret-hyphen",
    "camel-api-key-secret",
    "camel-secret-key-secret",
    "camel-private-key-secret",
    "camel-auth-token-secret",
    "camel-cookie-secret",
    "camel-credential-secret",
    "/Users/kevin",
    "/workspace/project/.env",
}


def _assert_control_input_is_redacted(value: object) -> None:
    dumped = json.dumps(value, sort_keys=True, default=str)
    for raw_text in LEAKY_CONTROL_TEXT:
        assert raw_text not in dumped
    assert "[redacted-prompt]" in dumped
    assert "[redacted-output]" in dumped
    assert "[redacted]" in dumped
    assert "[redacted-path]" in dumped


def _load_control_fixture(name: str) -> list[ControlEventBase]:
    from magi_agent.runtime.transcript import TranscriptStore

    store = TranscriptStore(
        file_path=Path(__file__).parent / "fixtures" / "transcript" / name
    )
    events: list[ControlEventBase] = []
    for entry in store.read_all():
        if entry.kind != "control_event":
            continue
        payload = entry.model_dump(by_alias=True)
        events.append(
            ControlEventBase(
                eventId=entry.event_id,
                seq=entry.seq,
                ts=entry.ts,
                sessionKey=str(payload["sessionKey"]),
                turnId=entry.turn_id,
                type=entry.event_type,
                idempotencyKey=str(payload["idempotencyKey"]),
            )
        )
    return events


def test_control_import_does_not_load_write_capable_transcript_module() -> None:
    sys.modules.pop("magi_agent.runtime.control", None)
    sys.modules.pop("magi_agent.runtime.transcript", None)

    importlib.import_module("magi_agent.runtime.control")

    assert "magi_agent.runtime.transcript" not in sys.modules


def test_permission_decision_event_preserves_envelope_and_reference_shape() -> None:
    event = PermissionDecisionControlEvent(
        event_id="evt-1",
        seq=1,
        ts=123,
        session_key="agent:main:app:default",
        turn_id="turn-1",
        source="turn",
        tool_name="Bash",
        decision="ask",
        reason="dangerous",
    )

    assert event.v == 1
    assert event.type == "permission_decision"
    assert event.tool_name == "Bash"
    assert make_transcript_reference(event).model_dump(by_alias=True) == {
        "kind": "control_event",
        "ts": 123,
        "turnId": "turn-1",
        "seq": 1,
        "eventId": "evt-1",
        "eventType": "permission_decision",
    }


def test_control_ledger_requires_monotonic_sequence() -> None:
    ledger = ControlEventLedger()
    first = PermissionDecisionControlEvent(
        event_id="evt-1",
        seq=1,
        ts=123,
        session_key="agent:main:app:default",
        source="turn",
        decision="allow",
    )
    duplicate = first.model_copy(update={"event_id": "evt-2"})

    ledger.append(first)

    try:
        ledger.append(duplicate)
    except ValueError as exc:
        assert "monotonic" in str(exc)
    else:
        raise AssertionError("duplicate sequence should fail")


def test_control_request_record_models_tool_permission_state() -> None:
    record = ControlRequestRecord(
        request_id="req-1",
        kind="tool_permission",
        state="pending",
        session_key="agent:main:app:default",
        turn_id="turn-1",
        source="turn",
        prompt="Allow Bash?",
        created_at=1,
        expires_at=2,
    )

    assert record.kind == "tool_permission"
    assert record.state == "pending"
    assert {
        "control_request_created",
        "control_request_resolved",
        "control_request_cancelled",
        "control_request_timed_out",
    }.issubset(CONTROL_EVENT_TYPES)


def test_direct_permission_decision_event_redacts_updated_input() -> None:
    event = PermissionDecisionControlEvent(
        event_id="evt-permission-redacted",
        seq=2,
        ts=124,
        session_key="agent:main:app:default",
        source="child-agent",
        tool_name="Bash",
        decision="ask",
        updatedInput=LEAKY_CONTROL_INPUT,
    )

    assert event.updated_input == {
        "promptPreview": "[redacted-prompt]",
        "logsPreview": "[redacted-output]",
        "logPreview": "[redacted-output]",
        "stdoutPreview": "[redacted-output]",
        "stderrPreview": "[redacted-output]",
        "outputPreview": "[redacted-output]",
        "Authorization": "[redacted]",
        "api_key": "[redacted]",
        "serviceRoleKey": "[redacted]",
        "serviceRoleKEY": "[redacted]",
        "service-role-key": "[redacted]",
        "apiKey": "[redacted]",
        "secretKey": "[redacted]",
        "privateKey": "[redacted]",
        "authToken": "[redacted]",
        "sessionCookie": "[redacted]",
        "clientCredential": "[redacted]",
        "path": "[redacted-path]",
        "workspacePath": "[redacted-path]",
    }
    _assert_control_input_is_redacted(event.model_dump(by_alias=True))


def test_direct_control_request_record_redacts_proposed_and_updated_input() -> None:
    record = ControlRequestRecord(
        request_id="req-redacted",
        kind="tool_permission",
        state="approved",
        session_key="agent:main:app:default",
        turn_id="turn-redacted",
        source="child-agent",
        prompt="Allow child task?",
        proposedInput=LEAKY_CONTROL_INPUT,
        created_at=1,
        expires_at=2,
        resolved_at=3,
        decision="approved",
        updatedInput=LEAKY_CONTROL_INPUT,
    )

    assert record.proposed_input == record.updated_input
    _assert_control_input_is_redacted(record.model_dump(by_alias=True))


def test_control_request_created_event_accepts_typescript_alias_shape() -> None:
    event = ControlRequestCreatedEvent(
        eventId="evt-created",
        seq=2,
        ts=124,
        sessionKey="agent:main:app:default",
        turnId="turn-2",
        idempotencyKey="idem-created",
        request={
            "requestId": "req-2",
            "kind": "tool_permission",
            "state": "pending",
            "sessionKey": "agent:main:app:default",
            "turnId": "turn-2",
            "channelName": "telegram",
            "source": "turn",
            "prompt": "Allow Shell?",
            "proposedInput": {"command": "ls"},
            "createdAt": 124,
            "expiresAt": 184,
        },
    )

    assert event.type == "control_request_created"
    assert event.idempotency_key == "idem-created"
    assert event.request.request_id == "req-2"
    assert event.request.channel_name == "telegram"
    assert event.model_dump(by_alias=True)["idempotencyKey"] == "idem-created"
    assert event.model_dump(by_alias=True)["request"]["requestId"] == "req-2"
    assert make_transcript_reference(event).model_dump(by_alias=True) == {
        "kind": "control_event",
        "ts": 124,
        "turnId": "turn-2",
        "seq": 2,
        "eventId": "evt-created",
        "eventType": "control_request_created",
    }


def test_control_request_resolved_event_accepts_typescript_alias_shape() -> None:
    event = ControlRequestResolvedEvent(
        eventId="evt-resolved",
        seq=3,
        ts=125,
        sessionKey="agent:main:app:default",
        turnId="turn-2",
        requestId="req-2",
        decision="answered",
        feedback="Use read-only mode",
        updatedInput={"command": "ls -la"},
        answer="Yes, list the directory.",
    )

    assert event.type == "control_request_resolved"
    assert event.request_id == "req-2"
    assert event.decision == "answered"
    assert event.updated_input == {"commandPreview": "[redacted-command]"}
    assert event.model_dump(by_alias=True)["requestId"] == "req-2"
    assert event.model_dump(by_alias=True)["updatedInput"] == {
        "commandPreview": "[redacted-command]"
    }
    assert make_transcript_reference(event).event_type == "control_request_resolved"


def test_direct_control_request_resolved_event_redacts_updated_input() -> None:
    event = ControlRequestResolvedEvent(
        eventId="evt-resolved-redacted",
        seq=4,
        ts=126,
        sessionKey="agent:main:app:default",
        turnId="turn-redacted",
        requestId="req-redacted",
        decision="approved",
        updatedInput=LEAKY_CONTROL_INPUT,
    )

    assert event.updated_input == {
        "promptPreview": "[redacted-prompt]",
        "logsPreview": "[redacted-output]",
        "logPreview": "[redacted-output]",
        "stdoutPreview": "[redacted-output]",
        "stderrPreview": "[redacted-output]",
        "outputPreview": "[redacted-output]",
        "Authorization": "[redacted]",
        "api_key": "[redacted]",
        "serviceRoleKey": "[redacted]",
        "serviceRoleKEY": "[redacted]",
        "service-role-key": "[redacted]",
        "apiKey": "[redacted]",
        "secretKey": "[redacted]",
        "privateKey": "[redacted]",
        "authToken": "[redacted]",
        "sessionCookie": "[redacted]",
        "clientCredential": "[redacted]",
        "path": "[redacted-path]",
        "workspacePath": "[redacted-path]",
    }
    _assert_control_input_is_redacted(event.model_dump(by_alias=True))


def test_control_request_cancelled_event_accepts_typescript_alias_shape() -> None:
    event = ControlRequestCancelledEvent(
        eventId="evt-cancelled",
        seq=4,
        ts=126,
        sessionKey="agent:main:app:default",
        requestId="req-3",
        reason="turn_cancelled",
    )

    assert event.type == "control_request_cancelled"
    assert event.request_id == "req-3"
    assert event.reason == "turn_cancelled"
    assert make_transcript_reference(event).model_dump(by_alias=True) == {
        "kind": "control_event",
        "ts": 126,
        "turnId": None,
        "seq": 4,
        "eventId": "evt-cancelled",
        "eventType": "control_request_cancelled",
    }


def test_control_request_timed_out_event_accepts_typescript_alias_shape() -> None:
    event = ControlRequestTimedOutEvent(
        eventId="evt-timeout",
        seq=5,
        ts=127,
        sessionKey="agent:main:app:default",
        requestId="req-4",
    )

    assert event.type == "control_request_timed_out"
    assert event.request_id == "req-4"
    assert make_transcript_reference(event).model_dump(by_alias=True) == {
        "kind": "control_event",
        "ts": 127,
        "turnId": None,
        "seq": 5,
        "eventId": "evt-timeout",
        "eventType": "control_request_timed_out",
    }


def test_typescript_lifecycle_transcript_refs_are_monotonic_and_idempotent() -> None:
    events = _load_control_fixture("typescript_control_lifecycle_refs.jsonl")

    assert [event.type for event in events] == [
        "control_request_created",
        "control_request_resolved",
        "control_request_cancelled",
        "control_request_timed_out",
    ]
    assert [event.seq for event in events] == [1, 2, 3, 4]
    assert len({event.event_id for event in events}) == 4
    assert len({event.idempotency_key for event in events}) == 4
    assert [
        make_transcript_reference(event).model_dump(by_alias=True)
        for event in events
    ] == [
        {
            "kind": "control_event",
            "ts": 10,
            "turnId": "turn-control-fixture",
            "seq": 1,
            "eventId": "ctrl-created-1",
            "eventType": "control_request_created",
        },
        {
            "kind": "control_event",
            "ts": 11,
            "turnId": "turn-control-fixture",
            "seq": 2,
            "eventId": "ctrl-resolved-1",
            "eventType": "control_request_resolved",
        },
        {
            "kind": "control_event",
            "ts": 12,
            "turnId": "turn-control-fixture",
            "seq": 3,
            "eventId": "ctrl-cancelled-1",
            "eventType": "control_request_cancelled",
        },
        {
            "kind": "control_event",
            "ts": 13,
            "turnId": "turn-control-fixture",
            "seq": 4,
            "eventId": "ctrl-timeout-1",
            "eventType": "control_request_timed_out",
        },
    ]
