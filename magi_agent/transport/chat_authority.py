"""Runner-error diagnostics, public-safe redaction and authority/tooling
metadata builders, pure move out of magi_agent/transport/chat_routes.py (PR-G4).

Bodies moved verbatim (source order preserved). chat_routes re-imports every
name so import paths are preserved. Depends downward on chat_shared only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from fastapi.responses import JSONResponse
from magi_agent.evidence.gate1a_egress_correlation import Gate1AEgressCorrelationContext
from magi_agent.evidence.observed_egress import ObservedEgressEvidence, get_observed_egress_evidence_provider, observed_egress_diagnostics
from magi_agent.gates.gate1a_readonly_tools import GATE1A_FORBIDDEN_TOOL_NAMES, Gate1AReadOnlyToolBundle
from magi_agent.gates.gate5b_full_toolhost import GATE5B_FULL_TOOLHOST_TOOL_NAMES, Gate5BFullToolBundle
from magi_agent.gates.gate8_readiness import gate8_readiness_health_metadata
from magi_agent.introspection.egress_gate import EgressVerifierStatus
from magi_agent.runtime.child_runner_status import child_runner_availability_metadata
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.user_visible_model_routing import _SAFE_LABEL_RE, _safe_label_or_none
from magi_agent.shadow.gate5b4c3_shadow_counter_store import Gate5B4C3ShadowCounterReservation
from magi_agent.transport.chat_shared import Gate5BUserVisibleChatRouteConfig, _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE, _bounded_public_text, _is_sha256_digest, _route_tool_bundle_full, _route_tool_bundle_mode, _route_tool_bundle_names, _route_tool_bundle_readonly, _route_tool_bundle_ready, _safe_label_or_default, _sha256_digest
from magi_agent.transport.gate2_sandbox_canary import Gate5BUserVisibleDeliveryReceiptPayload
from magi_agent.transport.generation_request import UserVisibleGenerationRequest
from magi_agent.transport.shadow_generations import Gate5B4C3ShadowGenerationRouteConfig


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")


_INCOMPLETE_RUNNER_OUTPUT_RE = re.compile(
    r"(?:"
    r"잠시만\s*기다|"
    r"기다려\s*주|"
    r"조금만\s*더\s*기다|"
    r"완료되면|"
    r"전달(?:드리|해)\s*겠|"
    r"진행\s*중|"
    r"처리\s*중|"
    r"실행\s*중|"
    r"작업\s*중|"
    r"please\s+wait|"
    r"still\s+working|"
    r"in\s+progress|"
    r"once\s+(?:it\s+is\s+)?complete|"
    r"when\s+(?:it\s+is\s+)?complete|"
    r"i(?:'|’)ll\s+(?:continue|update)|"
    r"i\s+will\s+(?:continue|update)|"
    r"will\s+(?:continue|update|send|share)\b"
    r")",
    re.IGNORECASE,
)


_FALLBACK_RECEIPT_SCOPE_GATES = frozenset(
    {
        "gate1a_readonly_tools",
        "gate7_5_context_continuity",
    }
)


_FALSE_RUNTIME_AUTHORITY_KEYS = (
    "transcriptWritesAllowed",
    "sseWritesAllowed",
    "channelWritesAllowed",
    "dbWritesAllowed",
    "workspaceMutationAllowed",
    "childExecutionAllowed",
    "missionRuntimeAllowed",
    "evidenceBlockModeAllowed",
)


_FALSE_RESPONSE_AUTHORITY_KEYS = (
    "memoryWriteAllowed",
    "toolDispatchAllowed",
    *_FALSE_RUNTIME_AUTHORITY_KEYS,
)


_GATE1A_EGRESS_DISCIPLINE_MODE = "bounded_provider_tunnels"


_GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT = 2


def _finish_counter_error(
    route_config: Gate5B4C3ShadowGenerationRouteConfig,
    reservation: Gate5B4C3ShadowCounterReservation,
    reason: str,
    *,
    runner_error_diagnostic: Mapping[str, object] | None = None,
) -> object:
    return route_config.counter_store.finish(
        reservation,
        status="error",
        reason=reason,
        runner_error_diagnostic=runner_error_diagnostic,
    )


def _boundary_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    boundary_result: object,
) -> dict[str, object] | None:
    diagnostic = getattr(boundary_result, "runner_error_diagnostic", None)
    if diagnostic is None:
        return None
    if hasattr(diagnostic, "model_dump"):
        payload = diagnostic.model_dump(by_alias=True, mode="json", warnings=False)
    elif isinstance(diagnostic, Mapping):
        payload = dict(diagnostic)
    else:
        return None
    return _augment_runner_error_diagnostic(runtime=runtime, payload=payload)


def _chat_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    generation: UserVisibleGenerationRequest,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
    stage: str,
    reason_code: str,
    exception_class: str | None,
    exception_category: str | None,
    gate1a_egress_context: Gate1AEgressCorrelationContext | None,
    gate1a_egress_proxy_url: str | None,
) -> dict[str, object]:
    correlation_ready = (
        gate1a_egress_context is not None
        and bool(str(gate1a_egress_proxy_url or "").strip())
    )
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    payload: dict[str, object] = {
        "schemaVersion": "gate5b4c3.runnerErrorDiagnostic.v1",
        "stage": _safe_label_or_default(stage, "unexpected_exception"),
        "reasonCode": _safe_label_or_default(reason_code, "runner_error"),
        "requestDigest": generation.request_id_digest,
        "traceIdDigest": generation.trace_id_digest,
        "routeMode": "user_visible_generation",
        "gateMode": _route_tool_bundle_mode(gate1a_bundle),
        "toolsPolicy": _safe_label_or_default(
            generation.recipe_profile.tools_policy,
            "unknown",
        ),
        "routingSource": _safe_label_or_default(
            generation.model_routing.routing_source,
            "unknown",
        ),
        "correlationMode": "proxy_connect_headers" if correlation_ready else "none",
        "activeToolNames": _public_safe_tool_names(gate1a_bundle.exposed_tool_names),
        "adkInvoked": False,
        "runnerAttempted": False,
        "modelCallAttempted": False,
        "toolsEnabled": not generation.policy.tools_disabled,
        "toolHostDispatchAllowed": generation.policy.tool_host_dispatch_allowed,
        "adkPrimitivesLoaderConfigured": True,
        "gate1aEgressCorrelationContextPresent": gate1a_egress_context is not None,
        "gate1aProxyUrlConfigured": bool(str(gate1a_egress_proxy_url or "").strip()),
        "egressCorrelationHeadersConfigured": correlation_ready,
    }
    if exception_class is not None:
        payload["exceptionClass"] = _safe_label_or_default(exception_class, "Exception")
    if exception_category is not None:
        payload["exceptionCategory"] = _safe_label_or_default(
            exception_category,
            "unexpected_exception",
        )
    if gate1a_egress_context is not None:
        payload["correlationDigest"] = gate1a_egress_context.correlation_digest
        if gate1a_egress_context.model_attempt_digest is not None:
            payload["modelAttemptDigest"] = gate1a_egress_context.model_attempt_digest
    return _augment_runner_error_diagnostic(runtime=runtime, payload=payload) or payload


def _augment_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    safe_payload = _public_safe_runner_error_diagnostic(payload)
    if safe_payload is None:
        return None
    runtime_version = _safe_label_or_none(getattr(runtime.config.build, "version", None))
    build_sha = _safe_label_or_none(getattr(runtime.config.build, "build_sha", None))
    if runtime_version is not None:
        safe_payload["runtimeVersion"] = runtime_version
    if build_sha is not None:
        safe_payload["buildSha"] = build_sha
    provider = get_observed_egress_evidence_provider(runtime)
    egress_diagnostic = observed_egress_diagnostics(provider)
    safe_payload["observedEgressEvidenceAvailable"] = bool(
        egress_diagnostic["observedEgressEvidenceAvailable"]
    )
    safe_payload["gate1aEgressEvidenceReady"] = bool(
        egress_diagnostic["gate1aEgressEvidenceReady"]
    )
    return safe_payload


def _public_safe_runner_error_diagnostic(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    safe_payload: dict[str, object] = {}
    string_fields = {
        "schemaVersion",
        "stage",
        "reasonCode",
        "exceptionClass",
        "exceptionCategory",
        "routeMode",
        "gateMode",
        "toolsPolicy",
        "routingSource",
        "correlationMode",
    }
    digest_fields = {
        "requestDigest",
        "traceIdDigest",
        "modelAttemptDigest",
        "correlationDigest",
    }
    bool_fields = {
        "adkInvoked",
        "runnerAttempted",
        "modelCallAttempted",
        "toolsEnabled",
        "toolHostDispatchAllowed",
        "adkPrimitivesLoaderConfigured",
        "gate1aEgressCorrelationContextPresent",
        "gate1aProxyUrlConfigured",
        "egressCorrelationHeadersConfigured",
    }
    for key, value in payload.items():
        if key in string_fields and isinstance(value, str):
            safe_value = _safe_label_or_none(value)
            if safe_value is not None:
                safe_payload[key] = safe_value
            continue
        if key in digest_fields and isinstance(value, str) and _is_sha256_digest(value):
            safe_payload[key] = value
            continue
        if key in bool_fields and isinstance(value, bool):
            safe_payload[key] = value
            continue
        if key == "activeToolNames" and isinstance(value, (list, tuple)):
            tool_names = _public_safe_tool_names(value)
            if tool_names:
                safe_payload[key] = tool_names
            continue
        if key == "errorPreview" and isinstance(value, str):
            error_preview = _public_safe_error_preview_or_none(value)
            if error_preview is not None:
                safe_payload[key] = error_preview
            continue
        if key == "tracebackMarkers" and isinstance(value, (list, tuple)):
            traceback_markers = _public_safe_traceback_markers(value)
            if traceback_markers:
                safe_payload[key] = traceback_markers
    if "stage" not in safe_payload or "reasonCode" not in safe_payload:
        return None
    safe_payload["schemaVersion"] = "gate5b4c3.runnerErrorDiagnostic.v1"
    return safe_payload


def _public_safe_tool_names(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_names: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if _SAFE_LABEL_RE.match(text) and text not in safe_names:
            safe_names.append(text)
    return safe_names


def _public_safe_error_preview_or_none(value: object) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > 256:
        return None
    if _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE.search(text):
        return None
    return text


def _public_safe_traceback_markers(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_markers: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if _SAFE_LABEL_RE.match(text) and text not in safe_markers:
            safe_markers.append(text)
        if len(safe_markers) >= 12:
            break
    return safe_markers


def _fallback_only_scope_error(
    *,
    payload: Gate5BUserVisibleDeliveryReceiptPayload,
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> str | None:
    if (
        payload.gate not in _FALLBACK_RECEIPT_SCOPE_GATES
        or payload.delivery_status != "fallback_served"
        or not payload.python_attempted
        or payload.python_counter_record_present
    ):
        return None
    if payload.selected_scope is None:
        return "selected_scope_required"
    if payload.selected_scope.selected_bot_digest != _sha256_digest(runtime.config.bot_id):
        return "selected_scope_mismatch"
    if (
        payload.selected_scope.selected_owner_user_id_digest
        != _sha256_digest(runtime.config.user_id)
    ):
        return "selected_scope_mismatch"
    if payload.selected_scope.environment != route_config.environment:
        return "selected_scope_mismatch"
    return None


def _runner_incomplete_output_reason(value: object) -> str | None:
    text = _bounded_public_text(str(value or ""), max_chars=4096).strip()
    if not text:
        return None
    if _INCOMPLETE_RUNNER_OUTPUT_RE.search(text):
        return "runner_incomplete_output"
    return None


def _canary_gate_error(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> str | None:
    authority = runtime.config.authority
    if route_config.kill_switch_enabled is not False:
        return "python_disabled"
    if route_config.selected_bot_digest != _sha256_digest(runtime.config.bot_id):
        return "python_disabled"
    if route_config.selected_owner_user_id_digest != _sha256_digest(runtime.config.user_id):
        return "python_disabled"
    if not route_config.environment or route_config.environment not in route_config.environment_allowlist:
        return "python_disabled"
    if (
        runtime.config.gate8_readiness.enabled
        and _gate8_selected_authority_metadata(runtime) is None
    ):
        return "python_disabled"
    if (
        authority.user_visible_output_allowed is not True
        or authority.canary_routing_allowed is not True
    ):
        return "invalid_authority"
    for key in _FALSE_RUNTIME_AUTHORITY_KEYS:
        attr = _camel_to_snake(key).replace("writes", "write")
        if getattr(authority, attr) is not False:
            return "invalid_authority"
    return None


def _python_ready_response(
    *,
    runtime: OpenMagiRuntime,
    content: str,
    event_count: int,
    adk_invoked: bool,
    runner_attempted: bool,
    model_call_attempted: bool,
    mocked_runner_invoked: bool,
    provider: str | None = None,
    model: str | None = None,
    counter_state: object | None = None,
    counter_status: str = "runner_completed",
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    model_attempt_digest: str | None = None,
    observed_egress_evidence: ObservedEgressEvidence | None = None,
    public_events: Sequence[Mapping[str, object]] = (),
    research_first_metadata: Mapping[str, object] | None = None,
    first_party_harness_metadata: Mapping[str, object] | None = None,
    verifier_evidence_status: EgressVerifierStatus | None = None,
) -> JSONResponse:
    active_tools = _route_tool_bundle_names(gate1a_bundle)
    gate8_metadata = _gate8_selected_authority_metadata(runtime)
    gate8_ready = bool(gate8_metadata and gate8_metadata.get("readinessReady") is True)
    body: dict[str, object] = {
        "schemaVersion": "gate5b.userVisibleChatCompletion.v1",
        "status": "python_ready",
        "fallbackStatus": "none",
        "responseAuthority": "python",
        "runtime": runtime.config.runtime,
        "runtimeEngine": runtime.config.runtime_engine,
        "authority": _python_canary_authority(gate1a_bundle, gate8_ready=gate8_ready),
        "safety": _surface_safety(gate1a_bundle, gate8_ready=gate8_ready),
        "adk": {
            "available": runtime.adk_boundary.available,
            "invoked": adk_invoked,
        },
        "activeTools": active_tools,
        "runnerAttempted": runner_attempted,
        "modelCallAttempted": model_call_attempted,
        "modelAttemptCount": 1 if model_call_attempted else 0,
        "mockedRunnerInvoked": mocked_runner_invoked,
        "eventCount": event_count,
        "publicEvents": [
            dict(event)
            for event in public_events
            if isinstance(event, Mapping)
        ],
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
    }
    if provider is not None:
        body["provider"] = provider
    if model is not None:
        body["model"] = model
    if counter_state is not None and hasattr(counter_state, "model_dump"):
        body["counter"] = {
            "status": counter_status,
            "state": counter_state.model_dump(by_alias=True, mode="json"),
        }
    if _route_tool_bundle_ready(gate1a_bundle):
        body["tooling"] = _route_tooling_metadata(gate1a_bundle)
    if model_call_attempted and (
        _route_tool_bundle_ready(gate1a_bundle) or gate8_ready
    ):
        body.update(
            _gate1a_observed_egress_metadata(
                observed_egress_evidence=observed_egress_evidence,
                model_attempt_digest=model_attempt_digest,
            )
        )
    if gate8_ready and gate8_metadata is not None:
        body["gate"] = "gate8_selected_python_authority"
        body["gate8Readiness"] = gate8_metadata
    if research_first_metadata is not None:
        body["researchFirst"] = dict(research_first_metadata)
    if first_party_harness_metadata is not None:
        body["firstPartyHarness"] = dict(first_party_harness_metadata)
    # Egress critic gate signal (default-OFF). Only added to the body when the
    # gate ran AND produced a non-None status, so the off-state body is
    # byte-identical to before.
    if verifier_evidence_status is not None:
        body["verifierEvidenceStatus"] = verifier_evidence_status
    return JSONResponse(status_code=200, content=body)


def _python_canary_authority(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    *,
    gate8_ready: bool = False,
) -> dict[str, bool]:
    gate1a_ready = _route_tool_bundle_readonly(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    authority = {
        "userVisibleOutputAllowed": True,
        "canaryRoutingAllowed": True,
        **{key: False for key in _FALSE_RESPONSE_AUTHORITY_KEYS},
    }
    if gate1a_ready:
        authority["readOnlyToolDispatchAllowed"] = True
    if full_toolhost_ready:
        authority["toolDispatchAllowed"] = True
        authority["selectedWorkspaceMutationAllowed"] = True
        authority["productionWorkspaceMutationAllowed"] = False
        authority["bashCommandAllowed"] = "Bash" in _route_tool_bundle_names(gate1a_bundle)
    if gate8_ready:
        authority["readOnlyToolDispatchAllowed"] = False
        authority["backgroundTaskAllowed"] = False
        authority["selfImprovementAllowed"] = False
    return authority


def _surface_safety(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    *,
    gate8_ready: bool = False,
) -> dict[str, object]:
    gate1a_ready = _route_tool_bundle_readonly(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    safety: dict[str, object] = {
        "toolsActive": False,
        "memoryProviderActive": False,
        "browserActive": False,
        "workspaceMutationAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "telegramDeliveryAllowed": False,
        "artifactChannelDeliveryAllowed": False,
        "evidenceBlockModeAllowed": False,
        "productionTranscriptWritesAllowed": False,
        "productionSseWritesAllowed": False,
        "productionDbWritesAllowed": False,
    }
    if gate1a_ready:
        safety.update(
            {
                "toolsActive": True,
                "readOnlyToolsActive": True,
                "toolHostMode": "shadow_readonly",
                "allowedReadOnlyTools": list(gate1a_bundle.exposed_tool_names),
                "writeMutationAllowed": False,
            }
        )
    if full_toolhost_ready:
        safety.update(
            {
                "toolsActive": True,
                "readOnlyToolsActive": False,
                "toolHostMode": "selected_full_toolhost",
                "allowedToolNames": _route_tool_bundle_names(gate1a_bundle),
                "selectedWorkspaceMutationAllowed": True,
                "productionWorkspaceMutationAllowed": False,
                "writeMutationAllowed": True,
                "bashCommandAllowed": "Bash" in _route_tool_bundle_names(gate1a_bundle),
            }
        )
    if gate8_ready:
        safety.update(
            {
                "readOnlyToolsActive": False,
                "toolHostMode": "disabled",
                "schedulerMutationAllowed": False,
                "backgroundTaskAllowed": False,
                "selfImprovementAllowed": False,
            }
        )
    return safety


def _disabled_surface_safety() -> dict[str, bool]:
    return {
        key: value
        for key, value in _surface_safety().items()
        if isinstance(value, bool)
    }


def _gate8_selected_authority_metadata(
    runtime: OpenMagiRuntime,
) -> dict[str, object] | None:
    gate8 = gate8_readiness_health_metadata(
        runtime.config.gate8_readiness,
        runtime.config.context_continuity,
        bot_id=runtime.config.bot_id,
        user_id=runtime.config.user_id,
        observed_egress=observed_egress_diagnostics(
            get_observed_egress_evidence_provider(runtime)
        ),
    )
    return gate8 if gate8.get("readinessReady") is True else None


def _route_tooling_metadata(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> dict[str, object]:
    if isinstance(bundle, Gate5BFullToolBundle):
        return _gate5b_full_tooling_metadata(bundle)
    return _gate1a_tooling_metadata(bundle)


def _gate1a_tooling_metadata(bundle: Gate1AReadOnlyToolBundle) -> dict[str, object]:
    attachment_flags = bundle.attachment_flags.model_dump(by_alias=True, mode="json")
    exposed = set(bundle.exposed_tool_names)
    forbidden = sorted(exposed.intersection(GATE1A_FORBIDDEN_TOOL_NAMES))
    return {
        "schemaVersion": "gate1a.readOnlyTooling.v1",
        "mode": "shadow_readonly",
        "toolsPolicy": "shadow_readonly",
        "allowedToolNames": list(bundle.exposed_tool_names),
        "forbiddenToolsExposed": forbidden,
        "receiptCount": bundle.host.counter.receipt_count,
        "routeAttached": attachment_flags["routeAttached"],
        "productionAttached": attachment_flags["productionAttached"],
        "attachmentFlags": attachment_flags,
        "sourceLedgerProjection": bundle.source_ledger_projection,
        "receiptLimits": {
            "maxToolCallsPerTurn": bundle.host.config.max_tool_calls_per_turn,
            "maxPerToolOutputBytes": bundle.host.config.max_per_tool_output_bytes,
            "maxAggregateOutputBytes": bundle.host.config.max_aggregate_output_bytes,
        },
    }


def _gate5b_full_tooling_metadata(bundle: Gate5BFullToolBundle) -> dict[str, object]:
    attachment_flags = bundle.attachment_flags.model_dump(by_alias=True, mode="json")
    exposed = set(bundle.exposed_tool_names)
    forbidden = sorted(
        name
        for name in exposed
        if name not in set(GATE5B_FULL_TOOLHOST_TOOL_NAMES)
    )
    return {
        "schemaVersion": "gate5b.selectedFullToolhost.v1",
        "mode": "selected_full_toolhost",
        "toolsPolicy": "selected_full_toolhost",
        "allowedToolNames": list(bundle.exposed_tool_names),
        "childRunner": child_runner_availability_metadata(
            legacy_child_execution_allowed=False,
            allowed_tool_names=bundle.exposed_tool_names,
        ),
        "forbiddenToolsExposed": forbidden,
        "receiptCount": bundle.host.counter.receipt_count,
        "routeAttached": attachment_flags["routeAttached"],
        "productionAttached": attachment_flags["productionAttached"],
        "workspaceRootDigest": bundle.workspace_root_digest,
        "attachmentFlags": attachment_flags,
        "receiptLimits": {
            "maxToolCallsPerTurn": bundle.host.config.max_tool_calls_per_turn,
            "maxPerToolOutputBytes": bundle.host.config.max_per_tool_output_bytes,
            "commandTimeoutMs": bundle.host.config.command_timeout_ms,
        },
    }


def _gate1a_observed_egress_metadata(
    *,
    observed_egress_evidence: ObservedEgressEvidence | None,
    model_attempt_digest: str | None,
) -> dict[str, object]:
    if observed_egress_evidence is None:
        metadata: dict[str, object] = {
            "egressEvidenceStatus": "missing_observed_egress_evidence",
        }
        if model_attempt_digest is not None:
            metadata["modelAttemptDigest"] = model_attempt_digest
        return metadata

    evidence = observed_egress_evidence.model_dump(by_alias=True, mode="json")
    provider_request_count = observed_egress_evidence.provider_request_count
    expected_max = (
        _GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT
        * max(provider_request_count, 1)
    )
    metadata = {
        "egressEvidenceStatus": "observed_egress_evidence_present",
        "observedEgressEvidence": evidence,
        "providerRequestCount": provider_request_count,
        "egressTunnelCount": observed_egress_evidence.egress_tunnel_count,
        "egressHostClasses": list(observed_egress_evidence.egress_host_classes),
        "egressDisciplineMode": _GATE1A_EGRESS_DISCIPLINE_MODE,
        "expectedEgressTunnelRange": {"min": 0, "max": expected_max},
        "egressEvidenceSource": observed_egress_evidence.evidence_source,
        "egressEvidenceRedactionStatus": observed_egress_evidence.redaction_status,
        "egressEvidenceDecisionReason": observed_egress_evidence.decision_reason,
        "egressWindowStartedAt": observed_egress_evidence.observed_window_start,
        "egressWindowEndedAt": observed_egress_evidence.observed_window_end,
    }
    correlation_digest = (
        observed_egress_evidence.correlation_digest
        or observed_egress_evidence.request_digest
    )
    if correlation_digest is not None:
        metadata["egressCorrelationDigest"] = correlation_digest
    if observed_egress_evidence.model_attempt_digest is not None:
        metadata["modelAttemptDigest"] = observed_egress_evidence.model_attempt_digest
    elif model_attempt_digest is not None:
        metadata["modelAttemptDigest"] = model_attempt_digest
    return metadata
