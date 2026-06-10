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
    "magi_agent.transport.egress_critic": [
        "_EGRESS_CRITIC_DEFAULT_MODEL",
        "_ENV_EGRESS_CRITIC_MODEL",
        "_build_egress_evidence_view",
        "_egress_critic_model_factory",
        "_log_egress_critic_evidence",
        "_maybe_run_egress_critic_gate",
        "_production_egress_critic_model_factory",
        "_safe_egress_critic_evidence_log_record",
    ],
    "magi_agent.transport.gate2_sandbox_canary": [
        "Gate1ASelectedAttemptPreflightPayload",
        "Gate2SandboxWorkspaceCanaryConfig",
        "Gate5BSelectedScopeReceiptPayload",
        "Gate5BUserVisibleDeliveryReceiptPayload",
        "_DELIVERY_RECEIPT_MODEL_CONFIG",
        "_GATE2_PARENT_CREATE_BOOL_FIELDS",
        "_GATE2_PARENT_CREATE_COUNT_FIELDS",
        "_GATE2_PARENT_CREATE_LABEL_FIELDS",
        "_GATE2_PARENT_CREATE_SAFE_LABEL_RE",
        "_build_gate2_body_digest",
        "_build_gate2_durable_evidence_store",
        "_build_gate2_request_digest",
        "_gate2_canary_gate_error",
        "_gate2_exception_response",
        "_gate2_failure_chain",
        "_gate2_receipt_rejection_status",
        "_gate2_receipt_scope_error",
        "_gate2_request_digest",
        "_gate2_request_digest_status",
        "_gate2_response_extra",
        "_gate2_sandbox_canary_authority",
        "_gate2_sandbox_canary_config",
        "_gate2_message_content_to_text",
        "_gate2_optional_bool",
        "_gate2_parent_create_diagnostics_payload",
        "_gate2_parent_create_safe_label",
        "_gate2_scope_match",
        "_gate2_selected_sandbox_root_readiness",
        "_minimal_gate2_exception_chain",
        "_record_gate2_sandbox_workspace_delivery_receipt",
        "_run_gate2_sandbox_workspace_canary_chat",
        "_run_gate2_sandbox_workspace_canary_chat_impl",
        "_safe_gate2_chain_label",
        "_safe_gate2_digest",
        "_safe_optional_gate2_digest",
        "_summarize_gate2_messages",
        "_verify_gate2_durable_evidence_on_disk",
        "build_gate2_sandbox_workspace_canary_config_from_env",
    ],
    "magi_agent.transport.chat_routes": [
        "_FALLBACK_RECEIPT_SCOPE_GATES",
        "_FALSE_RESPONSE_AUTHORITY_KEYS",
        "_FALSE_RUNTIME_AUTHORITY_KEYS",
        "_FIRST_PARTY_HARNESS_RECIPE_PACK_IDS",
        "_GATE1A_EGRESS_DISCIPLINE_MODE",
        "_GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT",
        "_INCOMPLETE_RUNNER_OUTPUT_RE",
        "_augment_runner_error_diagnostic",
        "_boundary_runner_error_diagnostic",
        "_bounded_tuple",
        "_build_gate1a_egress_correlation_context",
        "_canary_gate_error",
        "_chat_runner_error_diagnostic",
        "_client_disconnected",
        "_collect_gate1a_observed_egress_evidence",
        "_disabled_surface_safety",
        "_fallback_only_scope_error",
        "_finish_counter_error",
        "_first_party_harness_families",
        "_first_party_harness_metadata",
        "_first_party_recipe_pack_ids_from_payload",
        "_gate1a_config",
        "_gate1a_observed_egress_metadata",
        "_gate1a_readonly_tool_bundle",
        "_gate1a_tooling_metadata",
        "_gate1a_workspace_root",
        "_gate5b_full_tooling_metadata",
        "_gate5b_full_toolhost_bundle",
        "_gate5b_full_toolhost_config",
        "_gate5b_full_toolhost_public_events",
        "_gate5b_full_toolhost_tool_event_id",
        "_gate5b_full_toolhost_workspace_root",
        "_gate8_selected_authority_metadata",
        "_local_adk_chat_response",
        "_local_adk_chat_sse",
        "_local_chat_prompt_text",
        "_local_chat_route_enabled",
        "_local_chat_string",
        "_local_runtime_event_delta",
        "_model_attempt_digest",
        "_public_safe_error_preview_or_none",
        "_public_safe_runner_error_diagnostic",
        "_public_safe_tool_names",
        "_public_safe_traceback_markers",
        "_python_canary_authority",
        "_python_ready_response",
        "_route_tooling_metadata",
        "_run_live_chat_runner",
        "_run_mocked_chat_runner",
        "_runner_incomplete_output_reason",
        "_schedule_runtime_direct_usage_receipt",
        "_sse_data",
        "_sse_event",
        "_surface_safety",
        "_swallow_task_result",
        "gate5b_user_visible_chat_gate_active",
        "register_chat_routes",
        "run_gate5b_user_visible_chat_response",
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


# Names the pre-split chat.py imported from elsewhere and re-exported; the shim
# must keep exposing them because external code patched/imported them via chat.
PASSTHROUGH_REEXPORTS: dict[str, list[str]] = {
    "magi_agent.runtime.message_builder": ["_collect_image_blocks"],
    "magi_agent.runtime.session_identity": ["_memory_mode_from_header"],
}


def test_passthrough_reexports_survive_on_shim() -> None:
    chat = importlib.import_module("magi_agent.transport.chat")
    for module_name, symbols in PASSTHROUGH_REEXPORTS.items():
        module = importlib.import_module(module_name)
        for symbol in symbols:
            assert getattr(module, symbol) is getattr(chat, symbol), (
                f"shim lost passthrough re-export {symbol} from {module_name}"
            )
