from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from magi_agent.transport.sse import InMemorySseWriter


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "frontend_ts_compatibility"
    / "python_adk_run.json"
)

PRIVATE_MARKERS = (
    "raw adk provider detail",
    "private active snapshot",
    "private child output",
    "private mission payload",
    "private goal payload",
    "private browser cdp",
    "private tool arguments",
    "fixture private token",
)

PRIVATE_ONLY_KEYS = {
    "arguments",
    "cdpEndpoint",
    "content",
    "finalText",
    "hiddenReasoning",
    "patch",
    "prompt",
    "raw",
    "rawAdkEvent",
    "rawArguments",
    "rawBoard",
    "rawCronPayload",
    "rawGoalPayload",
    "rawInput",
    "rawLedgerState",
    "rawMissionPayload",
    "rawOutput",
    "rawPlan",
    "rawPrompt",
    "rawProviderError",
    "rawTranscript",
    "stopReason",
    "updatedInput",
}

REQUIRED_EVENT_TYPES = {
    "turn_start",
    "turn_phase",
    "tool_start",
    "tool_progress",
    "tool_end",
    "source_inspected",
    "rule_check",
    "task_board",
    "runtime_trace",
    "child_abort",
    "document_draft",
    "turn_end",
}

REQUIRED_SANITIZED_EVENT_TYPES = REQUIRED_EVENT_TYPES | {
    "deterministic_fallback",
    "response_clear",
    "text_delta",
}


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _data_payloads(sse_body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in sse_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _sse_payloads(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    writer = InMemorySseWriter()
    for event in events:
        writer.agent(event)
    return _data_payloads(writer.body), writer.body


def _walk(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        found: list[tuple[tuple[str, ...], str]] = []
        for index, item in enumerate(value):
            found.extend(_walk(item, (*path, str(index))))
        return found
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            found.extend(_walk(item, (*path, key)))
        return found
    return []


def test_fixture_is_local_fake_only_and_default_off() -> None:
    fixture = _load_fixture()

    assert fixture["schemaVersion"] == "frontend-ts-compatibility.fixture.v1"
    assert fixture["providerKind"] == "local_fake_python_adk"
    assert fixture["fixtureOnly"] is True
    assert fixture["liveProviderInvoked"] is False
    assert fixture["adkRunnerInvoked"] is False
    assert fixture["liveToolsInvoked"] is False
    assert fixture["productionRoutingActivated"] is False
    assert fixture["defaultOff"] is True


def test_fixture_covers_required_frontend_public_event_contract() -> None:
    fixture = _load_fixture()
    event_types = {event["type"] for event in fixture["events"]}

    assert REQUIRED_EVENT_TYPES <= event_types
    assert any(event["type"] == "tool_progress" for event in fixture["events"])
    assert any(
        event["type"] == "source_inspected"
        and event["source"]["kind"] in {"browser", "web_fetch", "external_doc"}
        for event in fixture["events"]
    )
    assert any(
        event["type"] == "rule_check"
        and event["ruleId"] == "claim-citation-gate"
        for event in fixture["events"]
    )
    assert any(
        event["type"] == "child_abort"
        and event.get("source") == "default_off_child_blocked"
        for event in fixture["events"]
    )


def test_fixture_events_round_trip_through_python_sse_sanitizer() -> None:
    fixture = _load_fixture()

    payloads, body = _sse_payloads(fixture["events"])
    payload_types = {payload["type"] for payload in payloads}

    assert REQUIRED_SANITIZED_EVENT_TYPES <= payload_types
    assert payloads == fixture["expectedSanitizedEvents"]
    assert any(
        payload["type"] == "source_inspected"
        and payload["source"]["sourceId"] == "src-python-adk-docs-1"
        and payload["source"]["contentHash"].startswith("receipt:sha256:")
        for payload in payloads
    )
    assert any(
        payload["type"] == "turn_end"
        and payload["status"] == "committed"
        and payload["usage"] == {
            "inputTokens": 128,
            "outputTokens": 37,
            "costUsd": 0.0042,
        }
        for payload in payloads
    )
    assert all("content" not in payload for payload in payloads if payload["type"] == "document_draft")
    assert all("raw" not in payload.get("source", {}) for payload in payloads)
    for marker in PRIVATE_MARKERS:
        assert marker not in body


def test_public_events_keep_private_payload_attempts_in_dropped_fields_only() -> None:
    fixture = _load_fixture()

    for path, value in _walk(fixture["events"]):
        if not any(marker in value for marker in PRIVATE_MARKERS):
            continue
        assert path[-1] in PRIVATE_ONLY_KEYS, ".".join(path)


def test_malicious_private_payload_attempts_are_explicitly_drop_only(monkeypatch) -> None:
    # The drop-only projection is the MAGI_STREAM_THINKING=OFF path; that flag is
    # now profile-default-ON (thought/thinking pass-through), so pin it OFF here.
    monkeypatch.setenv("MAGI_STREAM_THINKING", "0")
    fixture = _load_fixture()
    attempts = fixture["maliciousPrivatePayloadAttempts"]

    assert len(attempts) >= 3
    for attempt in attempts:
        assert attempt["expectedDrop"] is True
        encoded = json.dumps(attempt, sort_keys=True)
        assert any(marker in encoded for marker in PRIVATE_MARKERS)

    payloads, body = _sse_payloads(attempts)
    assert payloads == []
    for marker in PRIVATE_MARKERS:
        assert marker not in body


def test_turn_end_usage_is_sanitized_public_accounting_only() -> None:
    fixture = _load_fixture()
    turn_end = next(event for event in fixture["events"] if event["type"] == "turn_end")

    assert turn_end["status"] == "committed"
    assert turn_end["usage"] == {
        "inputTokens": 128,
        "outputTokens": 37,
        "costUsd": 0.0042,
    }
    encoded_usage = json.dumps(turn_end["usage"], sort_keys=True)
    assert not any(marker in encoded_usage for marker in PRIVATE_MARKERS)
