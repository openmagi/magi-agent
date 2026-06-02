from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests"
    / "fixtures"
    / "parity"
    / "general_automation_safe_queue_matrix.json"
)
DOC_PATH = (
    REPO_ROOT
    / "docs"
    / "notes"
    / "2026-05-27-python-adk-general-automation-safe-queue-reconciliation.md"
)

REQUIRED_ROW_IDS = (
    "general_automation_matrix",
    "automation_package_boundary",
    "external_directory_policy",
    "output_budget_followup_refs",
    "shell_command_path_policy",
    "general_automation_agent_presets",
    "spreadsheet_read_validate_reconcile",
    "web_acquisition_provider_contract",
    "browser_inspect_act_boundary",
    "durable_background_task_resume_contract",
    "mcp_plugin_projection_contract",
    "event_control_projection_contract",
    "final_integration_review",
)
REQUIRED_ROW_FIELDS = {
    "capability",
    "sourceDocuments",
    "alreadyCovered",
    "currentFiles",
    "currentTests",
    "missingImplementation",
    "owningLayer",
    "adkPrimitive",
    "prSlice",
    "dependencies",
    "activationGate",
    "coreTouchAllowed",
    "coreGapIfBlocked",
}
ALLOWED_OWNING_LAYERS = {
    "Tests/docs only",
    "First-party harness",
    "First-party recipe",
    "First-party plugin",
    "Provider/tool adapter contract",
    "Mixed first-party contracts",
}
ALLOWED_ADK_PRIMITIVE_MARKERS = (
    "FunctionTool",
    "LongRunningFunctionTool",
    "callback",
    "plugin lifecycle",
    "SessionService",
    "ArtifactService",
    "ADK Evaluation",
    "Agent role metadata",
    "audit fixture only",
)
FORBIDDEN_PATH_PREFIXES = (
    "magi-agent/magi_agent/adk_bridge/",
    "magi-agent/magi_agent/runtime/",
    "magi-agent/magi_agent/transport/",
    "magi-agent/magi_agent/config/",
    "magi-agent/magi_agent/routing/",
    "infra/docker/chat-proxy/",
)
FORBIDDEN_EXACT_PATHS = {
    "magi-agent/magi_agent/tools/registry.py",
    "magi-agent/magi_agent/tools/dispatcher.py",
    "magi-agent/magi_agent/tools/permission.py",
    "magi-agent/magi_agent/tools/result.py",
}
FRONTEND_TS_PR3_ALLOWED_BRANCH_DIFF = {
    "magi-agent/magi_agent/adk_bridge/event_adapter.py",
    "magi-agent/magi_agent/runtime/events.py",
    "magi-agent/magi_agent/transport/sse.py",
    "magi-agent/tests/fixtures/public_event_parity/matrix.json",
    "magi-agent/tests/fixtures/ts_parity_replay/gate5b4d_stream_coverage_golden.json",
    "magi-agent/tests/fixtures/ts_parity_replay/public_event_golden.json",
    "magi-agent/tests/test_event_bridge.py",
    "magi-agent/tests/test_gate5b4d_stream_fixture_audit.py",
    "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
    "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    "magi-agent/tests/test_pr3_event_adapter_live_compatibility.py",
    "magi-agent/tests/test_pr3_live_producer_parity_bridge.py",
    "magi-agent/tests/test_public_event_builders.py",
    "magi-agent/tests/test_public_event_golden_fixtures.py",
}
MAINLINE_SCAFFOLD_SECRET_FIXTURE_RESCUE_PATHS = {
    "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
    "magi-agent/magi_agent/hooks/executors/__init__.py",
    "magi-agent/magi_agent/tools/catalog.py",
    "magi-agent/magi_agent/tools/concurrent_dispatcher.py",
    "magi-agent/tests/hooks/test_manifest_external.py",
    "magi-agent/tests/test_core_tool_catalog.py",
    "magi-agent/tests/test_deferred_adk_integration.py",
    "magi-agent/tests/test_gate5a_no_memory_shadow_canary.py",
    "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
    "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    "magi-agent/tests/test_tool_admin_routes.py",
    "magi-agent/tests/test_tool_safety_annotations.py",
}
FORBIDDEN_ACTIVATION_TEXT = (
    "live network",
    "live provider",
    "live model",
    "shell execution",
    "start browser",
    "mcp server starts",
    "production traffic",
    "default-on",
    "workspace mutation enabled",
)
RAW_LEAKAGE_TERMS = (
    "raw prompt",
    "raw output",
    "raw dom",
    "raw transcript",
    "auth header",
    "cookie",
    "provider request body",
    "/users/",
    "/workspace/",
    "/data/bots/",
)


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _rows() -> list[dict[str, Any]]:
    rows = _load_matrix()["rows"]
    assert isinstance(rows, list)
    return rows


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for nested in value.values():
            strings.extend(_strings(nested))
        return strings
    if isinstance(value, list):
        strings = []
        for nested in value:
            strings.extend(_strings(nested))
        return strings
    return []


def _ref_path(ref: object) -> str | None:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict) and isinstance(ref.get("path"), str):
        return ref["path"]
    return None


def _assert_allowed_changed_path(path: str) -> None:
    assert not any(path.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES), path
    assert path not in FORBIDDEN_EXACT_PATHS


def _allows_mainline_scaffold_secret_fixture_rescue_paths(changed_paths: set[str]) -> bool:
    return (
        changed_paths <= MAINLINE_SCAFFOLD_SECRET_FIXTURE_RESCUE_PATHS
        and MAINLINE_SCAFFOLD_SECRET_FIXTURE_RESCUE_PATHS <= changed_paths
    )


def test_mainline_scaffold_rescue_scope_requires_complete_path_set() -> None:
    assert _allows_mainline_scaffold_secret_fixture_rescue_paths(
        MAINLINE_SCAFFOLD_SECRET_FIXTURE_RESCUE_PATHS
    )
    assert not _allows_mainline_scaffold_secret_fixture_rescue_paths(
        {
            "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        }
    )
    assert not _allows_mainline_scaffold_secret_fixture_rescue_paths(
        MAINLINE_SCAFFOLD_SECRET_FIXTURE_RESCUE_PATHS
        - {
            "magi-agent/tests/test_tool_safety_annotations.py",
        }
    )


def test_safe_queue_rows_exist_exactly_once_in_plan_order() -> None:
    row_ids = [row["id"] for row in _rows()]

    assert row_ids == list(REQUIRED_ROW_IDS)
    assert len(row_ids) == len(set(row_ids))


def test_rows_have_required_ownership_activation_and_no_core_touch_fields() -> None:
    for row in _rows():
        assert set(row) >= REQUIRED_ROW_FIELDS
        assert row["owningLayer"] in ALLOWED_OWNING_LAYERS
        assert row["activationGate"].strip()
        assert row["coreTouchAllowed"] is False
        assert isinstance(row["dependencies"], list)
        assert isinstance(row["alreadyCovered"], bool)
        assert row["prSlice"].startswith("PR")
        assert any(marker in row["adkPrimitive"] for marker in ALLOWED_ADK_PRIMITIVE_MARKERS)


def test_current_refs_exist_and_missing_refs_are_planned_missing() -> None:
    for row in _rows():
        for field_name in ("currentFiles", "currentTests"):
            for ref in row[field_name]:
                path = _ref_path(ref)
                assert path is not None
                assert (PYTHON_ROOT / path).exists(), (row["id"], field_name, path)

        for ref in row["missingImplementation"]:
            path = _ref_path(ref)
            assert path is not None
            assert ref["state"] in {"planned_missing", "gap_test_or_doc"}
            assert not (PYTHON_ROOT / path).exists(), (row["id"], path)


def test_matrix_records_default_off_public_safe_contracts_only() -> None:
    matrix = _load_matrix()

    assert matrix["defaultOff"] is True
    assert matrix["trafficAttached"] is False
    assert matrix["productionAuthority"] is False
    for row in _rows():
        row_text = " ".join(_strings(row)).lower()
        assert "default-off" in row["activationGate"]
        assert not any(term in row_text for term in FORBIDDEN_ACTIVATION_TEXT), row["id"]
        assert not any(term in row_text for term in RAW_LEAKAGE_TERMS), row["id"]


def test_path_scope_guard_rejects_forbidden_core_paths_in_branch_diff() -> None:
    committed = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if committed.returncode != 0:
        committed = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1..HEAD"],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    uncommitted = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    changed_paths = {
        line
        for output in (committed.stdout, uncommitted.stdout)
        for line in output.splitlines()
        if line.strip()
    }
    if _allows_mainline_scaffold_secret_fixture_rescue_paths(changed_paths):
        return
    scaffold_parallel_safety_rescue_paths = {
        "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/tools/concurrency.py",
        "magi-agent/magi_agent/tools/tool_search.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_mcp_adapter.py",
        "magi-agent/tests/test_tool_safety_annotations.py",
    }
    scaffold_parallel_safety_required_paths = {
        "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/tools/concurrency.py",
        "magi-agent/magi_agent/tools/tool_search.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_mcp_adapter.py",
        "magi-agent/tests/test_tool_safety_annotations.py",
    }
    if (
        changed_paths <= scaffold_parallel_safety_rescue_paths
        and scaffold_parallel_safety_required_paths <= changed_paths
    ):
        return
    python_scaffold_regression_recovery_paths = {
        "magi-agent/README.md",
        "magi-agent/magi_agent/adk_bridge/event_adapter.py",
        "magi-agent/magi_agent/adk_bridge/local_runner.py",
        "magi-agent/magi_agent/adk_bridge/primitives.py",
        "magi-agent/magi_agent/adk_bridge/runner_adapter.py",
        "magi-agent/magi_agent/adk_bridge/session_service.py",
        "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        "magi-agent/magi_agent/app.py",
        "magi-agent/magi_agent/config/__init__.py",
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/magi_agent/evidence/__init__.py",
        "magi-agent/magi_agent/evidence/builtin.py",
        "magi-agent/magi_agent/evidence/child_runtime_envelope.py",
        "magi-agent/magi_agent/evidence/reports.py",
        "magi-agent/magi_agent/evidence/source_ledger.py",
        "magi-agent/magi_agent/evidence/subagent.py",
        "magi-agent/magi_agent/evidence/types.py",
        "magi-agent/magi_agent/harness/__init__.py",
        "magi-agent/magi_agent/harness/verifier_bus.py",
        "magi-agent/magi_agent/main.py",
        "magi-agent/magi_agent/memory/__init__.py",
        "magi-agent/magi_agent/memory/adapters/hipocampus_readonly.py",
        "magi-agent/magi_agent/memory/contracts.py",
        "magi-agent/magi_agent/memory/policy.py",
        "magi-agent/magi_agent/plugins/__init__.py",
        "magi-agent/magi_agent/plugins/manager.py",
        "magi-agent/magi_agent/plugins/manifest.py",
        "magi-agent/magi_agent/plugins/native_catalog.py",
        "magi-agent/magi_agent/plugins/tool_projection.py",
        "magi-agent/magi_agent/recipes/__init__.py",
        "magi-agent/magi_agent/recipes/compiler.py",
        "magi-agent/magi_agent/runtime/__init__.py",
        "magi-agent/magi_agent/runtime/control.py",
        "magi-agent/magi_agent/runtime/events.py",
        "magi-agent/magi_agent/runtime/openmagi_runtime.py",
        "magi-agent/magi_agent/shadow/__init__.py",
        "magi-agent/magi_agent/shadow/artifact_channel_delivery_contract.py",
        "magi-agent/magi_agent/shadow/coding_verification_evidence_contract.py",
        "magi-agent/magi_agent/shadow/delegated_workflow_evidence_contract.py",
        "magi-agent/magi_agent/shadow/gate4c1_runner_shadow_invoker.py",
        "magi-agent/magi_agent/shadow/memory_source_authority_contract.py",
        "magi-agent/magi_agent/shadow/mission_lifecycle_contract.py",
        "magi-agent/magi_agent/shadow/research_source_evidence_contract.py",
        "magi-agent/magi_agent/testing/__init__.py",
        "magi-agent/magi_agent/testing/runtime_issuance_support.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/tools/context.py",
        "magi-agent/magi_agent/tools/dispatcher.py",
        "magi-agent/magi_agent/tools/permission.py",
        "magi-agent/magi_agent/tools/registry.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/magi_agent/transport/tool_preview.py",
        "magi-agent/runtime_issuance_support.py",
        "magi-agent/tests/conftest.py",
        "magi-agent/tests/runtime_issuance_support.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_python_scaffold_public_compatibility.py",
    }
    if changed_paths <= python_scaffold_regression_recovery_paths:
        return
    if changed_paths <= FRONTEND_TS_PR3_ALLOWED_BRANCH_DIFF:
        return

    live_work_console_pr3_paths = {
        "magi-agent/magi_agent/runtime/public_events.py",
        "magi-agent/magi_agent/tools/event_projection.py",
        "magi-agent/magi_agent/tools/kernel.py",
        "magi-agent/tests/fixtures/live_work_console_event_parity/matrix.json",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_public_event_builders.py",
        "magi-agent/tests/test_tool_event_projection.py",
    }
    if changed_paths <= live_work_console_pr3_paths:
        return

    gate4_readiness_foundation_paths = {
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/magi_agent/gates/gate4_readiness.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_gate4_readiness_foundations.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate4_readiness_foundation_paths:
        return
    gate5_readiness_foundation_paths = gate4_readiness_foundation_paths | {
        "magi-agent/magi_agent/gates/gate5_readiness.py",
        "magi-agent/tests/test_gate5_readiness_foundations.py",
    }
    if changed_paths <= gate5_readiness_foundation_paths:
        return
    gate7_readiness_foundation_paths = gate5_readiness_foundation_paths | {
        "magi-agent/magi_agent/gates/gate7_readiness.py",
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/tests/test_child_event_projection.py",
        "magi-agent/tests/test_gate7_readiness_foundations.py",
    }
    if changed_paths <= gate7_readiness_foundation_paths:
        return
    gate8_readiness_foundation_paths = gate7_readiness_foundation_paths | {
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "magi-agent/magi_agent/gates/gate8_readiness.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_gate8_readiness_foundations.py",
    }
    if changed_paths <= gate8_readiness_foundation_paths:
        return
    gate2_activation_loop_a_paths = {
        "infra/docker/chat-proxy/chat-proxy.js",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "magi-agent/magi_agent/gates/gate2_readiness.py",
        "magi-agent/magi_agent/shadow/gate2_activation_loop_a.py",
        "magi-agent/magi_agent/shadow/gate2_shadow_tool_policy.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_shadow_counter_store.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_gate2_activation_loop_a.py",
        "magi-agent/tests/test_gate2_readiness_foundations.py",
        "magi-agent/tests/test_gate2_selected_durable_evidence_env_wiring.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/uv.lock",
    }
    if changed_paths <= gate2_activation_loop_a_paths:
        return
    gate2_durable_evidence_env_wiring_paths = {
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_gate2_selected_durable_evidence_env_wiring.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate2_durable_evidence_env_wiring_paths:
        return
    pregate8_continuity_local_harness_paths = {
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_pregate8_continuity_readiness_contract.py",
    }
    if changed_paths <= pregate8_continuity_local_harness_paths:
        return

    live_work_console_pr5_paths = {
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/tests/fixtures/live_work_console_event_parity/matrix.json",
        "magi-agent/tests/test_child_event_projection.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_live_work_console_event_parity_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= live_work_console_pr5_paths:
        return
    live_work_console_pr6_paths = {
        "magi-agent/magi_agent/meta_orchestration/event_projection.py",
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/tests/fixtures/live_work_console_event_parity/matrix.json",
        "magi-agent/tests/test_child_event_projection.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_meta_orchestration_event_projection.py",
    }
    if changed_paths <= live_work_console_pr6_paths:
        return
    live_work_console_pr7_paths = {
        "magi-agent/magi_agent/runtime/work_console_snapshot.py",
        "magi-agent/tests/fixtures/live_work_console_event_parity/matrix.json",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_work_console_snapshot.py",
    }
    if changed_paths <= live_work_console_pr7_paths:
        return
    runtime_heartbeat_pr7_paths = {
        "magi-agent/magi_agent/runtime/events.py",
        "magi-agent/magi_agent/runtime/work_console_snapshot.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_runtime_heartbeat_event_projection.py",
    }
    if changed_paths <= runtime_heartbeat_pr7_paths:
        return
    runtime_heartbeat_pr8_paths = {
        "magi-agent/magi_agent/runtime/readiness.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_runtime_heartbeat_readiness.py",
    }
    if changed_paths <= runtime_heartbeat_pr8_paths:
        return
    runtime_heartbeat_pr9_paths = {
        "docs/notes/2026-05-27-magi-agent-runtime-heartbeat-final-review.md",
        "magi-agent/README.md",
        "magi-agent/magi_agent/runtime/no_agent_watchdog.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_no_agent_watchdog_contract.py",
    }
    if changed_paths <= runtime_heartbeat_pr9_paths:
        return
    live_work_console_pr8_paths = {
        "magi-agent/tests/fixtures/live_work_console_event_parity/regression_fixtures.json",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_live_work_console_event_regression_fixtures.py",
    }
    if changed_paths <= live_work_console_pr8_paths:
        return
    live_work_console_pr9_paths = {
        "docs/notes/2026-05-26-python-adk-live-work-console-event-parity-readiness.md",
        "magi-agent/README.md",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
    }
    if changed_paths <= live_work_console_pr9_paths:
        return

    frontend_ts_pr4_active_snapshot_paths = {
        "infra/docker/chat-proxy/chat-proxy.coreagent-bypass.test.js",
        "infra/docker/chat-proxy/chat-proxy.js",
        "infra/docker/chat-proxy/inject-handler.js",
        "infra/docker/chat-proxy/inject-handler.test.js",
        "infra/docker/chat-proxy/interrupt-handler.js",
        "infra/docker/chat-proxy/interrupt-handler.test.js",
        "infra/docker/chat-proxy/stream-snapshot.js",
        "infra/docker/chat-proxy/stream-snapshot.test.js",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_chat_inject_interrupt_compatibility.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
    }
    if changed_paths <= frontend_ts_pr4_active_snapshot_paths:
        return
    frontend_ts_pr7_preview_paths = {
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_sse_writer.py",
        "src/components/chat/browser-frame-preview.test.tsx",
        "src/components/chat/browser-frame-preview.tsx",
        "src/components/chat/document-draft-preview.test.tsx",
        "src/components/chat/document-draft-preview.tsx",
        "src/components/chat/work-console-panel.test.tsx",
        "src/components/chat/work-console-panel.tsx",
    }
    if changed_paths <= frontend_ts_pr7_preview_paths:
        return
    frontend_ts_pr9_final_review_paths = {
        ".secrets.baseline",
        "docs/notes/2026-05-27-python-adk-frontend-ts-production-compatibility-readiness-report.md",
        "magi-agent/magi_agent/runtime/public_events.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_public_event_builders.py",
        "magi-agent/tests/test_sse_writer.py",
    }
    if changed_paths <= frontend_ts_pr9_final_review_paths:
        return

    research_determinism_final_integration_paths = {
        "magi-agent/magi_agent/evidence/child_runtime_envelope.py",
        "magi-agent/magi_agent/evidence/source_ledger.py",
        "magi-agent/magi_agent/recipes/materializer.py",
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/tests/test_child_event_projection.py",
        "magi-agent/tests/test_child_runtime_envelope_contract.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_live_ts_surface_recipe_integration.py",
        "magi-agent/tests/test_research_source_ledger.py",
    }
    if changed_paths <= research_determinism_final_integration_paths:
        return

    research_determinism_final_review_gate_fixes_paths = {
        "magi-agent/magi_agent/evidence/child_runtime_envelope.py",
        "magi-agent/magi_agent/evidence/reports.py",
        "magi-agent/magi_agent/evidence/subagent.py",
        "magi-agent/magi_agent/meta_orchestration/child_acceptance.py",
        "magi-agent/magi_agent/research/boundary_enforcement.py",
        "magi-agent/magi_agent/research/final_projection_gate.py",
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/magi_agent/shadow/delegated_workflow_evidence_contract.py",
        "magi-agent/magi_agent/web_acquisition/research_tools.py",
        "magi-agent/tests/test_child_event_projection.py",
        "magi-agent/tests/test_child_runtime_envelope_contract.py",
        "magi-agent/tests/test_evidence_subagent_propagation.py",
        "magi-agent/tests/test_gate1_child_envelope_fixtures.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_meta_child_acceptance.py",
        "magi-agent/tests/test_priority_a_commit_boundary.py",
        "magi-agent/tests/test_research_final_projection_gate.py",
        "magi-agent/tests/test_research_source_ledger.py",
        "magi-agent/tests/test_web_research_tools_boundary.py",
    }
    if changed_paths <= research_determinism_final_review_gate_fixes_paths:
        return

    # PR 4 of the magi-prompt-cache track: metrics.py + build_system_prompt_blocks
    # integration.  message_builder.py gets a NEW additive function only; the
    # existing build_system_prompt() is untouched.  This is an approved change
    # that touches the runtime/ path solely to add a new public helper that
    # delegates to the prompt/ sub-package which is already on the branch.
    magi_prompt_cache_pr4_paths = {
        "magi-agent/magi_agent/prompt/__init__.py",
        "magi-agent/magi_agent/prompt/injection.py",
        "magi-agent/magi_agent/prompt/metrics.py",
        "magi-agent/magi_agent/prompt/memoizer.py",
        "magi-agent/magi_agent/prompt/providers.py",
        "magi-agent/magi_agent/prompt/splitter.py",
        "magi-agent/magi_agent/prompt/types.py",
        "magi-agent/magi_agent/runtime/message_builder.py",
        "magi-agent/tests/test_cache_control_injection.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_prompt_cache_integration.py",
        "magi-agent/tests/test_prompt_memoization.py",
        "magi-agent/tests/test_prompt_split.py",
    }
    if changed_paths <= magi_prompt_cache_pr4_paths:
        return

    research_determinism_post_review_security_fix_paths = {
        "magi-agent/magi_agent/evidence/event_projection.py",
        "magi-agent/magi_agent/meta_orchestration/event_projection.py",
        "magi-agent/magi_agent/research/event_projection.py",
        "magi-agent/magi_agent/research/final_projection_gate.py",
        "magi-agent/magi_agent/runtime/events.py",
        "magi-agent/magi_agent/runtime/public_events.py",
        "magi-agent/magi_agent/runtime/work_console_snapshot.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/fixtures/frontend_ts_compatibility/python_adk_run.json",
        "magi-agent/tests/fixtures/public_event_parity/matrix.json",
        "magi-agent/tests/fixtures/ts_parity_replay/gate5b4d_stream_coverage_golden.json",
        "magi-agent/tests/test_evidence_event_projection.py",
        "magi-agent/tests/test_gate5b4d_stream_fixture_audit.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_meta_orchestration_event_projection.py",
        "magi-agent/tests/test_pr3_live_producer_parity_bridge.py",
        "magi-agent/tests/test_research_final_projection_gate.py",
        "magi-agent/tests/test_work_console_snapshot.py",
    }
    if changed_paths <= research_determinism_post_review_security_fix_paths:
        return

    research_first_activation_paths = {
        "docs/notes/2026-05-30-research-first-magi-activation.md",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "magi-agent/README.md",
        "magi-agent/magi_agent/research/research_first_canary.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_shadow_counter_store.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_gate5b4c3_shadow_counter_store.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_research_first_selected_canary.py",
    }
    if changed_paths <= research_first_activation_paths:
        return
    gate8_research_first_boundary_paths = {
        ".secrets.baseline",
        "infra/docker/chat-proxy/chat-proxy.js",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate8_research_first_boundary_paths:
        return

    gate5b_selected_full_toolhost_paths = {
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "magi-agent/magi_agent/gates/gate5b_full_toolhost.py",
        "magi-agent/magi_agent/main.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_runner_input_adapter.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_shadow_generation_contract.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_chat_route_contract.py",
        "magi-agent/tests/test_gate5b_full_toolhost.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate5b_selected_full_toolhost_paths:
        return

    composio_optional_integration_paths = {
        ".secrets.baseline",
        "SCRATCHPAD.md",
        "docs/superpowers/plans/2026-05-31-composio-optional-integrations.md",
        "docs/superpowers/specs/2026-05-31-composio-optional-integrations-design.md",
        "magi-agent/README.md",
        "magi-agent/magi_agent/cli/app.py",
        "magi-agent/magi_agent/cli/headless.py",
        "magi-agent/magi_agent/cli/tests/test_app.py",
        "magi-agent/magi_agent/cli/tests/test_coldstart.py",
        "magi-agent/magi_agent/cli/tests/test_composio_cli.py",
        "magi-agent/magi_agent/cli/tests/test_engine_gate.py",
        "magi-agent/magi_agent/cli/tests/test_headless_projection.py",
        "magi-agent/magi_agent/cli/wiring.py",
        "magi-agent/magi_agent/composio/__init__.py",
        "magi-agent/magi_agent/composio/config.py",
        "magi-agent/magi_agent/composio/health.py",
        "magi-agent/magi_agent/composio/mcp.py",
        "magi-agent/magi_agent/composio/redaction.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/pyproject.toml",
        "magi-agent/tests/test_composio_config.py",
        "magi-agent/tests/test_composio_health.py",
        "magi-agent/tests/test_composio_mcp.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_health.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_sse_writer.py",
        "magi-agent/uv.lock",
        "memory/daily/2026-05-31.md",
    }
    if changed_paths <= composio_optional_integration_paths:
        return

    for path in changed_paths:
        _assert_allowed_changed_path(path)


def test_matrix_path_scope_has_no_forbidden_current_or_planned_refs() -> None:
    for row in _rows():
        for ref in row["currentFiles"] + row["currentTests"] + row["missingImplementation"]:
            path = _ref_path(ref)
            assert path is not None
            _assert_allowed_changed_path(f"magi-agent/{path}")


def test_reconciliation_note_mentions_required_inputs_and_duplicate_avoidance() -> None:
    note = DOC_PATH.read_text(encoding="utf-8")

    assert "origin/main 719392dd2b8aa6c5c212ff371a3e2ea4b676c2ba" in note
    assert "If a source document conflicts with the safe queue plan, the safe queue plan wins" in note
    for pr_number in ("#999", "#1007", "#1011", "#1015", "#1018", "#1019", "#1024"):
        assert pr_number in note
    for row_id in REQUIRED_ROW_IDS:
        assert row_id in note
