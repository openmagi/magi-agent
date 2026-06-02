from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from types import ModuleType


EXPECTED_GROUP_A_PATHS = (
    "turn_scoped_model_provider_routing",
    "provider_capability_metadata",
    "runner_invocation_metadata_projection",
    "retry_fallback_policy_metadata",
    "empty_response_fallback_metadata",
    "polling_downgrade_restore_metadata",
    "route_cache_metadata",
)


def _readiness_module() -> ModuleType:
    return importlib.import_module("openmagi_core_agent.runtime.readiness")


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_priority_a_readiness_is_default_off_and_non_authoritative() -> None:
    readiness = _readiness_module()

    snapshot = readiness.build_priority_a_readiness_snapshot()

    assert snapshot.schema_version == "priorityA.localReadiness.v1"
    assert snapshot.priority_group == "A"
    assert snapshot.enabled is False
    assert snapshot.readiness_status == "not_ready"
    assert snapshot.diagnostic_ready is False
    assert snapshot.selected_turn_ready is False
    assert snapshot.replacement_ready is False
    assert snapshot.response_authority == "none"
    assert snapshot.future_live_primitives == (
        "ADK Runner",
        "ADK Agent",
        "ADK Event",
        "ADK SessionService",
    )

    flags = snapshot.authority_flags
    assert flags.user_visible_output_allowed is False
    assert flags.canary_routing_allowed is False
    assert flags.toolhost_active is False
    assert flags.memory_provider_active is False
    assert flags.transcript_writes_allowed is False
    assert flags.sse_writes_allowed is False
    assert flags.channel_writes_allowed is False
    assert flags.db_writes_allowed is False
    assert flags.workspace_mutation_allowed is False
    assert flags.child_execution_allowed is False
    assert flags.mission_runtime_allowed is False
    assert flags.artifact_delivery_allowed is False
    assert flags.evidence_block_mode_allowed is False


def test_priority_a_group_paths_are_diagnostic_metadata_only() -> None:
    readiness = _readiness_module()

    snapshot = readiness.build_priority_a_readiness_snapshot()

    assert tuple(path.path_key for path in snapshot.paths) == EXPECTED_GROUP_A_PATHS
    for path in snapshot.paths:
        assert path.priority_group == "A"
        assert path.enabled is False
        assert path.diagnostic_ready is False
        assert path.live_ready is False
        assert path.status == "not_ready"
        assert path.response_authority == "none"
        assert path.reason in {
            "disabled_by_default",
            "local_diagnostic_metadata_only",
        }


def test_priority_a_authority_flags_cannot_be_enabled_by_construct_or_copy() -> None:
    readiness = _readiness_module()

    flags = readiness.PriorityAReadinessAuthorityFlags.model_construct(
        userVisibleOutputAllowed=True,
        canaryRoutingAllowed=True,
        toolHostActive=True,
        memoryProviderActive=True,
        transcriptWritesAllowed=True,
        sseWritesAllowed=True,
        channelWritesAllowed=True,
        dbWritesAllowed=True,
        workspaceMutationAllowed=True,
        childExecutionAllowed=True,
        missionRuntimeAllowed=True,
        artifactDeliveryAllowed=True,
        evidenceBlockModeAllowed=True,
    )
    copied = flags.model_copy(
        update={
            "userVisibleOutputAllowed": True,
            "toolHostActive": True,
            "memoryProviderActive": True,
            "evidenceBlockModeAllowed": True,
        }
    )

    assert flags.user_visible_output_allowed is False
    assert flags.canary_routing_allowed is False
    assert flags.toolhost_active is False
    assert flags.memory_provider_active is False
    assert flags.transcript_writes_allowed is False
    assert flags.sse_writes_allowed is False
    assert flags.channel_writes_allowed is False
    assert flags.db_writes_allowed is False
    assert flags.workspace_mutation_allowed is False
    assert flags.child_execution_allowed is False
    assert flags.mission_runtime_allowed is False
    assert flags.artifact_delivery_allowed is False
    assert flags.evidence_block_mode_allowed is False
    assert copied.user_visible_output_allowed is False
    assert copied.toolhost_active is False
    assert copied.memory_provider_active is False
    assert copied.evidence_block_mode_allowed is False


def test_priority_a_snapshot_revalidates_bypass_attempts_to_false_only() -> None:
    readiness = _readiness_module()
    baseline = readiness.build_priority_a_readiness_snapshot()

    snapshot = readiness.PriorityAReadinessSnapshot.model_validate(
        baseline.model_dump(by_alias=True, mode="python")
        | {
            "enabled": True,
            "readinessStatus": "ready",
            "diagnosticReady": True,
            "selectedTurnReady": True,
            "replacementReady": True,
            "responseAuthority": "python",
            "authorityFlags": {
                "userVisibleOutputAllowed": True,
                "canaryRoutingAllowed": True,
                "toolHostActive": True,
                "memoryProviderActive": True,
                "transcriptWritesAllowed": True,
                "sseWritesAllowed": True,
                "channelWritesAllowed": True,
                "dbWritesAllowed": True,
                "workspaceMutationAllowed": True,
                "childExecutionAllowed": True,
                "missionRuntimeAllowed": True,
                "artifactDeliveryAllowed": True,
                "evidenceBlockModeAllowed": True,
            },
        }
    )

    assert snapshot.enabled is False
    assert snapshot.readiness_status == "not_ready"
    assert snapshot.diagnostic_ready is False
    assert snapshot.selected_turn_ready is False
    assert snapshot.replacement_ready is False
    assert snapshot.response_authority == "none"
    assert snapshot.authority_flags.user_visible_output_allowed is False
    assert snapshot.authority_flags.canary_routing_allowed is False
    assert snapshot.authority_flags.toolhost_active is False
    assert snapshot.authority_flags.memory_provider_active is False


def test_priority_a_readiness_import_stays_runtime_only() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

before = set(sys.modules)
module = importlib.import_module("openmagi_core_agent.runtime.readiness")
assert hasattr(module, "build_priority_a_readiness_snapshot")

forbidden_prefixes = (
    "google.adk",
    "google.generativeai",
    "google.cloud",
    "openai",
    "anthropic",
    "httpx",
    "requests",
    "fastapi",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
    "openmagi_core_agent.app",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.workspace",
)
loaded = [
    module_name
    for module_name in set(sys.modules) - before
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"priority A readiness import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_priority_a_readiness_source_has_no_route_runner_or_write_activation() -> None:
    readiness = _readiness_module()
    source = inspect.getsource(readiness)

    forbidden_snippets = (
        "FastAPI(",
        "APIRouter(",
        "@app.",
        "add_api_route",
        "include_router",
        "Runner(",
        ".run_async(",
        "FunctionTool(",
        "LongRunningFunctionTool(",
        "open(",
        ".write_text(",
        ".write_bytes(",
        ".mkdir(",
        "subprocess.",
        "requests.",
        "httpx.",
        "supabase",
        "psycopg",
        "asyncpg",
        "kubernetes",
    )
    found = [snippet for snippet in forbidden_snippets if snippet in source]

    assert found == []
