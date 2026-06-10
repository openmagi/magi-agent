"""Regression guard for the ``magi_agent.transport.chat`` re-export shim.

08-PR1 decomposes ``transport/chat.py`` into focused modules while keeping
``transport.chat`` as a re-export shim so existing importers keep working
unchanged. This test freezes the module's public surface (``dir()`` minus
underscore names, captured before the split) plus the underscore symbols that
in-repo importers rely on, and asserts every name stays importable from the
shim. As each extracted module lands, same-object assertions are added so the
shim provably re-exports the identical objects (no duplicated globals).
"""

from __future__ import annotations

import importlib

# Frozen pre-split public surface of magi_agent.transport.chat
# (dir() minus names starting with "_", captured at base c39af684).
PUBLIC_SYMBOLS = [
    "AdkPrimitivesLoader",
    "AgentRecipeCompiler",
    "Any",
    "AsyncIterator",
    "Awaitable",
    "BaseModel",
    "Callable",
    "ClientDisconnectedProbe",
    "ConfigDict",
    "EgressVerifierStatus",
    "FastAPI",
    "Field",
    "GATE1A_EGRESS_CORRELATION_MODE",
    "GATE1A_EGRESS_TELEMETRY_SOURCE",
    "GATE1A_FORBIDDEN_TOOL_NAMES",
    "GATE1A_READONLY_TOOL_NAMES",
    "GATE5B_FULL_TOOLHOST_TOOL_NAMES",
    "Gate1AEgressCorrelationContext",
    "Gate1AReadOnlyToolBundle",
    "Gate1AReadOnlyToolConfig",
    "Gate1ASelectedAttemptPreflightPayload",
    "Gate2DurableEvidenceStore",
    "Gate2SandboxCanaryRequest",
    "Gate2SandboxRootReadiness",
    "Gate2SandboxWorkspaceCanaryConfig",
    "Gate5B4C3ShadowCounterReservation",
    "Gate5B4C3ShadowGenerationConfig",
    "Gate5B4C3ShadowGenerationRequest",
    "Gate5B4C3ShadowGenerationRouteConfig",
    "Gate5BFullToolBundle",
    "Gate5BFullToolHostConfig",
    "Gate5BSelectedScopeReceiptPayload",
    "Gate5BUserVisibleChatRouteConfig",
    "Gate5BUserVisibleDeliveryReceiptPayload",
    "Iterator",
    "JSONDecodeError",
    "JSONResponse",
    "Literal",
    "Mapping",
    "MockedChatRunner",
    "ObservedEgressEvidence",
    "OpenMagiRuntime",
    "PackRegistry",
    "Path",
    "ProfileResolutionRequest",
    "RecipeMaterializer",
    "Request",
    "Sequence",
    "StreamingResponse",
    "TYPE_CHECKING",
    "ValidationError",
    "annotations",
    "asyncio",
    "build_gate1a_readonly_tool_bundle",
    "build_gate1a_readonly_tools_config_from_env",
    "build_gate2_sandbox_workspace_canary_config_from_env",
    "build_gate5b4c3_shadow_generation_diagnostic",
    "build_gate5b_full_toolhost_bundle",
    "build_gate5b_full_toolhost_config_from_env",
    "build_gate5b_user_visible_canary_runner_request",
    "build_gate5b_user_visible_chat_route_config_from_env",
    "build_public_identity_policy",
    "build_research_first_selected_response",
    "check_gate2_sandbox_root_readiness",
    "dataclass",
    "datetime",
    "emit_runtime_direct_usage_receipt",
    "gate2_readiness_health_metadata",
    "gate5b_user_visible_chat_gate_active",
    "gate8_readiness_health_metadata",
    "get_observed_egress_evidence_provider",
    "hashlib",
    "inspect",
    "is_egress_gate_enabled",
    "is_read_quality_enabled",
    "json",
    "model_validator",
    "observed_egress_diagnostics",
    "os",
    "re",
    "register_chat_routes",
    "research_first_selected_canary_active",
    "run_gate2_sandbox_workspace_canary",
    "run_gate5b4c3_live_runner_boundary_async",
    "run_gate5b_user_visible_chat_response",
    "sanitize_gate5b_model_visible_identity_text",
    "time",
    "timezone",
    "tool_end_event",
    "tool_progress_event",
    "tool_start_event",
    "turn_phase_event",
    "usage_receipt_enabled",
]

# Underscore symbols that in-repo importers (tests / transport modules)
# import or call from magi_agent.transport.chat. The shim must keep these.
UNDERSCORE_IMPORTER_SYMBOLS = [
    "_build_gate5b_sanitized_recent_history",
    "_build_user_visible_generation_request",
    "_extract_last_user_image_blocks",
    "_gate2_request_digest_status",
    "_gate2_scope_match",
    "_local_adk_chat_sse",
    "_schedule_runtime_direct_usage_receipt",
    "_utc_now_iso",
]

# Extracted modules and the representative symbols the shim must re-export
# as the SAME object. Entries are appended as each module extraction lands.
EXTRACTED_MODULE_SYMBOLS: dict[str, list[str]] = {
    "magi_agent.runtime.user_visible_model_routing": [
        "_SAFE_LABEL_RE",
        "_credential_ref_for_user_visible_provider",
        "_provider_model_from_user_visible_model",
        "_requested_user_visible_model_route",
        "_safe_label_or_none",
        "_select_user_visible_model_route",
        "_single_config_value",
    ],
    "magi_agent.transport.chat_shared": [
        "ClientDisconnectedProbe",
        "Gate5BUserVisibleChatRouteConfig",
        "MockedChatRunner",
        "_CONTEXT_REASON_CODE_FORBIDDEN_RE",
        "_RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE",
        "_bounded_public_text",
        "_camel_to_snake",
        "_context_continuity_chat_diagnostic",
        "_csv_values",
        "_env_bool_default_true",
        "_fallback_response",
        "_int_env",
        "_int_for_public_metadata",
        "_is_sha256_digest",
        "_is_true",
        "_public_safe_context_continuity_metadata",
        "_public_safe_context_reason_codes",
        "_reason_for_gate_error",
        "_route_config",
        "_route_tool_bundle_full",
        "_route_tool_bundle_mode",
        "_route_tool_bundle_names",
        "_route_tool_bundle_readonly",
        "_route_tool_bundle_ready",
        "_safe_label_or_default",
        "_sha256_digest",
        "_shadow_generation_route_config",
        "_utc_now_iso",
        "build_gate1a_readonly_tools_config_from_env",
        "build_gate5b_full_toolhost_config_from_env",
        "build_gate5b_user_visible_chat_route_config_from_env",
    ],
    "magi_agent.transport.generation_request": [
        "_APP_CHANNEL_HISTORY_SCHEMA",
        "_DATA_URL_RE",
        "_LEGACY_IDENTITY_PATTERNS",
        "_MODEL_VISIBLE_CONTEXT_MAX_CHARS",
        "_PUBLIC_IDENTITY_POLICY",
        "_app_channel_history_messages",
        "_build_gate5b_model_visible_current_turn_text",
        "_build_gate5b_sanitized_recent_history",
        "_build_user_visible_generation_request",
        "_dedupe_latest_history",
        "_extend_unique",
        "_extract_last_user_image_blocks",
        "_extract_last_user_text",
        "_latest_model_visible_messages",
        "_message_content_to_text",
        "_normalize_gate5b_model_visible_identity_text",
        "_normalize_image_block",
        "_safe_chat_role",
        "_sanitized_history_message",
        "build_gate5b_user_visible_canary_runner_request",
        "build_public_identity_policy",
        "sanitize_gate5b_model_visible_identity_text",
    ],
}


def test_chat_shim_exports_frozen_public_surface() -> None:
    chat = importlib.import_module("magi_agent.transport.chat")
    missing = [name for name in PUBLIC_SYMBOLS if not hasattr(chat, name)]
    assert missing == [], f"chat shim lost public symbols: {missing}"


def test_chat_shim_exports_importer_underscore_symbols() -> None:
    chat = importlib.import_module("magi_agent.transport.chat")
    missing = [
        name for name in UNDERSCORE_IMPORTER_SYMBOLS if not hasattr(chat, name)
    ]
    assert missing == [], f"chat shim lost importer-used symbols: {missing}"


def test_chat_shim_all_unchanged() -> None:
    chat = importlib.import_module("magi_agent.transport.chat")
    assert chat.__all__ == [
        "Gate2SandboxWorkspaceCanaryConfig",
        "Gate5BUserVisibleChatRouteConfig",
        "build_gate1a_readonly_tools_config_from_env",
        "build_gate2_sandbox_workspace_canary_config_from_env",
        "build_gate5b_full_toolhost_config_from_env",
        "build_gate5b_user_visible_chat_route_config_from_env",
        "build_gate5b_user_visible_canary_runner_request",
        "build_public_identity_policy",
        "gate5b_user_visible_chat_gate_active",
        "register_chat_routes",
        "run_gate5b_user_visible_chat_response",
        "sanitize_gate5b_model_visible_identity_text",
    ]


def test_extracted_modules_share_objects_with_shim() -> None:
    """Each extracted module's symbols must be the same objects on the shim."""
    chat = importlib.import_module("magi_agent.transport.chat")
    for module_name, symbols in EXTRACTED_MODULE_SYMBOLS.items():
        module = importlib.import_module(module_name)
        for symbol in symbols:
            assert getattr(module, symbol) is getattr(chat, symbol), (
                f"{module_name}.{symbol} is not the same object as "
                f"transport.chat.{symbol}"
            )
