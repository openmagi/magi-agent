from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, ClassVar, Literal, Self, TypeAlias

logger = logging.getLogger(__name__)

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.config.env import parse_output_continuation_env
from magi_agent.evidence.gate1a_egress_correlation import (
    Gate1AEgressCorrelationContext,
    build_gate1a_proxy_http_options,
)
from magi_agent.runtime.output_continuation import (
    OutputContinuationConfig,
    build_continuation_message,
    should_continue,
    stop_reason_is_truncated,
)
from magi_agent.runtime.public_events import (
    tool_end_event,
    tool_input_preview,
    tool_progress_event,
    tool_start_event,
    turn_phase_event,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ModelRoutingSource,
    Gate5B4C3ShadowGenerationAuthorityFlags,
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationDiagnostic,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    build_gate5b4c3_runner_input,
)
from magi_agent.shadow.gate5b4c3_image_parts import image_blocks_to_parts
from magi_agent.shadow.session_service_registry import (
    SessionServiceRegistry,
    default_session_service_registry,
)


Gate5B4C3LiveRunnerStatus: TypeAlias = Literal["skipped", "dropped", "completed", "error"]
Gate5B4C3LiveRunnerReason: TypeAlias = Literal[
    "not_accepted",
    "input_adapter_drop",
    "adk_primitives_error",
    "runner_completed",
    "runner_incomplete",
    "runner_output_missing",
    "runner_timeout",
    "runner_error",
]
Gate5B4C3LiveRunnerDiagnosticStage: TypeAlias = Literal[
    "route_admission",
    "runner_input_adapter",
    "gate1a_tool_policy",
    "adk_primitives_load",
    "generate_content_config",
    "proxy_correlation_config",
    "session_service_construction",
    "adk_agent_construction",
    "adk_runner_construction",
    "runner_message_adapter",
    "provider_client_setup",
    "toolhost_attachment",
    "adk_tool_schema",
    "adk_tool_invocation_adapter",
    "provider_request_serialization",
    "runner_execution",
    "runner_output_projection",
    "unexpected_exception",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ALLOWED_AGENT_KWARGS = (
    "description",
    "generate_content_config",
    "instruction",
    "model",
    "name",
    "tools",
)
_ALLOWED_RUNNER_KWARGS = (
    "agent",
    "app_name",
    "auto_create_session",
    "plugins",
    "session_service",
)
_ALLOWED_RUN_ASYNC_KWARGS = ("new_message", "run_config", "session_id", "user_id")
_MAX_MANUAL_TOOL_CONTINUATIONS = 4
_MANUAL_TOOL_EVENT_LIMIT = 64
_DEFAULT_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT = 2048
_MAX_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT = 8192
_MAX_MANUAL_TOOL_RESULTS_BYTES = 8192
_ERROR_REDACTION_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"(?:Cookie|Set-Cookie):\s*[^;\r\n]+(?:;[^\r\n]*)?|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"[\"']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|private[_-]?key|session[_-]?key)[\"']?\s*:"
    r"\s*[\"'][^\"'\r\n]{4,}[\"']|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|"
    r"_API_KEY|_SERVICE_ROLE_KEY))"
    r"\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password|service[_-]?role[_-]?key)"
    r"\s*[:=]\s*\S+|"
    r"hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|"
    r"private_tool_preview|private_tool_input|private_tool_output|raw_tool_preview|"
    r"\b(?:prompt|output|request[_-]?body|response[_-]?body)\s*[:=]\s*\S+|"
    r"/(?:data/bots|workspace|var/lib/kubelet|mnt|private|Users)\S*|"
    r"\b(?:kubectl|helm|kustomize|sealed-secrets|kubeconfig)\b|"
    r"\bmagi\.pro\b\S*|"
    r"https?://\S+|"
    r"s3://\S+"
    r")",
    re.IGNORECASE,
)
_PROVIDER_CLIENT_SETUP_RE = re.compile(
    r"(?:"
    r"\b(?:no|missing|required|not\s+configured|not\s+found|could\s+not\s+find|"
    r"unable\s+to\s+find)\b.{0,80}\b(?:api[-_ ]?key|credential|credentials|auth)\b|"
    r"\b(?:api[-_ ]?key|credential|credentials|auth)\b.{0,80}"
    r"\b(?:missing|required|not\s+configured|not\s+found)\b|"
    r"application\s+default\s+credentials"
    r")",
    re.IGNORECASE,
)
_PROXY_CORRELATION_RE = re.compile(
    r"\b(?:gate1a|x-gate1a|correlation|connect\s+header|connect\s+headers)\b",
    re.IGNORECASE,
)
_ADK_FUNCTION_TOOL_SCHEMA_RE = re.compile(
    r"\b(?:function\s*tool|functiontool|function_tool|tool\s+schema|"
    r"function\s+declaration|tool\s+signature|function\s+signature|"
    r"callable\s+signature)\b",
    re.IGNORECASE,
)
_ADK_FUNCTION_TOOL_INVOCATION_RE = re.compile(
    r"\b(?:tool\s+context|tool_context|tool\s+call|function\s+response|"
    r"missing\s+input\s+parameter)\b",
    re.IGNORECASE,
)
_PROVIDER_REQUEST_SERIALIZATION_RE = re.compile(
    r"\b(?:generatecontentparameters|functiondeclaration|"
    r"function_declaration|convert_to_dict|encode_unserializable|"
    r"request\s+serialization)\b",
    re.IGNORECASE,
)
_INCOMPLETE_WAIT_OUTPUT_RE = re.compile(
    r"(?:잠시만|기다려\s*주세요|기다려\s*주시면|please\s+wait|still\s+working|"
    r"one\s+moment)",
    re.IGNORECASE,
)
_INCOMPLETE_PROMISE_OUTPUT_RE = re.compile(
    r"(?:하겠습니다|진행하겠습니다|실행하겠습니다|준비하겠습니다|"
    r"\bI\s+will\b|\bI'll\b|\bI\s+am\s+going\s+to\b|\bI'm\s+going\s+to\b|"
    r"\bwill\s+(?:run|execute|start|prepare|analy[sz]e|work)\b)",
    re.IGNORECASE,
)
_INCOMPLETE_WORK_REF_RE = re.compile(
    r"(?:/[A-Za-z0-9_.:-]+|분석|리포트|보고서|작업|병렬|실행|"
    r"\breport\b|\banalys[ie]s\b|\bqueue\b|\bparallel\b|\btask\b)",
    re.IGNORECASE,
)
_COMPLETION_EVIDENCE_RE = re.compile(
    r"(?:완료|끝났|마쳤|결과|final\s+answer|completed|done)",
    re.IGNORECASE,
)
# Promise+work-reference phrasing only signals an incomplete stub for short
# outputs; longer answers that carry real delivered content are treated as
# complete even without an explicit completion token.
_INCOMPLETE_PROMISE_MAX_OUTPUT_CHARS = 600
_PRE_PROVIDER_EXCEPTION_CATEGORIES = frozenset(
    {
        "provider_client_setup_failure",
        "proxy_correlation_config_failure",
        "adk_function_tool_schema_mismatch",
        "provider_request_serialization_failure",
    }
)
_GATE5B_LITELLM_PROVIDER_PREFIX: Mapping[str, str] = {
    "openai": "openai",
    "fireworks": "fireworks_ai",
}
_GATE5B_LITELLM_PROVIDER_ENV_KEYS: Mapping[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
}


@dataclass(frozen=True)
class Gate5B4C3LiveAdkPrimitives:
    Agent: type
    Runner: type
    InMemorySessionService: type
    Content: type
    Part: type
    GenerateContentConfig: type


class Gate5B4C3LiveRunnerErrorDiagnostic(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate5b4c3.runnerErrorDiagnostic.v1"] = Field(
        default="gate5b4c3.runnerErrorDiagnostic.v1",
        alias="schemaVersion",
    )
    stage: Gate5B4C3LiveRunnerDiagnosticStage
    reason_code: str = Field(alias="reasonCode")
    exception_class: str | None = Field(default=None, alias="exceptionClass")
    exception_category: str | None = Field(default=None, alias="exceptionCategory")
    error_preview: str | None = Field(default=None, max_length=256, alias="errorPreview")
    traceback_markers: tuple[str, ...] = Field(default=(), alias="tracebackMarkers")
    request_digest: str = Field(alias="requestDigest")
    trace_id_digest: str | None = Field(default=None, alias="traceIdDigest")
    model_attempt_digest: str | None = Field(default=None, alias="modelAttemptDigest")
    correlation_digest: str | None = Field(default=None, alias="correlationDigest")
    route_mode: str = Field(alias="routeMode")
    gate_mode: str = Field(alias="gateMode")
    tools_policy: str = Field(alias="toolsPolicy")
    routing_source: str = Field(alias="routingSource")
    correlation_mode: str = Field(alias="correlationMode")
    active_tool_names: tuple[str, ...] = Field(default=(), alias="activeToolNames")
    adk_invoked: bool = Field(default=False, alias="adkInvoked")
    runner_attempted: bool = Field(default=False, alias="runnerAttempted")
    model_call_attempted: bool = Field(default=False, alias="modelCallAttempted")
    tools_enabled: bool = Field(default=False, alias="toolsEnabled")
    tool_host_dispatch_allowed: bool = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    adk_primitives_loader_configured: bool = Field(
        default=False,
        alias="adkPrimitivesLoaderConfigured",
    )
    gate1a_egress_correlation_context_present: bool = Field(
        default=False,
        alias="gate1aEgressCorrelationContextPresent",
    )
    gate1a_proxy_url_configured: bool = Field(
        default=False,
        alias="gate1aProxyUrlConfigured",
    )
    egress_correlation_headers_configured: bool = Field(
        default=False,
        alias="egressCorrelationHeadersConfigured",
    )

    @model_validator(mode="after")
    def _validate_public_safe_fields(self) -> Self:
        for label in (
            self.schema_version,
            self.reason_code,
            self.exception_class,
            self.exception_category,
            self.route_mode,
            self.gate_mode,
            self.tools_policy,
            self.routing_source,
            self.correlation_mode,
        ):
            if label is not None and not _SAFE_LABEL_RE.match(label):
                raise ValueError("runner diagnostic labels must be public-safe")
        for digest in (
            self.request_digest,
            self.trace_id_digest,
            self.model_attempt_digest,
            self.correlation_digest,
        ):
            if digest is not None and not _DIGEST_RE.match(digest):
                raise ValueError("runner diagnostic digests must be sha256 digests")
        for tool_name in self.active_tool_names:
            if not _SAFE_TOOL_NAME_RE.match(tool_name):
                raise ValueError("runner diagnostic tool names must be public-safe")
        if self.error_preview is not None and _ERROR_REDACTION_RE.search(
            self.error_preview
        ):
            raise ValueError("runner diagnostic error preview must be redacted")
        for marker in self.traceback_markers:
            if not _SAFE_LABEL_RE.match(marker):
                raise ValueError("runner diagnostic traceback markers must be public-safe")
        return self


class Gate5B4C3LiveRunnerBoundaryResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate5b4c3.liveRunnerBoundary.v1"] = Field(
        default="gate5b4c3.liveRunnerBoundary.v1",
        alias="schemaVersion",
    )
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic
    status: Gate5B4C3LiveRunnerStatus
    reason: Gate5B4C3LiveRunnerReason
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fail_open: Literal[True] = Field(default=True, alias="failOpen")
    adk_invoked: bool = Field(default=False, alias="adkInvoked")
    runner_attempted: bool = Field(default=False, alias="runnerAttempted")
    model_call_via_adk_runner_attempted: bool = Field(
        default=False,
        alias="modelCallViaAdkRunnerAttempted",
    )
    event_count: int = Field(default=0, ge=0, alias="eventCount")
    latency_ms: int = Field(default=0, ge=0, alias="latencyMs")
    timeout_ms: int = Field(default=0, ge=0, alias="timeoutMs")
    selected_provider: str = Field(default="", alias="selectedProvider")
    selected_model: str = Field(default="", alias="selectedModel")
    routing_source: Gate5B4C3ModelRoutingSource = Field(alias="routingSource")
    agent_kwargs_keys: tuple[str, ...] = Field(default=(), alias="agentKwargsKeys")
    runner_kwargs_keys: tuple[str, ...] = Field(default=(), alias="runnerKwargsKeys")
    run_async_kwargs_keys: tuple[str, ...] = Field(default=(), alias="runAsyncKwargsKeys")
    error_class: str | None = Field(default=None, alias="errorClass")
    error_preview: str | None = Field(default=None, alias="errorPreview")
    runner_error_diagnostic: Gate5B4C3LiveRunnerErrorDiagnostic | None = Field(
        default=None,
        alias="runnerErrorDiagnostic",
    )
    output_text_internal: str | None = Field(
        default=None,
        alias="outputTextInternal",
        exclude=True,
    )
    usage_internal: dict[str, int] | None = Field(
        default=None,
        alias="usageInternal",
        exclude=True,
    )
    user_visible_output: str | None = Field(default=None, alias="userVisibleOutput")
    authority: Gate5B4C3ShadowGenerationAuthorityFlags = Field(
        default_factory=Gate5B4C3ShadowGenerationAuthorityFlags,
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        data = {
            key: value.model_dump(by_alias=True, mode="python", warnings=False)
            if isinstance(value, BaseModel)
            else value
            for key, value in values.items()
        }
        return cls(**data)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            name_to_alias = {
                name: field.alias or name
                for name, field in self.__class__.model_fields.items()
            }
            data.update({name_to_alias.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _force_non_authoritative_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "typescript"
        data["diagnosticOnly"] = True
        data["localOnly"] = True
        data["failOpen"] = True
        data["userVisibleOutput"] = None
        return data

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


AdkPrimitivesLoader: TypeAlias = Callable[[], Gate5B4C3LiveAdkPrimitives]
Gate5B4C3PublicEventSink: TypeAlias = Callable[[Mapping[str, object]], None]


def _noop() -> None:
    return None


class Gate5B4C3LiveRunnerBoundary:
    def __init__(
        self,
        adk_primitives_loader: AdkPrimitivesLoader | None = None,
        *,
        adk_tools: Sequence[object] = (),
        gate1a_egress_correlation_context: Gate1AEgressCorrelationContext | None = None,
        gate1a_egress_proxy_url: str | None = None,
        public_event_sink: Gate5B4C3PublicEventSink | None = None,
        session_service_registry: SessionServiceRegistry | None = None,
        control_plane_plugins: Sequence[object] = (),
    ) -> None:
        self._adk_primitives_loader = (
            adk_primitives_loader or load_gate5b4c3_live_adk_primitives
        )
        self._adk_tools = tuple(adk_tools)
        self._gate1a_egress_correlation_context = gate1a_egress_correlation_context
        self._gate1a_egress_proxy_url = str(gate1a_egress_proxy_url or "").strip()
        self._public_event_sink = public_event_sink
        self._session_service_registry = session_service_registry
        # MAGI_GATE5B_GOVERNANCE: optional control-plane plugin(s) to attach to
        # the gate5b ADK runner so loop-guard / compaction / edit-retry /
        # self-review / max-steps / tool-synthesis controls reach this path the
        # SAME way they reach the cli/engine runner (via App/Runner plugins). The
        # caller (chat_routes) builds these from ``build_default_plugin`` only
        # when the governance flag is ON; the default empty tuple keeps every
        # pre-existing call site byte-identical (no ``plugins`` kwarg passed).
        self._control_plane_plugins = tuple(control_plane_plugins)

    def invoke(
        self,
        request: Gate5B4C3ShadowGenerationRequest,
        *,
        config: Gate5B4C3ShadowGenerationConfig | None = None,
    ) -> Gate5B4C3LiveRunnerBoundaryResult:
        return asyncio.run(self.invoke_async(request, config=config))

    async def invoke_async(
        self,
        request: Gate5B4C3ShadowGenerationRequest,
        *,
        config: Gate5B4C3ShadowGenerationConfig | None = None,
    ) -> Gate5B4C3LiveRunnerBoundaryResult:
        """Run one boundary turn, releasing any session-service lease at the end.

        Behind ``MAGI_HOSTED_SESSION_REUSE`` the session registry marks the
        turn's ``(bot_id_digest, session_id)`` key busy (per-key single-flight)
        so overlapping same-key turns — ``invoke()`` runs ``asyncio.run`` per
        call and may execute on multiple worker threads — never mutate one
        session service concurrently. The busy mark must drop only after the
        turn fully stops consuming the runner (including the no-tool finalizer
        rerun), so the release lives here, in a ``finally`` around the whole
        turn body — covering every return and exception path.
        """
        session_lease_releases: list[Callable[[], None]] = []
        try:
            result = await self._invoke_async_turn(
                request,
                config=config,
                session_lease_releases=session_lease_releases,
            )
            self._emit_turn_completion(request, result)
            return result
        finally:
            for release_session_lease in session_lease_releases:
                try:
                    release_session_lease()
                except Exception:
                    # Lease release must never change the boundary result.
                    pass

    async def _invoke_async_turn(
        self,
        request: Gate5B4C3ShadowGenerationRequest,
        *,
        config: Gate5B4C3ShadowGenerationConfig | None = None,
        session_lease_releases: list[Callable[[], None]],
    ) -> Gate5B4C3LiveRunnerBoundaryResult:
        started = time.monotonic()
        diagnostic = build_gate5b4c3_shadow_generation_diagnostic(request, config=config)
        if not diagnostic.accepted:
            return _result(
                request,
                diagnostic,
                status="skipped",
                reason="not_accepted",
                started=started,
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage="route_admission",
                    reason_code="route_not_accepted",
                    exception_category="route_config_admission_failure",
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
            )

        runner_input_result = build_gate5b4c3_runner_input(request)
        if runner_input_result.status != "accepted" or runner_input_result.runner_input is None:
            return _result(
                request,
                diagnostic,
                status="dropped",
                reason="input_adapter_drop",
                started=started,
                error_preview=runner_input_result.reason,
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage="runner_input_adapter",
                    reason_code=runner_input_result.reason,
                    exception_category="request_shape_runner_input_adapter_failure",
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
            )
        runner_input = runner_input_result.runner_input
        if runner_input.tools_enabled != bool(self._adk_tools):
            return _result(
                request,
                diagnostic,
                status="dropped",
                reason="input_adapter_drop",
                started=started,
                error_preview="tool_policy_mismatch",
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage="gate1a_tool_policy",
                    reason_code="gate1a_tool_policy_mismatch",
                    exception_category="gate1a_tool_policy_mismatch",
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
            )

        try:
            primitives = self._adk_primitives_loader()
        except Exception as exc:
            return _result(
                request,
                diagnostic,
                status="error",
                reason="adk_primitives_error",
                started=started,
                error_class=type(exc).__name__,
                error_preview=_redacted_preview(str(exc)),
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage="adk_primitives_load",
                    reason_code="adk_primitives_load_failed",
                    exception=exc,
                    exception_category="adk_primitives_load_failure",
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
            )

        try:
            generate_content_config = primitives.GenerateContentConfig(
                maxOutputTokens=runner_input.max_output_tokens,
            )
        except Exception as exc:
            return _setup_error_result(
                request,
                diagnostic,
                started=started,
                stage="generate_content_config",
                reason_code="generate_content_config_failed",
                exception=exc,
                exception_category="request_shape_runner_input_adapter_failure",
                active_tools=self._adk_tools,
                gate1a_egress_correlation_context=self._gate1a_egress_correlation_context,
                gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
            )
        try:
            model_for_agent = _gate1a_correlated_model_or_label(
                runner_input.provider_label,
                runner_input.model_label,
                self._gate1a_egress_correlation_context,
                self._gate1a_egress_proxy_url,
            )
        except Exception as exc:
            return _setup_error_result(
                request,
                diagnostic,
                started=started,
                stage="proxy_correlation_config",
                reason_code="proxy_correlation_config_failed",
                exception=exc,
                exception_category="proxy_correlation_config_failure",
                active_tools=self._adk_tools,
                gate1a_egress_correlation_context=self._gate1a_egress_correlation_context,
                gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
            )
        agent_kwargs = {
            "name": "openmagi_gate5b4c3_shadow_generation_agent",
            "description": "OpenMagi Gate 5B-4c-3 diagnostic shadow generation agent.",
            "model": model_for_agent,
            "instruction": runner_input.system_instruction,
            "tools": list(self._adk_tools),
            "generate_content_config": generate_content_config,
        }
        try:
            session_service, session_reused, mark_session_seeded = (
                self._acquire_session_service(
                    request,
                    primitives,
                    session_lease_releases,
                )
            )
        except Exception as exc:
            return _setup_error_result(
                request,
                diagnostic,
                started=started,
                stage="session_service_construction",
                reason_code="session_service_construction_failed",
                exception=exc,
                exception_category="adk_runner_construction_failure",
                active_tools=self._adk_tools,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                gate1a_egress_correlation_context=self._gate1a_egress_correlation_context,
                gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
            )
        try:
            agent = primitives.Agent(**_allowlist_kwargs(agent_kwargs, _ALLOWED_AGENT_KWARGS))
        except Exception as exc:
            toolhost_failure = runner_input.tools_enabled
            return _setup_error_result(
                request,
                diagnostic,
                started=started,
                stage="toolhost_attachment" if toolhost_failure else "adk_agent_construction",
                reason_code=(
                    "toolhost_attachment_failed"
                    if toolhost_failure
                    else "adk_agent_construction_failed"
                ),
                exception=exc,
                exception_category=(
                    "toolhost_attachment_failure"
                    if toolhost_failure
                    else "adk_runner_construction_failure"
                ),
                active_tools=self._adk_tools,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                gate1a_egress_correlation_context=self._gate1a_egress_correlation_context,
                gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
            )
        runner_kwargs = {
            "app_name": "openmagi-gate5b4c3-shadow-generation",
            "agent": agent,
            "session_service": session_service,
            "auto_create_session": True,
        }
        # MAGI_GATE5B_GOVERNANCE: attach the control-plane plugin(s) only when the
        # caller supplied them (flag ON). The key is omitted entirely otherwise so
        # ``_allowlist_kwargs`` never adds ``plugins`` to the Runner call — the
        # flag-OFF runner construction is byte-identical to today.
        if self._control_plane_plugins:
            runner_kwargs["plugins"] = list(self._control_plane_plugins)
        try:
            runner = primitives.Runner(**_allowlist_kwargs(runner_kwargs, _ALLOWED_RUNNER_KWARGS))
        except Exception as exc:
            return _setup_error_result(
                request,
                diagnostic,
                started=started,
                stage="adk_runner_construction",
                reason_code="adk_runner_construction_failed",
                exception=exc,
                exception_category="adk_runner_construction_failure",
                active_tools=self._adk_tools,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                gate1a_egress_correlation_context=self._gate1a_egress_correlation_context,
                gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
            )
        try:
            message = primitives.Content(
                parts=_build_user_message_parts(
                    runner_input,
                    primitives=primitives,
                    # On a session-registry hit the reused ADK session already
                    # holds the prior turns; re-ingesting the re-sent sanitized
                    # history would duplicate context. History stays a
                    # seed-on-miss (and the flag-OFF path always seeds).
                    include_history=not session_reused,
                ),
                role="user",
            )
        except Exception as exc:
            return _setup_error_result(
                request,
                diagnostic,
                started=started,
                stage="runner_message_adapter",
                reason_code="runner_message_adapter_failed",
                exception=exc,
                exception_category="request_shape_runner_input_adapter_failure",
                active_tools=self._adk_tools,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                gate1a_egress_correlation_context=self._gate1a_egress_correlation_context,
                gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
            )
        selected_full_toolhost = (
            request.recipe_profile.tools_policy == "selected_full_toolhost"
        )
        run_kwargs = {
            "user_id": "gate5b4c3-shadow-user",
            "session_id": _shadow_session_id(request),
            "new_message": message,
        }
        run_config = _selected_full_toolhost_run_config(
            selected_full_toolhost,
            max_llm_calls=request.budgets.max_adk_llm_calls,
        )
        if run_config is not None:
            run_kwargs["run_config"] = run_config

        event_count = 0
        output_chunks: list[str] = []
        manual_continuations = 0
        output_continuations = 0
        tool_only_events_seen = False
        prestarted_tool_event_ids: set[str] = set()
        completed_tool_event_ids: set[str] = set()
        live_tool_event_ids_by_adk_id: dict[str, str] = {}
        pending_live_tool_event_ids_by_name: dict[str, list[str]] = {}
        live_tool_started_at_by_id: dict[str, float] = {}
        usage_totals = [0, 0, 0]
        output_continuation = (
            _output_continuation_config_from_env() if selected_full_toolhost else None
        )
        self._emit_record(
            {
                "type": "turn_start",
                "prompt": getattr(runner_input, "sanitized_user_input", None),
                "provider": getattr(runner_input, "provider_label", None),
                "model": getattr(runner_input, "model_label", None),
            },
            request=request,
        )
        try:
            async with asyncio.timeout(request.budgets.python_runner_timeout_ms / 1000):
                next_message: object = message
                while True:
                    function_calls: list[Mapping[str, object]] = []
                    function_call_keys: set[str] = set()
                    function_responses_seen = False
                    current_run_output_chunks: list[str] = []
                    stream_usage: tuple[int, int, int] | None = None
                    current_run_truncated = False
                    current_run_kwargs = {**run_kwargs, "new_message": next_message}
                    async for event in runner.run_async(
                        **_allowlist_kwargs(
                            current_run_kwargs,
                            _ALLOWED_RUN_ASYNC_KWARGS,
                        )
                    ):
                        mark_session_seeded()
                        event_count += 1
                        chunk = _event_text(event)
                        if chunk:
                            visible_delta = _event_visible_text_delta(
                                event,
                                chunk,
                                current_run_output_chunks,
                            )
                            if visible_delta:
                                current_run_output_chunks.append(visible_delta)
                                output_chunks.append(visible_delta)
                                self._emit_public_event(
                                    {
                                        "type": "text_delta",
                                        "delta": visible_delta,
                                    }
                                )
                        thinking_chunk = _event_thinking_text(event)
                        if thinking_chunk and _event_is_partial(event):
                            # Stream model reasoning on the thinking channel.
                            # sse.py gates this behind MAGI_STREAM_THINKING; only
                            # partial events to avoid the non-partial aggregate.
                            self._emit_public_event(
                                {
                                    "type": "thinking_delta",
                                    "delta": thinking_chunk,
                                }
                            )
                        event_usage = _event_usage_metadata(event)
                        if event_usage is not None:
                            stream_usage = event_usage
                        if not current_run_truncated:
                            current_run_truncated = _event_finish_reason_is_truncated(event)
                        event_function_calls = _event_function_calls(event)
                        for function_call in event_function_calls:
                            function_call_key = _json_dumps(function_call)
                            if function_call_key in function_call_keys:
                                continue
                            function_call_index = len(function_calls)
                            function_call_keys.add(function_call_key)
                            function_calls.append(function_call)
                            tool_event_id = _live_tool_event_id_for_function_call(
                                function_call,
                                index=function_call_index,
                            )
                            _remember_live_tool_event_id(
                                function_call,
                                tool_event_id=tool_event_id,
                                ids_by_adk_id=live_tool_event_ids_by_adk_id,
                                pending_ids_by_name=pending_live_tool_event_ids_by_name,
                            )
                            self._emit_record(
                                _transcript_tool_call_record(
                                    function_call, call_id=tool_event_id
                                ),
                                request=request,
                            )
                            subagent_record = _transcript_subagent_record(function_call)
                            if subagent_record is not None:
                                self._emit_record(subagent_record, request=request)
                            if not _function_call_tool_emits_public_events(
                                function_call,
                                self._adk_tools,
                            ):
                                if tool_event_id not in prestarted_tool_event_ids:
                                    prestarted_tool_event_ids.add(tool_event_id)
                                    live_tool_started_at_by_id[tool_event_id] = (
                                        time.monotonic()
                                    )
                                    self._emit_public_event(
                                        tool_start_event(
                                            tool_id=tool_event_id,
                                            name=str(function_call.get("name", "")),
                                            input_preview=tool_input_preview(
                                                _function_call_args(function_call)
                                            ),
                                        )
                                    )
                                    self._emit_public_event(
                                        tool_progress_event(
                                            tool_id=tool_event_id,
                                            label=str(function_call.get("name", "")),
                                            status="in_progress",
                                            message="Tool execution started",
                                        )
                                    )
                        event_function_responses = _event_function_responses(event)
                        for function_response in event_function_responses:
                            normalized_response = _normalize_function_response(
                                function_response
                            )
                            if normalized_response is None:
                                continue
                            if _function_response_tool_emits_public_events(
                                normalized_response,
                                self._adk_tools,
                            ):
                                continue
                            tool_event_id = _live_tool_event_id_for_function_response(
                                normalized_response,
                                ids_by_adk_id=live_tool_event_ids_by_adk_id,
                                pending_ids_by_name=pending_live_tool_event_ids_by_name,
                            )
                            if tool_event_id in completed_tool_event_ids:
                                continue
                            completed_tool_event_ids.add(tool_event_id)
                            response_payload = normalized_response.get("response")
                            result_digest = _digest(response_payload)
                            status = _manual_tool_status(response_payload)
                            self._emit_record(
                                _transcript_tool_result_record(
                                    normalized_response, call_id=tool_event_id
                                ),
                                request=request,
                            )
                            self._emit_public_event(
                                tool_end_event(
                                    tool_id=tool_event_id,
                                    status="ok" if status == "ok" else "error",
                                    output_preview=f"result:{result_digest}",
                                    error=None if status == "ok" else status,
                                    receipt_refs=(f"result:{result_digest}",),
                                    duration_ms=_elapsed_ms(
                                        live_tool_started_at_by_id.get(
                                            tool_event_id,
                                            time.monotonic(),
                                        )
                                    ),
                                )
                            )
                        if event_function_calls or event_function_responses:
                            tool_only_events_seen = True
                        if event_function_responses:
                            function_responses_seen = True
                        if event_count >= _stream_event_limit(
                            selected_full_toolhost=selected_full_toolhost
                        ):
                            break
                    if stream_usage is not None:
                        usage_totals[0] += stream_usage[0]
                        usage_totals[1] += stream_usage[1]
                        usage_totals[2] += stream_usage[2]
                    if (
                        selected_full_toolhost
                        and event_count
                        < _stream_event_limit(
                            selected_full_toolhost=selected_full_toolhost
                        )
                        and not function_calls
                        and not function_responses_seen
                        and _should_continue_truncated_output(
                            output_continuation,
                            truncated=current_run_truncated,
                            output_seen=bool(current_run_output_chunks),
                            continuations_used=output_continuations,
                        )
                    ):
                        output_continuations += 1
                        assert output_continuation is not None
                        self._emit_public_event(
                            {
                                "type": "output_continuation",
                                "continuation": output_continuations,
                                "max": output_continuation.max_continuations,
                            }
                        )
                        next_message = primitives.Content(
                            parts=[
                                primitives.Part.from_text(
                                    text=_build_output_continuation_message()
                                )
                            ],
                            role="user",
                        )
                        continue
                    # Drive pending tool calls to execution even when the model
                    # also emitted preamble text in the same turn. Short-circuiting
                    # on `output_chunks` here discarded the model's unexecuted tool
                    # calls, leaving only a "I'll do it" promise — the structural
                    # cause of fail-closed "runner_incomplete" turns. Continue while
                    # the model still has unsatisfied tool-call intent we can run.
                    if (
                        event_count >= 64
                        or not function_calls
                        or function_responses_seen
                        or not self._adk_tools
                        or not selected_full_toolhost
                    ):
                        break
                    if manual_continuations >= _MAX_MANUAL_TOOL_CONTINUATIONS:
                        break
                    self._emit_public_event(
                        turn_phase_event(
                            turn_id=request.turn.turn_id,
                            phase="executing",
                        )
                    )
                    manual_results = await _run_manual_tool_calls(
                        function_calls,
                        self._adk_tools,
                        public_event_sink=self._emit_public_event,
                        prestarted_tool_event_ids=prestarted_tool_event_ids,
                    )
                    if not manual_results:
                        break
                    self._emit_public_event(
                        turn_phase_event(
                            turn_id=request.turn.turn_id,
                            phase="committing",
                        )
                    )
                    manual_continuations += 1
                    next_message = primitives.Content(
                        parts=[
                            primitives.Part.from_text(
                                text=_manual_tool_followup_text(manual_results),
                            )
                        ],
                        role="user",
                    )
                    if event_count >= 64:
                        break
        except TimeoutError:
            return _result(
                request,
                diagnostic,
                status="error",
                reason="runner_timeout",
                started=started,
                adk_invoked=True,
                runner_attempted=True,
                model_attempted=True,
                event_count=event_count,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                run_async_kwargs_keys=tuple(sorted(run_kwargs)),
                error_class="TimeoutError",
                error_preview="ADK Runner shadow generation exceeded its timeout budget.",
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage="runner_execution",
                    reason_code="runner_timeout",
                    exception_class="TimeoutError",
                    exception_category="runner_timeout",
                    adk_invoked=True,
                    runner_attempted=True,
                    model_attempted=True,
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
                output_text=_joined_output(output_chunks),
                usage=_usage_dict(tuple(usage_totals)),
            )
        except Exception as exc:
            if selected_full_toolhost and tool_only_events_seen:
                finalizer_output, finalizer_events = await _run_no_tool_finalizer(
                    primitives=primitives,
                    session_service=session_service,
                    request=request,
                    runner_input=runner_input,
                    run_kwargs=run_kwargs,
                    agent_kwargs=agent_kwargs,
                    runner_kwargs=runner_kwargs,
                    public_event_sink=self._emit_public_event,
                )
                event_count += finalizer_events
                if finalizer_output:
                    output_chunks.append(finalizer_output)
                    return _result(
                        request,
                        diagnostic,
                        status="completed",
                        reason="runner_completed",
                        started=started,
                        adk_invoked=True,
                        runner_attempted=True,
                        model_attempted=True,
                        event_count=event_count,
                        agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                        runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                        run_async_kwargs_keys=tuple(sorted(run_kwargs)),
                        output_text=_joined_output(output_chunks),
                        usage=_usage_dict(tuple(usage_totals)),
                    )
            stage, reason_code, exception_category = _classify_runner_exception(exc)
            model_attempted = not (
                event_count == 0
                and exception_category in _PRE_PROVIDER_EXCEPTION_CATEGORIES
            )
            return _result(
                request,
                diagnostic,
                status="error",
                reason="runner_error",
                started=started,
                adk_invoked=True,
                runner_attempted=True,
                model_attempted=model_attempted,
                event_count=event_count,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                run_async_kwargs_keys=tuple(sorted(run_kwargs)),
                error_class=type(exc).__name__,
                error_preview=_redacted_preview(str(exc)),
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage=stage,
                    reason_code=reason_code,
                    exception=exc,
                    exception_category=exception_category,
                    adk_invoked=True,
                    runner_attempted=True,
                    model_attempted=model_attempted,
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
                output_text=_joined_output(output_chunks),
                usage=_usage_dict(tuple(usage_totals)),
            )

        output_text = _joined_output(output_chunks)
        if output_text is None and selected_full_toolhost and tool_only_events_seen:
            finalizer_output, finalizer_events = await _run_no_tool_finalizer(
                primitives=primitives,
                session_service=session_service,
                request=request,
                runner_input=runner_input,
                run_kwargs=run_kwargs,
                agent_kwargs=agent_kwargs,
                runner_kwargs=runner_kwargs,
                public_event_sink=self._emit_public_event,
            )
            event_count += finalizer_events
            output_text = finalizer_output
        if output_text is None:
            return _result(
                request,
                diagnostic,
                status="error",
                reason="runner_output_missing",
                started=started,
                adk_invoked=True,
                runner_attempted=True,
                model_attempted=True,
                event_count=event_count,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                run_async_kwargs_keys=tuple(sorted(run_kwargs)),
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage="runner_output_projection",
                    reason_code="runner_output_missing",
                    exception_category="runner_output_projection_failure",
                    adk_invoked=True,
                    runner_attempted=True,
                    model_attempted=True,
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
            )
        if (
            selected_full_toolhost
            and _looks_like_incomplete_full_toolhost_output(output_text)
        ):
            return _result(
                request,
                diagnostic,
                status="error",
                reason="runner_incomplete",
                started=started,
                adk_invoked=True,
                runner_attempted=True,
                model_attempted=True,
                event_count=event_count,
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                run_async_kwargs_keys=tuple(sorted(run_kwargs)),
                runner_error_diagnostic=_runner_error_diagnostic(
                    request,
                    stage="runner_output_projection",
                    reason_code="runner_incomplete",
                    exception_category="runner_output_projection_failure",
                    adk_invoked=True,
                    runner_attempted=True,
                    model_attempted=True,
                    active_tools=self._adk_tools,
                    gate1a_egress_correlation_context=(
                        self._gate1a_egress_correlation_context
                    ),
                    gate1a_egress_proxy_url=self._gate1a_egress_proxy_url,
                ),
                output_text=output_text,
            )

        return _result(
            request,
            diagnostic,
            status="completed",
            reason="runner_completed",
            started=started,
            adk_invoked=True,
            runner_attempted=True,
            model_attempted=True,
            event_count=event_count,
            agent_kwargs_keys=tuple(sorted(agent_kwargs)),
            runner_kwargs_keys=tuple(sorted(runner_kwargs)),
            run_async_kwargs_keys=tuple(sorted(run_kwargs)),
            output_text=output_text,
            usage=_usage_dict(tuple(usage_totals)),
        )

    def _acquire_session_service(
        self,
        request: Gate5B4C3ShadowGenerationRequest,
        primitives: Gate5B4C3LiveAdkPrimitives,
        session_lease_releases: list[Callable[[], None]],
    ) -> tuple[object, bool, Callable[[], None]]:
        """Build (or, behind the reuse flag, fetch) the turn's session service.

        Flag OFF (default) preserves the historical fresh-instance-per-turn
        behavior and performs no registry interaction at all. Flag ON acquires
        from the process-scope registry keyed by the full
        ``(bot_id_digest, session_id)`` pair — distinct bots or sessions can
        never share session state — via :meth:`try_acquire`, which marks the
        key busy for the duration of the turn; an overlapping same-key turn
        gets a fresh, unregistered fallback service (single-flight). The
        matching identity-checked release is appended to
        ``session_lease_releases`` and runs in :meth:`invoke_async`'s
        ``finally`` once the turn fully ends. The second element reports
        whether an existing live session was reused (registry hit).

        Miss-created registry entries are provisional until the boundary sees
        a runner event, which is the first local proof that ADK consumed and
        seeded the new message. If setup or early runner execution fails before
        that, the release discards the exact provisional service and the next
        same-key turn reseeds sanitized history.

        Requests without a stable ``session_key_digest`` bypass the registry
        entirely (fresh service, never registered): their session id falls
        back to the per-request-unique request digest, so a registry entry
        could never be reused — each one would only churn the LRU and evict
        this bot's live sessions.
        """
        # Lazy import: shadow -> config is a function-level dependency by
        # convention in this package (avoids import cycles).
        from magi_agent.config.env import is_hosted_session_reuse_enabled

        if not is_hosted_session_reuse_enabled():
            return primitives.InMemorySessionService(), False, _noop
        if not request.selection.session_key_digest:
            return primitives.InMemorySessionService(), False, _noop
        registry = self._session_service_registry
        if registry is None:
            registry = default_session_service_registry()
        session_key = (request.selection.bot_id_digest, _shadow_session_id(request))
        session_service, session_reused = registry.try_acquire(
            session_key,
            primitives.InMemorySessionService,
        )
        seed_completed = {"value": session_reused}

        def mark_session_seeded() -> None:
            seed_completed["value"] = True

        bound_registry = registry
        session_lease_releases.append(
            lambda: bound_registry.release(
                session_key,
                session_service,
                seeded=seed_completed["value"],
            )
        )
        return session_service, session_reused, mark_session_seeded

    def _emit_public_event(self, payload: Mapping[str, object]) -> None:
        if self._public_event_sink is None:
            return
        try:
            self._public_event_sink(dict(payload))
        except Exception:
            # Streaming hints must never change the selected boundary result.
            return

    def _emit_record(
        self, event: Mapping[str, object], *, request: Gate5B4C3ShadowGenerationRequest
    ) -> None:
        """Append one full-fidelity record to the process-global session
        transcript sink (separate from the SSE ``_emit_public_event`` seam). No-op
        and fully fail-open when no transcript sink is registered (flag OFF) — so
        this never alters the boundary result or the SSE contract."""
        try:
            from magi_agent.observability.transcript import (
                get_active_transcript_sink,
            )

            sink = get_active_transcript_sink()
            if sink is None:
                return
            sink(dict(event), _shadow_session_id(request), request.turn.turn_id)
        except Exception:
            logger.debug("gate5b transcript record failed", exc_info=True)

    def _emit_turn_completion(
        self,
        request: Gate5B4C3ShadowGenerationRequest,
        result: object,
    ) -> None:
        """Emit the assembled assistant ``message`` (if any) and a ``turn_end``
        record from the structured boundary result — one chokepoint covering every
        return path of the turn body."""
        output_text = getattr(result, "output_text_internal", None)
        if output_text:
            self._emit_record(
                {"type": "message", "role": "assistant", "content": output_text},
                request=request,
            )
        self._emit_record(
            {
                "type": "turn_end",
                "terminal": getattr(result, "status", None),
                "reason": getattr(result, "reason", None),
                "usage": getattr(result, "usage_internal", None),
                "provider": getattr(result, "selected_provider", None),
                "model": getattr(result, "selected_model", None),
                "event_count": getattr(result, "event_count", None),
                "latency_ms": getattr(result, "latency_ms", None),
            },
            request=request,
        )


def load_gate5b4c3_live_adk_primitives() -> Gate5B4C3LiveAdkPrimitives:
    from google.adk import agents as adk_agents
    from google.adk import runners as adk_runners
    from google.adk import sessions as adk_sessions

    return Gate5B4C3LiveAdkPrimitives(
        Agent=adk_agents.Agent,
        Runner=adk_runners.Runner,
        InMemorySessionService=adk_sessions.InMemorySessionService,
        Content=adk_runners.types.Content,
        Part=adk_runners.types.Part,
        GenerateContentConfig=adk_runners.types.GenerateContentConfig,
    )


def _build_user_message_parts(
    runner_input: object,
    *,
    primitives: object,
    include_history: bool = True,
) -> list:
    parts = [
        primitives.Part.from_text(
            text=_runner_message_text(runner_input, include_history=include_history)
        )
    ]
    raw_blocks = getattr(runner_input, "sanitized_image_blocks", ()) or ()
    if not raw_blocks:
        return parts
    converter_blocks = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": b.media_type,  # validated str at ingestion
                "data": b.data,  # validated base64 str at ingestion
            },
        }
        for b in raw_blocks
    ]
    parts.extend(
        image_blocks_to_parts(converter_blocks, part_factory=primitives.Part.from_bytes)
    )
    return parts


def _runner_message_text(runner_input: object, *, include_history: bool = True) -> str:
    current = str(getattr(runner_input, "sanitized_user_input", "") or "")
    if not include_history:
        # Session-reuse hit: the prior turns already live in the reused ADK
        # session, so only the current sanitized turn text is sent.
        return current
    history = getattr(runner_input, "sanitized_recent_history", ())
    if not history:
        return current
    lines: list[str] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            lines.append(f"{role}: {content}")
    if not lines:
        return current
    return (
        "Recent sanitized conversation:\n"
        + "\n".join(lines)
        + "\n\nCurrent user message:\n"
        + current
    )


def run_gate5b4c3_live_runner_boundary(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    config: Gate5B4C3ShadowGenerationConfig | None = None,
    adk_primitives_loader: AdkPrimitivesLoader | None = None,
    adk_tools: Sequence[object] = (),
    gate1a_egress_correlation_context: Gate1AEgressCorrelationContext | None = None,
    gate1a_egress_proxy_url: str | None = None,
    public_event_sink: Gate5B4C3PublicEventSink | None = None,
    control_plane_plugins: Sequence[object] = (),
) -> Gate5B4C3LiveRunnerBoundaryResult:
    boundary = Gate5B4C3LiveRunnerBoundary(
        adk_primitives_loader or load_gate5b4c3_live_adk_primitives,
        adk_tools=adk_tools,
        gate1a_egress_correlation_context=gate1a_egress_correlation_context,
        gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        public_event_sink=public_event_sink,
        control_plane_plugins=control_plane_plugins,
    )
    return boundary.invoke(request, config=config)


async def run_gate5b4c3_live_runner_boundary_async(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    config: Gate5B4C3ShadowGenerationConfig | None = None,
    adk_primitives_loader: AdkPrimitivesLoader | None = None,
    adk_tools: Sequence[object] = (),
    gate1a_egress_correlation_context: Gate1AEgressCorrelationContext | None = None,
    gate1a_egress_proxy_url: str | None = None,
    public_event_sink: Gate5B4C3PublicEventSink | None = None,
    control_plane_plugins: Sequence[object] = (),
) -> Gate5B4C3LiveRunnerBoundaryResult:
    boundary = Gate5B4C3LiveRunnerBoundary(
        adk_primitives_loader or load_gate5b4c3_live_adk_primitives,
        adk_tools=adk_tools,
        gate1a_egress_correlation_context=gate1a_egress_correlation_context,
        gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        public_event_sink=public_event_sink,
        control_plane_plugins=control_plane_plugins,
    )
    return await boundary.invoke_async(request, config=config)


def _is_anthropic_route(provider_label: str, model_label: str) -> bool:
    """True when the route resolves to a Claude/Anthropic model via ADK.

    ADK's ``LLMRegistry`` matches Claude on ``claude-3-*`` / ``claude-*-4*``
    model ids; an explicit ``anthropic`` provider label also selects it. We
    mirror that here so the cache-aware subclass is chosen for the same routes
    ADK would route to a Claude/Anthropic model.

    Note: ``startswith("claude-")`` is a deliberate *superset* of ADK's two
    regexes (``claude-3-.*`` / ``claude-.*-4.*``). This is intentional
    future-proofing — any new ``claude-<gen>`` id should still take the
    cache-aware path. A label that starts with ``claude-`` but isn't yet in
    ADK's registry would simply fail to resolve inside ADK (unchanged from
    today), so the broader prefix is safe.
    """
    label = (model_label or "").lower()
    if provider_label == "anthropic":
        return True
    return label.startswith("claude-") or label.startswith("anthropic/")


def _gate1a_correlated_model_or_label(
    provider_label: str,
    model_label: str,
    context: Gate1AEgressCorrelationContext | None,
    proxy_url: str | None,
) -> object:
    if _is_anthropic_route(provider_label, model_label):
        # Route Claude/anthropic models through magi's cache-aware ADK subclass
        # so the outgoing Anthropic request carries rolling-tail cache markers
        # (gated on MAGI_MESSAGE_CACHE_ENABLED). The ``anthropic`` package is
        # imported lazily inside the builder, matching ADK's own gating.
        from magi_agent.adk_bridge.anthropic_cache_model import (
            build_cache_aware_claude,
        )

        try:
            return build_cache_aware_claude(model_label)
        except ModuleNotFoundError as exc:
            if exc.name != "anthropic":
                raise
            return model_label

    if provider_label in _GATE5B_LITELLM_PROVIDER_PREFIX:
        return _gate5b_litellm_model(provider_label, model_label)

    if (
        context is None
        or not proxy_url
        or provider_label != "google"
        or not model_label.startswith("gemini")
    ):
        return model_label

    from google.adk.models import Gemini
    from google.genai import Client

    class Gate1AEgressCorrelatedGemini(Gemini):
        openmagi_gate1a_proxy_connect_headers_enabled: ClassVar[bool] = True

        @cached_property
        def api_client(self) -> Client:
            base_url, api_version = self._base_url_and_api_version
            http_options = build_gate1a_proxy_http_options(
                context,
                proxy_url=proxy_url,
            )
            http_options.headers = self._tracking_headers()
            http_options.retry_options = self.retry_options
            http_options.base_url = base_url
            if api_version:
                http_options.api_version = api_version
            kwargs: dict[str, Any] = {"http_options": http_options}
            if self.model.startswith("projects/"):
                kwargs["vertexai"] = True
            return Client(**kwargs)

    return Gate1AEgressCorrelatedGemini(model=model_label)


def _gate5b_litellm_model(provider_label: str, model_label: str) -> object:
    """Build ADK LiteLlm for non-native selected canary routes.

    Gate5B passes provider and model as separate safe labels. ADK's LiteLlm
    needs the provider-prefixed model string, otherwise routes such as Fireworks
    and OpenAI are treated as bare model ids and cannot be exercised by the
    selected canary.
    """
    prefix = _GATE5B_LITELLM_PROVIDER_PREFIX[provider_label]
    try:
        from google.adk.models.lite_llm import LiteLlm  # noqa: PLC0415
    except Exception as exc:  # ImportError or downstream litellm import errors.
        raise RuntimeError(
            f"{provider_label}_litellm_dependency_missing",
        ) from exc
    try:
        import litellm  # noqa: PLC0415

        litellm.suppress_debug_info = True
    except Exception:
        pass
    kwargs: dict[str, Any] = {"model": f"{prefix}/{model_label}"}
    api_key = _first_present_env_value(
        _GATE5B_LITELLM_PROVIDER_ENV_KEYS.get(provider_label, ()),
    )
    if api_key:
        kwargs["api_key"] = api_key
    return LiteLlm(**kwargs)


def _first_present_env_value(names: Sequence[str]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _setup_error_result(
    request: Gate5B4C3ShadowGenerationRequest,
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic,
    *,
    started: float,
    stage: Gate5B4C3LiveRunnerDiagnosticStage,
    reason_code: str,
    exception: Exception,
    exception_category: str,
    active_tools: Sequence[object],
    agent_kwargs_keys: tuple[str, ...] = (),
    runner_kwargs_keys: tuple[str, ...] = (),
    gate1a_egress_correlation_context: Gate1AEgressCorrelationContext | None = None,
    gate1a_egress_proxy_url: str | None = None,
) -> Gate5B4C3LiveRunnerBoundaryResult:
    return _result(
        request,
        diagnostic,
        status="error",
        reason="runner_error",
        started=started,
        agent_kwargs_keys=agent_kwargs_keys,
        runner_kwargs_keys=runner_kwargs_keys,
        error_class=type(exception).__name__,
        error_preview=_redacted_preview(str(exception)),
        runner_error_diagnostic=_runner_error_diagnostic(
            request,
            stage=stage,
            reason_code=reason_code,
            exception=exception,
            exception_category=exception_category,
            active_tools=active_tools,
            gate1a_egress_correlation_context=gate1a_egress_correlation_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        ),
    )


def _looks_like_incomplete_full_toolhost_output(output_text: str) -> bool:
    normalized = " ".join(output_text.split())
    if not normalized:
        return False
    if _COMPLETION_EVIDENCE_RE.search(normalized):
        return False
    if _INCOMPLETE_WAIT_OUTPUT_RE.search(normalized):
        return True
    # A "promise to do work" phrase ("...분석을 실행하겠습니다") only marks an
    # incomplete stub when the turn delivered little else. A substantive answer
    # (e.g. a finished Korean financial analysis) legitimately mixes polite
    # future-tense with work references and rarely contains a literal completion
    # token, so only short promise-dominated output should be treated as
    # incomplete — otherwise real answers fail closed as "runner_incomplete".
    if len(normalized) > _INCOMPLETE_PROMISE_MAX_OUTPUT_CHARS:
        return False
    return bool(
        _INCOMPLETE_PROMISE_OUTPUT_RE.search(normalized)
        and _INCOMPLETE_WORK_REF_RE.search(normalized)
    )


def _classify_runner_exception(
    exception: Exception,
) -> tuple[Gate5B4C3LiveRunnerDiagnosticStage, str, str]:
    text = str(exception)
    traceback_markers = " ".join(_traceback_markers(exception))
    if _PROXY_CORRELATION_RE.search(text):
        return (
            "proxy_correlation_config",
            "proxy_correlation_config_failed",
            "proxy_correlation_config_failure",
        )
    if _PROVIDER_CLIENT_SETUP_RE.search(text):
        return (
            "provider_client_setup",
            "provider_client_setup_failed",
            "provider_client_setup_failure",
        )
    if isinstance(exception, TypeError) and (
        _ADK_FUNCTION_TOOL_SCHEMA_RE.search(text)
        or _ADK_FUNCTION_TOOL_SCHEMA_RE.search(traceback_markers)
    ):
        return (
            "adk_tool_schema",
            "adk_function_tool_schema_mismatch",
            "adk_function_tool_schema_mismatch",
        )
    if isinstance(exception, TypeError) and (
        _ADK_FUNCTION_TOOL_INVOCATION_RE.search(text)
        or _ADK_FUNCTION_TOOL_INVOCATION_RE.search(traceback_markers)
    ):
        return (
            "adk_tool_invocation_adapter",
            "adk_tool_invocation_argument_mismatch",
            "adk_tool_invocation_argument_mismatch",
        )
    if isinstance(exception, TypeError) and (
        _PROVIDER_REQUEST_SERIALIZATION_RE.search(text)
        or _PROVIDER_REQUEST_SERIALIZATION_RE.search(traceback_markers)
    ):
        return (
            "provider_request_serialization",
            "provider_request_serialization_failed",
            "provider_request_serialization_failure",
        )
    return ("runner_execution", "runner_execution_failed", "unexpected_exception")


def _traceback_markers(exception: Exception) -> tuple[str, ...]:
    markers: list[str] = []
    traceback = exception.__traceback__
    while traceback is not None:
        module_name = str(traceback.tb_frame.f_globals.get("__name__", ""))
        function_name = traceback.tb_frame.f_code.co_name
        if module_name.startswith(
            (
                "google.adk",
                "google.genai",
                "httpx",
                "httpcore",
                "magi_agent",
            )
        ):
            marker = f"{module_name}:{function_name}"
            if _SAFE_LABEL_RE.match(marker) and marker not in markers:
                markers.append(marker)
        if len(markers) >= 12:
            break
        traceback = traceback.tb_next
    return tuple(markers)


def _runner_error_diagnostic(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    stage: Gate5B4C3LiveRunnerDiagnosticStage,
    reason_code: str,
    exception: Exception | None = None,
    exception_class: str | None = None,
    exception_category: str | None = None,
    adk_invoked: bool = False,
    runner_attempted: bool = False,
    model_attempted: bool = False,
    active_tools: Sequence[object] = (),
    gate1a_egress_correlation_context: Gate1AEgressCorrelationContext | None = None,
    gate1a_egress_proxy_url: str | None = None,
) -> Gate5B4C3LiveRunnerErrorDiagnostic:
    tools_policy = _safe_label(request.recipe_profile.tools_policy, "unknown")
    correlation_ready = (
        gate1a_egress_correlation_context is not None
        and bool(str(gate1a_egress_proxy_url or "").strip())
    )
    return Gate5B4C3LiveRunnerErrorDiagnostic(
        stage=stage,
        reasonCode=_safe_label(reason_code, "runner_error"),
        exceptionClass=_safe_label(
            exception_class or (type(exception).__name__ if exception is not None else None),
            "Exception",
        )
        if exception is not None or exception_class is not None
        else None,
        exceptionCategory=_safe_label(exception_category, "unexpected_exception")
        if exception_category is not None
        else None,
        errorPreview=_redacted_preview(str(exception)) if exception is not None else None,
        tracebackMarkers=_traceback_markers(exception) if exception is not None else (),
        requestDigest=request.request_id_digest,
        traceIdDigest=request.trace_id_digest,
        modelAttemptDigest=(
            gate1a_egress_correlation_context.model_attempt_digest
            if gate1a_egress_correlation_context is not None
            else None
        ),
        correlationDigest=(
            gate1a_egress_correlation_context.correlation_digest
            if gate1a_egress_correlation_context is not None
            else None
        ),
        routeMode="user_visible_generation",
        gateMode=(
            "gate1a_readonly_tools"
            if tools_policy == "shadow_readonly"
            else "no_gate1a_tools"
        ),
        toolsPolicy=tools_policy,
        routingSource=_safe_label(request.model_routing.routing_source, "unknown"),
        correlationMode="proxy_connect_headers" if correlation_ready else "none",
        activeToolNames=_public_tool_names(active_tools),
        adkInvoked=adk_invoked,
        runnerAttempted=runner_attempted,
        modelCallAttempted=model_attempted,
        toolsEnabled=not request.policy.tools_disabled,
        toolHostDispatchAllowed=request.policy.tool_host_dispatch_allowed,
        adkPrimitivesLoaderConfigured=True,
        gate1aEgressCorrelationContextPresent=(
            gate1a_egress_correlation_context is not None
        ),
        gate1aProxyUrlConfigured=bool(str(gate1a_egress_proxy_url or "").strip()),
        egressCorrelationHeadersConfigured=correlation_ready,
    )


def _public_tool_names(active_tools: Sequence[object]) -> tuple[str, ...]:
    names: list[str] = []
    for tool in active_tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not _SAFE_TOOL_NAME_RE.match(name):
            continue
        if name not in names:
            names.append(name)
    return tuple(names)


def _safe_label(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text if _SAFE_LABEL_RE.match(text) else fallback


def _result(
    request: Gate5B4C3ShadowGenerationRequest,
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic,
    *,
    status: Gate5B4C3LiveRunnerStatus,
    reason: Gate5B4C3LiveRunnerReason,
    started: float,
    adk_invoked: bool = False,
    runner_attempted: bool = False,
    model_attempted: bool = False,
    event_count: int = 0,
    agent_kwargs_keys: tuple[str, ...] = (),
    runner_kwargs_keys: tuple[str, ...] = (),
    run_async_kwargs_keys: tuple[str, ...] = (),
    error_class: str | None = None,
    error_preview: str | None = None,
    runner_error_diagnostic: Gate5B4C3LiveRunnerErrorDiagnostic | None = None,
    output_text: str | None = None,
    usage: dict[str, int] | None = None,
) -> Gate5B4C3LiveRunnerBoundaryResult:
    return Gate5B4C3LiveRunnerBoundaryResult(
        diagnostic=diagnostic.model_dump(by_alias=True, mode="python", warnings=False),
        status=status,
        reason=reason,
        adkInvoked=adk_invoked,
        runnerAttempted=runner_attempted,
        modelCallViaAdkRunnerAttempted=model_attempted,
        eventCount=event_count,
        latencyMs=_elapsed_ms(started),
        timeoutMs=request.budgets.python_runner_timeout_ms,
        selectedProvider=request.model_routing.provider_label,
        selectedModel=request.model_routing.model_label,
        routingSource=request.model_routing.routing_source,
        agentKwargsKeys=agent_kwargs_keys,
        runnerKwargsKeys=runner_kwargs_keys,
        runAsyncKwargsKeys=run_async_kwargs_keys,
        errorClass=error_class,
        errorPreview=error_preview,
        runnerErrorDiagnostic=(
            runner_error_diagnostic.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            )
            if runner_error_diagnostic is not None
            else None
        ),
        outputTextInternal=output_text,
        usageInternal=usage,
    )


def _allowlist_kwargs(payload: Mapping[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in allowed if key in payload}


def _build_shadow_instruction(request: Gate5B4C3ShadowGenerationRequest) -> str:
    return (
        "Run a Gate 5B-4c-3 diagnostic shadow generation pass only. "
        "Keep TypeScript as response authority. Do not request tools, write state, "
        "or attach output to any user-visible channel. Use only the sanitized "
        "current-turn text supplied for this diagnostic. "
        f"Routing source: {request.model_routing.routing_source}. "
        f"Recipe profile: {request.recipe_profile.profile_id}."
    )


def _shadow_session_id(request: Gate5B4C3ShadowGenerationRequest) -> str:
    digest = (request.selection.session_key_digest or request.request_id_digest).removeprefix(
        "sha256:"
    )
    return f"gate5b4c3-shadow-{digest[:24]}"


def _redacted_preview(value: str, *, max_chars: int = 256) -> str:
    redacted = _ERROR_REDACTION_RE.sub("[REDACTED]", value)
    if len(redacted) > max_chars:
        return redacted[:max_chars]
    return redacted


def _event_text(event: object) -> str | None:
    value = _mapping_or_attr(event, "text")
    if isinstance(value, str) and value:
        return value
    chunks = _text_chunks_from_parts(_event_parts(event))
    if chunks:
        return "".join(chunks)
    dumped = _safe_model_dump_mapping(event)
    if dumped is not None:
        value = _mapping_or_attr(dumped, "text")
        if isinstance(value, str) and value:
            return value
        chunks = _text_chunks_from_parts(_event_parts(dumped))
        if chunks:
            return "".join(chunks)
    return None


def _output_continuation_config_from_env() -> OutputContinuationConfig | None:
    parsed = parse_output_continuation_env(os.environ)
    if not parsed.enabled:
        return None
    return OutputContinuationConfig(
        enabled=True,
        max_continuations=parsed.max_continuations,
    )


def _stream_event_limit(*, selected_full_toolhost: bool) -> int:
    if selected_full_toolhost:
        raw = os.environ.get("MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT", "").strip()
        if not raw:
            return _DEFAULT_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT
        try:
            parsed = int(raw)
        except ValueError:
            return _DEFAULT_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT
        if parsed < _MANUAL_TOOL_EVENT_LIMIT:
            return _MANUAL_TOOL_EVENT_LIMIT
        return min(parsed, _MAX_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT)
    return _MANUAL_TOOL_EVENT_LIMIT


def _build_output_continuation_message() -> str:
    return build_continuation_message()


def _should_continue_truncated_output(
    config: OutputContinuationConfig | None,
    *,
    truncated: bool,
    output_seen: bool,
    continuations_used: int,
) -> bool:
    return should_continue(
        config,
        truncated=truncated,
        output_seen=output_seen,
        continuations_used=continuations_used,
    )


def _event_finish_reason_is_truncated(event: object) -> bool:
    return stop_reason_is_truncated(_event_finish_reason(event))


def _event_finish_reason(event: object, *, depth: int = 0) -> str | None:
    if depth > 3:
        return None
    for candidate in (event, _safe_model_dump_mapping(event)):
        if candidate is None:
            continue
        for name in (
            "finish_reason",
            "finishReason",
            "stop_reason",
            "stopReason",
            "finish_message",
            "finishMessage",
        ):
            normalized = _normalize_finish_reason(_mapping_or_attr(candidate, name))
            if normalized is not None:
                return normalized
        for nested_name in ("llm_response", "llmResponse", "response"):
            nested = _mapping_or_attr(candidate, nested_name)
            if nested is None:
                continue
            nested_reason = _event_finish_reason(nested, depth=depth + 1)
            if nested_reason is not None:
                return nested_reason
    return None


def _normalize_finish_reason(value: object) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None) or getattr(value, "name", None)
    if isinstance(enum_value, str):
        return enum_value
    if isinstance(value, str):
        return value
    return str(value)


def _event_visible_text_delta(
    event: object,
    text: str,
    current_run_output_chunks: Sequence[str],
) -> str | None:
    """Return the new user-visible text for an ADK event.

    ADK SSE runs can emit token-level ``partial=True`` events followed by a
    final non-partial event whose content is the aggregate text for the same
    model call. The final aggregate is useful for ADK's transcript, but sending
    it again as a public delta duplicates the answer in hosted chat.
    """

    if _event_is_partial(event):
        return text
    current_text = "".join(current_run_output_chunks)
    if not current_text:
        return text
    if text.startswith(current_text):
        suffix = text[len(current_text) :]
        return suffix or None
    return text


def _event_is_partial(event: object) -> bool:
    value = _mapping_or_attr(event, "partial")
    if isinstance(value, bool):
        return value
    dumped = _safe_model_dump_mapping(event)
    if dumped is not None:
        value = _mapping_or_attr(dumped, "partial")
        if isinstance(value, bool):
            return value
    return False


def _text_chunks_from_parts(parts: Sequence[object]) -> list[str]:
    chunks: list[str] = []
    for part in parts:
        # Model reasoning (thought=True) is NOT visible answer text; it is
        # surfaced separately via _thinking_chunks_from_parts so the hosted UI
        # renders it in the collapsible thinking block instead of leaking it
        # into the final answer.
        if bool(_mapping_or_attr(part, "thought")):
            continue
        text = _mapping_or_attr(part, "text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return chunks


def _thinking_chunks_from_parts(parts: Sequence[object]) -> list[str]:
    chunks: list[str] = []
    for part in parts:
        if not bool(_mapping_or_attr(part, "thought")):
            continue
        text = _mapping_or_attr(part, "text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return chunks


def _event_thinking_text(event: object) -> str | None:
    chunks = _thinking_chunks_from_parts(_event_parts(event))
    if chunks:
        return "".join(chunks)
    dumped = _safe_model_dump_mapping(event)
    if dumped is not None:
        chunks = _thinking_chunks_from_parts(_event_parts(dumped))
        if chunks:
            return "".join(chunks)
    return None


def _event_function_calls(event: object) -> list[Mapping[str, object]]:
    calls: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for candidate in (event, _safe_model_dump_mapping(event)):
        if candidate is None:
            continue
        for part in _event_parts(candidate):
            for normalized in _part_function_calls(part):
                key = _json_dumps(normalized)
                if key not in seen:
                    seen.add(key)
                    calls.append(normalized)
        for function_call in _event_direct_function_calls(candidate):
            normalized = _normalize_function_call(function_call)
            if normalized is None:
                continue
            key = _json_dumps(normalized)
            if key not in seen:
                seen.add(key)
                calls.append(normalized)
    return calls


def _event_function_responses(event: object) -> tuple[object, ...]:
    responses: list[object] = []
    for candidate in (event, _safe_model_dump_mapping(event)):
        if candidate is None:
            continue
        for part in _event_parts(candidate):
            response = (
                _mapping_or_attr(part, "function_response")
                or _mapping_or_attr(part, "functionResponse")
            )
            if response is not None:
                responses.append(response)
    return tuple(responses)


def _usage_int(meta: object, *names: str) -> int | None:
    for name in names:
        value = _mapping_or_attr(meta, name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _event_usage_metadata(event: object, *, depth: int = 0) -> tuple[int, int, int] | None:
    if depth > 3:
        return None
    meta = _mapping_or_attr(event, "usage_metadata") or _mapping_or_attr(
        event,
        "usageMetadata",
    )
    if meta is not None:
        prompt = _usage_int(meta, "prompt_token_count", "promptTokenCount")
        candidates = _usage_int(meta, "candidates_token_count", "candidatesTokenCount")
        cached = _usage_int(
            meta,
            "cached_content_token_count",
            "cachedContentTokenCount",
        )
        if prompt is not None or candidates is not None or cached is not None:
            return (prompt or 0, candidates or 0, cached or 0)
    for nested_name in ("llm_response", "response"):
        nested = _mapping_or_attr(event, nested_name)
        if nested is not None:
            found = _event_usage_metadata(nested, depth=depth + 1)
            if found is not None:
                return found
    return None


def _usage_dict(totals: tuple[int, int, int]) -> dict[str, int] | None:
    if not any(totals):
        return None
    return {
        "inputTokens": totals[0],
        "outputTokens": totals[1],
        "cacheReadTokens": totals[2],
    }


def _event_parts(
    event: object,
    *,
    depth: int = 0,
    seen_ids: frozenset[int] = frozenset(),
) -> list[object]:
    if depth > 3:
        return []
    object_id = id(event)
    if object_id in seen_ids:
        return []
    next_seen_ids = seen_ids | {object_id}
    parts: list[object] = []
    content = _mapping_or_attr(event, "content")
    parts.extend(_content_parts(content))
    for candidate in _sequence_from(_mapping_or_attr(event, "candidates")):
        parts.extend(_content_parts(_mapping_or_attr(candidate, "content")))
    response = _mapping_or_attr(event, "response")
    if response is not None:
        parts.extend(_event_parts(response, depth=depth + 1, seen_ids=next_seen_ids))
    llm_response = _mapping_or_attr(event, "llm_response")
    if llm_response is not None:
        parts.extend(_event_parts(llm_response, depth=depth + 1, seen_ids=next_seen_ids))
    return parts


def _content_parts(content: object) -> list[object]:
    if content is None:
        return []
    parts = _mapping_or_attr(content, "parts")
    return list(_sequence_from(parts))


def _part_function_calls(part: object) -> list[Mapping[str, object]]:
    normalized_calls: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for candidate in (part, _safe_model_dump_mapping(part)):
        if candidate is None:
            continue
        function_calls = (
            _sequence_from(_mapping_or_attr(candidate, "function_calls"))
            or _sequence_from(_mapping_or_attr(candidate, "functionCalls"))
        )
        direct = (
            _mapping_or_attr(candidate, "function_call")
            or _mapping_or_attr(candidate, "functionCall")
        )
        if direct is not None:
            function_calls = (*function_calls, direct)
        for function_call in function_calls:
            normalized = _normalize_function_call(function_call)
            if normalized is not None:
                key = _json_dumps(normalized)
                if key not in seen:
                    seen.add(key)
                    normalized_calls.append(normalized)
        normalized = _normalize_function_call(candidate)
        if normalized is not None:
            key = _json_dumps(normalized)
            if key not in seen:
                seen.add(key)
                normalized_calls.append(normalized)
    if normalized_calls:
        return normalized_calls
    normalized = _normalize_function_call(part)
    return [normalized] if normalized is not None else []


def _event_direct_function_calls(event: object) -> tuple[object, ...]:
    return (
        *_safe_function_call_method(event),
        *_sequence_from(_mapping_or_attr(event, "function_calls")),
        *_sequence_from(_mapping_or_attr(event, "functionCalls")),
        *_sequence_from(_mapping_or_attr(event, "tool_calls")),
        *_sequence_from(_mapping_or_attr(event, "toolCalls")),
    )


def _safe_function_call_method(event: object) -> tuple[object, ...]:
    get_function_calls = _mapping_or_attr(event, "get_function_calls")
    if not callable(get_function_calls):
        return ()
    try:
        return _sequence_from(get_function_calls())
    except Exception:
        return ()


def _sequence_from(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(value)
    return ()


def _mapping_or_attr(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _safe_model_dump_mapping(value: object) -> Mapping[str, object] | None:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        return None
    for kwargs in (
        {"by_alias": True, "mode": "python", "warnings": False},
        {"by_alias": True},
        {},
    ):
        try:
            dumped = model_dump(**kwargs)
        except TypeError:
            continue
        except Exception:
            return None
        if isinstance(dumped, Mapping):
            return dumped
    return None


def _normalize_function_call(function_call: object) -> Mapping[str, object] | None:
    if function_call is None:
        return None
    if isinstance(function_call, Mapping):
        name = function_call.get("name")
        args = function_call.get("args")
        call_id = function_call.get("id")
    else:
        model_dump = getattr(function_call, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(by_alias=True)
            except Exception:
                dumped = None
            if isinstance(dumped, Mapping):
                return _normalize_function_call(dumped)
        name = getattr(function_call, "name", None)
        args = getattr(function_call, "args", None)
        call_id = getattr(function_call, "id", None)
    if not isinstance(name, str) or not _SAFE_TOOL_NAME_RE.match(name):
        return None
    safe_args = dict(args) if isinstance(args, Mapping) else {}
    safe_call_id = str(call_id or "")
    return {"name": name, "args": safe_args, "id": safe_call_id}


def _normalize_function_response(function_response: object) -> Mapping[str, object] | None:
    if function_response is None:
        return None
    if isinstance(function_response, Mapping):
        name = function_response.get("name")
        response = function_response.get("response")
        response_id = function_response.get("id")
    else:
        model_dump = getattr(function_response, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(by_alias=True)
            except Exception:
                dumped = None
            if isinstance(dumped, Mapping):
                return _normalize_function_response(dumped)
        name = getattr(function_response, "name", None)
        response = getattr(function_response, "response", None)
        response_id = getattr(function_response, "id", None)
    if not isinstance(name, str) or not _SAFE_TOOL_NAME_RE.match(name):
        return None
    return {
        "name": name,
        "response": response if response is not None else {},
        "id": str(response_id or ""),
    }


def _live_tool_event_id_for_function_call(
    function_call: Mapping[str, object],
    *,
    index: int,
) -> str:
    return _manual_tool_event_id(
        name=str(function_call.get("name", "")),
        args=_function_call_args(function_call),
        call_id=function_call.get("id"),
        index=index,
    )


def _function_call_args(function_call: Mapping[str, object]) -> dict[str, object]:
    args = function_call.get("args")
    return dict(args) if isinstance(args, Mapping) else {}


_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|authorization"
    r"|auth[_-]?token|access[_-]?key|private[_-]?key|bearer|session[_-]?token)",
    re.IGNORECASE,
)


def _redact_secrets(value: object) -> object:
    """Mask values under secret-named keys while keeping all other values at full
    fidelity. Recursive over mappings/sequences. Defends the plaintext transcript
    against logging credential/vault tool args+results; value-pattern redaction
    (e.g. bare ``AKIA…`` strings under innocuous keys) is a known follow-up."""
    if isinstance(value, Mapping):
        out: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                out[key] = "[redacted]"
            else:
                out[key] = _redact_secrets(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    return value


def _transcript_tool_call_record(
    function_call: Mapping[str, object], *, call_id: str
) -> dict[str, object]:
    """Full-fidelity tool_call record for the session transcript (no truncation —
    unlike the SSE ``tool_start`` event which carries only an input *preview*).
    Secret-named arg values are redacted; everything else is verbatim."""
    return {
        "type": "tool_call",
        "tool_name": str(function_call.get("name", "")),
        "args": _redact_secrets(_function_call_args(function_call)),
        "call_id": call_id,
    }


def _transcript_tool_result_record(
    normalized_response: Mapping[str, object], *, call_id: str
) -> dict[str, object]:
    """Full-fidelity tool_result record. Carries the real response payload — the
    SSE ``tool_end`` event only carries a digest (``result:<sha>``), useless for
    debugging what a subagent or tool actually returned."""
    response = normalized_response.get("response")
    return {
        "type": "tool_result",
        "call_id": call_id,
        "tool_name": str(normalized_response.get("name", "")),
        "status": _manual_tool_status(response),
        "output": _redact_secrets(response),
    }


def _transcript_subagent_record(
    function_call: Mapping[str, object],
) -> dict[str, object] | None:
    """Flattened convenience record for a ``SpawnAgent`` call so subagent spawns
    are greppable in one line. Non-lossy: full args retained alongside the
    best-effort flattened ``prompt``/``persona``."""
    if str(function_call.get("name", "")) != "SpawnAgent":
        return None
    args = _function_call_args(function_call)
    return {
        "type": "subagent_spawn",
        "prompt": args.get("prompt") or args.get("task"),
        "persona": args.get("persona"),
        "args": _redact_secrets(args),
    }


def _remember_live_tool_event_id(
    function_call: Mapping[str, object],
    *,
    tool_event_id: str,
    ids_by_adk_id: dict[str, str],
    pending_ids_by_name: dict[str, list[str]],
) -> None:
    call_id = str(function_call.get("id") or "")
    if call_id:
        ids_by_adk_id.setdefault(call_id, tool_event_id)
    name = str(function_call.get("name", ""))
    if name:
        pending_ids_by_name.setdefault(name, []).append(tool_event_id)


def _live_tool_event_id_for_function_response(
    function_response: Mapping[str, object],
    *,
    ids_by_adk_id: Mapping[str, str],
    pending_ids_by_name: dict[str, list[str]],
) -> str:
    response_id = str(function_response.get("id") or "")
    name = str(function_response.get("name", ""))
    if response_id:
        matched = ids_by_adk_id.get(response_id)
        if matched:
            pending_ids = pending_ids_by_name.get(name)
            if pending_ids and matched in pending_ids:
                pending_ids.remove(matched)
            return matched
    pending_ids = pending_ids_by_name.get(name)
    if pending_ids:
        return pending_ids.pop(0)
    return "tu_" + _digest(
        {
            "name": name,
            "id": response_id,
            "response": _bounded_json_value(
                function_response.get("response"),
                max_bytes=512,
            ),
        }
    )


def _function_call_tool_emits_public_events(
    function_call: Mapping[str, object],
    tools: Sequence[object],
) -> bool:
    return _tool_name_emits_public_events(str(function_call.get("name", "")), tools)


def _function_response_tool_emits_public_events(
    function_response: Mapping[str, object],
    tools: Sequence[object],
) -> bool:
    return _tool_name_emits_public_events(str(function_response.get("name", "")), tools)


def _tool_name_emits_public_events(name: str, tools: Sequence[object]) -> bool:
    if not _SAFE_TOOL_NAME_RE.match(name):
        return False
    for tool in tools:
        if str(getattr(tool, "name", "")) == name and _tool_emits_public_events(tool):
            return True
    return False


async def _run_manual_tool_calls(
    function_calls: Sequence[Mapping[str, object]],
    tools: Sequence[object],
    *,
    public_event_sink: Gate5B4C3PublicEventSink | None = None,
    prestarted_tool_event_ids: set[str] | frozenset[str] = frozenset(),
) -> list[Mapping[str, object]]:
    tool_by_name = {
        str(getattr(tool, "name", "")): tool
        for tool in tools
        if _SAFE_TOOL_NAME_RE.match(str(getattr(tool, "name", "")))
    }
    results: list[Mapping[str, object]] = []
    for index, call in enumerate(function_calls):
        name = str(call.get("name", ""))
        args = call.get("args")
        safe_args = dict(args) if isinstance(args, Mapping) else {}
        tool = tool_by_name.get(name)
        tool_emits_public_events = (
            tool is not None and _tool_emits_public_events(tool)
        )
        tool_event_id = _manual_tool_event_id(
            name=name,
            args=safe_args,
            call_id=call.get("id"),
            index=index,
        )
        started = time.monotonic()
        should_emit_start = tool_event_id not in prestarted_tool_event_ids
        if (
            public_event_sink is not None
            and not tool_emits_public_events
            and should_emit_start
        ):
            _emit_manual_tool_public_event(
                public_event_sink,
                lambda: tool_start_event(
                    tool_id=tool_event_id,
                    name=name,
                    input_preview=tool_input_preview(safe_args),
                ),
            )
            _emit_manual_tool_public_event(
                public_event_sink,
                lambda: tool_progress_event(
                    tool_id=tool_event_id,
                    label=name,
                    status="in_progress",
                    message="Tool execution started",
                ),
            )
        if tool is None:
            result_digest = _digest(
                {
                    "toolName": name,
                    "status": "blocked",
                    "reason": "tool_not_registered",
                }
            )
            if public_event_sink is not None and not tool_emits_public_events:
                _emit_manual_tool_public_event(
                    public_event_sink,
                    lambda: tool_end_event(
                        tool_id=tool_event_id,
                        status="error",
                        output_preview=f"result:{result_digest}",
                        error="tool_not_registered",
                        receipt_refs=(f"result:{result_digest}",),
                        duration_ms=_elapsed_ms(started),
                    )
                )
            results.append(
                {
                    "toolName": name,
                    "status": "blocked",
                    "reason": "tool_not_registered",
                }
            )
            continue
        try:
            result = await _invoke_manual_tool(tool, safe_args)
        except Exception:
            result = {"status": "error", "reason": "tool_execution_failed"}
        manual_status = _manual_tool_status(result)
        result_digest = _digest(result)
        if public_event_sink is not None and not tool_emits_public_events:
            _emit_manual_tool_public_event(
                public_event_sink,
                lambda: tool_end_event(
                    tool_id=tool_event_id,
                    status="ok" if manual_status == "ok" else "error",
                    output_preview=f"result:{result_digest}",
                    error=None if manual_status == "ok" else manual_status,
                    receipt_refs=(f"result:{result_digest}",),
                    duration_ms=_elapsed_ms(started),
                )
            )
        results.append(
            {
                "toolName": name,
                "status": manual_status,
                "resultDigest": result_digest,
                "result": _bounded_manual_tool_result(result),
            }
        )
    return results


def _emit_manual_tool_public_event(
    public_event_sink: Gate5B4C3PublicEventSink | None,
    event_factory: Callable[[], Mapping[str, object]],
) -> None:
    if public_event_sink is None:
        return
    try:
        public_event_sink(dict(event_factory()))
    except Exception:
        return


def _manual_tool_event_id(
    *,
    name: str,
    args: Mapping[str, object],
    call_id: object,
    index: int,
) -> str:
    return "tu_" + _digest(
        {
            "name": name,
            "args": _bounded_json_value(args, max_bytes=512),
            "id": str(call_id or ""),
            "index": index,
        }
    )[7:19]


async def _invoke_manual_tool(tool: object, args: Mapping[str, object]) -> object:
    safe_args = _manual_tool_invocation_args(args)
    run_async = getattr(tool, "run_async", None)
    if callable(run_async):
        return await run_async(args=safe_args, tool_context=object())
    func = getattr(tool, "func", None)
    if callable(func):
        result = func(**safe_args)
        if hasattr(result, "__await__"):
            return await result
        return result
    raise TypeError("manual tool is not invocable")


def _tool_emits_public_events(tool: object) -> bool:
    if getattr(tool, "_magi_gate5b_emits_public_events", False):
        return True
    func = getattr(tool, "func", None)
    return bool(getattr(func, "_magi_gate5b_emits_public_events", False))


def _manual_tool_invocation_args(args: Mapping[str, object]) -> dict[str, object]:
    safe_args = dict(args)
    if set(safe_args) == {"arguments"} and isinstance(safe_args["arguments"], Mapping):
        return dict(safe_args["arguments"])
    return safe_args


async def _run_no_tool_finalizer(
    *,
    primitives: Gate5B4C3LiveAdkPrimitives,
    session_service: object,
    request: Gate5B4C3ShadowGenerationRequest,
    runner_input: object,
    run_kwargs: Mapping[str, object],
    agent_kwargs: Mapping[str, object],
    runner_kwargs: Mapping[str, object],
    public_event_sink: Gate5B4C3PublicEventSink | None = None,
) -> tuple[str | None, int]:
    try:
        finalizer_agent_kwargs = {
            **dict(agent_kwargs),
            "instruction": _no_tool_finalizer_instruction(request),
            "tools": (),
        }
        finalizer_agent = primitives.Agent(
            **_allowlist_kwargs(finalizer_agent_kwargs, _ALLOWED_AGENT_KWARGS)
        )
        finalizer_runner_kwargs = {
            **dict(runner_kwargs),
            "agent": finalizer_agent,
            "session_service": session_service,
        }
        finalizer_runner = primitives.Runner(
            **_allowlist_kwargs(finalizer_runner_kwargs, _ALLOWED_RUNNER_KWARGS)
        )
        finalizer_message = primitives.Content(
            parts=[
                primitives.Part.from_text(
                    text=_no_tool_finalizer_message(runner_input),
                )
            ],
            role="user",
        )
    except Exception:
        return None, 0

    finalizer_chunks: list[str] = []
    finalizer_events = 0
    finalizer_run_kwargs = {
        **dict(run_kwargs),
        "new_message": finalizer_message,
        "run_config": _no_tool_finalizer_run_config(),
    }
    try:
        async for event in finalizer_runner.run_async(
            **_allowlist_kwargs(finalizer_run_kwargs, _ALLOWED_RUN_ASYNC_KWARGS)
        ):
            finalizer_events += 1
            chunk = _event_text(event)
            if chunk:
                visible_delta = _event_visible_text_delta(
                    event,
                    chunk,
                    finalizer_chunks,
                )
                if visible_delta:
                    finalizer_chunks.append(visible_delta)
                    if public_event_sink is not None:
                        public_event_sink(
                            {
                                "type": "text_delta",
                                "delta": visible_delta,
                            }
                        )
            thinking_chunk = _event_thinking_text(event)
            if thinking_chunk and _event_is_partial(event) and public_event_sink is not None:
                public_event_sink(
                    {
                        "type": "thinking_delta",
                        "delta": thinking_chunk,
                    }
                )
            if finalizer_events >= 8:
                break
    except Exception:
        return None, finalizer_events
    return _joined_output(finalizer_chunks), finalizer_events


def _manual_tool_status(result: object) -> str:
    if isinstance(result, Mapping):
        status = result.get("status")
        if isinstance(status, str) and _SAFE_LABEL_RE.match(status):
            return status
    return "ok"


def _bounded_manual_tool_result(result: object) -> object:
    return _bounded_json_value(result, max_bytes=_MAX_MANUAL_TOOL_RESULTS_BYTES)


def _manual_tool_followup_text(results: Sequence[Mapping[str, object]]) -> str:
    payload = _bounded_json_value(tuple(results), max_bytes=_MAX_MANUAL_TOOL_RESULTS_BYTES)
    return (
        "Tool execution results for the previous model-requested function calls. "
        "Use these results to produce the final answer for the user. "
        "Do not mention hidden chain-of-thought or private policy. "
        f"Results: {_json_dumps(payload)}"
    )


def _no_tool_finalizer_instruction(request: Gate5B4C3ShadowGenerationRequest) -> str:
    return (
        "You are completing an OpenMagi selected full-toolhost turn after the "
        "runtime already executed the available tool/function calls. Do not request "
        "or call any tools in this finalizer pass. Use only the conversation and "
        "tool/function response events already present in the current ADK session. "
        "Return a normal user-visible text answer. If the gathered evidence is "
        "insufficient, say what is missing in plain text instead of calling tools. "
        f"Routing source: {request.model_routing.routing_source}."
    )


def _no_tool_finalizer_message(runner_input: object) -> str:
    del runner_input
    return (
        "The selected toolhost pass has ended with tool/function events but no "
        "text answer. Produce the final answer now using the existing session "
        "evidence. Do not call tools."
    )


def _selected_full_toolhost_run_config(
    enabled: bool,
    *,
    max_llm_calls: int,
) -> object | None:
    if not enabled:
        return None
    return _run_config(max_llm_calls=max_llm_calls)


def _no_tool_finalizer_run_config() -> object | None:
    return _run_config(max_llm_calls=2)


def _run_config(*, max_llm_calls: int) -> object | None:
    try:
        from google.adk.agents import RunConfig
    except Exception:
        try:
            from google.adk.agents.run_config import RunConfig  # type: ignore[no-redef]
        except Exception:
            return None
    try:
        from google.adk.agents.run_config import StreamingMode
    except Exception:
        StreamingMode = None  # type: ignore[assignment]
    try:
        kwargs: dict[str, object] = {"max_llm_calls": max_llm_calls}
        if StreamingMode is not None:
            kwargs["streaming_mode"] = StreamingMode.SSE
        return RunConfig(**kwargs)
    except Exception:
        return None


def _bounded_json_value(value: object, *, max_bytes: int) -> object:
    encoded = _json_dumps(value).encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return {"truncated": True, "digest": _digest(value)}


def _json_dumps(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _joined_output(chunks: list[str]) -> str | None:
    if not chunks:
        return None
    return "".join(chunks)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = [
    "AdkPrimitivesLoader",
    "Gate5B4C3LiveAdkPrimitives",
    "Gate5B4C3LiveRunnerBoundary",
    "Gate5B4C3LiveRunnerErrorDiagnostic",
    "Gate5B4C3LiveRunnerBoundaryResult",
    "Gate5B4C3LiveRunnerReason",
    "Gate5B4C3LiveRunnerStatus",
    "load_gate5b4c3_live_adk_primitives",
    "run_gate5b4c3_live_runner_boundary",
    "run_gate5b4c3_live_runner_boundary_async",
]
