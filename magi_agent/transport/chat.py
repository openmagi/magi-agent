"""Re-export shim for the decomposed Gate5B chat serving stack (08-PR1).

``transport/chat.py`` was decomposed into focused modules; this shim preserves
the module's full historical surface (``__all__`` unchanged, every previous
top-level name re-exported as the same object) so existing importers keep
working without modification. New code should import from the owning module:

- ``magi_agent.runtime.user_visible_model_routing`` — model-route policy
- ``magi_agent.transport.chat_shared`` — shared config/contract primitives
- ``magi_agent.transport.generation_request`` — generation request builders
- ``magi_agent.transport.egress_critic`` — egress critic + evidence projection
- ``magi_agent.transport.gate2_sandbox_canary`` — gate2 canary + receipts
- ``magi_agent.transport.chat_routes`` — route registration + serving engine
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
from json import JSONDecodeError
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from magi_agent.evidence.gate2_durable_evidence import (
    Gate2DurableEvidenceStore,
)
from magi_agent.evidence.gate1a_egress_correlation import (
    GATE1A_EGRESS_CORRELATION_MODE,
    GATE1A_EGRESS_TELEMETRY_SOURCE,
    Gate1AEgressCorrelationContext,
)
from magi_agent.evidence.observed_egress import (
    ObservedEgressEvidence,
    get_observed_egress_evidence_provider,
    observed_egress_diagnostics,
)
from magi_agent.gates.gate1a_readonly_tools import (
    GATE1A_FORBIDDEN_TOOL_NAMES,
    GATE1A_READONLY_TOOL_NAMES,
    Gate1AReadOnlyToolBundle,
    Gate1AReadOnlyToolConfig,
    build_gate1a_readonly_tool_bundle,
)
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolBundle,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)
from magi_agent.config.env import is_egress_gate_enabled, is_read_quality_enabled
from magi_agent.introspection.egress_gate import EgressVerifierStatus
from magi_agent.gates.gate2_readiness import gate2_readiness_health_metadata
from magi_agent.gates.gate8_readiness import gate8_readiness_health_metadata
# reuse the established image sanitizer; message_builder exposes no public image API
from magi_agent.runtime.message_builder import _collect_image_blocks
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.session_identity import _memory_mode_from_header

if TYPE_CHECKING:
    from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.runtime.public_events import (
    tool_end_event,
    tool_progress_event,
    tool_start_event,
    turn_phase_event,
)
from magi_agent.research.research_first_canary import (
    build_research_first_selected_response,
    research_first_selected_canary_active,
)
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)
from magi_agent.recipes.materializer import RecipeMaterializer
from magi_agent.shadow.gate2_activation_loop_a import (
    Gate2SandboxCanaryRequest,
    Gate2SandboxRootReadiness,
    check_gate2_sandbox_root_readiness,
    run_gate2_sandbox_workspace_canary,
)
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    AdkPrimitivesLoader,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterReservation,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)
from magi_agent.transport.usage_receipt_emit import (
    emit_runtime_direct_usage_receipt,
    usage_receipt_enabled,
)
from magi_agent.runtime.user_visible_model_routing import (
    _SAFE_LABEL_RE,
    _credential_ref_for_user_visible_provider,
    _provider_model_from_user_visible_model,
    _requested_user_visible_model_route,
    _safe_label_or_none,
    _select_user_visible_model_route,
    _single_config_value,
)
from magi_agent.transport.chat_shared import (
    ClientDisconnectedProbe,
    Gate5BUserVisibleChatRouteConfig,
    MockedChatRunner,
    _CONTEXT_REASON_CODE_FORBIDDEN_RE,
    _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE,
    _bounded_public_text,
    _context_continuity_chat_diagnostic,
    _csv_values,
    _env_bool_default_true,
    _fallback_response,
    _int_env,
    _int_for_public_metadata,
    _is_sha256_digest,
    _is_true,
    _public_safe_context_continuity_metadata,
    _public_safe_context_reason_codes,
    _reason_for_gate_error,
    _route_tool_bundle_full,
    _route_tool_bundle_readonly,
    _route_tool_bundle_ready,
    _safe_label_or_default,
    _sha256_digest,
    _shadow_generation_route_config,
    build_gate1a_readonly_tools_config_from_env,
    build_gate5b_full_toolhost_config_from_env,
    build_gate5b_user_visible_chat_route_config_from_env,
)
from magi_agent.transport.generation_request import (
    _APP_CHANNEL_HISTORY_SCHEMA,
    _DATA_URL_RE,
    _LEGACY_IDENTITY_PATTERNS,
    _MODEL_VISIBLE_CONTEXT_MAX_CHARS,
    _PUBLIC_IDENTITY_POLICY,
    _app_channel_history_messages,
    _build_gate5b_model_visible_current_turn_text,
    _build_gate5b_sanitized_recent_history,
    _build_user_visible_generation_request,
    _dedupe_latest_history,
    _extend_unique,
    _extract_last_user_image_blocks,
    _extract_last_user_text,
    _latest_model_visible_messages,
    _message_content_to_text,
    _normalize_gate5b_model_visible_identity_text,
    _normalize_image_block,
    _safe_chat_role,
    _sanitized_history_message,
    build_gate5b_user_visible_canary_runner_request,
    build_public_identity_policy,
    sanitize_gate5b_model_visible_identity_text,
)
from magi_agent.transport.egress_critic import (
    _EGRESS_CRITIC_DEFAULT_MODEL,
    _ENV_EGRESS_CRITIC_MODEL,
    _build_egress_evidence_view,
    _egress_critic_model_factory,
    _log_egress_critic_evidence,
    _maybe_run_egress_critic_gate,
    _production_egress_critic_model_factory,
    _safe_egress_critic_evidence_log_record,
)
from magi_agent.transport.gate2_sandbox_canary import (
    Gate1ASelectedAttemptPreflightPayload,
    Gate2SandboxWorkspaceCanaryConfig,
    Gate5BSelectedScopeReceiptPayload,
    Gate5BUserVisibleDeliveryReceiptPayload,
    _DELIVERY_RECEIPT_MODEL_CONFIG,
    _GATE2_PARENT_CREATE_BOOL_FIELDS,
    _GATE2_PARENT_CREATE_COUNT_FIELDS,
    _GATE2_PARENT_CREATE_LABEL_FIELDS,
    _GATE2_PARENT_CREATE_SAFE_LABEL_RE,
    _build_gate2_body_digest,
    _build_gate2_durable_evidence_store,
    _build_gate2_request_digest,
    _gate2_canary_gate_error,
    _gate2_exception_response,
    _gate2_failure_chain,
    _gate2_message_content_to_text,
    _gate2_optional_bool,
    _gate2_parent_create_diagnostics_payload,
    _gate2_parent_create_safe_label,
    _gate2_receipt_rejection_status,
    _gate2_receipt_scope_error,
    _gate2_request_digest,
    _gate2_request_digest_status,
    _gate2_response_extra,
    _gate2_sandbox_canary_authority,
    _gate2_sandbox_canary_config,
    _gate2_scope_match,
    _gate2_selected_sandbox_root_readiness,
    _minimal_gate2_exception_chain,
    _record_gate2_sandbox_workspace_delivery_receipt,
    _run_gate2_sandbox_workspace_canary_chat,
    _run_gate2_sandbox_workspace_canary_chat_impl,
    _safe_gate2_chain_label,
    _safe_gate2_digest,
    _safe_optional_gate2_digest,
    _summarize_gate2_messages,
    _verify_gate2_durable_evidence_on_disk,
    build_gate2_sandbox_workspace_canary_config_from_env,
)
from magi_agent.transport.chat_routes import (
    _FALLBACK_RECEIPT_SCOPE_GATES,
    _FALSE_RESPONSE_AUTHORITY_KEYS,
    _FALSE_RUNTIME_AUTHORITY_KEYS,
    _FIRST_PARTY_HARNESS_RECIPE_PACK_IDS,
    _GATE1A_EGRESS_DISCIPLINE_MODE,
    _GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT,
    _INCOMPLETE_RUNNER_OUTPUT_RE,
    _augment_runner_error_diagnostic,
    _boundary_runner_error_diagnostic,
    _bounded_tuple,
    _build_gate1a_egress_correlation_context,
    _camel_to_snake,
    _canary_gate_error,
    _chat_runner_error_diagnostic,
    _client_disconnected,
    _collect_gate1a_observed_egress_evidence,
    _disabled_surface_safety,
    _fallback_only_scope_error,
    _finish_counter_error,
    _first_party_harness_families,
    _first_party_harness_metadata,
    _first_party_recipe_pack_ids_from_payload,
    _gate1a_config,
    _gate1a_observed_egress_metadata,
    _gate1a_readonly_tool_bundle,
    _gate1a_tooling_metadata,
    _gate1a_workspace_root,
    _gate5b_full_tooling_metadata,
    _gate5b_full_toolhost_bundle,
    _gate5b_full_toolhost_config,
    _gate5b_full_toolhost_public_events,
    _gate5b_full_toolhost_tool_event_id,
    _gate5b_full_toolhost_workspace_root,
    _gate8_selected_authority_metadata,
    _local_adk_chat_response,
    _local_adk_chat_sse,
    _local_chat_prompt_text,
    _local_chat_route_enabled,
    _local_chat_string,
    _local_runtime_event_delta,
    _model_attempt_digest,
    _public_safe_error_preview_or_none,
    _public_safe_runner_error_diagnostic,
    _public_safe_tool_names,
    _public_safe_traceback_markers,
    _python_canary_authority,
    _python_ready_response,
    _route_config,
    _route_tool_bundle_mode,
    _route_tool_bundle_names,
    _route_tooling_metadata,
    _resolve_local_learning_live_readiness,
    _run_live_chat_runner,
    _run_mocked_chat_runner,
    _runner_incomplete_output_reason,
    _schedule_runtime_direct_usage_receipt,
    _sse_data,
    _sse_event,
    _surface_safety,
    _swallow_task_result,
    _utc_now_iso,
    gate5b_user_visible_chat_gate_active,
    register_chat_routes,
    run_gate5b_user_visible_chat_response,
)


__all__ = [
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
