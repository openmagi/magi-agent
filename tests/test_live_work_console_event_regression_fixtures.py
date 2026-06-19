from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from magi_agent.runtime.work_console_snapshot import (
    build_work_console_snapshot,
)
from magi_agent.transport.sse import InMemorySseWriter


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "live_work_console_event_parity"
    / "regression_fixtures.json"
)

REQUIRED_CASE_IDS = {
    "raw_adk_event_payload_leak",
    "hidden_reasoning_leak",
    "raw_tool_args_results_leak",
    "source_snapshot_leak",
    "private_path_leak",
    "source_inspected_without_receipt",
    "child_completed_without_envelope",
    "unsupported_event_marked_supported",
    "unbounded_heartbeat_progress_spam",
    "projection_digest_mismatch",
}


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _events(case: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    for item in case.get("events", ()):
        if not isinstance(item, Mapping):
            continue
        repeat = item.get("repeat")
        event = item.get("event")
        if isinstance(repeat, int) and isinstance(event, Mapping):
            for index in range(max(0, repeat)):
                events.append(
                    {
                        str(key): _format_value(value, index=index)
                        for key, value in event.items()
                    }
                )
        else:
            events.append({str(key): value for key, value in item.items()})
    return tuple(events)


def _format_value(value: object, *, index: int) -> object:
    if isinstance(value, str):
        return value.format(index=index)
    if isinstance(value, list):
        return [_format_value(item, index=index) for item in value]
    if isinstance(value, dict):
        return {key: _format_value(nested, index=index) for key, nested in value.items()}
    return value


def _sse_payloads(events: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    writer = InMemorySseWriter()
    for event in events:
        writer.agent(dict(event))
    return [
        json.loads(line.removeprefix("data: "))
        for line in writer.body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _case_by_id(fixture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cases = fixture["cases"]
    assert isinstance(cases, list)
    return {
        case["id"]: case
        for case in cases
        if isinstance(case, Mapping) and isinstance(case.get("id"), str)
    }


def test_regression_fixture_manifest_covers_required_cases() -> None:
    fixture = _load_fixture()
    cases = _case_by_id(fixture)

    assert fixture["schemaVersion"] == "liveWorkConsoleRegressionFixtures.v1"
    assert set(cases) == REQUIRED_CASE_IDS


def test_regression_fixtures_do_not_project_private_or_raw_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Hosted/default posture guard: with MAGI_STREAM_THINKING OFF no thought text
    # (e.g. the SECRET_DO_NOT_PROJECT marker) may reach the public dump. Pin the
    # flag OFF explicitly so a leaked env default (the local serve overlay enables
    # streaming thinking on the user's own trusted machine) can't make this guard
    # falsely fail; the ON path is covered by the dedicated thinking_delta tests.
    monkeypatch.delenv("MAGI_STREAM_THINKING", raising=False)
    fixture = _load_fixture()
    blocked_fragments = tuple(fixture["blockedPublicFragments"])

    for case in _case_by_id(fixture).values():
        events = _events(case)
        public_dump = _dump(
            {
                "sse": _sse_payloads(events),
                "snapshot": build_work_console_snapshot(events).public_projection(),
            }
        )

        for fragment in blocked_fragments:
            assert fragment not in public_dump, case["id"]


def test_source_and_child_receipt_regressions_project_blocked_not_completed_work() -> None:
    cases = _case_by_id(_load_fixture())

    source_payloads = _sse_payloads(_events(cases["source_inspected_without_receipt"]))
    assert source_payloads == [
        {
            "type": "runtime_trace",
            "eventId": "evt-source-missing-ref:blocked",
            "turnId": "turn-source-missing-ref",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "source_inspected omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        }
    ]
    source_snapshot = build_work_console_snapshot(
        _events(cases["source_inspected_without_receipt"])
    ).public_projection()
    assert source_snapshot["evidenceCounts"] == {"evidenceRefs": 0, "sources": 0}

    child_payloads = _sse_payloads(_events(cases["child_completed_without_envelope"]))
    assert child_payloads == [
        {
            "type": "runtime_trace",
            "eventId": "evt-child-missing-envelope:blocked",
            "turnId": "turn-child-missing-envelope",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "child_completed omitted: missing public child receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        }
    ]
    child_snapshot = build_work_console_snapshot(
        _events(cases["child_completed_without_envelope"])
    ).public_projection()
    assert child_snapshot["subagents"] == []


def test_unsupported_event_marked_supported_fixture_is_rejected() -> None:
    case = _case_by_id(_load_fixture())["unsupported_event_marked_supported"]
    row = case["matrixRow"]

    assert _invalid_supported_row_reasons(row) == [
        "supported row requires producer evidence",
        "supported row requires sanitizer evidence",
    ]


def test_unbounded_spam_fixture_remains_bounded() -> None:
    case = _case_by_id(_load_fixture())["unbounded_heartbeat_progress_spam"]
    projection = build_work_console_snapshot(_events(case)).public_projection()

    assert projection["heartbeatElapsedMs"] == 1000000
    assert len(projection["activeTools"]) == 1
    assert projection["processedEventCount"] == 1000
    assert projection["projectionDigest"].startswith("sha256:")


def test_projection_digest_mismatch_fixture_detects_drift() -> None:
    case = _case_by_id(_load_fixture())["projection_digest_mismatch"]
    projection = build_work_console_snapshot(_events(case)).public_projection()

    assert projection["projectionDigest"] != case["expectedProjectionDigest"]


def _invalid_supported_row_reasons(row: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if row.get("status") != "supported":
        return reasons
    producer = row.get("pythonProducer")
    if not isinstance(producer, Mapping) or not producer.get("evidence"):
        reasons.append("supported row requires producer evidence")
    sanitizer = row.get("pythonSseSanitizer")
    if not isinstance(sanitizer, Mapping) or not sanitizer.get("evidence"):
        reasons.append("supported row requires sanitizer evidence")
    return reasons
