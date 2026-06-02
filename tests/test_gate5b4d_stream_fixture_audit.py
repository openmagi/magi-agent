from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openmagi_core_agent.shadow.gate5b4d_stream_fixture_audit import (
    audit_gate5b4d_stream_fixture,
    load_gate5b4d_stream_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ts_parity_replay"


REQUIRED_GAPS = {
    "tool",
    "control",
    "child",
    "source",
    "browser",
    "artifact",
    "intermediate",
    "final",
    "error",
    "provider_fallback",
    "temporal_progress",
    "channel_delivery_absent",
}


def _agent_payloads(sse_body: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in sse_body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payload = json.loads(line.removeprefix("data: "))
        if isinstance(payload, dict) and payload.get("type"):
            payloads.append(payload)
    return payloads


def test_gate5b4d_stream_fixture_audit_covers_required_projection_gaps() -> None:
    fixture = load_gate5b4d_stream_fixture(
        "gate5b4d_stream_coverage_golden.json",
        fixture_root=FIXTURES,
    )

    audit = audit_gate5b4d_stream_fixture(fixture)

    assert audit.fixture_id == "gate5b4d-stream-coverage-golden"
    assert audit.local_diagnostic is True
    assert audit.covered_gap_ids == tuple(sorted(REQUIRED_GAPS))
    assert audit.missing_gap_ids == ()
    assert audit.safe_agent_event_types == (
        "tool_start",
        "tool_progress",
        "tool_end",
        "control_event",
        "control_event",
        "control_event",
        "control_event",
        "spawn_started",
        "child_started",
        "child_progress",
        "child_completed",
        "child_failed",
        "child_cancelled",
        "source_inspected",
        "research_artifact_delta",
        "rule_check",
        "browser_frame",
        "document_draft",
        "patch_preview",
        "response_clear",
        "turn_phase",
        "llm_progress",
        "text_delta",
        "turn_end",
        "error",
        "model_fallback",
        "runtime_trace",
        "heartbeat",
    )
    assert audit.false_only_live_authority_flags == {
        "adkRunnerInvoked": False,
        "liveUserVisibleStreaming": False,
        "runtimeSelectorActivated": False,
        "productionTranscriptWritten": False,
        "productionSseWritten": False,
        "durableStoreWritten": False,
        "frontendSwitchEnabled": False,
        "liveToolDispatched": False,
        "memoryProviderCalled": False,
        "chatProxyRouteAttached": False,
        "telegramAttached": False,
        "k8sOrProvisioningTouched": False,
    }


def test_gate5b4d_stream_fixture_replays_sanitized_local_diagnostic_sse() -> None:
    fixture = load_gate5b4d_stream_fixture(
        "gate5b4d_stream_coverage_golden.json",
        fixture_root=FIXTURES,
    )

    audit = audit_gate5b4d_stream_fixture(fixture)
    payloads = _agent_payloads(audit.sse_body)

    assert "event: agent" in audit.sse_body
    assert "data: [DONE]\n\n" in audit.sse_body
    assert any(payload.get("type") == "response_clear" for payload in payloads)
    response_clear_index = audit.safe_agent_event_types.index("response_clear")
    text_delta_index = audit.safe_agent_event_types.index("text_delta")
    turn_end_index = audit.safe_agent_event_types.index("turn_end")
    assert response_clear_index < text_delta_index < turn_end_index
    assert audit.transport_markers == ("response_clear", "legacy_finish", "[DONE]")
    assert audit.utf8_chunking == {
        "splitByteSequences": 2,
        "replacementCharactersObserved": False,
        "reassembledText": "안녕, stream 🌊",
    }
    assert audit.duplicate_legacy_rendering_prevention == {
        "agentChannelAuthoritative": True,
        "legacyDeltaMirrored": True,
        "duplicateLegacyDeltaSuppressed": True,
        "visibleTextSource": "agent_text_delta",
    }

    unsafe_fragments = (
        "Bearer ",
        "sk-",
        "ghp_",
        "supabase-service-role",
        "/data/bots",
        "/workspace",
        "infra/k8s",
        "deploy.sh",
        "telegram",
        "pythonResponseAuthority",
        "raw secret",
    )
    for fragment in unsafe_fragments:
        assert fragment not in audit.sse_body

    rule_check = next(payload for payload in payloads if payload.get("type") == "rule_check")
    assert rule_check == {
        "type": "rule_check",
        "eventId": "evt-rule-check-1",
        "turnId": "turn-gate5b4d-1",
        "ruleId": "claim-citation-gate",
        "verdict": "pending",
        "detail": "No live authority attached",
        "evidenceRef": (
            "receipt:sha256:"
            "6666666666666666666666666666666666666666666666666666666666666666"
        ),
        "checkedAt": 1710000004,
    }
    runtime_trace = next(
        payload for payload in payloads if payload.get("type") == "runtime_trace"
    )
    assert runtime_trace["phase"] == "terminal_abort"
    assert "channel_delivery" not in audit.safe_agent_event_types


def test_gate5b4d_stream_fixture_requires_response_clear_ordering() -> None:
    fixture = load_gate5b4d_stream_fixture(
        "gate5b4d_stream_coverage_golden.json",
        fixture_root=FIXTURES,
    )
    payload = fixture.model_dump(by_alias=True, mode="json", warnings=False)
    payload["agentEvents"] = [
        event for event in payload["agentEvents"] if event.get("type") != "response_clear"
    ]

    with pytest.raises(ValueError, match="response_clear"):
        audit_gate5b4d_stream_fixture(payload)


def test_gate5b4d_stream_fixture_boundaries_are_false_and_isolated() -> None:
    fixture = load_gate5b4d_stream_fixture(
        "gate5b4d_stream_coverage_golden.json",
        fixture_root=FIXTURES,
    )

    audit = audit_gate5b4d_stream_fixture(fixture)

    assert audit.active_snapshot_boundary == {
        "attached": False,
        "isolated": True,
        "snapshotAuthority": False,
        "source": "fixture_metadata_only",
    }
    assert audit.durable_write_boundary == {
        "productionTranscriptWritten": False,
        "productionSseWritten": False,
        "durableStoreWritten": False,
        "activeSnapshotWritten": False,
    }


@pytest.mark.parametrize(
    "forbidden_module",
    (
        "google.adk.runners",
        "openmagi_core_agent.adk_bridge.local_runner",
        "openmagi_core_agent.adk_bridge.runner_adapter",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.app",
        "openmagi_core_agent.transport.chat",
        "supabase",
        "postgrest",
        "kubernetes",
        "telegram",
    ),
)
def test_gate5b4d_stream_fixture_import_boundary_stays_shadow_only(
    forbidden_module: str,
) -> None:
    code = f"""
import sys

from openmagi_core_agent.shadow.gate5b4d_stream_fixture_audit import (
    audit_gate5b4d_stream_fixture,
    load_gate5b4d_stream_fixture,
)

fixture = load_gate5b4d_stream_fixture(
    'gate5b4d_stream_coverage_golden.json',
    fixture_root='tests/fixtures/ts_parity_replay',
)
audit_gate5b4d_stream_fixture(fixture)

if {forbidden_module!r} in sys.modules:
    raise AssertionError({forbidden_module!r})
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
