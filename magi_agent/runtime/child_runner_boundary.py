from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from magi_agent.evidence.runtime_issuance import issue_runtime_authority
from magi_agent.evidence.subagent import DelegatedEvidenceRequirement

from ..meta_orchestration.child_acceptance import accept_real_child_envelope


ChildRunnerStatus = Literal["disabled", "blocked", "ok", "error"]
ChildDeliveryMode = Literal["return", "background"]
ChildRole = Literal["coding", "research", "reviewer", "implementer", "debugging", "general"]

#: PR1 placeholder surface — child execution is local-fake only.
FUTURE_ADK_CHILD_RUNNER_SURFACE = "future_adk_runner"
#: PR2 real surface — a child turn is routed through the (local) ADK turn
#: runner.  Reachable ONLY inside the opt-in real-child-execution feature-pack.
REAL_ADK_CHILD_RUNNER_SURFACE = "local_adk_turn_runner"

#: Hard cap on the number of child agents that may be spawned within a single
#: parent run.  This bounds runaway fan-out independently of the per-child
#: in-flight Semaphore (which bounds concurrency, not the total count).
#: The workflow executor also enforces this as a parent-run preflight before
#: dispatching pending children; the boundary keeps the same cap as a final
#: per-child guard for real-child execution.
MAX_TOTAL_AGENTS_PER_RUN = 1000


def clamp_total_agents_per_run(requested: int) -> int:
    """Clamp a requested total-agents budget to ``[1, MAX_TOTAL_AGENTS_PER_RUN]``.

    Used by callers to validate a config-supplied budget before constructing a
    ``LocalChildRunnerBoundary``.  The workflow executor enforces the parent-run
    budget before fan-out, and the boundary keeps the same guard at runtime via
    ``agents_spawned_so_far``.
    """
    if not isinstance(requested, int) or isinstance(requested, bool):
        return MAX_TOTAL_AGENTS_PER_RUN
    return max(1, min(requested, MAX_TOTAL_AGENTS_PER_RUN))

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|AKIA[A-Z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|ACCESS[_-]?KEY)[A-Z0-9_]*"
    r"\s*[:=]\s*[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_-]?(?:child|tool|prompt|transcript|output|result|log|args)|"
    r"child[_-]?(?:prompt|output|transcript)|tool[_-]?(?:log|args|result)|"
    r"hidden[_-]?reasoning|chain[_-]?of[_-]?thought|private[_-]?reasoning|"
    r"reasoning[_-]?trace|model[_-]?internal|authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_PUBLIC_REF_RE = re.compile(
    r"^(?:child|evidence|artifact|audit|policy|workspace|prompt):[A-Za-z0-9._:-]+$"
)
_RUNTIME_OUTPUT_REF_RE = re.compile(
    r"^(?P<namespace>child|evidence|artifact|audit):[a-f0-9]{16}$"
)
_CHILD_OUTPUT_REF_RE = re.compile(
    r"^(?P<namespace>child|evidence|artifact|audit):[A-Za-z0-9._:-]+$"
)


class ChildRunnerConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_child_runner_enabled: bool = Field(
        default=False,
        alias="localFakeChildRunnerEnabled",
    )
    #: Opt-in feature-pack flag.  When False (default) the boundary behaves
    #: byte-identically to PR1 (local-fake, ``future_adk_runner`` surface).
    #: When True AND an ``adk_turn_boundary`` is supplied AND local-fake is
    #: trusted, ``run()`` routes a child turn through the REAL (local) ADK turn
    #: runner surface.  This is the ONLY config flag that can promote the
    #: surface; all production-authority flags below remain ``Literal[False]``.
    real_child_execution_pack_enabled: bool = Field(
        default=False,
        alias="realChildExecutionPackEnabled",
    )
    #: Parallel real-bool live gate (FileDelivery / web_acquisition precedent:
    #: "default-False IS the seal").  When True AND a trusted live child runner
    #: (``openmagi_live_provider=True``) is injected, ``run()`` drives that REAL
    #: model-backed runner directly via ``run_child`` — PARALLEL to the local-fake
    #: path, not through the sealed ADK shadow surface.  This does NOT unseal any
    #: ``production_*`` ``Literal[False]`` flag below, nor any authority flag; the
    #: live runner is a *local* surface and never flips hosted authority.  Default
    #: False keeps the fake/shadow/fallback behaviour byte-identical to before.
    live_child_runner_enabled: bool = Field(
        default=False,
        alias="liveChildRunnerEnabled",
    )
    runtime_id: str = Field(default="openmagi.child-runner-boundary", alias="runtimeId")
    max_output_refs: int = Field(default=8, alias="maxOutputRefs", ge=1, le=32)
    #: Bounded spawn-depth ceiling for real child execution (no runaway
    #: recursion).  PR2 raises the runtime default to a bounded production value.
    max_spawn_depth: int = Field(default=2, alias="maxSpawnDepth", ge=1, le=4)
    adk_runner_surface: Literal["future_adk_runner", "local_adk_turn_runner"] = Field(
        default="future_adk_runner",
        alias="adkRunnerSurface",
    )
    production_child_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionChildExecutionEnabled",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    #: Provider/model a spawned child turn runs on. Callers should set these to
    #: the parent's configured provider/model so children inherit it instead of
    #: being pinned to a single hardcoded model. A per-task override on
    #: ``ChildTaskRequest`` takes precedence. The model must be a route known to
    #: the local ``ModelTierRegistry`` (the model-tier is resolved from it); the
    #: defaults preserve the historical child route.
    child_provider: str = Field(default="google", alias="childProvider")
    child_model: str = Field(default="gemini-3.5-flash", alias="childModel")


class ChildRunnerAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    child_runner_attached: Literal[False] = Field(default=False, alias="childRunnerAttached")
    real_child_runner_executed: Literal[False] = Field(
        default=False,
        alias="realChildRunnerExecuted",
    )
    raw_transcript_injected: Literal[False] = Field(
        default=False,
        alias="rawTranscriptInjected",
    )
    raw_tool_logs_injected: Literal[False] = Field(
        default=False,
        alias="rawToolLogsInjected",
    )
    hidden_reasoning_injected: Literal[False] = Field(
        default=False,
        alias="hiddenReasoningInjected",
    )
    parent_context_raw_injection: Literal[False] = Field(
        default=False,
        alias="parentContextRawInjection",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    memory_provider_called: Literal[False] = Field(default=False, alias="memoryProviderCalled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "child_runner_attached",
        "real_child_runner_executed",
        "raw_transcript_injected",
        "raw_tool_logs_injected",
        "hidden_reasoning_injected",
        "parent_context_raw_injection",
        "workspace_mutated",
        "memory_provider_called",
        "route_attached",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ChildTaskRequest(BaseModel):
    model_config = _MODEL_CONFIG

    parent_execution_id: str = Field(alias="parentExecutionId")
    turn_id: str = Field(alias="turnId")
    task_id: str = Field(alias="taskId")
    objective: str
    role: ChildRole = "general"
    delivery: ChildDeliveryMode = "return"
    budget_tokens: int = Field(default=0, alias="budgetTokens", ge=0)
    budget_ms: int = Field(default=0, alias="budgetMs", ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)
    #: Optional per-subagent model override. When set, this child turn runs on
    #: the given provider/model instead of the boundary's configured child route
    #: — e.g. a main session on Opus can explicitly delegate a subtask to Sonnet
    #: or Gemini. The model must be a route known to the local
    #: ``ModelTierRegistry``. ``None`` inherits the ``ChildRunnerConfig`` route.
    provider: str | None = None
    model: str | None = None

    @field_validator("parent_execution_id", "turn_id", "task_id", "objective")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("child task fields must be non-empty")
        return value


class ChildRunnerEnvelopeRef(BaseModel):
    model_config = _MODEL_CONFIG

    child_ref: str = Field(alias="childRef")
    task_id: str = Field(alias="taskId")
    child_execution_id: str = Field(alias="childExecutionId")
    parent_execution_id: str = Field(alias="parentExecutionId")
    status: Literal["completed", "blocked", "failed"]
    summary: str = ""
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    audit_event_refs: tuple[str, ...] = Field(default=(), alias="auditEventRefs")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_validator("child_ref", mode="before")
    @classmethod
    def _validate_child_ref(cls, value: object) -> str:
        return _runtime_output_ref(str(value), namespace="child")

    @field_validator("task_id", "child_execution_id", "parent_execution_id")
    @classmethod
    def _validate_ids(cls, value: str) -> str:
        return _safe_public_identifier(value)

    @field_validator("summary", mode="before")
    @classmethod
    def _sanitize_summary(cls, value: object) -> object:
        if isinstance(value, str):
            return _sanitize_public_text(value, max_chars=512)
        return value

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_evidence_refs(cls, value: object) -> object:
        return _safe_ref_tuple(value, namespace="evidence")

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def _sanitize_artifact_refs(cls, value: object) -> object:
        return _safe_ref_tuple(value, namespace="artifact")

    @field_validator("audit_event_refs", mode="before")
    @classmethod
    def _sanitize_audit_refs(cls, value: object) -> object:
        return _safe_ref_tuple(value, namespace="audit")


class ChildRunnerResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: ChildRunnerStatus
    task_id: str = Field(alias="taskId")
    prompt_ref: str = Field(alias="promptRef")
    envelope: ChildRunnerEnvelopeRef | None = None
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: ChildRunnerAuthorityFlags = Field(
        default_factory=ChildRunnerAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ChildRunnerAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data["authorityFlags"] = ChildRunnerAuthorityFlags()
        return type(self).model_validate(data)

    @field_validator("task_id", "prompt_ref")
    @classmethod
    def _validate_result_refs(cls, value: str) -> str:
        return _safe_public_identifier(value)

    def public_projection(self) -> dict[str, object]:
        safe_envelope = _safe_envelope_projection(self.envelope)
        return {
            "status": self.status,
            "taskId": self.task_id,
            "promptRef": self.prompt_ref,
            "childEnvelope": (
                None
                if safe_envelope is None
                else safe_envelope.model_dump(by_alias=True, mode="python", warnings=False)
            ),
            "parentOutputRefs": _parent_refs(safe_envelope),
            "errorCode": self.error_code,
            "errorMessage": _sanitize_public_text(self.error_message or "", max_chars=240) or None,
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": ChildRunnerAuthorityFlags().model_dump(by_alias=True),
        }


class LocalChildRunnerBoundary:
    """Child Runner boundary with default-OFF live-child-runner admission.

    Three execution surfaces are available, each behind its own off-switch:
    - **local-fake** (``localFakeChildRunnerEnabled``): deterministic stub for
      tests and development.
    - **sealed ADK shadow** (``realChildExecutionPackEnabled`` opt-in pack): routes
      a child turn through the real local ADK turn-runner surface.
    - **live-child-runner** (``liveChildRunnerEnabled``): drives a REAL
      model-backed runner injected via ``child_runner`` (``openmagi_live_provider``
      marker required) — PARALLEL to the fake path, NOT through the sealed ADK
      shadow surface.  All production-authority ``Literal[False]`` flags and seals
      remain untouched; this is a *local* surface only.

    All three gates default to ``False`` (byte-identical to the original PR1
    behaviour when none are enabled).
    """

    def __init__(
        self,
        config: ChildRunnerConfig,
        *,
        child_runner: object | None = None,
        adk_turn_boundary: object | None = None,
        agents_spawned_so_far: int = 0,
    ) -> None:
        self.config = config
        self.child_runner = child_runner
        #: Injected real-execution surface (a ``LocalAdkTurnRunnerBoundary``).
        #: Only consulted when the opt-in pack flag is on.  When ``None`` the
        #: boundary cannot reach real execution regardless of the pack flag.
        self.adk_turn_boundary = adk_turn_boundary
        #: Total agents already spawned in this parent run (for the ≤1000 cap).
        self.agents_spawned_so_far = agents_spawned_so_far

    async def run(self, request: ChildTaskRequest) -> ChildRunnerResult:
        diagnostics = _diagnostics(self.config)
        prompt_ref = _prompt_ref(request)
        if not self.config.enabled:
            return _result(
                request,
                prompt_ref,
                "disabled",
                error_code="child_runner_disabled",
                diagnostics=diagnostics,
            )

        # --- LIVE (real, model-backed) child runner — PARALLEL to local-fake ---
        # A live-marked runner (``openmagi_live_provider=True``) is driven via the
        # SAME injected ``run_child`` invocation path as the fake, then routed
        # through the SAME ``_envelope_from_output`` sanitisation.  Two off-switches
        # gate it: (a) the ``live_child_runner_enabled`` real-bool, and (b) a
        # live-trusted runner.  A live-marked runner provided while the gate is OFF
        # is BLOCKED (never silently executed); when neither the gate nor a
        # live-marked runner is present we fall straight through to the fake/shadow
        # paths below, byte-identical to before.
        if self.config.live_child_runner_enabled and _is_trusted_live_child_runner(
            self.child_runner
        ):
            return await self._run_live_child(request, prompt_ref, diagnostics)
        if (
            not self.config.live_child_runner_enabled
            and self.child_runner is not None
            and _is_trusted_live_child_runner(self.child_runner)
        ):
            return _result(
                request,
                prompt_ref,
                "blocked",
                error_code="live_child_runner_not_enabled",
                diagnostics=diagnostics,
            )

        if not self.config.local_fake_child_runner_enabled or self.child_runner is None:
            return _result(
                request,
                prompt_ref,
                "disabled",
                error_code="local_fake_child_runner_disabled",
                diagnostics=diagnostics,
            )
        if getattr(self.child_runner, "openmagi_local_fake_provider", False) is not True:
            return _result(
                request,
                prompt_ref,
                "blocked",
                error_code="local_fake_child_runner_untrusted",
                diagnostics=diagnostics,
            )

        # --- PR2 gated real-execution surface (opt-in pack only) -------------
        # Two independent off-switches must BOTH be satisfied to reach real
        # execution: (a) the opt-in pack flag, and (b) a supplied adk boundary.
        # When the pack is disabled we fall straight through to local-fake,
        # byte-identical to PR1.
        if self.config.real_child_execution_pack_enabled and self.adk_turn_boundary is not None:
            # Bound 1 — spawn-depth cap (no runaway recursion).
            spawn_depth = _requested_spawn_depth(request)
            if spawn_depth > self.config.max_spawn_depth:
                return _result(
                    request,
                    prompt_ref,
                    "blocked",
                    error_code="child_spawn_depth_exceeded",
                    diagnostics=diagnostics,
                )
            # Bound 2 — total-agents-per-run cap (≤1000).
            if self.agents_spawned_so_far >= MAX_TOTAL_AGENTS_PER_RUN:
                return _result(
                    request,
                    prompt_ref,
                    "blocked",
                    error_code="total_agents_per_run_exceeded",
                    diagnostics=diagnostics,
                )
            return await self._run_real_child(request, prompt_ref, diagnostics)

        try:
            output = await self._call_child_runner(request)
        except Exception as exc:
            diagnostics["localFakeChildRunnerCalled"] = True
            diagnostics["providerError"] = (
                _sanitize_public_text(str(exc), max_chars=240)
                or "[redacted-provider-error]"
            )
            return _result(
                request,
                prompt_ref,
                "error",
                error_code="local_fake_child_runner_error",
                diagnostics=diagnostics,
            )
        diagnostics["localFakeChildRunnerCalled"] = True
        return ChildRunnerResult(
            status="ok",
            taskId=request.task_id,
            promptRef=prompt_ref,
            envelope=_envelope_from_output(request, output, max_refs=self.config.max_output_refs),
            diagnosticMetadata=diagnostics,
            authorityFlags=ChildRunnerAuthorityFlags(),
        )

    async def _run_real_child(
        self,
        request: ChildTaskRequest,
        prompt_ref: str,
        diagnostics: dict[str, object],
    ) -> ChildRunnerResult:
        """Route a child turn through the REAL (local) ADK turn-runner surface.

        Reuses ``runtime.adk_turn_runner`` (no parallel runner).  The injected
        ``adk_turn_boundary`` must be a ``LocalAdkTurnRunnerBoundary`` wrapping a
        trusted local runner — the turn runner enforces that no live
        ADK/provider runner can be attached.  The local-fake ``run_child`` is
        NOT used on this path: the envelope refs are derived from the real
        turn's sanitised, runtime-issued result only.  We never surface the raw
        turn events/transcript; ONLY sanitised envelope refs cross back into the
        parent context.
        """
        # Function-local imports keep the boundary's module-import surface free
        # of any live-runner/runtime modules until the pack is actually used
        # (the module-import-isolation contract relies on this).
        from google.genai import types as _genai_types

        from magi_agent.runtime.adk_turn_runner import (
            AdkTurnRequest,
            AdkTurnRunnerConfig,
        )
        from magi_agent.runtime.adk_turn_runner import (
            AdkTurnRunner as _AdkTurnRunnerCls,
        )
        from magi_agent.runtime.model_tiers import ModelTierRegistry

        # Resolve the child's model route: a per-task override wins, else the
        # boundary's configured child route.  The model-tier is derived from the
        # registry so the runner config is self-consistent; an unknown route is
        # rejected by ``AdkTurnRunnerConfig`` (caught below) rather than silently
        # falling back to a single hardcoded model.
        child_provider = request.provider or self.config.child_provider
        child_model = request.model or self.config.child_model
        child_tier = (
            ModelTierRegistry.with_defaults()
            .resolve(provider=child_provider, model=child_model)
            .tier
        )

        # Drive the REAL adk turn-runner surface for the child turn.  Any
        # failure on the real surface degrades to a blocked/error result rather
        # than leaking partial state.
        try:
            turn_request = AdkTurnRequest(
                turnId=request.turn_id,
                userId=request.parent_execution_id,
                sessionId=request.parent_execution_id,
                invocationId=request.task_id,
                newMessage=_genai_types.Content(
                    role="user",
                    # Redaction-safe skeleton: request.objective / role / budgets
                    # are intentionally NOT forwarded here yet.  Forwarding the
                    # real objective is deferred to a later Track 17 PR.
                    parts=[_genai_types.Part(text="child-turn")],
                ),
            )
            turn_runner = _AdkTurnRunnerCls()
            turn_result = await turn_runner.run_turn(
                turn_request,
                runner=self.adk_turn_boundary,
                config=AdkTurnRunnerConfig(
                    enabled=True,
                    provider=child_provider,
                    model=child_model,
                    modelTier=child_tier,
                ),
            )
        except Exception as exc:
            diagnostics["realChildRunnerExecuted"] = False
            diagnostics["providerError"] = (
                _sanitize_public_text(str(exc), max_chars=240)
                or "[redacted-provider-error]"
            )
            return _result(
                request,
                prompt_ref,
                "error",
                error_code="real_child_runner_error",
                diagnostics=diagnostics,
            )

        runner_invoked = bool(getattr(turn_result, "runner_invoked", False))
        turn_status = getattr(turn_result, "status", "failed")
        diagnostics["adkRunnerSurface"] = REAL_ADK_CHILD_RUNNER_SURFACE
        diagnostics["realChildRunnerExecuted"] = True
        diagnostics["adkTurnRunnerInvoked"] = runner_invoked

        if turn_status != "succeeded" or not runner_invoked:
            return _result(
                request,
                prompt_ref,
                "blocked",
                error_code="real_child_runner_not_succeeded",
                diagnostics=diagnostics,
            )

        child_execution_id = f"child-exec-{_digest(f'{request.parent_execution_id}:{request.task_id}')}"
        receipt_ref = f"receipt:{_digest(f'{request.parent_execution_id}:{request.task_id}:receipt')}"
        audit_ref = f"audit:{_digest(f'{request.parent_execution_id}:{request.task_id}:adk-turn')}"
        envelope = _runtime_child_acceptance_envelope(
            request,
            child_execution_id=child_execution_id,
            receipt_ref=receipt_ref,
            audit_ref=audit_ref,
            prompt_ref=prompt_ref,
        )
        policy = _runtime_child_acceptance_policy(
            request,
            child_execution_id=child_execution_id,
            receipt_ref=receipt_ref,
            audit_ref=audit_ref,
        )
        verdict = accept_real_child_envelope(
            envelope,
            receipt_ref=receipt_ref,
            policy=policy,
        )
        diagnostics["childAcceptanceStatus"] = verdict.status
        diagnostics["childAcceptanceReason"] = ",".join(verdict.reason_codes)
        if verdict.status != "accepted":
            return _result(
                request,
                prompt_ref,
                "blocked",
                error_code="real_child_acceptance_rejected",
                diagnostics=diagnostics,
            )

        diagnostics["childAcceptanceAcceptedEvidenceCount"] = len(
            verdict.accepted_evidence_refs
        )

        # Build the sanitised parent-visible envelope from accepted REAL turn
        # evidence.  No raw transcript/events are read; the parent sees only
        # opaque runtime refs, and token-validated acceptance has already passed.
        real_output: dict[str, object] = {
            "childExecutionId": child_execution_id,
            "status": "completed",
            "summary": "",
            "evidenceRefs": (
                f"evidence:{_digest(f'{request.turn_id}:{request.task_id}:adk-turn')}",
            ),
            "artifactRefs": (),
            "auditEventRefs": (audit_ref,),
        }
        return ChildRunnerResult(
            status="ok",
            taskId=request.task_id,
            promptRef=prompt_ref,
            envelope=_envelope_from_output(
                request, real_output, max_refs=self.config.max_output_refs
            ),
            diagnosticMetadata=diagnostics,
            authorityFlags=ChildRunnerAuthorityFlags(),
        )

    async def _run_live_child(
        self,
        request: ChildTaskRequest,
        prompt_ref: str,
        diagnostics: dict[str, object],
    ) -> ChildRunnerResult:
        """Drive a REAL (model-backed) live child runner.

        PARALLEL to the local-fake path: the injected live runner is invoked via
        the SAME ``run_child`` seam (``_call_child_runner``), and its output is
        routed through the SAME ``_envelope_from_output`` sanitisation — so no
        secrets/paths/raw transcript can leak into the parent context.  The
        spawn-depth, total-agents and ``max_output_refs`` caps are enforced
        exactly as the ADK shadow path does.  The sealed ``ChildRunnerAuthorityFlags``
        are NEVER flipped (this is a *local* surface); a non-authority
        ``liveChildRunnerCalled`` diagnostic signals a live run attempt instead.
        """
        # Bound 1 — spawn-depth cap (no runaway recursion).
        spawn_depth = _requested_spawn_depth(request)
        if spawn_depth > self.config.max_spawn_depth:
            return _result(
                request,
                prompt_ref,
                "blocked",
                error_code="child_spawn_depth_exceeded",
                diagnostics=diagnostics,
            )
        # Bound 2 — total-agents-per-run cap (≤1000).
        # NOTE(contract): ``agents_spawned_so_far`` is a preflight *snapshot*
        # supplied by the caller.  The boundary checks it but does NOT
        # self-increment it — a multi-spawn caller (e.g. Task C ``spawn_agent``)
        # MUST own and increment the running count across successive ``run()``
        # calls to prevent races where the cap appears satisfied mid-fan-out.
        if self.agents_spawned_so_far >= MAX_TOTAL_AGENTS_PER_RUN:
            return _result(
                request,
                prompt_ref,
                "blocked",
                error_code="total_agents_per_run_exceeded",
                diagnostics=diagnostics,
            )
        try:
            output = await self._call_child_runner(request)
        except Exception as exc:
            diagnostics["liveChildRunnerCalled"] = True
            diagnostics["providerError"] = (
                _sanitize_public_text(str(exc), max_chars=240)
                or "[redacted-provider-error]"
            )
            return _result(
                request,
                prompt_ref,
                "error",
                error_code="live_child_runner_error",
                diagnostics=diagnostics,
            )
        diagnostics["liveChildRunnerCalled"] = True
        return ChildRunnerResult(
            status="ok",
            taskId=request.task_id,
            promptRef=prompt_ref,
            envelope=_envelope_from_output(
                request, output, max_refs=self.config.max_output_refs
            ),
            diagnosticMetadata=diagnostics,
            authorityFlags=ChildRunnerAuthorityFlags(),
        )

    async def _call_child_runner(self, request: ChildTaskRequest) -> object:
        """Invoke ANY injected child runner via the ``run_child`` seam.

        Shared by the local-fake and the live paths: it only calls
        ``run_child(request)`` and awaits the result if it is awaitable.  It does
        NOT import or construct any live-runner/runtime surface, so the module's
        import-isolation contract is preserved on both paths.
        """
        method = getattr(self.child_runner, "run_child", None)
        if method is None:
            raise ValueError("child runner must expose run_child")
        value = method(request)
        if inspect.isawaitable(value):
            return await value
        return value


def _is_trusted_live_child_runner(runner: object | None) -> bool:
    """A live (real, model-backed) child runner declares ``openmagi_live_provider``.

    Parallel to the inline ``openmagi_local_fake_provider`` fake-trust check: the
    two markers are mutually exclusive in practice, so a fake-marked runner is
    NEVER admitted via the live branch and vice-versa.
    """
    return getattr(runner, "openmagi_live_provider", False) is True


def _runtime_child_acceptance_envelope(
    request: ChildTaskRequest,
    *,
    child_execution_id: str,
    receipt_ref: str,
    audit_ref: str,
    prompt_ref: str,
) -> ChildRuntimeEnvelope:
    role = _evidence_agent_role(request.role)
    task_id = _acceptance_task_id(request)
    policy_snapshot_id = "policy:child-runner-boundary"
    runtime_authority = issue_runtime_authority(
        authority_id="authority:child-runner-boundary",
        scopes=("child_runtime_envelope",),
    )
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=runtime_authority,
        issuer="openmagi_runtime_boundary",
        mode="return",
        status="accepted",
        parentBoundary={
            "executionId": request.parent_execution_id,
            "agentId": "parent-agent",
            "turnId": request.turn_id,
            "policyScope": role,
            "policySnapshotId": policy_snapshot_id,
            "agentRole": role,
            "runOn": "main",
            "spawnDepth": 0,
        },
        childBoundary={
            "executionId": child_execution_id,
            "agentId": "child-agent",
            "parentExecutionId": request.parent_execution_id,
            "taskId": task_id,
            "turnId": request.turn_id,
            "policyScope": role,
            "policySnapshotId": policy_snapshot_id,
            "agentRole": role,
            "runOn": "child",
            "spawnDepth": _requested_spawn_depth(request),
        },
        task={
            "taskId": task_id,
            "persona": role,
            "role": role,
            "spawnDepth": _requested_spawn_depth(request),
            "deliver": request.delivery,
            "promptRef": prompt_ref,
        },
        policySnapshot={
            "parentPolicySnapshotId": policy_snapshot_id,
            "childPolicySnapshotId": policy_snapshot_id,
            "taskLocalPolicyCompatibilityRefs": (),
            "allowedToolNames": tuple(
                item
                for item in _metadata_str_tuple(request.metadata.get("allowedTools"))
                if _PUBLIC_REF_RE.fullmatch(item)
            ),
            "permissionRefs": ("permission:child-runner-boundary",),
            "callbackHookRefs": ("callback:child-acceptance",),
        },
        ledgerRef={
            "ledgerId": f"ledger:{child_execution_id}",
            "executionId": child_execution_id,
            "agentId": "child-agent",
            "parentExecutionId": request.parent_execution_id,
            "taskId": task_id,
            "policySnapshotId": policy_snapshot_id,
            "childLedgerRefs": ("ledger:adk-turn",),
        },
        delegatedEvidenceRequirements=(
            DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        ),
        workspaceIsolation={
            "workspacePolicy": "trusted",
            "isolationRef": f"workspace-isolation:{request.task_id}",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
            "privateNotes": (),
        },
        completionContract={
            "requiredEvidence": "tool_call",
            "requiredFiles": (),
            "requireNonEmptyResult": True,
            "summaryIsEvidence": False,
            "acceptedEvidenceMetadataOnly": True,
        },
        auditEventRefs=(audit_ref,),
        adkPrimitiveOwnership={
            "agentOwner": "adk_future_agent",
            "runnerOwner": "adk_future_runner",
            "eventOwner": "adk_event_bridge",
            "toolOwner": "adk_function_tool_future",
            "callbackOwner": "adk_callbacks_future",
            "runnerAttached": False,
            "childExecutionAttached": False,
            "allowedToolNames": tuple(
                item
                for item in _metadata_str_tuple(request.metadata.get("allowedTools"))
                if _PUBLIC_REF_RE.fullmatch(item)
            ),
            "callbackHookRefs": ("callback:child-acceptance",),
        },
        authorityFlags=ChildRuntimeEnvelopeAuthorityFlags(),
        rawTranscriptRef=None,
        privateMetadata={},
    )


def _runtime_child_acceptance_policy(
    request: ChildTaskRequest,
    *,
    child_execution_id: str,
    receipt_ref: str,
    audit_ref: str,
) -> dict[str, object]:
    policy_snapshot_id = "policy:child-runner-boundary"
    return {
        "parentExecutionId": request.parent_execution_id,
        "childExecutionId": child_execution_id,
        "taskId": _acceptance_task_id(request),
        "parentPolicySnapshotId": policy_snapshot_id,
        "childPolicySnapshotId": policy_snapshot_id,
        "runtimeReceiptRef": receipt_ref,
        "requiredEvidenceRefs": (
            f"ledger:{child_execution_id}",
            receipt_ref,
            audit_ref,
        ),
        "maxRetryBudget": 0,
        "currentAttempt": 0,
    }


def _acceptance_task_id(request: ChildTaskRequest) -> str:
    return f"task:{_digest(f'{request.parent_execution_id}:{request.task_id}')}"


def _evidence_agent_role(role: ChildRole) -> Literal["general", "coding", "research"]:
    if role == "research":
        return "research"
    if role in {"coding", "reviewer", "implementer", "debugging"}:
        return "coding"
    return "general"


def _metadata_str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _requested_spawn_depth(request: ChildTaskRequest) -> int:
    raw = request.metadata.get("spawnDepth") if isinstance(request.metadata, Mapping) else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 1
    return max(1, raw)


def _diagnostics(config: ChildRunnerConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeChildRunnerEnabled": config.local_fake_child_runner_enabled,
        "liveChildRunnerEnabled": config.live_child_runner_enabled,
        "productionChildExecutionEnabled": False,
        "productionWritesEnabled": False,
        "adkRunnerSurface": config.adk_runner_surface,
        "localFakeChildRunnerCalled": False,
        "realChildRunnerExecuted": False,
        "liveChildRunnerCalled": False,
    }


def _result(
    request: ChildTaskRequest,
    prompt_ref: str,
    status: ChildRunnerStatus,
    *,
    error_code: str | None,
    diagnostics: Mapping[str, object],
) -> ChildRunnerResult:
    return ChildRunnerResult(
        status=status,
        taskId=request.task_id,
        promptRef=prompt_ref,
        errorCode=error_code,
        diagnosticMetadata=diagnostics,
        authorityFlags=ChildRunnerAuthorityFlags(),
    )


def _envelope_from_output(
    request: ChildTaskRequest,
    output: object,
    *,
    max_refs: int,
) -> ChildRunnerEnvelopeRef:
    data = output if isinstance(output, Mapping) else {}
    child_execution_id = _safe_public_identifier(
        str(data.get("childExecutionId") or f"child:{request.task_id}")
    )
    status = data.get("status")
    if status not in {"completed", "blocked", "failed"}:
        status = "completed"
    return ChildRunnerEnvelopeRef(
        childRef=f"child:{_digest(f'{request.parent_execution_id}:{child_execution_id}')}",
        taskId=request.task_id,
        childExecutionId=child_execution_id,
        parentExecutionId=request.parent_execution_id,
        status=status,
        summary=_sanitize_public_text(str(data.get("summary") or ""), max_chars=512),
        evidenceRefs=_runtime_issued_refs(
            request,
            child_execution_id,
            data.get("evidenceRefs"),
            namespace="evidence",
            max_refs=max_refs,
        ),
        artifactRefs=_runtime_issued_refs(
            request,
            child_execution_id,
            data.get("artifactRefs"),
            namespace="artifact",
            max_refs=max_refs,
        ),
        auditEventRefs=_runtime_issued_refs(
            request,
            child_execution_id,
            data.get("auditEventRefs"),
            namespace="audit",
            max_refs=max_refs,
        ),
    )


def _parent_refs(envelope: ChildRunnerEnvelopeRef | None) -> list[str]:
    if envelope is None:
        return []
    refs = [
        envelope.child_ref,
        *envelope.evidence_refs,
        *envelope.artifact_refs,
        *envelope.audit_event_refs,
    ]
    return list(dict.fromkeys(refs))


def _safe_envelope_projection(envelope: ChildRunnerEnvelopeRef | None) -> ChildRunnerEnvelopeRef | None:
    if envelope is None:
        return None
    reissued = _reissue_parent_visible_envelope_refs(envelope)
    return ChildRunnerEnvelopeRef.model_validate(
        {
            **envelope.model_dump(by_alias=True, mode="python", warnings=False),
            "childRef": reissued["child"],
            "evidenceRefs": reissued["evidence"],
            "artifactRefs": reissued["artifact"],
            "auditEventRefs": reissued["audit"],
        }
    )


def _prompt_ref(request: ChildTaskRequest) -> str:
    seed = f"{request.parent_execution_id}:{request.turn_id}:{request.task_id}:{request.objective}"
    return f"prompt:{_digest(seed)}"


def _runtime_issued_refs(
    request: ChildTaskRequest,
    child_execution_id: str,
    value: object,
    *,
    namespace: Literal["evidence", "artifact", "audit"],
    max_refs: int,
) -> tuple[str, ...]:
    child_refs = _child_output_refs(value, namespace=namespace, max_refs=max_refs)
    issued: list[str] = []
    for child_ref in child_refs:
        seed = (
            f"{request.parent_execution_id}:{request.turn_id}:{request.task_id}:"
            f"{child_execution_id}:{namespace}:{child_ref}"
        )
        issued.append(f"{namespace}:{_digest(seed)}")
    return tuple(dict.fromkeys(issued))


def _reissue_parent_visible_envelope_refs(
    envelope: ChildRunnerEnvelopeRef,
) -> dict[str, str | tuple[str, ...]]:
    seed_prefix = (
        f"{envelope.parent_execution_id}:{envelope.task_id}:{envelope.child_execution_id}"
    )
    return {
        "child": f"child:{_digest(f'{seed_prefix}:child:{envelope.child_ref}')}",
        "evidence": _reissue_stored_refs(seed_prefix, envelope.evidence_refs, "evidence"),
        "artifact": _reissue_stored_refs(seed_prefix, envelope.artifact_refs, "artifact"),
        "audit": _reissue_stored_refs(seed_prefix, envelope.audit_event_refs, "audit"),
    }


def _reissue_stored_refs(
    seed_prefix: str,
    refs: Sequence[str],
    namespace: Literal["evidence", "artifact", "audit"],
) -> tuple[str, ...]:
    issued: list[str] = []
    for ref in refs:
        match = _CHILD_OUTPUT_REF_RE.fullmatch(ref)
        if match is None or match.group("namespace") != namespace:
            continue
        issued.append(f"{namespace}:{_digest(f'{seed_prefix}:{namespace}:{ref}')}")
    return tuple(dict.fromkeys(issued))


def _child_output_refs(
    value: object,
    *,
    namespace: Literal["evidence", "artifact", "audit"],
    max_refs: int,
) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_items: Sequence[object]
    if isinstance(value, str):
        raw_items = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, bytes):
        raw_items = value
    else:
        return ()
    refs: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            continue
        safe = _sanitize_public_text(item, max_chars=180)
        match = _CHILD_OUTPUT_REF_RE.fullmatch(safe)
        if match is not None and match.group("namespace") == namespace:
            refs.append(safe)
        if len(refs) >= max_refs:
            break
    return tuple(dict.fromkeys(refs))


def _safe_ref_tuple(
    value: object,
    *,
    namespace: Literal["evidence", "artifact", "audit"],
    max_refs: int = 32,
) -> tuple[str, ...]:
    refs: list[str] = []
    for child_ref in _child_output_refs(value, namespace=namespace, max_refs=max_refs):
        refs.append(_runtime_output_ref(child_ref, namespace=namespace))
    return tuple(dict.fromkeys(refs))


def _runtime_output_ref(
    value: str,
    *,
    namespace: Literal["child", "evidence", "artifact", "audit"],
) -> str:
    safe = _sanitize_public_text(value, max_chars=180)
    runtime_match = _RUNTIME_OUTPUT_REF_RE.fullmatch(safe)
    if runtime_match is not None and runtime_match.group("namespace") == namespace:
        return safe
    child_match = _CHILD_OUTPUT_REF_RE.fullmatch(safe)
    if child_match is not None and child_match.group("namespace") == namespace:
        return f"{namespace}:{_digest(f'{namespace}:{safe}')}"
    return f"{namespace}:{_digest(value)}"


def _safe_public_identifier(value: str) -> str:
    clean = _sanitize_public_text(value, max_chars=180)
    if _PUBLIC_REF_RE.fullmatch(clean) is not None:
        return clean
    if re.fullmatch(r"[A-Za-z0-9._:-]+", clean):
        return clean
    return f"child:{_digest(value)}"


def _sanitize_public_text(value: str, *, max_chars: int) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None and _PRIVATE_PATH_RE.search(line) is None
    ]
    clean = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean[:max_chars]


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(
            marker in normalized_key
            for marker in (
                "raw",
                "prompt",
                "transcript",
                "tool",
                "reasoning",
                "secret",
                "token",
                "key",
                "credential",
                "auth",
                "path",
            )
        ):
            continue
        if isinstance(value, str):
            safe[str(key)] = _sanitize_public_text(value, max_chars=240)
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "FUTURE_ADK_CHILD_RUNNER_SURFACE",
    "MAX_TOTAL_AGENTS_PER_RUN",
    "REAL_ADK_CHILD_RUNNER_SURFACE",
    "ChildRunnerAuthorityFlags",
    "ChildRunnerConfig",
    "ChildRunnerEnvelopeRef",
    "ChildRunnerResult",
    "ChildTaskRequest",
    "LocalChildRunnerBoundary",
    "clamp_total_agents_per_run",
]
