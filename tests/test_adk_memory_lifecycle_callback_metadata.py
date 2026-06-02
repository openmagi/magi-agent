from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "adk_memory_lifecycle"

EXPECTED_CASE_ORDER = (
    "before_agent_callback_before_turn_start_snapshot_only",
    "before_model_callback_before_llm_call_source_authority_no_projection",
    "after_model_callback_after_llm_call_redacted_audit_only",
    "after_agent_callback_after_turn_end_write_intent_requires_receipt",
    "on_model_error_callback_on_error_provider_failure_fail_open_no_claim",
)

FALSE_ONLY_FLAGS = (
    "adkRunnerInvoked",
    "adkMemoryServiceReplaced",
    "liveProviderCalls",
    "hipocampusQmdCalls",
    "agentMemoryCalls",
    "promptInjection",
    "memoryWrites",
    "routesAttached",
    "productionStorage",
    "canaryTraffic",
    "userVisibleAuthority",
)


def load_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))


def assert_false_only_flags(flags: dict[str, Any]) -> None:
    assert tuple(flags) == FALSE_ONLY_FLAGS
    assert set(flags.values()) == {False}


def test_adk_memory_lifecycle_callback_fixture_covers_metadata_only_policy() -> None:
    payload = load_fixture()

    assert payload["schemaVersion"] == "adkMemoryLifecycleCallbackMetadataFixture.v1"
    assert payload["fixtureId"] == "adk_memory_lifecycle_callback_metadata_matrix_0001"
    assert payload["recordingMode"] == "local_diagnostic_fixture"
    assert payload["adkFirst"] == {
        "adkOwns": [
            "MemoryService",
            "callback_lifecycle_primitives",
        ],
        "openMagiOwns": [
            "memory_mode",
            "source_authority",
            "continuity",
            "receipts",
            "redaction",
            "tenant_scope",
            "audit_metadata",
        ],
        "memoryEnabledApproval": False,
    }
    assert_false_only_flags(payload["attachmentFlags"])

    cases = {case["caseId"]: case for case in payload["cases"]}
    assert tuple(cases) == EXPECTED_CASE_ORDER

    before_agent = cases["before_agent_callback_before_turn_start_snapshot_only"]
    assert before_agent["adkCallback"] == "before_agent_callback"
    assert before_agent["lifecyclePoint"] == "beforeTurnStart"
    assert before_agent["metadataKind"] == "memory_mode_source_scope_snapshot"
    assert before_agent["metadata"] == {
        "memoryMode": "normal",
        "sourceScope": "tenant_bot_session",
        "continuity": "snapshot_only",
        "liveMemoryAttached": False,
    }
    assert before_agent["promptProjectionAllowed"] is False
    assert before_agent["liveMemoryAttached"] is False

    before_model = cases[
        "before_model_callback_before_llm_call_source_authority_no_projection"
    ]
    assert before_model["adkCallback"] == "before_model_callback"
    assert before_model["lifecyclePoint"] == "beforeLLMCall"
    assert before_model["metadataKind"] == "source_authority"
    assert before_model["metadata"]["sourceAuthority"] == "current_turn_authoritative"
    assert before_model["promptProjectionAllowed"] is False
    assert before_model["reasonCodes"] == ["source_authority_recorded_no_prompt_projection"]

    after_model = cases["after_model_callback_after_llm_call_redacted_audit_only"]
    assert after_model["adkCallback"] == "after_model_callback"
    assert after_model["lifecyclePoint"] == "afterLLMCall"
    assert after_model["metadataKind"] == "redacted_audit"
    assert after_model["redactionStatus"] == "verified"
    assert after_model["metadata"] == {
        "auditRef": "audit:adk-memory-lifecycle:after-model:redacted",
        "redacted": True,
        "rawModelResponseCaptured": False,
        "rawMemoryPayloadCaptured": False,
    }

    after_agent = cases[
        "after_agent_callback_after_turn_end_write_intent_requires_receipt"
    ]
    assert after_agent["adkCallback"] == "after_agent_callback"
    assert after_agent["lifecyclePoint"] == "afterTurnEnd"
    assert after_agent["metadataKind"] == "observe_write_intent_requires_receipt"
    assert after_agent["writeIntent"] is True
    assert after_agent["writeReceiptRef"] is None
    assert after_agent["writeClaimAllowed"] is False
    assert "write_intent_observed_receipt_required" in after_agent["reasonCodes"]

    on_error = cases[
        "on_model_error_callback_on_error_provider_failure_fail_open_no_claim"
    ]
    assert on_error["adkCallback"] == "on_model_error_callback"
    assert on_error["lifecyclePoint"] == "onError"
    assert on_error["metadataKind"] == "provider_failure_fail_open"
    assert on_error["decision"] == "fail_open_no_claim"
    assert on_error["providerStatus"] == "failed"
    assert on_error["writeClaimAllowed"] is False
    assert on_error["noUserFacingMemoryClaim"] is True

    for case in payload["cases"]:
        assert_false_only_flags(case["attachmentFlags"])
        assert case["liveMemoryAttached"] is False
        assert case["memoryServiceReplacementAllowed"] is False
        assert case["promptProjectionAllowed"] is False
        assert case["memoryWriteAllowed"] is False
        assert case["routeOrProxyAttached"] is False
        assert case["productionStorageWritten"] is False
        assert case["canaryTrafficAttached"] is False
        assert case["userVisibleAuthority"] is False


def test_adk_memory_lifecycle_fixture_contains_no_live_or_prompt_payloads() -> None:
    fixture_json = json.dumps(load_fixture(), sort_keys=True)

    unsafe_fragments = (
        "Bearer unsafe",
        "ghp_memorysecret",
        "sk-memory-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        "raw memory payload",
        "google.adk.memory.MemoryService",
        "AgentMemory",
        "adkRunnerInvoked\": true",
        "adkMemoryServiceReplaced\": true",
        "liveProviderCalls\": true",
        "hipocampusQmdCalls\": true",
        "agentMemoryCalls\": true",
        "promptInjection\": true",
        "memoryWrites\": true",
        "routesAttached\": true",
        "productionStorage\": true",
        "canaryTraffic\": true",
        "userVisibleAuthority\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in fixture_json


@pytest.mark.parametrize(
    "case_id,field",
    (
        ("before_agent_callback_before_turn_start_snapshot_only", "liveMemoryAttached"),
        (
            "before_model_callback_before_llm_call_source_authority_no_projection",
            "promptProjectionAllowed",
        ),
        ("after_model_callback_after_llm_call_redacted_audit_only", "memoryWriteAllowed"),
        (
            "after_agent_callback_after_turn_end_write_intent_requires_receipt",
            "writeClaimAllowed",
        ),
        (
            "on_model_error_callback_on_error_provider_failure_fail_open_no_claim",
            "userVisibleAuthority",
        ),
    ),
)
def test_adk_memory_lifecycle_fixture_rejects_live_policy_bypasses(
    case_id: str,
    field: str,
) -> None:
    payload = load_fixture()
    case = next(case for case in payload["cases"] if case["caseId"] == case_id)

    case[field] = True

    with pytest.raises(AssertionError):
        for candidate in payload["cases"]:
            assert candidate["liveMemoryAttached"] is False
            assert candidate["promptProjectionAllowed"] is False
            assert candidate["memoryWriteAllowed"] is False
            assert candidate["writeClaimAllowed"] is False
            assert candidate["userVisibleAuthority"] is False


def test_adk_memory_lifecycle_import_boundary_stays_fixture_only() -> None:
    code = """
import json
import sys
from pathlib import Path

fixture = json.loads(Path('tests/fixtures/adk_memory_lifecycle/policy_matrix.json').read_text())
assert fixture['fixtureId'] == 'adk_memory_lifecycle_callback_metadata_matrix_0001'

forbidden_prefixes = (
    'google.adk.memory',
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.routes',
    'magi_agent.proxy',
    'magi_agent.transport.chat',
    'magi_agent.providers',
    'magi_agent.plugins.agentmemory',
    'magi_agent.memory.providers',
    'magi_agent.services.memory',
    'magi_agent.hipocampus',
    'magi_agent.qmd',
    'openai',
    'anthropic',
    'google.genai',
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f'{prefix}.') for prefix in forbidden_prefixes)
]
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
