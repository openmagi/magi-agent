from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
import subprocess
import sys
from pathlib import Path

from openmagi_core_agent.shadow.gate5b4c3_mock_runner_boundary import (
    run_gate5b4c3_mock_runner_boundary,
)
from openmagi_core_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
)


BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64
SANITIZED_DIGEST = "sha256:" + "1" * 64
ROUTER_DIGEST = "sha256:" + "2" * 64
PROFILE_DIGEST = "sha256:" + "3" * 64
BOT_CONFIG_DIGEST = "sha256:" + "4" * 64


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "mode": "shadow_generation_diagnostic",
        "responseAuthority": "typescript",
        "shadowGenerationId": "shadow_gen_001",
        "requestIdDigest": REQUEST_DIGEST,
        "traceIdDigest": TRACE_DIGEST,
        "createdAt": 1779200000000,
        "selection": {
            "botIdDigest": BOT_DIGEST,
            "ownerUserIdDigest": OWNER_DIGEST,
            "environment": "production",
            "selectedTarget": "gate5b_selected_bot",
            "sessionKeyDigest": SESSION_DIGEST,
        },
        "turn": {
            "turnId": "turn_opaque_001",
            "turnDigest": TURN_DIGEST,
            "sanitizedCurrentTurnText": "Please summarize the approved redacted note.",
            "sanitizedInputTextDigest": SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_001",
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": ROUTER_DIGEST,
            "routingProfileDigest": PROFILE_DIGEST,
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "temperature": 0.2,
            "maxOutputTokens": 512,
        },
        "recipeProfile": {
            "recipeId": "office-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
            "profileVersion": "v1",
            "runtimeEngine": "adk-python",
            "toolsPolicy": "disabled",
            "memoryMode": "disabled",
            "sourceAuthority": "current_turn_only",
        },
        "policy": {
            "typeScriptResponseAuthority": True,
            "pythonDiagnosticOnly": True,
            "outputIsolation": "local_diagnostic_only",
            "toolsDisabled": True,
            "toolHostDispatchAllowed": False,
            "memoryProviderCallsAllowed": False,
            "memoryWritesAllowed": False,
            "promptMemoryInjectionAllowed": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "missionRuntimeAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "budgets": {},
        "redaction": {
            "sanitizerId": "chat-proxy-sanitizer",
            "sanitizerVersion": "v1",
            "policyId": "gate5b4c3-redaction",
            "status": "passed",
            "redactedAt": 1779200000001,
            "redactedByteCount": 47,
            "forbiddenFieldScan": "passed",
            "sanitizedPayloadDigest": SANITIZED_DIGEST,
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(_payload())


def _enabled_config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=("anthropic",),
        allowedModelLabels=("claude-3-5-sonnet-latest",),
        allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
        allowedShadowCredentialRefs=("server-shadow-ref",),
    )


def test_default_disabled_request_does_not_call_mock_runner_and_fails_open_to_typescript() -> None:
    calls: list[str] = []

    result = run_gate5b4c3_mock_runner_boundary(
        _request(),
        mock_runner=lambda request: calls.append(request.shadow_generation_id),
    )

    assert calls == []
    assert result.diagnostic.accepted is False
    assert result.diagnostic.reason == "disabled"
    assert result.diagnostic.response_authority == "typescript"
    assert result.diagnostic.adk_invoked is False
    assert result.diagnostic.runner_attempted is False
    assert result.diagnostic.model_call_attempted is False
    assert result.mock_runner_invoked is False
    assert result.mock_runner_completed is False
    assert result.mock_output_preview_internal is None
    assert result.mock_output_digest is None
    assert result.authority.user_visible_output_allowed is False
    assert result.authority.tool_dispatch_allowed is False
    assert result.authority.memory_write_allowed is False
    assert result.authority.child_execution_allowed is False
    assert result.authority.mission_runtime_allowed is False


def test_accepted_config_calls_only_injected_mock_runner_and_keeps_output_internal() -> None:
    calls: list[str] = []

    def mock_runner(request: Gate5B4C3ShadowGenerationRequest) -> str:
        calls.append(request.shadow_generation_id)
        return "safe local mock output " + ("x" * 300)

    result = run_gate5b4c3_mock_runner_boundary(
        _request(),
        config=_enabled_config(),
        mock_runner=mock_runner,
        preview_byte_limit=64,
    )

    assert calls == ["shadow_gen_001"]
    assert result.diagnostic.accepted is True
    assert result.diagnostic.response_authority == "typescript"
    assert result.diagnostic.adk_invoked is False
    assert result.diagnostic.runner_attempted is False
    assert result.diagnostic.model_call_attempted is False
    assert result.mock_runner_invoked is True
    assert result.mock_runner_completed is True
    assert result.mock_runner_failed_open is False
    assert result.mock_output_accepted is True
    assert result.mock_output_preview_internal is not None
    assert len(result.mock_output_preview_internal.encode("utf-8")) <= 64
    assert result.mock_output_digest is not None
    assert result.user_visible_output is None
    assert result.authority.user_visible_output_allowed is False
    assert result.authority.db_writes_allowed is False
    assert result.authority.workspace_mutation_allowed is False


def test_forbidden_mock_output_is_reduced_to_reason_and_hash_without_preview() -> None:
    result = run_gate5b4c3_mock_runner_boundary(
        _request(),
        config=_enabled_config(),
        mock_runner=lambda _request: "Authorization: Bearer raw-secret-token",
    )

    assert result.mock_runner_invoked is True
    assert result.mock_runner_completed is True
    assert result.mock_runner_failed_open is False
    assert result.mock_output_accepted is False
    assert result.mock_output_rejection_reason == "unsafe_mock_output"
    assert result.mock_output_preview_internal is None
    assert result.mock_output_digest is not None
    assert result.diagnostic.response_authority == "typescript"
    assert result.diagnostic.adk_invoked is False
    assert result.user_visible_output is None


def test_mock_output_forbidden_object_keys_are_reduced_to_reason_and_hash() -> None:
    @dataclass(frozen=True)
    class DataclassOutput:
        rawUserText: str

    class SlottedOutput:
        __slots__ = ("rawUserText",)

        def __init__(self) -> None:
            self.rawUserText = "safe-looking text"

    NamedTupleOutput = namedtuple("NamedTupleOutput", ["rawUserText"])

    for output in (
        {"rawUserText": "safe-looking text"},
        DataclassOutput(rawUserText="safe-looking text"),
        SlottedOutput(),
        NamedTupleOutput(rawUserText="safe-looking text"),
    ):
        result = run_gate5b4c3_mock_runner_boundary(
            _request(),
            config=_enabled_config(),
            mock_runner=lambda _request, output=output: output,
        )

        assert result.mock_runner_invoked is True
        assert result.mock_runner_completed is True
        assert result.mock_output_accepted is False
        assert result.mock_output_rejection_reason == "unsafe_mock_output"
        assert result.mock_output_preview_internal is None
        assert result.mock_output_digest is not None
        assert result.user_visible_output is None


def test_arbitrary_object_mock_output_is_hash_only_without_preview() -> None:
    class OpaqueOutput:
        def __str__(self) -> str:
            return "safe-looking opaque output"

    result = run_gate5b4c3_mock_runner_boundary(
        _request(),
        config=_enabled_config(),
        mock_runner=lambda _request: OpaqueOutput(),
    )

    assert result.mock_output_accepted is False
    assert result.mock_output_rejection_reason == "unsafe_mock_output"
    assert result.mock_output_preview_internal is None
    assert result.mock_output_digest is not None
    assert result.user_visible_output is None


def test_mock_boundary_result_copy_and_construct_cannot_create_user_visible_output() -> None:
    result = run_gate5b4c3_mock_runner_boundary(
        _request(),
        config=_enabled_config(),
        mock_runner=lambda _request: "safe local mock output",
    )

    copied = result.model_copy(
        update={
            "responseAuthority": "python",
            "diagnosticOnly": False,
            "localOnly": False,
            "userVisibleOutput": "leak",
        }
    )
    constructed = type(result).model_construct(
        diagnostic=result.diagnostic,
        responseAuthority="python",
        diagnosticOnly=False,
        localOnly=False,
        userVisibleOutput="leak",
    )

    for mutated in (copied, constructed):
        assert mutated.response_authority == "typescript"
        assert mutated.diagnostic_only is True
        assert mutated.local_only is True
        assert mutated.user_visible_output is None
        assert mutated.authority.user_visible_output_allowed is False


def test_mock_runner_error_or_timeout_fails_open_to_typescript() -> None:
    error_result = run_gate5b4c3_mock_runner_boundary(
        _request(),
        config=_enabled_config(),
        mock_runner=lambda _request: (_ for _ in ()).throw(RuntimeError("mock failed")),
    )
    timeout_result = run_gate5b4c3_mock_runner_boundary(
        _request(),
        config=_enabled_config(),
        mock_runner=lambda _request: (_ for _ in ()).throw(TimeoutError("mock timeout")),
    )

    assert error_result.mock_runner_failed_open is True
    assert error_result.fail_open_reason == "mock_runner_error"
    assert error_result.diagnostic.response_authority == "typescript"
    assert error_result.diagnostic.adk_invoked is False
    assert error_result.diagnostic.runner_attempted is False
    assert error_result.diagnostic.model_call_attempted is False
    assert error_result.user_visible_output is None

    assert timeout_result.mock_runner_failed_open is True
    assert timeout_result.fail_open_reason == "mock_runner_timeout"
    assert timeout_result.diagnostic.response_authority == "typescript"
    assert timeout_result.user_visible_output is None


def test_mock_runner_boundary_import_does_not_load_adk_model_tool_memory_or_runtime_routes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module(
    "openmagi_core_agent.shadow.gate5b4c3_mock_runner_boundary"
)
assert module is not None

forbidden_exact = (
    "google.adk",
    "openai",
    "anthropic",
)
forbidden_prefixes = (
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.children",
    "openmagi_core_agent.evidence",
    "openmagi_core_agent.missions",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.provisioning",
    "openmagi_core_agent.k8s",
    "openmagi_core_agent.telegram",
    "openmagi_core_agent.database",
    "openmagi_core_agent.api",
    "openmagi_core_agent.dashboard",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"mock runner boundary loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_mock_runner_boundary_source_forbids_real_runner_model_tool_memory_imports() -> None:
    source = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "shadow"
        / "gate5b4c3_mock_runner_boundary.py"
    ).read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "openai",
        "anthropic",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.runtime",
        "openmagi_core_agent.routing",
        "openmagi_core_agent.workspace",
        "openmagi_core_agent.children",
        "openmagi_core_agent.evidence",
        "openmagi_core_agent.missions",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.channels",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "MemoryService" not in source
    assert "AgentMemory" not in source
    assert "exec(" not in source
    assert "eval(" not in source
