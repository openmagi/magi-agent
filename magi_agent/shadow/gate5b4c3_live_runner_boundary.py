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
from magi_agent.ops.health import _truthy_env
from magi_agent.evidence.gate1a_egress_correlation import (
    Gate1AEgressCorrelationContext,
    build_gate1a_proxy_http_options,
)
from magi_agent.runtime.adk_instruction import state_injection_safe_instruction
from magi_agent.runtime.output_continuation import (
    OutputContinuationConfig,
    build_continuation_message,
    should_continue,
    stop_reason_is_truncated,
)
from magi_agent.runtime.public_events import (
    tool_end_event,
    tool_event_id as _shared_tool_event_id,
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
)
from magi_agent.runtime.session_ownership import (
    acquire_hosted_session_lease,
    probe_session_event_count as _probe_session_event_count,
    resolve_include_history as _resolve_include_history,
    seeded_history_message_count as _seeded_history_message_count,
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
    # Continuity observability (PR-1): make the hosted session-reuse verdict and
    # the seed decision permanently diagnosable from the durable turn record.
    # ``session_reused`` is the registry verdict; ``session_event_count`` is the
    # ADK session's event count probed at turn start (0 when unknown/miss);
    # ``seeded_message_count`` is the number of sanitized history messages sent
    # into the model prompt this turn (0 on a reuse hit that skips seeding).
    session_reused: bool = Field(default=False, alias="sessionReused")
    session_event_count: int = Field(default=0, ge=0, alias="sessionEventCount")
    seeded_message_count: int = Field(default=0, ge=0, alias="seededMessageCount")
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


def build_gate5b4c3_input_drop_boundary_result(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic,
    drop_reason: str,
    active_tools: Sequence[object] = (),
    gate1a_egress_correlation_context: Gate1AEgressCorrelationContext | None = None,
    gate1a_egress_proxy_url: str | None = None,
) -> Gate5B4C3LiveRunnerBoundaryResult:
    """Synthesize the input-adapter-drop boundary result without the legacy engine.

    P5-M1a: the governed serving path (``transport/gate5b_serving.py``) used to
    delegate a dropped runner input to ``run_gate5b4c3_live_runner_boundary_async``
    purely so the boundary's error-result path would build the drop response.
    That was the last governed -> legacy-engine call. This helper reproduces that
    exact wire shape directly, so the boundary class can retire (M1b) without the
    governed path losing its refusal behavior.

    The construction mirrors ``Gate5B4C3LiveRunnerBoundary._invoke_async_turn``'s
    drop branch byte-for-byte:
    ``_result(request, diagnostic, status="dropped", reason="input_adapter_drop",
    started=..., error_preview=<adapter reason>, runner_error_diagnostic=...)``.
    The drop path never starts the ADK runner and never registers a session
    lease, so there is no orchestration to reproduce beyond the result object and
    the boundary's turn-completion transcript emission (``_emit_turn_completion``).
    For a drop, ``output_text_internal`` is ``None``, so only a ``turn_end`` record
    is emitted (no ``message`` record), matching the legacy chokepoint exactly.

    Note there is no ``public_event_sink`` parameter: the boundary drives its SSE
    ``_emit_public_event`` seam only from inside the ADK runner event loop, which
    a dropped input never enters, so the drop path emits no public/SSE event. The
    process-global transcript sink below is the only side effect, matching the
    boundary's ``_emit_turn_completion`` for a drop.
    """
    started = time.monotonic()
    result = _result(
        request,
        diagnostic,
        status="dropped",
        reason="input_adapter_drop",
        started=started,
        error_preview=drop_reason,
        runner_error_diagnostic=_runner_error_diagnostic(
            request,
            stage="runner_input_adapter",
            reason_code=drop_reason,
            exception_category="request_shape_runner_input_adapter_failure",
            active_tools=active_tools,
            gate1a_egress_correlation_context=gate1a_egress_correlation_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        ),
    )
    # Mirror Gate5B4C3LiveRunnerBoundary._emit_turn_completion for a drop: emit
    # the assembled message ONLY when there is output text (None here), then
    # always the turn_end record, through the SAME process-global transcript
    # sink chokepoint (emit_transcript_record) the boundary uses.
    from magi_agent.observability.transcript import emit_transcript_record

    if result.output_text_internal:
        emit_transcript_record(
            {
                "type": "message",
                "role": "assistant",
                "content": result.output_text_internal,
            },
            _shadow_session_id(request),
            request.turn.turn_id,
        )
    emit_transcript_record(
        {
            "type": "turn_end",
            "terminal": result.status,
            "reason": result.reason,
            "usage": result.usage_internal,
            "provider": result.selected_provider,
            "model": result.selected_model,
            "event_count": result.event_count,
            "latency_ms": result.latency_ms,
        },
        _shadow_session_id(request),
        request.turn.turn_id,
    )
    return result


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
        # Route Claude/anthropic models through magi's cache-aware ADK
        # subclass so the outgoing Anthropic request carries rolling-tail
        # cache markers. E-7: this delegates to the single seam
        # ``runtime/model_factory.maybe_build_cache_aware_anthropic`` so
        # the CLI and serve surfaces cannot drift. ``gate_on_flag=False``
        # preserves the hosted-serve unconditional behavior (the flag
        # gates only the local CLI surface today). The factory's robust
        # fallback returns ``None`` on any failure (missing ``anthropic``
        # package, ADK import error, etc.); we surface that as the raw
        # label so the runner falls back to its default routing.
        from magi_agent.runtime.model_factory import (  # noqa: PLC0415
            maybe_build_cache_aware_anthropic,
        )

        # The shadow path historically passed a bare label rather than a
        # ProviderConfig — synthesize the minimum shape the factory's
        # Protocol expects (it only reads ``provider``, ``model``,
        # ``api_key``).
        class _ShadowConfig:
            provider = "anthropic"
            model = model_label
            api_key = ""  # hosted credential surfacing is handled upstream

        built = maybe_build_cache_aware_anthropic(
            _ShadowConfig(), env=None, gate_on_flag=False
        )
        return built if built is not None else model_label

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
    session_reused: bool = False,
    session_event_count: int = 0,
    seeded_message_count: int = 0,
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
        sessionReused=session_reused,
        sessionEventCount=session_event_count,
        seededMessageCount=seeded_message_count,
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


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# Public API surface for the correlated model/label builder. The hosted governed
# serving path (gate5b_serving) reaches this builder on every turn; it is exposed
# under a stable public name in addition to the private
# ``_gate1a_correlated_model_or_label`` symbol. The private name is retained as an
# alias for existing importers / test monkeypatch targets that bind it by the
# underscore name.
gate1a_correlated_model_or_label = _gate1a_correlated_model_or_label


__all__ = [
    "AdkPrimitivesLoader",
    "Gate5B4C3LiveAdkPrimitives",
    "Gate5B4C3LiveRunnerErrorDiagnostic",
    "Gate5B4C3LiveRunnerBoundaryResult",
    "Gate5B4C3LiveRunnerReason",
    "Gate5B4C3LiveRunnerStatus",
    "build_gate5b4c3_input_drop_boundary_result",
    "gate1a_correlated_model_or_label",
    "load_gate5b4c3_live_adk_primitives",
]
