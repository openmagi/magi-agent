from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json

from magi_agent.runtime.events import (
    normalized_events_to_agent_events,
    runtime_heartbeat_status_event,
    runtime_resume_status_event,
    runtime_stale_status_event,
    runtime_watchdog_status_event,
)
from magi_agent.runtime.heartbeat_contract import (
    HeartbeatReceipt,
    ResumeDecision,
    StaleRunVerdict,
    heartbeat_receipt_digest,
)
from magi_agent.runtime.no_agent_watchdog import (
    NoAgentWatchdogRequest,
    evaluate_no_agent_watchdog,
)
from magi_agent.runtime.work_console_snapshot import build_work_console_snapshot
from magi_agent.transport.sse import InMemorySseWriter


NOW = datetime(2026, 5, 28, 21, 5, tzinfo=UTC)
ACTIVITY_DIGEST = "sha256:activity:" + "a" * 64


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _data_payloads(sse_body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in sse_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _heartbeat() -> HeartbeatReceipt:
    payload: dict[str, object] = {
        "schemaVersion": "openmagi.runtime.heartbeat.receipt.v1",
        "heartbeatId": "heartbeat:alpha",
        "runId": "run:alpha",
        "leaseId": "lease:alpha",
        "sequence": 7,
        "emittedAt": NOW.isoformat().replace("+00:00", "Z"),
        "lastActivityAt": (NOW - timedelta(seconds=30)).isoformat().replace(
            "+00:00",
            "Z",
        ),
        "lastActivityReceiptDigest": ACTIVITY_DIGEST,
        "lastEventId": "event:alpha",
        "lastReceiptId": "activity:alpha",
        "phase": "running",
        "activeToolName": "FileRead",
        "activeChildId": "child:alpha",
        "pendingApprovalIds": ["approval:alpha"],
        "publicSafe": True,
    }
    payload["digest"] = heartbeat_receipt_digest(payload)
    return HeartbeatReceipt.model_validate(payload)


def _stale_verdict(heartbeat: HeartbeatReceipt) -> StaleRunVerdict:
    return StaleRunVerdict(
        verdict="lease_expired",
        runId="run:alpha",
        checkedAt=NOW + timedelta(seconds=10),
        reasonCodes=("lease_expired", "worker_lost"),
        heartbeatDigest=heartbeat.digest,
        activityDigest=ACTIVITY_DIGEST,
        leaseDigest=_digest_text("lease:alpha"),
        metadata={"source": "stale_detector"},
    )


def _resume_decision(verdict: StaleRunVerdict) -> ResumeDecision:
    return ResumeDecision(
        decision="retry_from_checkpoint",
        runId="run:alpha",
        decidedAt=NOW + timedelta(seconds=20),
        reasonCodes=("checkpoint_available_for_stale_run",),
        checkpointDigest=_digest_text("checkpoint:turn-17"),
        verdictDigest=_digest_text(str(verdict.public_projection())),
        metadata={"source": "resume_decision", "runnerInvoked": False},
    )


def _watchdog_decision():
    return evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:alpha",
            tickId="tick:alpha",
            jobRef="job:runtime-heartbeat",
            stdout="alert from /Users/kevin/private TOKEN=secret",
            exitCode=0,
            wakeAgent=False,
        )
    )


def _runtime_status_payloads() -> list[dict[str, object]]:
    heartbeat = _heartbeat()
    stale = _stale_verdict(heartbeat)
    resume = _resume_decision(stale)
    watchdog = _watchdog_decision()
    normalized = [
        runtime_heartbeat_status_event(
            event_id="event:runtime-heartbeat",
            turn_id="turn:alpha",
            ts=1,
            heartbeat=heartbeat,
        ),
        runtime_stale_status_event(
            event_id="event:runtime-stale",
            turn_id="turn:alpha",
            ts=2,
            stale_verdict=stale,
        ),
        runtime_resume_status_event(
            event_id="event:runtime-resume",
            turn_id="turn:alpha",
            ts=3,
            resume_decision=resume,
        ),
        runtime_watchdog_status_event(
            event_id="event:runtime-watchdog",
            turn_id="turn:alpha",
            ts=4,
            watchdog_decision=watchdog,
        ),
    ]
    writer = InMemorySseWriter()
    for event in normalized_events_to_agent_events(normalized):
        writer.agent(event)
    return _data_payloads(writer.body)


def test_runtime_status_builders_project_digest_safe_agent_events_in_order() -> None:
    payloads = _runtime_status_payloads()
    rendered = json.dumps(payloads, sort_keys=True)

    assert [payload["type"] for payload in payloads] == [
        "runtime_heartbeat_status",
        "runtime_stale_status",
        "runtime_resume_status",
        "runtime_watchdog_status",
    ]
    assert payloads[0]["heartbeatDigest"].startswith("sha256:heartbeat:")
    assert payloads[0]["leaseDigest"] == _digest_text("lease:alpha")
    assert payloads[0]["sequence"] == 7
    assert payloads[0]["liveAuthority"] is False
    assert payloads[0]["trafficAttached"] is False
    assert payloads[1]["status"] == "lease_expired"
    assert payloads[1]["heartbeatDigest"] == payloads[0]["heartbeatDigest"]
    assert payloads[1]["reasonCodeDigests"][0].startswith("sha256:")
    assert payloads[2]["decision"] == "retry_from_checkpoint"
    assert payloads[2]["checkpointDigest"] == _digest_text("checkpoint:turn-17")
    assert payloads[2]["runnerInvoked"] is False
    assert payloads[3]["status"] == "alert_output"
    assert payloads[3]["alertRequired"] is True
    assert payloads[3]["wakeAgent"] is False
    assert payloads[3]["schedulerAttached"] is False
    assert payloads[3]["modelCallEnabled"] is False
    assert payloads[3]["channelDeliveryEnabled"] is False

    for forbidden in (
        "lease:alpha",
        "worker:runtime",
        "checkpoint:turn-17",
        "watchdog:alpha",
        "tick:alpha",
        "job:runtime-heartbeat",
        "stdoutPreview",
        "/Users/kevin",
        "TOKEN=secret",
        "FileRead",
        "child:alpha",
        "approval:alpha",
    ):
        assert forbidden not in rendered


def test_runtime_status_sse_sanitizer_drops_raw_fields_and_forces_false_authority() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "runtime_watchdog_status",
            "eventId": "event:unsafe-watchdog",
            "turnId": "turn:alpha",
            "status": "alert_output",
            "alertKind": "output",
            "alertRequired": True,
            "watchdogId": "watchdog:raw",
            "tickId": "tick:raw",
            "jobRef": "job:raw",
            "stdoutPreview": "secret from /Users/kevin/private TOKEN=secret",
            "modelCallEnabled": True,
            "toolExecutionEnabled": True,
            "channelDeliveryEnabled": True,
            "workspaceMutationEnabled": True,
            "memoryWriteEnabled": True,
            "schedulerAttached": True,
            "wakeAgent": True,
            "liveAuthority": True,
            "trafficAttached": True,
        }
    )

    payload = _data_payloads(writer.body)[0]
    rendered = json.dumps(payload, sort_keys=True)

    assert payload == {
        "type": "runtime_watchdog_status",
        "eventId": "event:unsafe-watchdog",
        "turnId": "turn:alpha",
        "status": "alert_output",
        "alertKind": "output",
        "alertRequired": True,
        "publicSafe": True,
        "liveAuthority": False,
        "trafficAttached": False,
        "wakeAgent": False,
        "schedulerAttached": False,
        "modelCallEnabled": False,
        "providerCallEnabled": False,
        "toolExecutionEnabled": False,
        "channelDeliveryEnabled": False,
        "workspaceMutationEnabled": False,
        "memoryWriteEnabled": False,
        "productionWritesEnabled": False,
        "runnerInvoked": False,
        "resumeExecutionAllowed": False,
    }
    for forbidden in (
        "watchdog:raw",
        "tick:raw",
        "job:raw",
        "stdoutPreview",
        "/Users/kevin",
        "TOKEN=secret",
    ):
        assert forbidden not in rendered


def test_runtime_statuses_rebuild_reconnect_snapshot_rows_deterministically() -> None:
    payloads = _runtime_status_payloads()
    events = [
        {"type": "turn_start", "eventId": "event:turn", "turnId": "turn:alpha"},
        *payloads,
        payloads[1],
    ]

    projection = build_work_console_snapshot(events).public_projection()
    rebuilt = build_work_console_snapshot(events).public_projection()

    assert projection == rebuilt
    assert projection["deduplicatedEventCount"] == 1
    assert [row["type"] for row in projection["runtimeStatuses"]] == [
        "runtime_heartbeat_status",
        "runtime_stale_status",
        "runtime_resume_status",
        "runtime_watchdog_status",
    ]
    assert projection["runtimeStatuses"][0]["sequence"] == 7
    assert projection["runtimeStatuses"][1]["status"] == "lease_expired"
    assert projection["runtimeStatuses"][2]["decision"] == "retry_from_checkpoint"
    assert projection["runtimeStatuses"][3]["alertRequired"] is True
    assert "stdoutPreview" not in json.dumps(projection, sort_keys=True)
