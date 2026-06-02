from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MATRIX_PATH = Path(__file__).parent / "fixtures/parity/runtime_heartbeat_matrix.json"

REQUIRED_TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "fixtureId",
    "scope",
    "track",
    "auditDate",
    "defaultOff",
    "noLiveExecution",
    "trafficAttached",
    "publicSseHeartbeatMonitorUnchanged",
    "expectedConcepts",
    "hermesPatternsReviewed",
    "rows",
}
REQUIRED_ROW_IDS = (
    "durable_run_lease_ownership",
    "durable_heartbeat_receipt_records",
    "stale_run_detector",
    "inactivity_timeout_recorded_activity",
    "restart_resume_decision_record",
    "no_agent_watchdog_contract",
    "scheduler_tick_lock_overlap_prevention",
    "model_visible_heartbeat_projection_boundary",
    "fail_closed_recovery_stale_write_child_mission_runs",
    "public_sse_heartbeat_monitor_separation",
)
EXPECTED_CONCEPTS = {
    "RunLease",
    "HeartbeatReceipt",
    "ActivityReceipt",
    "StaleRunVerdict",
    "ResumeDecision",
    "NoAgentWatchdog",
}
HERMES_PATTERNS = {
    "cron storage",
    "gateway ticker",
    "no-agent watchdog",
    "inactivity timeout",
    "restart resume",
    "cron toolset restrictions",
}
REQUIRED_ROW_FIELDS = {
    "id",
    "targetBehavior",
    "hermesPatterns",
    "openMagiConcepts",
    "owner",
    "alreadyCovered",
    "missingImplementation",
    "activationRequired",
    "defaultOff",
    "trafficAttached",
    "liveAuthority",
    "publicSseHeartbeatMonitorImpact",
    "notes",
}
ALLOWED_OWNERS = {
    "runtime_lease_contract",
    "runtime_receipt_contract",
    "runtime_stale_detector_contract",
    "runtime_resume_contract",
    "runtime_watchdog_contract",
    "runtime_scheduler_contract",
    "runtime_projection_contract",
    "runtime_recovery_contract",
    "public_ui_heartbeat_boundary",
}
FORBIDDEN_TEXT = (
    "HEART" + "BEAT.md",
    "de" + "ploy",
    "k" + "8s",
    "sec" + "ret",
    "live scheduler " + "enabled",
    "mission runtime " + "enabled",
)


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for nested in value.values():
            result.extend(_strings(nested))
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            result.extend(_strings(nested))
        return result
    return []


def test_runtime_heartbeat_matrix_has_locked_scope_and_rows() -> None:
    matrix = _load_matrix()
    row_ids = [row["id"] for row in matrix["rows"]]

    assert set(matrix) == REQUIRED_TOP_LEVEL_FIELDS
    assert matrix["schemaVersion"] == "runtimeHeartbeatMatrix.v1"
    assert matrix["fixtureId"] == "runtime_heartbeat_pr0_matrix_20260527"
    assert matrix["scope"] == "docs-tests-only"
    assert matrix["track"] == "magi-agent-runtime-heartbeat-lease-pr0"
    assert matrix["auditDate"] == "2026-05-27"
    assert row_ids == list(REQUIRED_ROW_IDS)
    assert len(row_ids) == len(set(row_ids))


def test_hermes_patterns_and_openmagi_concepts_are_explicit() -> None:
    matrix = _load_matrix()

    assert set(matrix["expectedConcepts"]) == EXPECTED_CONCEPTS
    assert set(matrix["hermesPatternsReviewed"]) == HERMES_PATTERNS

    row_concepts = {
        concept
        for row in matrix["rows"]
        for concept in row["openMagiConcepts"]
    }
    assert EXPECTED_CONCEPTS <= row_concepts

    row_patterns = {
        pattern
        for row in matrix["rows"]
        for pattern in row["hermesPatterns"]
    }
    assert HERMES_PATTERNS <= row_patterns


def test_all_rows_have_explicit_owner_default_off_status_and_gap_fields() -> None:
    for row in _load_matrix()["rows"]:
        assert set(row) == REQUIRED_ROW_FIELDS
        assert row["owner"] in ALLOWED_OWNERS
        assert isinstance(row["alreadyCovered"], bool)
        assert isinstance(row["missingImplementation"], list)
        assert row["missingImplementation"]
        assert isinstance(row["activationRequired"], bool)
        assert row["defaultOff"] is True
        assert row["trafficAttached"] is False
        assert row["liveAuthority"] is False
        assert row["publicSseHeartbeatMonitorImpact"] in {
            "none",
            "separate-contract-only",
        }

        if row["id"] == "public_sse_heartbeat_monitor_separation":
            assert row["alreadyCovered"] is True
            assert row["activationRequired"] is False
        else:
            assert row["alreadyCovered"] is False
            assert row["activationRequired"] is True


def test_matrix_preserves_public_sse_heartbeat_monitor_boundary() -> None:
    matrix = _load_matrix()
    rows = {row["id"]: row for row in matrix["rows"]}
    public_boundary = rows["public_sse_heartbeat_monitor_separation"]

    assert matrix["publicSseHeartbeatMonitorUnchanged"] is True
    assert (
        public_boundary["publicSseHeartbeatMonitorImpact"]
        == "separate-contract-only"
    )
    assert public_boundary["alreadyCovered"] is True
    assert public_boundary["activationRequired"] is False
    for row in matrix["rows"]:
        if row["id"] != "public_sse_heartbeat_monitor_separation":
            assert row["publicSseHeartbeatMonitorImpact"] == "none"


def test_heartbeat_receipts_do_not_prove_activity_without_lease_validation() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    heartbeat_text = " ".join(_strings(rows["durable_heartbeat_receipt_records"])).lower()
    inactivity_text = " ".join(
        _strings(rows["inactivity_timeout_recorded_activity"])
    ).lower()
    stale_detector_text = " ".join(_strings(rows["stale_run_detector"])).lower()

    assert "heartbeat receipts alone cannot reset inactivity timers" in heartbeat_text
    assert "heartbeat receipts alone cannot prove activity" in inactivity_text
    assert "forged" in heartbeat_text
    assert "unauthenticated" in heartbeat_text
    assert "wrong-lease" in heartbeat_text
    assert "lease-owner validation" in heartbeat_text
    assert "wrong-lease heartbeat receipts cannot prove activity" in stale_detector_text


def test_matrix_has_no_live_authority_or_disallowed_authority_sources() -> None:
    matrix = _load_matrix()
    assert matrix["defaultOff"] is True
    assert matrix["noLiveExecution"] is True
    assert matrix["trafficAttached"] is False

    haystack = "\n".join(_strings(matrix))
    lowered = haystack.lower()
    for forbidden in FORBIDDEN_TEXT:
        assert forbidden.lower() not in lowered
