from __future__ import annotations

from collections.abc import Mapping
import os
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ModelRoutingSource,
    Gate5B4C3ShadowGenerationAuthorityFlags,
    Gate5B4C3ShadowGenerationImageBlock,
    Gate5B4C3ShadowGenerationRequest,
    Gate5B4C3SourceAuthority,
)

_UNSAFE_INPUT_RE = re.compile(
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
    r"/(?:data/bots|workspace|var/lib/kubelet|mnt|private|Users)\S*|"
    r"\b(?:kubectl|helm|kustomize|sealed-secrets|kubeconfig)\b|"
    r"\bmagi\.pro\b\S*|"
    r"https?://\S+|"
    r"s3://\S+"
    r")",
    re.IGNORECASE,
)


Gate5B4C3RunnerInputStatus: TypeAlias = Literal["accepted", "dropped"]
Gate5B4C3RunnerInputReason: TypeAlias = Literal[
    "accepted",
    "sanitized_input_too_large",
    "unsafe_input",
    "input_token_budget_exceeded",
    "total_token_budget_exceeded",
    "unsafe_policy",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class _Gate5B4C3RunnerInputModel(BaseModel):
    model_config = _MODEL_CONFIG

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


class Gate5B4C3RunnerInput(_Gate5B4C3RunnerInputModel):
    schema_version: Literal["gate5b4c3.runnerInput.v1"] = Field(
        default="gate5b4c3.runnerInput.v1",
        alias="schemaVersion",
    )
    system_instruction: str = Field(alias="systemInstruction")
    sanitized_user_input: str = Field(alias="sanitizedUserInput")
    sanitized_input_text_digest: str = Field(alias="sanitizedInputTextDigest")
    sanitized_recent_history: tuple[dict[str, str], ...] = Field(
        default=(),
        alias="sanitizedRecentHistory",
    )
    sanitized_image_blocks: tuple[Gate5B4C3ShadowGenerationImageBlock, ...] = Field(
        default=(),
        alias="sanitizedImageBlocks",
    )
    source_authority: Gate5B4C3SourceAuthority = Field(alias="sourceAuthority")
    provider_label: str = Field(alias="providerLabel")
    model_label: str = Field(alias="modelLabel")
    routing_source: Gate5B4C3ModelRoutingSource = Field(alias="routingSource")
    router_decision_digest: str | None = Field(default=None, alias="routerDecisionDigest")
    routing_profile_digest: str | None = Field(default=None, alias="routingProfileDigest")
    bot_config_model_digest: str | None = Field(default=None, alias="botConfigModelDigest")
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    fallback_approved: bool = Field(default=False, alias="fallbackApproved")
    shadow_credential_ref: str | None = Field(default=None, alias="shadowCredentialRef")
    credential_ref_source: Literal["server_config"] | None = Field(
        default=None,
        alias="credentialRefSource",
    )
    max_output_tokens: int = Field(alias="maxOutputTokens")
    estimated_input_tokens: int = Field(alias="estimatedInputTokens")
    estimated_total_tokens: int = Field(alias="estimatedTotalTokens")
    runner_timeout_ms: int = Field(alias="runnerTimeoutMs")
    cost_cap_usd: float = Field(alias="costCapUsd")
    retry_policy: Literal["none"] = Field(alias="retryPolicy")
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    tools_policy: Literal["disabled", "shadow_readonly", "selected_full_toolhost"] = Field(
        default="disabled",
        alias="toolsPolicy",
    )
    tools_enabled: bool = Field(default=False, alias="toolsEnabled")
    tool_host_dispatch_allowed: bool = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    memory_enabled: Literal[False] = Field(default=False, alias="memoryEnabled")
    workspace_enabled: Literal[False] = Field(default=False, alias="workspaceEnabled")
    child_execution_enabled: Literal[False] = Field(
        default=False,
        alias="childExecutionEnabled",
    )
    mission_runtime_enabled: Literal[False] = Field(
        default=False,
        alias="missionRuntimeEnabled",
    )
    evidence_block_mode_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockModeEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_non_authoritative(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "typescript"
        data["diagnosticOnly"] = True
        data["localOnly"] = True
        if data.get("toolsPolicy") in {"shadow_readonly", "selected_full_toolhost"}:
            data["toolsPolicy"] = data.get("toolsPolicy")
            data["toolsEnabled"] = data.get("toolsEnabled") is True
            data["toolHostDispatchAllowed"] = data.get("toolHostDispatchAllowed") is True
        else:
            data["toolsPolicy"] = "disabled"
            data["toolsEnabled"] = False
            data["toolHostDispatchAllowed"] = False
        data["memoryEnabled"] = False
        data["workspaceEnabled"] = False
        data["childExecutionEnabled"] = False
        data["missionRuntimeEnabled"] = False
        data["evidenceBlockModeEnabled"] = False
        return data

    @model_validator(mode="after")
    def _validate_tool_policy_coherence(self) -> Self:
        if self.tools_policy == "disabled":
            if self.tools_enabled or self.tool_host_dispatch_allowed:
                raise ValueError("disabled tool policy cannot enable tool dispatch")
        elif not self.tools_enabled or not self.tool_host_dispatch_allowed:
            raise ValueError("tool policy requires explicit dispatch enablement")
        return self


class Gate5B4C3RunnerInputAdapterResult(_Gate5B4C3RunnerInputModel):
    schema_version: Literal["gate5b4c3.runnerInputAdapterResult.v1"] = Field(
        default="gate5b4c3.runnerInputAdapterResult.v1",
        alias="schemaVersion",
    )
    status: Gate5B4C3RunnerInputStatus
    reason: Gate5B4C3RunnerInputReason
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    runner_input: Gate5B4C3RunnerInput | None = Field(default=None, alias="runnerInput")
    authority: Gate5B4C3ShadowGenerationAuthorityFlags = Field(
        default_factory=Gate5B4C3ShadowGenerationAuthorityFlags,
    )

    @model_validator(mode="before")
    @classmethod
    def _force_non_authoritative(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "typescript"
        data["diagnosticOnly"] = True
        data["localOnly"] = True
        return data

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


def build_gate5b4c3_runner_input(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    env: Mapping[str, str] | None = None,
) -> Gate5B4C3RunnerInputAdapterResult:
    sanitized_input = request.turn.sanitized_current_turn_text
    input_bytes = len(sanitized_input.encode("utf-8"))
    if input_bytes > request.budgets.max_sanitized_input_bytes:
        return _result("dropped", "sanitized_input_too_large")
    if _UNSAFE_INPUT_RE.search(sanitized_input):
        return _result("dropped", "unsafe_input")
    sanitized_history = _sanitized_history_for_runner(request)
    for item in sanitized_history:
        if _UNSAFE_INPUT_RE.search(item["content"]):
            return _result("dropped", "unsafe_input")

    estimated_input_tokens = _estimate_tokens(sanitized_input) + sum(
        _estimate_tokens(item["content"]) for item in sanitized_history
    )
    if estimated_input_tokens > request.budgets.max_estimated_input_tokens:
        return _result("dropped", "input_token_budget_exceeded")

    max_output_tokens = _resolved_max_output_tokens(request)
    estimated_total_tokens = estimated_input_tokens + max_output_tokens
    if estimated_total_tokens > request.budgets.max_total_estimated_tokens:
        return _result("dropped", "total_token_budget_exceeded")

    selected_toolhost_tools = request.recipe_profile.tools_policy in {
        "shadow_readonly",
        "selected_full_toolhost",
    }
    disabled_tools_policy_valid = (
        request.recipe_profile.tools_policy == "disabled"
        and request.policy.tools_disabled is True
        and request.policy.tool_host_dispatch_allowed is False
    )
    selected_toolhost_policy_valid = (
        selected_toolhost_tools
        and request.policy.tools_disabled is False
        and request.policy.tool_host_dispatch_allowed is True
    )
    if (
        not request.policy.type_script_response_authority
        or not request.policy.python_diagnostic_only
        or request.policy.output_isolation != "local_diagnostic_only"
        or not (disabled_tools_policy_valid or selected_toolhost_policy_valid)
        or request.policy.memory_provider_calls_allowed
        or request.policy.memory_writes_allowed
        or request.policy.prompt_memory_injection_allowed
        or request.policy.workspace_mutation_allowed
        or request.policy.child_execution_allowed
        or request.policy.mission_runtime_allowed
        or request.policy.evidence_block_mode_allowed
    ):
        return _result("dropped", "unsafe_policy")

    from magi_agent.config.env import model_aware_prompts_enabled

    runner_input = Gate5B4C3RunnerInput(
        systemInstruction=_build_system_instruction(
            request,
            model_aware=model_aware_prompts_enabled(os.environ if env is None else env),
        ),
        sanitizedUserInput=sanitized_input,
        sanitizedInputTextDigest=request.turn.sanitized_input_text_digest,
        sanitizedRecentHistory=sanitized_history,
        sanitizedImageBlocks=request.turn.sanitized_image_blocks,
        sourceAuthority=request.recipe_profile.source_authority,
        providerLabel=request.model_routing.provider_label,
        modelLabel=request.model_routing.model_label,
        routingSource=request.model_routing.routing_source,
        routerDecisionDigest=request.model_routing.router_decision_digest,
        routingProfileDigest=request.model_routing.routing_profile_digest,
        botConfigModelDigest=request.model_routing.bot_config_model_digest,
        fallbackReason=request.model_routing.fallback_reason,
        fallbackApproved=request.model_routing.fallback_approved,
        shadowCredentialRef=request.model_routing.shadow_credential_ref,
        credentialRefSource=request.model_routing.credential_ref_source,
        maxOutputTokens=max_output_tokens,
        estimatedInputTokens=estimated_input_tokens,
        estimatedTotalTokens=estimated_total_tokens,
        runnerTimeoutMs=request.budgets.python_runner_timeout_ms,
        costCapUsd=request.budgets.max_cost_usd,
        retryPolicy=request.budgets.retry_policy,
        toolsPolicy=request.recipe_profile.tools_policy,
        toolsEnabled=selected_toolhost_tools,
        toolHostDispatchAllowed=selected_toolhost_tools,
    )
    return _result("accepted", "accepted", runner_input=runner_input)


def _result(
    status: Gate5B4C3RunnerInputStatus,
    reason: Gate5B4C3RunnerInputReason,
    *,
    runner_input: Gate5B4C3RunnerInput | None = None,
) -> Gate5B4C3RunnerInputAdapterResult:
    return Gate5B4C3RunnerInputAdapterResult(
        status=status,
        reason=reason,
        runnerInput=runner_input.model_dump(by_alias=True, mode="python", warnings=False)
        if runner_input is not None
        else None,
    )


def _resolved_max_output_tokens(request: Gate5B4C3ShadowGenerationRequest) -> int:
    if request.model_routing.max_output_tokens is None:
        return request.budgets.max_output_tokens
    return min(request.model_routing.max_output_tokens, request.budgets.max_output_tokens)


def _sanitized_history_for_runner(
    request: Gate5B4C3ShadowGenerationRequest,
) -> tuple[dict[str, str], ...]:
    if request.recipe_profile.source_authority != "bounded_sanitized_recent_history":
        return ()
    return tuple(
        {
            "role": item.role,
            "content": item.sanitized_text,
        }
        for item in request.turn.sanitized_recent_history
    )


def _estimate_tokens(value: str) -> int:
    if not value:
        return 0
    return len(value.encode("utf-8"))


def _build_system_instruction(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    model_aware: bool = False,
) -> str:
    if request.recipe_profile.tools_policy == "selected_full_toolhost":
        base = (
            "You are running an OpenMagi Gate 5B selected full toolhost route with "
            "first-party recipe harness metadata. You may request only the approved "
            "tools exposed for this selected turn. Use coding, research, general "
            "automation, evidence, and methodology harness constraints as the active "
            "OpenMagi operating frame, but answer ordinary conversation directly without "
            "tools. Only request a tool when the user explicitly asks for file, shell, "
            "patch, calculation, search, or workspace work. For brief replies, do not "
            "call tools. Do not call tools only to inspect context or prove that tools "
            "are attached. If the selected tool list includes SpawnAgent and the user "
            "explicitly asks for parallel or delegated work, use SpawnAgent with a "
            "concrete delegated subtask prompt instead of pretending background work "
            "will happen later. Every turn must end with a normal text answer for the user; "
            "tool calls, function responses, or other non-text runner events alone are "
            "not a valid completion. Do not finish by promising future or background "
            "work, asking the user to wait, or claiming that a queued task is still "
            "running. When a task needs several steps, perform each step now with the "
            "available tools and keep working until the whole task is actually "
            "complete — never end by only describing a plan or what you will do next; "
            "execute it. Give a final text answer once the task is fully done, or "
            "clearly state a concrete blocker. Do not claim broad production workspace "
            "authority. Keep all workspace writes inside the selected workspace root. "
            "SpawnAgent is the selected first-party child-runner surface when it "
            "appears in the selected tool list; rely on its tool result to distinguish "
            "live attachment from child execution failure. Do not write memory, send "
            "channel messages, use browser or external integrations, execute child "
            "agents through any non-SpawnAgent path, run mission runtime, use evidence "
            "block mode, write transcripts, or write SSE events unless an explicit "
            "selected first-party authority surface is attached for that turn. "
            f"Routing source: {request.model_routing.routing_source}."
        )
        # PR10: the selected full toolhost route IS the coding-capable agent
        # path. When the model-aware flag is on, append the family-keyed coding
        # hint for the active model so it reaches the model on the LIVE request
        # path (chat.py -> gate5b4c3 live runner -> Agent.instruction). Default
        # family (incl. claude) contributes nothing, so the prefix is unchanged.
        if model_aware:
            from magi_agent.runtime.message_builder import _coding_model_hint_for

            hint = _coding_model_hint_for(request.model_routing.model_label)
            if hint:
                return f"{base}\n\n{hint}"
        return base
    if request.recipe_profile.tools_policy == "shadow_readonly":
        return (
            "You are running an OpenMagi Gate 1A read-only tools canary. "
            "You may request only the approved read-only tools exposed for this turn. "
            "Use tool results only as bounded, redacted evidence. "
            "Do not write state or mutate files. Do not use browser or web acquisition. "
            "Do not write memory, send channel messages, deliver artifacts, mutate workspace, "
            "execute child agents, run mission runtime, use evidence block mode, "
            "write transcripts, or write SSE events. "
            f"Routing source: {request.model_routing.routing_source}."
        )
    return (
        "You are running an OpenMagi Gate 5B-4c-3 no-memory, no-tools shadow "
        "generation diagnostic. Use only the sanitized current-turn input. Do not "
        "claim production authority. Do not request tools, memory, workspace, child "
        "agents, evidence block mode, channel delivery, transcript writes, or SSE writes. "
        f"Routing source: {request.model_routing.routing_source}."
    )


__all__ = [
    "Gate5B4C3RunnerInput",
    "Gate5B4C3RunnerInputAdapterResult",
    "Gate5B4C3RunnerInputReason",
    "Gate5B4C3RunnerInputStatus",
    "build_gate5b4c3_runner_input",
]
