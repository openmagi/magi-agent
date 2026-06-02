from __future__ import annotations

import json
from pathlib import Path


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "live_work_console_event_parity"
    / "matrix.json"
)
TS_PUBLIC_EVENT_MATRIX = (
    Path(__file__).parent / "fixtures" / "public_event_parity" / "matrix.json"
)

VALID_STATUSES = {"supported", "projected_alias", "deferred", "unsupported"}
SUPPORTED_STATUSES = {"supported", "projected_alias"}
DEFERRED_STATUSES = {"deferred", "unsupported"}
PRODUCER_STATUSES = {"producer", "fixture_producer"}
TS_CLASSIFICATION_STATUS_ALLOWLIST = {
    "supported_now": {"supported"},
    "projected_alias": {"projected_alias"},
    "default_off_boundary_only": {"deferred", "unsupported"},
    "blocked_until_gate": {"deferred", "unsupported"},
    "intentionally_unsupported": {"unsupported"},
}
PRIVATE_FRAGMENTS = (
    "/Users/",
    "/data/bots/",
    "/workspace/",
    "/workspace/private",
    "Bearer ",
    "Authorization:",
    "auth token",
    "access token",
    "sk-",
    "ghp_",
    "postgres://",
    "postgresql://",
    "supabase",
    "private prompt",
    "private payload",
    "raw prompt",
    "raw payload",
    "session key",
    "session secret",
)
PRIVATE_KEY_TERMS = (
    "raw",
    "private",
    "authorization",
    "authtoken",
    "authsecret",
    "authkey",
    "credential",
    "secret",
    "token",
    "sessionkey",
    "sessionsecret",
)
REQUIRED_AUDIT_AREAS = {
    "turn_lifecycle",
    "heartbeat",
    "retry_model_fallback",
    "tool_start",
    "tool_progress",
    "tool_end",
    "tool_error",
    "tool_blocked",
    "source_inspected",
    "rule_citation_gate",
    "runtime_trace",
    "task_board",
    "child_spawn_background",
    "control_ask_plan",
    "mission_cron_goal",
    "document_draft",
    "patch_preview",
    "browser_frame",
    "unsupported_deferred",
}


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_live_work_console_gap_matrix_covers_every_ts_public_event_family() -> None:
    matrix = _load_json(FIXTURE)
    ts_matrix = _load_json(TS_PUBLIC_EVENT_MATRIX)

    rows = matrix["rows"]
    assert isinstance(rows, list)

    covered_event_types = {
        event_type
        for row in rows
        for event_type in row.get("tsEventTypes", ())
    }
    ts_event_types = {row["eventType"] for row in ts_matrix["rows"]}

    assert matrix["schemaVersion"] == "liveWorkConsoleEventParityGapMatrix.v1"
    assert {row["auditArea"] for row in rows} >= REQUIRED_AUDIT_AREAS
    assert covered_event_types == ts_event_types

    for row in rows:
        assert row["status"] in VALID_STATUSES, row["eventFamily"]


def test_matrix_row_status_matches_ts_public_event_classification() -> None:
    matrix = _load_json(FIXTURE)
    ts_matrix = _load_json(TS_PUBLIC_EVENT_MATRIX)
    classifications = {
        row["eventType"]: row["classification"]
        for row in ts_matrix["rows"]
    }

    for row in matrix["rows"]:
        allowed_statuses = {
            status
            for event_type in row["tsEventTypes"]
            for status in TS_CLASSIFICATION_STATUS_ALLOWLIST[
                classifications[event_type]
            ]
        }
        assert row["status"] in allowed_statuses, row["eventFamily"]

        if row["status"] in SUPPORTED_STATUSES:
            unsupported_event_types = [
                event_type
                for event_type in row["tsEventTypes"]
                if classifications[event_type] != (
                    "projected_alias"
                    if row["status"] == "projected_alias"
                    else "supported_now"
                )
            ]
            assert unsupported_event_types == [], row["eventFamily"]


def test_supported_rows_have_producer_or_explicit_fixture_producer() -> None:
    matrix = _load_json(FIXTURE)

    for row in matrix["rows"]:
        if row["status"] not in SUPPORTED_STATUSES:
            continue

        producer = row["pythonProducer"]
        assert producer["status"] in PRODUCER_STATUSES, row["eventFamily"]
        assert producer["evidence"], row["eventFamily"]


def test_child_spawn_background_producer_row_does_not_overclaim_child_control_events() -> None:
    matrix = _load_json(FIXTURE)
    rows = {row["eventFamily"]: row for row in matrix["rows"]}

    spawn_row = rows["child_spawn_background_supported_core"]
    control_row = rows["child_tool_permission_fixture_projection"]

    assert set(spawn_row["tsEventTypes"]) == {
        "spawn_started",
        "spawn_result",
        "background_task",
    }
    assert spawn_row["pythonProducer"]["status"] == "producer"
    assert "openmagi_core_agent/runtime/child_event_projection.py" in (
        spawn_row["pythonProducer"]["evidence"]
    )

    assert set(control_row["tsEventTypes"]) == {
        "child_tool_request",
        "child_permission_decision",
    }
    assert control_row["pythonProducer"]["status"] == "fixture_producer"
    assert "openmagi_core_agent/runtime/child_event_projection.py" not in (
        control_row["pythonProducer"]["evidence"]
    )


def test_deferred_and_unsupported_rows_record_reason_and_follow_up() -> None:
    matrix = _load_json(FIXTURE)

    for row in matrix["rows"]:
        if row["status"] not in DEFERRED_STATUSES:
            continue

        assert row["reason"], row["eventFamily"]
        assert row["followUp"], row["eventFamily"]


def test_live_supported_rows_require_sanitization_test_evidence() -> None:
    matrix = _load_json(FIXTURE)

    for row in matrix["rows"]:
        sanitizer = row["pythonSseSanitizer"]
        assert isinstance(row["liveSupported"], bool), row["eventFamily"]
        if not row["liveSupported"]:
            continue

        assert sanitizer["status"] == "covered", row["eventFamily"]
        assert sanitizer["evidence"], row["eventFamily"]


def test_matrix_keeps_pr0_live_authority_boundaries_default_off_and_public_safe() -> None:
    matrix = _load_json(FIXTURE)
    rendered = json.dumps(matrix, sort_keys=True)

    assert set(matrix["boundary"].values()) == {False}
    for fragment in PRIVATE_FRAGMENTS:
        assert fragment not in rendered
    assert _unsafe_keys(matrix) == []


def _unsafe_keys(value: object, path: tuple[str, ...] = ()) -> list[str]:
    if isinstance(value, dict):
        unsafe = []
        for key, nested in value.items():
            normalized = "".join(char for char in key.lower() if char.isalnum())
            if any(term in normalized for term in PRIVATE_KEY_TERMS):
                unsafe.append(".".join((*path, key)))
            unsafe.extend(_unsafe_keys(nested, (*path, key)))
        return unsafe
    if isinstance(value, list):
        unsafe = []
        for index, nested in enumerate(value):
            unsafe.extend(_unsafe_keys(nested, (*path, str(index))))
        return unsafe
    return []
