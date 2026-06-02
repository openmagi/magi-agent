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
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/config/",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/routing/",
    "infra/docker/chat-proxy/",
)
FORBIDDEN_EXACT_PATHS = {
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/registry.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/dispatcher.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/permission.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/result.py",
}
FRONTEND_TS_PR3_ALLOWED_BRANCH_DIFF = {
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/event_adapter.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/events.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
    "infra/docker/clawy-core-agent-python/tests/fixtures/public_event_parity/matrix.json",
    "infra/docker/clawy-core-agent-python/tests/fixtures/ts_parity_replay/gate5b4d_stream_coverage_golden.json",
    "infra/docker/clawy-core-agent-python/tests/fixtures/ts_parity_replay/public_event_golden.json",
    "infra/docker/clawy-core-agent-python/tests/test_event_bridge.py",
    "infra/docker/clawy-core-agent-python/tests/test_gate5b4d_stream_fixture_audit.py",
    "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
    "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
    "infra/docker/clawy-core-agent-python/tests/test_pr3_event_adapter_live_compatibility.py",
    "infra/docker/clawy-core-agent-python/tests/test_pr3_live_producer_parity_bridge.py",
    "infra/docker/clawy-core-agent-python/tests/test_public_event_builders.py",
    "infra/docker/clawy-core-agent-python/tests/test_public_event_golden_fixtures.py",
}
MAINLINE_SCAFFOLD_SECRET_FIXTURE_RESCUE_PATHS = {
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/tool_adapter.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/hooks/executors/__init__.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/catalog.py",
    "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/concurrent_dispatcher.py",
    "infra/docker/clawy-core-agent-python/tests/hooks/test_manifest_external.py",
    "infra/docker/clawy-core-agent-python/tests/test_core_tool_catalog.py",
    "infra/docker/clawy-core-agent-python/tests/test_deferred_adk_integration.py",
    "infra/docker/clawy-core-agent-python/tests/test_gate5a_no_memory_shadow_canary.py",
    "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
    "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
    "infra/docker/clawy-core-agent-python/tests/test_tool_admin_routes.py",
    "infra/docker/clawy-core-agent-python/tests/test_tool_safety_annotations.py",
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
            "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/tool_adapter.py",
        }
    )
    assert not _allows_mainline_scaffold_secret_fixture_rescue_paths(
        MAINLINE_SCAFFOLD_SECRET_FIXTURE_RESCUE_PATHS
        - {
            "infra/docker/clawy-core-agent-python/tests/test_tool_safety_annotations.py",
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
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/tool_adapter.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/catalog.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/concurrency.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/tool_search.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_mcp_adapter.py",
        "infra/docker/clawy-core-agent-python/tests/test_tool_safety_annotations.py",
    }
    scaffold_parallel_safety_required_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/tool_adapter.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/catalog.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/concurrency.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/tool_search.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_mcp_adapter.py",
        "infra/docker/clawy-core-agent-python/tests/test_tool_safety_annotations.py",
    }
    if (
        changed_paths <= scaffold_parallel_safety_rescue_paths
        and scaffold_parallel_safety_required_paths <= changed_paths
    ):
        return
    python_scaffold_regression_recovery_paths = {
        "infra/docker/clawy-core-agent-python/README.md",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/event_adapter.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/local_runner.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/primitives.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/runner_adapter.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/session_service.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/adk_bridge/tool_adapter.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/app.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/config/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/config/env.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/config/models.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/builtin.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/child_runtime_envelope.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/reports.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/source_ledger.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/subagent.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/types.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/harness/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/harness/verifier_bus.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/main.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/memory/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/memory/adapters/hipocampus_readonly.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/memory/contracts.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/memory/policy.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/plugins/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/plugins/manager.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/plugins/manifest.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/plugins/native_catalog.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/plugins/tool_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/recipes/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/recipes/compiler.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/control.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/events.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/openmagi_runtime.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/artifact_channel_delivery_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/coding_verification_evidence_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/delegated_workflow_evidence_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/gate4c1_runner_shadow_invoker.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/memory_source_authority_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/mission_lifecycle_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/research_source_evidence_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/testing/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/testing/runtime_issuance_support.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/catalog.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/context.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/dispatcher.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/permission.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/registry.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/chat.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/health.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/tool_preview.py",
        "infra/docker/clawy-core-agent-python/runtime_issuance_support.py",
        "infra/docker/clawy-core-agent-python/tests/conftest.py",
        "infra/docker/clawy-core-agent-python/tests/runtime_issuance_support.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_python_scaffold_public_compatibility.py",
    }
    if changed_paths <= python_scaffold_regression_recovery_paths:
        return
    if changed_paths <= FRONTEND_TS_PR3_ALLOWED_BRANCH_DIFF:
        return

    live_work_console_pr3_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/public_events.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/event_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/kernel.py",
        "infra/docker/clawy-core-agent-python/tests/fixtures/live_work_console_event_parity/matrix.json",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_public_event_builders.py",
        "infra/docker/clawy-core-agent-python/tests/test_tool_event_projection.py",
    }
    if changed_paths <= live_work_console_pr3_paths:
        return

    gate4_readiness_foundation_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/config/env.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/config/models.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/gates/gate4_readiness.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/health.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate4_readiness_foundations.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate4_readiness_foundation_paths:
        return
    gate5_readiness_foundation_paths = gate4_readiness_foundation_paths | {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/gates/gate5_readiness.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate5_readiness_foundations.py",
    }
    if changed_paths <= gate5_readiness_foundation_paths:
        return
    gate7_readiness_foundation_paths = gate5_readiness_foundation_paths | {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/gates/gate7_readiness.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate7_readiness_foundations.py",
    }
    if changed_paths <= gate7_readiness_foundation_paths:
        return
    gate8_readiness_foundation_paths = gate7_readiness_foundation_paths | {
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/gates/gate8_readiness.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/chat.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate8_readiness_foundations.py",
    }
    if changed_paths <= gate8_readiness_foundation_paths:
        return
    gate2_activation_loop_a_paths = {
        "infra/docker/chat-proxy/chat-proxy.js",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/gates/gate2_readiness.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/gate2_activation_loop_a.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/gate2_shadow_tool_policy.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/gate5b4c3_shadow_counter_store.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/tools/catalog.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/chat.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/health.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate2_activation_loop_a.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate2_readiness_foundations.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate2_selected_durable_evidence_env_wiring.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/uv.lock",
    }
    if changed_paths <= gate2_activation_loop_a_paths:
        return
    gate2_durable_evidence_env_wiring_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/chat.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate2_selected_durable_evidence_env_wiring.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate2_durable_evidence_env_wiring_paths:
        return
    pregate8_continuity_local_harness_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/config/env.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_pregate8_continuity_readiness_contract.py",
    }
    if changed_paths <= pregate8_continuity_local_harness_paths:
        return

    live_work_console_pr5_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/fixtures/live_work_console_event_parity/matrix.json",
        "infra/docker/clawy-core-agent-python/tests/test_child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_live_work_console_event_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= live_work_console_pr5_paths:
        return
    live_work_console_pr6_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/meta_orchestration/event_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/fixtures/live_work_console_event_parity/matrix.json",
        "infra/docker/clawy-core-agent-python/tests/test_child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_meta_orchestration_event_projection.py",
    }
    if changed_paths <= live_work_console_pr6_paths:
        return
    live_work_console_pr7_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/work_console_snapshot.py",
        "infra/docker/clawy-core-agent-python/tests/fixtures/live_work_console_event_parity/matrix.json",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_work_console_snapshot.py",
    }
    if changed_paths <= live_work_console_pr7_paths:
        return
    runtime_heartbeat_pr7_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/events.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/work_console_snapshot.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_runtime_heartbeat_event_projection.py",
    }
    if changed_paths <= runtime_heartbeat_pr7_paths:
        return
    runtime_heartbeat_pr8_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/readiness.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/health.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_runtime_heartbeat_readiness.py",
    }
    if changed_paths <= runtime_heartbeat_pr8_paths:
        return
    runtime_heartbeat_pr9_paths = {
        "docs/notes/2026-05-27-magi-agent-runtime-heartbeat-final-review.md",
        "infra/docker/clawy-core-agent-python/README.md",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/no_agent_watchdog.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_no_agent_watchdog_contract.py",
    }
    if changed_paths <= runtime_heartbeat_pr9_paths:
        return
    live_work_console_pr8_paths = {
        "infra/docker/clawy-core-agent-python/tests/fixtures/live_work_console_event_parity/regression_fixtures.json",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_live_work_console_event_regression_fixtures.py",
    }
    if changed_paths <= live_work_console_pr8_paths:
        return
    live_work_console_pr9_paths = {
        "docs/notes/2026-05-26-python-adk-live-work-console-event-parity-readiness.md",
        "infra/docker/clawy-core-agent-python/README.md",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
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
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/chat.py",
        "infra/docker/clawy-core-agent-python/tests/test_chat_inject_interrupt_compatibility.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
    }
    if changed_paths <= frontend_ts_pr4_active_snapshot_paths:
        return
    frontend_ts_pr7_preview_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_sse_writer.py",
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
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/public_events.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_public_event_builders.py",
        "infra/docker/clawy-core-agent-python/tests/test_sse_writer.py",
    }
    if changed_paths <= frontend_ts_pr9_final_review_paths:
        return

    research_determinism_final_integration_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/child_runtime_envelope.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/source_ledger.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/recipes/materializer.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_child_runtime_envelope_contract.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_live_ts_surface_recipe_integration.py",
        "infra/docker/clawy-core-agent-python/tests/test_research_source_ledger.py",
    }
    if changed_paths <= research_determinism_final_integration_paths:
        return

    research_determinism_final_review_gate_fixes_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/child_runtime_envelope.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/reports.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/subagent.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/meta_orchestration/child_acceptance.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/research/boundary_enforcement.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/research/final_projection_gate.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/child_event_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/delegated_workflow_evidence_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/web_acquisition/research_tools.py",
        "infra/docker/clawy-core-agent-python/tests/test_child_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_child_runtime_envelope_contract.py",
        "infra/docker/clawy-core-agent-python/tests/test_evidence_subagent_propagation.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate1_child_envelope_fixtures.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_meta_child_acceptance.py",
        "infra/docker/clawy-core-agent-python/tests/test_priority_a_commit_boundary.py",
        "infra/docker/clawy-core-agent-python/tests/test_research_final_projection_gate.py",
        "infra/docker/clawy-core-agent-python/tests/test_research_source_ledger.py",
        "infra/docker/clawy-core-agent-python/tests/test_web_research_tools_boundary.py",
    }
    if changed_paths <= research_determinism_final_review_gate_fixes_paths:
        return

    # PR 4 of the magi-prompt-cache track: metrics.py + build_system_prompt_blocks
    # integration.  message_builder.py gets a NEW additive function only; the
    # existing build_system_prompt() is untouched.  This is an approved change
    # that touches the runtime/ path solely to add a new public helper that
    # delegates to the prompt/ sub-package which is already on the branch.
    magi_prompt_cache_pr4_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/prompt/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/prompt/injection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/prompt/metrics.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/prompt/memoizer.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/prompt/providers.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/prompt/splitter.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/prompt/types.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/message_builder.py",
        "infra/docker/clawy-core-agent-python/tests/test_cache_control_injection.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_prompt_cache_integration.py",
        "infra/docker/clawy-core-agent-python/tests/test_prompt_memoization.py",
        "infra/docker/clawy-core-agent-python/tests/test_prompt_split.py",
    }
    if changed_paths <= magi_prompt_cache_pr4_paths:
        return

    research_determinism_post_review_security_fix_paths = {
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/evidence/event_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/meta_orchestration/event_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/research/event_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/research/final_projection_gate.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/events.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/public_events.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/runtime/work_console_snapshot.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/tests/fixtures/frontend_ts_compatibility/python_adk_run.json",
        "infra/docker/clawy-core-agent-python/tests/fixtures/public_event_parity/matrix.json",
        "infra/docker/clawy-core-agent-python/tests/fixtures/ts_parity_replay/gate5b4d_stream_coverage_golden.json",
        "infra/docker/clawy-core-agent-python/tests/test_evidence_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate5b4d_stream_fixture_audit.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_meta_orchestration_event_projection.py",
        "infra/docker/clawy-core-agent-python/tests/test_pr3_live_producer_parity_bridge.py",
        "infra/docker/clawy-core-agent-python/tests/test_research_final_projection_gate.py",
        "infra/docker/clawy-core-agent-python/tests/test_work_console_snapshot.py",
    }
    if changed_paths <= research_determinism_post_review_security_fix_paths:
        return

    research_first_activation_paths = {
        "docs/notes/2026-05-30-research-first-magi-activation.md",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/clawy-core-agent-python/README.md",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/research/research_first_canary.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/gate5b4c3_shadow_counter_store.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/chat.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate5b4c3_shadow_counter_store.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_research_first_selected_canary.py",
    }
    if changed_paths <= research_first_activation_paths:
        return
    gate8_research_first_boundary_paths = {
        ".secrets.baseline",
        "infra/docker/chat-proxy/chat-proxy.js",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate8_research_first_boundary_paths:
        return

    gate5b_selected_full_toolhost_paths = {
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/gates/gate5b_full_toolhost.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/main.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/gate5b4c3_runner_input_adapter.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/shadow/gate5b4c3_shadow_generation_contract.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/chat.py",
        "infra/docker/clawy-core-agent-python/tests/test_chat_route_contract.py",
        "infra/docker/clawy-core-agent-python/tests/test_gate5b_full_toolhost.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed_paths <= gate5b_selected_full_toolhost_paths:
        return

    composio_optional_integration_paths = {
        ".secrets.baseline",
        "SCRATCHPAD.md",
        "docs/superpowers/plans/2026-05-31-composio-optional-integrations.md",
        "docs/superpowers/specs/2026-05-31-composio-optional-integrations-design.md",
        "infra/docker/clawy-core-agent-python/README.md",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/app.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/headless.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/tests/test_app.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/tests/test_coldstart.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/tests/test_composio_cli.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/tests/test_engine_gate.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/tests/test_headless_projection.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/cli/wiring.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/composio/__init__.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/composio/config.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/composio/health.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/composio/mcp.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/composio/redaction.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/health.py",
        "infra/docker/clawy-core-agent-python/openmagi_core_agent/transport/sse.py",
        "infra/docker/clawy-core-agent-python/pyproject.toml",
        "infra/docker/clawy-core-agent-python/tests/test_composio_config.py",
        "infra/docker/clawy-core-agent-python/tests/test_composio_health.py",
        "infra/docker/clawy-core-agent-python/tests/test_composio_mcp.py",
        "infra/docker/clawy-core-agent-python/tests/test_general_automation_safe_queue_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_health.py",
        "infra/docker/clawy-core-agent-python/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "infra/docker/clawy-core-agent-python/tests/test_sse_writer.py",
        "infra/docker/clawy-core-agent-python/uv.lock",
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
            _assert_allowed_changed_path(f"infra/docker/clawy-core-agent-python/{path}")


def test_reconciliation_note_mentions_required_inputs_and_duplicate_avoidance() -> None:
    note = DOC_PATH.read_text(encoding="utf-8")

    assert "origin/main 719392dd2b8aa6c5c212ff371a3e2ea4b676c2ba" in note
    assert "If a source document conflicts with the safe queue plan, the safe queue plan wins" in note
    for pr_number in ("#999", "#1007", "#1011", "#1015", "#1018", "#1019", "#1024"):
        assert pr_number in note
    for row_id in REQUIRED_ROW_IDS:
        assert row_id in note
