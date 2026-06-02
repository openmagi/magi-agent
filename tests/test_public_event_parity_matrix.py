from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from magi_agent.transport.public_event_parity import (
    PublicEventParityStatus,
    audit_public_event_projection,
    load_public_event_parity_matrix,
)
from magi_agent.transport.sse import InMemorySseWriter


FIXTURE = Path(__file__).parent / "fixtures" / "public_event_parity" / "matrix.json"
SUPPORTED_STATUSES: set[PublicEventParityStatus] = {"supported_now", "projected_alias"}
DEFERRED_STATUSES: set[PublicEventParityStatus] = {
    "default_off_boundary_only",
    "intentionally_unsupported",
    "blocked_until_gate",
}
PRIVATE_FRAGMENTS = (
    "raw adk provider detail",
    "private tool arguments",
    "private browser cdp",
    "private child output",
    "private active snapshot",
    "private goal payload",
    "/workspace/private",
)


def _fixture_value(*parts: str) -> str:
    return "".join(parts)


def _agent_payloads(sse_body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in sse_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def test_public_event_parity_matrix_covers_required_ts_public_event_surfaces() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)
    event_types = {row.event_type for row in matrix.rows}
    required_event_types = {
        "browser_frame",
        "document_draft",
        "tool_start",
        "tool_progress",
        "tool_end",
        "patch_preview",
        "source_inspected",
        "rule_check",
        "citation_gate",
        "runtime_trace",
        "task_board",
        "mission_created",
        "mission_event",
        "mission_progress",
        "cron_run",
        "goal_created",
        "goal_progress",
        "goal_completed",
        "goal_cancelled",
        "control_event",
        "ask_user",
        "plan_ready",
        "spawn_started",
        "spawn_result",
        "child_started",
        "child_progress",
        "child_completed",
        "child_failed",
        "child_cancelled",
        "child_tool_request",
        "child_permission_decision",
        "background_task",
        "turn_phase",
        "heartbeat",
        "retry",
        "response_clear",
        "text_delta",
        "recipe_selection",
        "active_snapshot",
        "inject",
        "interrupt",
    }

    assert required_event_types <= event_types
    assert len(event_types) == len(matrix.rows)
    assert {row.frontend_contract for row in matrix.rows} == {"event: agent"}


def test_supported_public_event_matrix_rows_project_to_expected_sse_payloads() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)

    for row in matrix.rows:
        if row.classification not in SUPPORTED_STATUSES:
            continue
        assert row.sample_event is not None, row.event_type
        assert row.expected_public is not None, row.event_type
        if row.classification == "projected_alias":
            assert row.projected_alias is not None, row.event_type

        writer = InMemorySseWriter()
        writer.agent(row.sample_event)
        payloads = _agent_payloads(writer.body)

        assert payloads == [row.expected_public], row.event_type
        for fragment in PRIVATE_FRAGMENTS:
            assert fragment not in writer.body, row.event_type


def test_recipe_selection_projection_accepts_model_dump_tuples_and_blocked_status() -> None:
    writer = InMemorySseWriter()
    writer.agent(
        {
            "type": "recipe_selection",
            "admissionBlocked": True,
            "selectionSource": "explicit",
            "requestedRecipeRefs": (
                {
                    "recipeId": "openmagi.research",
                    "version": "1",
                    "digest": "sha256:" + "1" * 64,
                },
            ),
            "omittedRecipeRefs": (
                {
                    "recipeId": "openmagi.research",
                    "version": "1",
                    "digest": "sha256:" + "1" * 64,
                },
            ),
            "omissionReasons": {
                "openmagi.research": ("explicit_recipe_disabled",),
            },
            "policySnapshotDigest": "sha256:" + "2" * 64,
        }
    )

    assert _agent_payloads(writer.body) == [
        {
            "type": "recipe_selection",
            "status": "blocked",
            "selectionSource": "explicit",
            "requestedRecipeRefs": [
                {
                    "recipeId": "openmagi.research",
                    "version": "1",
                    "digest": "sha256:" + "1" * 64,
                }
            ],
            "omittedRecipeRefs": [
                {
                    "recipeId": "openmagi.research",
                    "version": "1",
                    "digest": "sha256:" + "1" * 64,
                }
            ],
            "omissionReasons": {"openmagi.research": ["explicit_recipe_disabled"]},
            "policySnapshotDigest": "sha256:" + "2" * 64,
        }
    ]


def test_recipe_selection_projection_admission_blocked_overrides_applied_status() -> None:
    writer = InMemorySseWriter()
    writer.agent(
        {
            "type": "recipe_selection",
            "status": "applied",
            "admissionBlocked": True,
            "selectionSource": "mixed",
            "requestedRecipeRefs": [
                {
                    "recipeId": "openmagi.research",
                    "version": "1",
                    "digest": "sha256:" + "1" * 64,
                }
            ],
            "policySnapshotDigest": "sha256:" + "2" * 64,
        }
    )

    assert _agent_payloads(writer.body)[0]["status"] == "blocked"


def test_recipe_selection_projection_drops_token_shaped_version() -> None:
    for token_like_version in (
        _fixture_value("s", "k", "-proj-", "sec", "ret-", "tok", "en"),
        _fixture_value("s", "k", "-", "abc", "1234", "567", "890"),
        _fixture_value("github", "_pat_", "abc", "1234", "567", "890"),
    ):
        writer = InMemorySseWriter()
        writer.agent(
            {
                "type": "recipe_selection",
                "status": "blocked",
                "selectionSource": "explicit",
                "requestedRecipeRefs": [
                    {
                        "recipeId": "openmagi.research",
                        "version": token_like_version,
                        "digest": "sha256:" + "1" * 64,
                    }
                ],
                "policySnapshotDigest": "sha256:" + "2" * 64,
            }
        )

        payload = _agent_payloads(writer.body)[0]
        assert payload["requestedRecipeRefs"] == [
            {
                "recipeId": "openmagi.research",
                "digest": "sha256:" + "1" * 64,
            }
        ]
        assert token_like_version not in writer.body


def test_recipe_selection_projection_rejects_unsafe_ref_strings_and_reasons() -> None:
    writer = InMemorySseWriter()
    unsafe_recipe_id = _fixture_value("s", "k", "-proj-", "sec", "ret-", "tok", "en")
    unsafe_version = _fixture_value("Author", "ization: Bearer ", "private")
    writer.agent(
        {
            "type": "recipe_selection",
            "status": "blocked",
            "selectionSource": "explicit",
            "requestedRecipeRefs": [
                {
                    "recipeId": "openmagi.research",
                    "version": "1",
                    "digest": "sha256:" + "1" * 64,
                },
                {
                    "recipeId": unsafe_recipe_id,
                    "version": unsafe_version,
                    "digest": "not-a-digest",
                },
            ],
            "omittedRecipeRefs": [
                {
                    "recipeId": "/Users/kevin/.kube/config",
                    "version": "1",
                    "digest": "sha256:" + "3" * 64,
                }
            ],
            "omissionReasons": {
                "openmagi.research": [
                    "explicit_recipe_disabled",
                    unsafe_version,
                ],
                unsafe_recipe_id: ["explicit_recipe_missing"],
            },
            "policySnapshotDigest": "sha256:" + "2" * 64,
            "rawPolicySnapshot": "private active snapshot",
        }
    )

    payloads = _agent_payloads(writer.body)
    assert payloads == [
        {
            "type": "recipe_selection",
            "status": "blocked",
            "selectionSource": "explicit",
            "requestedRecipeRefs": [
                {
                    "recipeId": "openmagi.research",
                    "version": "1",
                    "digest": "sha256:" + "1" * 64,
                }
            ],
            "omissionReasons": {"openmagi.research": ["explicit_recipe_disabled"]},
            "policySnapshotDigest": "sha256:" + "2" * 64,
        }
    ]
    assert "sk-proj" not in writer.body
    assert "Bearer private" not in writer.body
    assert "/Users" not in writer.body
    assert "private active snapshot" not in writer.body


def test_deferred_or_unsupported_public_event_rows_are_explicitly_recorded() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)
    deferred_rows = [row for row in matrix.rows if row.classification in DEFERRED_STATUSES]

    assert {row.event_type for row in deferred_rows} >= {
        "thinking_delta",
        "active_snapshot",
        "channel_delivery_receipt",
        "mission_created",
        "mission_event",
        "mission_progress",
        "cron_run",
        "goal_created",
        "goal_progress",
        "goal_completed",
        "goal_cancelled",
    }
    for row in deferred_rows:
        assert row.follow_up_gate_reason, row.event_type
        assert row.expected_public is None, row.event_type
        if row.sample_event is not None:
            writer = InMemorySseWriter()
            writer.agent(row.sample_event)
            assert _agent_payloads(writer.body) == [], row.event_type


def test_malformed_source_inspected_drops_instead_of_emitting_empty_source() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)
    audit = audit_public_event_projection(
        {
            "type": "source_inspected",
            "source": {"title": "Missing identity and URL", "raw": "private active snapshot"},
        },
        matrix,
    )

    assert audit.classification == "supported_now"
    assert audit.dropped is True
    assert audit.drop_reason == "sanitizer_dropped_event"
    assert audit.payload is None


def test_unknown_public_event_projection_audit_records_drop_classification() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)
    audit = audit_public_event_projection(
        {"type": "raw_provider_event", "rawAdkEvent": "raw adk provider detail"},
        matrix,
    )

    assert audit.classification == "intentionally_unsupported"
    assert audit.dropped is True
    assert audit.drop_reason == "unclassified_event_type"
    assert audit.payload is None


def test_active_snapshot_reconnect_contracts_are_default_off_and_cover_lifecycle() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)
    scenarios = {contract.scenario for contract in matrix.active_snapshot_contracts}

    assert {
        "response_clear_ordering",
        "partial_utf8_frame",
        "running_snapshot",
        "final_snapshot",
        "detached_background_work",
        "snapshot_deleted",
        "snapshot_finalized",
    } <= scenarios
    for contract in matrix.active_snapshot_contracts:
        assert contract.production_write_enabled is False
        assert contract.route_activation_enabled is False
        assert contract.user_visible_output_enabled is False
        assert contract.follow_up_gate_reason


def test_inject_interrupt_route_contracts_are_default_off_and_private_safe() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)
    contracts_by_event = {
        contract.event_type: contract for contract in matrix.route_compatibility_contracts
    }

    assert set(contracts_by_event) == {"inject", "interrupt"}
    for contract in contracts_by_event.values():
        assert contract.route.startswith("/v1/chat/:botId/")
        assert contract.method == "POST"
        assert contract.selected_bot_authority_required is True
        assert contract.ts_fallback_on_python_unavailable is True
        assert contract.no_raw_payload_projection is True
        assert contract.default_off is True


def test_channel_delivery_receipt_stance_is_deferred_until_gate4() -> None:
    matrix = load_public_event_parity_matrix(FIXTURE)
    stance = matrix.channel_delivery_receipt_stance

    assert stance.classification == "blocked_until_gate"
    assert "Gate 4" in stance.follow_up_gate_reason
    assert stance.public_receipt_projection_enabled is False


def test_public_event_parity_matrix_import_boundary_is_contract_only() -> None:
    forbidden_modules = (
        "google.adk.runners",
        "magi_agent.adk_bridge.local_runner",
        "magi_agent.tools.dispatcher",
        "magi_agent.memory.adk_bridge",
        "magi_agent.browser.provider_boundary",
        "magi_agent.channels.telegram_adapter",
        "magi_agent.web_acquisition.provider_boundary",
    )
    code = f"""
import json
import sys
import magi_agent.transport.public_event_parity  # noqa: F401

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
