from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from openmagi_core_agent.shadow.control_projection_contract import (
    ControlProjectionAttachmentFlags,
    load_control_projection_fixture,
    project_control_projection_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "control_projection"


def test_control_projection_fixture_matches_typescript_projection_semantics() -> None:
    fixture = load_control_projection_fixture("policy_matrix.json", fixture_root=FIXTURES)

    projection = project_control_projection_fixture(fixture)

    assert fixture.schema_version == "controlProjectionContractFixture.v1"
    assert projection.fixture_id == "control_projection_policy_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.last_seq == 23
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.pending_request_ids == ("req-pending-live",)
    assert projection.request_states == {
        "req-approved-1": "approved",
        "req-cancelled-1": "cancelled",
        "req-timeout-event-1": "timed_out",
        "req-explicit-now-1": "timed_out",
        "req-pending-live": "pending",
    }
    assert projection.requests["req-approved-1"]["resolvedAt"] == 20
    assert projection.requests["req-approved-1"]["decision"] == "approved"
    assert projection.requests["req-approved-1"]["feedback"] == "approved once"
    assert projection.requests["req-cancelled-1"]["cancelReason"] == "operator_cancelled"
    assert projection.requests["req-timeout-event-1"]["resolvedAt"] == 24
    assert projection.requests["req-explicit-now-1"]["resolvedAt"] == 30
    assert projection.requests["req-pending-live"]["state"] == "pending"

    assert projection.active_plan == {
        "planId": "plan-1",
        "state": "verification_pending",
        "turnId": "turn-plan",
        "requestId": "req-approved-1",
        "plan": "Fixture-only plan.",
        "feedback": "approved once",
    }
    assert projection.task_board == {
        "tasks": [
            {"id": "task-1", "title": "Implement fixture", "status": "completed"},
            {"id": "task-2", "title": "Run verification", "status": "pending"},
        ],
    }
    assert projection.verification == {
        "type": "verification",
        "turnId": "turn-plan",
        "status": "pending",
        "reason": "approved plan requires verification",
    }
    assert projection.retry_counts == {"turn-retry": 2}
    assert projection.last_stop_reason_by_turn == {
        "turn-stop": "max_turns",
        "turn-plan": "end_turn",
    }
    assert projection.child_agents == {
        "child-running": {
            "taskId": "child-running",
            "state": "running",
            "parentTurnId": "turn-plan",
            "lastEventSeq": 20,
        },
        "child-completed": {
            "taskId": "child-completed",
            "state": "completed",
            "parentTurnId": "turn-plan",
            "lastEventSeq": 22,
            "summary": {"status": "ok", "finalText": "done", "toolCallCount": 1},
        },
        "child-failed": {
            "taskId": "child-failed",
            "state": "failed",
            "lastEventSeq": 23,
            "errorMessage": "redacted failure",
        },
    }


def test_control_projection_terminal_events_only_update_pending_requests() -> None:
    fixture = load_control_projection_fixture("policy_matrix.json", fixture_root=FIXTURES)

    projection = project_control_projection_fixture(fixture)

    assert projection.requests["req-approved-1"]["resolvedAt"] == 20
    assert projection.requests["req-approved-1"]["decision"] == "approved"
    assert projection.requests["req-approved-1"]["feedback"] == "approved once"
    assert projection.requests["req-cancelled-1"]["state"] == "cancelled"
    assert projection.requests["req-cancelled-1"]["resolvedAt"] == 22
    assert projection.requests["req-timeout-event-1"]["resolvedAt"] == 24


def test_control_projection_attachment_flags_remain_false_under_construct_and_copy() -> None:
    flags = ControlProjectionAttachmentFlags.model_construct()

    assert set(flags.model_dump(by_alias=True).values()) == {False}
    assert set(flags.model_copy(update={"adkRunnerInvoked": True}).model_dump(by_alias=True).values()) == {
        False,
    }


def test_control_projection_import_boundary_stays_local_diagnostic_only() -> None:
    code = """
import sys
from pathlib import Path

from openmagi_core_agent.shadow.control_projection_contract import (
    load_control_projection_fixture,
    project_control_projection_fixture,
)

fixture_root = Path('tests/fixtures/control_projection')
fixture = load_control_projection_fixture('policy_matrix.json', fixture_root=fixture_root)
project_control_projection_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'openmagi_core_agent.adk_bridge.local_runner',
    'openmagi_core_agent.adk_bridge.runner_adapter',
    'openmagi_core_agent.tools.dispatcher',
    'openmagi_core_agent.tools.registry',
    'openmagi_core_agent.transport.chat',
    'openmagi_core_agent.transport.routes.chat',
    'openmagi_core_agent.db',
    'openmagi_core_agent.database',
    'openmagi_core_agent.proxy',
    'openmagi_core_agent.canary',
    'openmagi_core_agent.memory',
    'openmagi_core_agent.plugins.agentmemory',
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
