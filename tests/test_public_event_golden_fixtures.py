from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openmagi_core_agent.shadow.ts_parity_replay import (
    load_ts_parity_replay_fixture,
    replay_ts_parity_fixture,
)
from openmagi_core_agent.transport.sse import InMemorySseWriter


FIXTURES = Path(__file__).parent / "fixtures" / "ts_parity_replay"


def _agent_payloads(sse_body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in sse_body.splitlines()
        if line.startswith("data: ")
    ]


def _by_type(payloads: list[dict[str, object]], event_type: str) -> list[dict[str, object]]:
    return [payload for payload in payloads if payload.get("type") == event_type]


def _typescript_safe_agent_event_payloads(fixture_name: str) -> list[dict[str, object]]:
    repo_root = Path(__file__).resolve().parents[4]
    ts_root = repo_root / "infra" / "docker" / "clawy-core-agent"
    tsx = ts_root / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        pytest.skip("TypeScript safeAgentEvent parity replay requires local tsx install")

    fixture_path = FIXTURES / fixture_name
    code = f"""
import {{ readFileSync }} from "node:fs";
import {{ safeAgentEvent }} from "./src/transport/safeAgentEvent.ts";

const fixture = JSON.parse(readFileSync({json.dumps(str(fixture_path))}, "utf8"));
const payloads = [];
for (const event of fixture.agentEvents) {{
  const safe = safeAgentEvent(event);
  if (safe) payloads.push(safe);
}}
console.log(JSON.stringify(payloads));
"""
    completed = subprocess.run(
        [str(tsx), "--eval", code],
        check=False,
        cwd=ts_root,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _typescript_safe_agent_event_payloads_from_events(
    agent_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    repo_root = Path(__file__).resolve().parents[4]
    ts_root = repo_root / "infra" / "docker" / "clawy-core-agent"
    tsx = ts_root / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        pytest.skip("TypeScript safeAgentEvent parity replay requires local tsx install")

    code = """
import { readFileSync } from "node:fs";
import { safeAgentEvent } from "./src/transport/safeAgentEvent.ts";

const agentEvents = JSON.parse(readFileSync(0, "utf8"));
const payloads = [];
for (const event of agentEvents) {
  const safe = safeAgentEvent(event);
  if (safe) payloads.push(safe);
}
console.log(JSON.stringify(payloads));
"""
    completed = subprocess.run(
        [str(tsx), "--eval", code],
        check=False,
        cwd=ts_root,
        input=json.dumps(agent_events),
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _python_safe_agent_event_payloads(
    agent_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    writer = InMemorySseWriter()
    for event in agent_events:
        writer.agent(event)
    return _agent_payloads(writer.body)


def _normalize_generated_browser_frame_captured_at(
    payload: dict[str, object],
) -> dict[str, object]:
    normalized = dict(payload)
    if normalized.get("imageBase64") == "aW1hZ2VPbmx5":
        captured_at = normalized.get("capturedAt")
        assert isinstance(captured_at, int | float)
        normalized["capturedAt"] = "<generated>"
    return normalized


def test_public_event_golden_fixture_redacts_browser_source_child_error_and_control_shapes() -> None:
    fixture = load_ts_parity_replay_fixture(
        "public_event_golden.json",
        fixture_root=FIXTURES,
    )

    replay = replay_ts_parity_fixture(fixture)
    payloads = _agent_payloads(replay.sse_body)
    event_types = tuple(payload["type"] for payload in payloads)

    assert replay.local_diagnostic is True
    assert set(replay.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert replay.no_false_memory_claims is True
    assert replay.control_lifecycle == {"req-public-1": ("created", "approved")}
    assert replay.memory_modes == ("normal", "read_only", "incognito")
    assert replay.compaction_boundary_ids == ("compact-public-1",)
    assert {
        "browser_frame",
        "source_inspected",
        "child_started",
        "child_completed",
        "child_failed",
        "tool_end",
        "runtime_trace",
        "error",
        "model_fallback",
        "control_event",
        "compaction_boundary",
    }.issubset(event_types)

    unsafe_fragments = (
        "Bearer public-secret",
        "ghp_publicsecret",
        "sk-public-secret",
        "stripe-live-secret",
        "supabase-service-role",
        "browser-session-secret",
        "cdpToken",
        "cdpEndpoint",
        "/data/bots/bot-secret",
        "/workspace/private",
        "rm -rf /workspace/private",
        "private child prompt",
        "private child answer",
        "raw child stdout",
        "memoryProviderPayload",
        "hipocampusMemory",
        "hidden reasoning",
        "raw transport details",
        "private tool args",
        "pythonResponseAuthority",
    )
    for fragment in unsafe_fragments:
        assert fragment not in replay.sse_body

    assert "[redacted]" in replay.sse_body

    browser_frame = _by_type(payloads, "browser_frame")[0]
    assert browser_frame == {
        "type": "browser_frame",
        "action": "observe",
        "url": "https://example.test/dashboard",
        "imageBase64": "dGlueS1mcmFtZQ==",
        "contentType": "image/png",
        "capturedAt": 1710000000,
    }
    ts_browser_frames = _by_type(
        _typescript_safe_agent_event_payloads("public_event_golden.json"),
        "browser_frame",
    )
    assert [browser_frame] == ts_browser_frames

    source_event = _by_type(payloads, "source_inspected")[0]
    source = source_event["source"]
    assert isinstance(source, dict)
    assert source["sourceId"] == "src_public_1"
    assert source["kind"] == "browser"
    assert source["uri"] == "https://example.test/docs"
    assert source["snippets"] == ["Visible excerpt token=[redacted]"]
    assert "metadata" not in source

    child_started = _by_type(payloads, "child_started")[0]
    assert child_started == {
        "type": "child_started",
        "taskId": "child-public-1",
        "childReceiptRef": "receipt:sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "parentTurnId": "turn-public-events-1",
        "detail": "Task: inspect public event surfaces",
    }
    assert _by_type(payloads, "child_completed")[0] == {
        "type": "child_completed",
        "taskId": "child-public-1",
        "childReceiptRef": "receipt:sha256:1111111111111111111111111111111111111111111111111111111111111111",
    }
    child_failed = _by_type(payloads, "child_failed")[0]
    assert child_failed["taskId"] == "child-public-2"
    assert (
        child_failed["childReceiptRef"]
        == "receipt:sha256:2222222222222222222222222222222222222222222222222222222222222222"
    )
    assert "token=[redacted]" in str(child_failed["errorMessage"])

    tool_error = _by_type(payloads, "tool_end")[0]
    assert tool_error["status"] == "error"
    assert "Authorization: Bearer [redacted]" in str(tool_error["output_preview"])

    control_events = _by_type(payloads, "control_event")
    assert control_events[0]["event"] == {
        "type": "control_request_created",
        "request": {
            "requestId": "req-public-1",
            "kind": "tool_permission",
            "state": "pending",
            "sessionKey": "redacted-session",
            "source": "turn",
            "prompt": "Allow browser observation?",
            "createdAt": 11,
            "expiresAt": 71,
        },
    }
    assert control_events[1]["event"] == {
        "type": "control_request_resolved",
        "requestId": "req-public-1",
        "decision": "approved",
        "feedback": "approved with redacted preview",
    }


def test_public_event_golden_fixture_import_boundary_stays_local_diagnostic_only() -> None:
    code = """
import sys
from pathlib import Path

from openmagi_core_agent.shadow.ts_parity_replay import (
    load_ts_parity_replay_fixture,
    replay_ts_parity_fixture,
)

fixture_root = Path('tests/fixtures/ts_parity_replay')
fixture = load_ts_parity_replay_fixture('public_event_golden.json', fixture_root=fixture_root)
replay_ts_parity_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'openmagi_core_agent.adk_bridge.local_runner',
    'openmagi_core_agent.adk_bridge.runner_adapter',
    'openmagi_core_agent.tools.dispatcher',
    'openmagi_core_agent.tools.registry',
    'openmagi_core_agent.plugins.agentmemory',
    'openmagi_core_agent.memory',
    'openmagi_core_agent.app',
    'openmagi_core_agent.transport.chat',
    'openmagi_core_agent.routes',
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_public_browser_frame_image_policy_matches_ts_safe_agent_event_only() -> None:
    fixture = load_ts_parity_replay_fixture(
        "public_browser_frame_image_policy_golden.json",
        fixture_root=FIXTURES,
    )
    agent_events = [event.as_dict() for event in fixture.agent_events]
    agent_events.append(
        {
            "type": "browser_frame",
            "action": "observe",
            "url": "https://example.test/oversized",
            "imageBase64": "A" * 1_000_001,
            "contentType": "image/png",
            "capturedAt": 1710000204,
        }
    )

    payloads = _python_safe_agent_event_payloads(agent_events)
    ts_payloads = _typescript_safe_agent_event_payloads_from_events(agent_events)
    normalized_payloads = [
        _normalize_generated_browser_frame_captured_at(payload) for payload in payloads
    ]
    normalized_ts_payloads = [
        _normalize_generated_browser_frame_captured_at(payload) for payload in ts_payloads
    ]

    assert normalized_payloads == normalized_ts_payloads
    assert normalized_payloads == [
        {
            "type": "browser_frame",
            "action": "observe",
            "imageBase64": "dGlueS1mcmFtZQ==",
            "contentType": "image/png",
            "capturedAt": 1710000200,
            "url": "https://example.test/frame",
        },
        {
            "type": "browser_frame",
            "action": "screenshot",
            "imageBase64": "YWJjZA==",
            "contentType": "image/jpeg",
            "capturedAt": 1710000201,
            "url": "https://example.test/photo",
        },
        {
            "type": "browser_frame",
            "action": "browser",
            "imageBase64": "aW1hZ2VPbmx5",
            "contentType": "image/png",
            "capturedAt": "<generated>",
        },
        {
            "type": "browser_frame",
            "action": ("a" * 61) + "...",
            "imageBase64": "dHJ1bmNhdGVkQWN0aW9u",
            "contentType": "image/png",
            "capturedAt": 1710000205,
            "url": "https://example.test/truncated-action",
        },
    ]


def test_public_event_union_golden_fixture_projects_additional_safe_agent_events() -> None:
    fixture = load_ts_parity_replay_fixture(
        "public_event_union_golden.json",
        fixture_root=FIXTURES,
    )

    replay = replay_ts_parity_fixture(fixture)
    payloads = _agent_payloads(replay.sse_body)
    event_types = tuple(payload["type"] for payload in payloads)

    assert replay.local_diagnostic is True
    assert set(replay.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert {
        "research_artifact_delta",
        "retry",
        "spawn_worktree_conflict",
        "structured_output",
        "turn_interrupted",
        "spawn_started",
        "spawn_result",
        "background_task",
        "child_llm_start",
        "child_llm_end",
        "child_tool_batch_start",
        "child_tool_batch_end",
        "child_abort",
        "tournament_result",
        "ask_user",
        "plan_ready",
        "plan_lifecycle",
        "session_stop",
        "context_activated",
        "compaction_impossible",
        "injection_queued",
        "injection_drained",
        "heartbeat",
    }.issubset(event_types)
    assert "mission_created" not in event_types
    assert "mission_event" not in event_types

    unsafe_fragments = (
        "sk-union-secret",
        "ghp_unionsecret",
        "stripe-union-secret",
        "/data/bots/bot-union",
        "/workspace/private-union",
        "private research raw payload",
        "private spawn prompt",
        "final child text should stay private",
        "raw child transcript",
        "private proposed command",
        "browser transport detail",
        "mission payload internals",
        "session transport internals",
    )
    for fragment in unsafe_fragments:
        assert fragment not in replay.sse_body

    research = _by_type(payloads, "research_artifact_delta")[0]
    assert research["claims"] == [
        {
            "claimId": "claim-public-1",
            "text": "Public claim with token=[redacted]",
            "claimType": "fact",
            "supportStatus": "supported",
            "sourceIds": ["src-public-1"],
            "confidence": 0.75,
            "reasoning": {
                "premiseSourceIds": ["src-public-1"],
                "inference": "Public inference with key=[redacted]",
                "assumptions": ["Public assumption path=[redacted-path]"],
                "status": "source_backed",
            },
        }
    ]
    assert research["claimSourceLinks"] == [
        {"claimId": "claim-public-1", "sourceId": "src-public-1", "support": "supports"}
    ]
    assert research["contradictions"] == [
        {
            "contradictionId": "contra-public-1",
            "claimIds": ["claim-public-1"],
            "sourceIds": ["src-public-2"],
            "resolution": "Public resolution token=[redacted]",
            "status": "handled",
        }
    ]

    assert _by_type(payloads, "retry")[0] == {
        "type": "retry",
        "reason": "Transient public failure token=[redacted]",
        "toolUseId": "toolu-public-1",
        "toolName": "WebFetch",
        "retryNo": 2,
    }
    assert _by_type(payloads, "spawn_worktree_conflict")[0] == {
        "type": "spawn_worktree_conflict",
        "action": "apply",
        "spawnDir": "[redacted-path]",
        "conflictKind": "parent_dirty",
        "mergeStrategy": "copy",
        "adoptedCommit": "abc123",
        "summary": "Public conflict summary token=[redacted]",
        "conflictedFiles": ["src/public.ts"],
        "changedFiles": ["src/public.ts", "[redacted-path]"],
        "suggestedActions": ["Review src/public.ts"],
    }
    assert _by_type(payloads, "structured_output")[0] == {
        "type": "structured_output",
        "status": "invalid",
        "schemaName": "PublicSchema",
        "reason": "missing public field token=[redacted]",
    }
    assert _by_type(payloads, "turn_interrupted")[0] == {
        "type": "turn_interrupted",
        "turnId": "turn-union-events-1",
        "source": "api",
        "handoffRequested": True,
    }
    assert _by_type(payloads, "spawn_started")[0] == {
        "type": "spawn_started",
        "taskId": "spawn-public-1",
        "persona": "researcher",
        "deliver": "background",
        "detail": "Inspect public source token=[redacted]",
    }
    assert _by_type(payloads, "spawn_result")[0] == {
        "type": "spawn_result",
        "taskId": "spawn-public-1",
        "status": "ok",
        "toolCallCount": 2,
    }
    assert _by_type(payloads, "background_task")[0] == {
        "type": "background_task",
        "taskId": "spawn-public-1",
        "persona": "researcher",
        "status": "running",
        "detail": "Working on public task token=[redacted]",
    }
    assert _by_type(payloads, "child_llm_start")[0] == {
        "type": "child_llm_start",
        "taskId": "child-public-1",
        "parentTurnId": "turn-union-events-1",
        "childTurnId": "child-turn-public-1",
        "traceId": "trace-public-1",
        "model": "public-model",
        "iter": 1,
    }
    assert _by_type(payloads, "child_llm_end")[0] == {
        "type": "child_llm_end",
        "taskId": "child-public-1",
        "parentTurnId": "turn-union-events-1",
        "childTurnId": "child-turn-public-1",
        "traceId": "trace-public-1",
        "model": "public-model",
        "stopReason": "end_turn",
        "iter": 1,
        "durationMs": 33,
    }
    assert _by_type(payloads, "child_tool_batch_start")[0] == {
        "type": "child_tool_batch_start",
        "taskId": "child-public-1",
        "iter": 1,
        "toolCount": 2,
        "toolNames": ["WebFetch", "SourceInspect"],
    }
    assert _by_type(payloads, "child_tool_batch_end")[0] == {
        "type": "child_tool_batch_end",
        "taskId": "child-public-1",
        "iter": 1,
        "toolCount": 2,
        "errorCount": 1,
        "durationMs": 44,
        "status": "error",
        "errorName": "ToolError",
        "errorMessage": "tool failed token=[redacted]",
    }
    assert _by_type(payloads, "child_abort")[0] == {
        "type": "child_abort",
        "taskId": "child-public-1",
        "source": "parent",
    }
    assert _by_type(payloads, "tournament_result")[0] == {
        "type": "tournament_result",
        "winnerIndex": 1,
        "variants": [
            {"variantIndex": 0, "score": 0.1},
            {"variantIndex": 1, "score": 0.9},
        ],
    }
    assert _by_type(payloads, "ask_user")[0] == {
        "type": "ask_user",
        "questionId": "question-public-1",
        "question": "Choose a public path token=[redacted]",
        "allowFreeText": False,
        "choices": [
            {
                "id": "choice-a",
                "label": "Proceed",
                "description": "Use safe public summary path=[redacted-path]",
            }
        ],
    }
    assert _by_type(payloads, "plan_ready")[0] == {
        "type": "plan_ready",
        "planId": "plan-public-1",
        "requestId": "req-plan-public-1",
        "state": "awaiting_approval",
        "plan": "1. Inspect public source\n2. Report safe summary token=[redacted]",
    }
    assert _by_type(payloads, "plan_lifecycle")[0] == {
        "type": "plan_lifecycle",
        "state": "approved",
        "previousMode": "plan",
    }
    assert _by_type(payloads, "session_stop")[0] == {
        "type": "session_stop",
        "taskId": "task-public-1",
        "reason": "target_met",
        "round": 3,
        "lastScore": 0.88,
    }
    assert _by_type(payloads, "context_activated")[0] == {
        "type": "context_activated",
        "contextId": "context-public-1",
        "title": "Public context token=[redacted]",
    }
    assert _by_type(payloads, "compaction_impossible")[0] == {
        "type": "compaction_impossible",
        "model": "public-model",
        "contextWindow": 128000,
        "effectiveReserveTokens": 4096,
        "effectiveBudgetTokens": 120000,
        "minViableBudgetTokens": 16000,
    }
    assert _by_type(payloads, "injection_queued")[0] == {
        "type": "injection_queued",
        "injectionId": "inject-public-1",
        "queuedCount": 2,
    }
    assert _by_type(payloads, "injection_drained")[0] == {
        "type": "injection_drained",
        "count": 2,
        "iteration": 4,
    }
    assert _by_type(payloads, "heartbeat")[0] == {
        "type": "heartbeat",
        "turnId": "turn-union-events-1",
        "iter": 4,
        "elapsedMs": 1200,
        "lastEventAt": 1710000100,
    }


def test_public_control_projection_fixture_projects_supplemental_agent_events_only() -> None:
    fixture = load_ts_parity_replay_fixture(
        "public_control_projection_event_golden.json",
        fixture_root=FIXTURES,
    )

    replay = replay_ts_parity_fixture(fixture)
    payloads = _agent_payloads(replay.sse_body)
    event_types = tuple(payload["type"] for payload in payloads)

    assert payloads == _typescript_safe_agent_event_payloads(
        "public_control_projection_event_golden.json"
    )
    assert replay.local_diagnostic is True
    assert set(replay.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert replay.no_false_memory_claims is True
    assert replay.control_lifecycle == {}
    assert "control_event" not in replay.transcript_kinds
    assert {
        "control_replay_complete",
        "document_draft",
        "llm_progress",
        "patch_preview",
        "task_board",
        "control_event",
    }.issubset(event_types)

    fixture_text = (FIXTURES / "public_control_projection_event_golden.json").read_text(
        encoding="utf-8"
    )
    unsafe_fixture_fragments = (
        "ghp_",
        "sk-control-secret",
        "Bearer control-secret",
        "/data/bots/",
        "/workspace/",
        "/var/lib/kubelet/",
    )
    for fragment in unsafe_fixture_fragments:
        assert fragment not in fixture_text

    dropped_internal_fragments = (
        "provider replay metadata",
        "full raw document internal body",
        "internal child prompt",
        "internal provider payload",
        "internal tool arguments",
        "internal patch before content",
        "+status=ready",
        "internal task board state",
        "internal task note",
        "internal evidence body",
        "pytest stdout internal detail",
        "logs/control-projection.log",
        "internal child summary",
        "internal updated input",
    )
    for fragment in dropped_internal_fragments:
        assert fragment not in replay.sse_body

    assert "[redacted]" not in replay.sse_body
    assert "[redacted-path]" not in replay.sse_body

    assert _by_type(payloads, "control_replay_complete")[0] == {
        "type": "control_replay_complete",
        "lastSeq": 99,
    }
    assert _by_type(payloads, "document_draft")[0] == {
        "type": "document_draft",
        "id": "draft-public-1",
        "filename": "docs/public-report.md",
        "format": "md",
        "contentPreview": (
            "# Public draft\nstatus=ready\nartifact=reports/public-control-summary.md"
        ),
        "contentLength": 70,
        "truncated": True,
    }
    assert _by_type(payloads, "llm_progress")[0] == {
        "type": "llm_progress",
        "turnId": "turn-control-projection-1",
        "iter": 2,
        "stage": "started",
        "label": "Calling public model",
        "detail": "provider call completed for public-model route",
        "elapsedMs": 1200,
    }
    assert _by_type(payloads, "patch_preview")[0] == {
        "type": "patch_preview",
        "toolUseId": "toolu-patch-public-1",
        "dryRun": False,
        "changedFiles": ["src/public.ts", "src/control-summary.ts"],
        "createdFiles": ["src/new-public.ts"],
        "deletedFiles": [],
        "files": [
            {
                "path": "src/public.ts",
                "operation": "update",
                "hunks": 1,
                "addedLines": 2,
                "removedLines": 1,
                "oldSha256": "old-public",
                "newSha256": "new-public",
            }
        ],
    }
    task_board = _by_type(payloads, "task_board")[0]
    assert set(task_board) == {"type", "tasks"}
    assert task_board == {
        "type": "task_board",
        "tasks": [
            {
                "id": "task-public-1",
                "title": "Inspect fixture",
                "description": "Review safe public fields",
                "status": "completed",
                "parallelGroup": "group-a",
            },
            {
                "id": "task-public-2",
                "title": "Verify sanitizer",
                "description": "Confirm projection parity",
                "status": "in_progress",
                "dependsOn": ["task-public-1"],
            },
        ],
    }

    control_events = _by_type(payloads, "control_event")
    assert [event["event"]["type"] for event in control_events] == [
        "task_board_snapshot",
        "verification",
        "child_progress",
        "child_tool_request",
        "child_permission_decision",
        "child_cancelled",
    ]
    assert control_events[0] == {
        "type": "control_event",
        "seq": 100,
        "event": {
            "type": "task_board_snapshot",
            "turnId": "turn-control-projection-1",
        },
    }
    assert control_events[1] == {
        "type": "control_event",
        "seq": 101,
        "event": {
            "type": "verification",
            "status": "passed",
            "reason": "pytest passed for supplemental projection",
        },
    }
    assert control_events[2] == {
        "type": "control_event",
        "seq": 102,
        "event": {
            "type": "child_progress",
            "taskId": "child-public-1",
            "detail": "Child is checking public fixture fields",
        },
    }
    assert control_events[3] == {
        "type": "control_event",
        "seq": 103,
        "event": {
            "type": "child_tool_request",
            "taskId": "child-public-1",
            "requestId": "req-child-tool-1",
            "toolName": "PatchApply",
        },
    }
    assert control_events[4] == {
        "type": "control_event",
        "seq": 104,
        "event": {
            "type": "child_permission_decision",
            "taskId": "child-public-1",
            "decision": "allow",
            "reason": "Allowed bounded patch preview",
        },
    }
    assert control_events[5] == {
        "type": "control_event",
        "seq": 105,
        "event": {
            "type": "child_cancelled",
            "taskId": "child-public-2",
            "reason": "cancelled after public projection check",
        },
    }
