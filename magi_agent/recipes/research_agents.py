from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel


ResearchAgentType = Literal["direct", "explore", "plan", "verifier"]
ResearchMaterializationStatus = Literal["direct", "ready", "blocked"]
ResearchMaterializationBlockReason = Literal[
    "missing_web_tools",
    "missing_attachment_inputs",
]
ResearchToolPermission = Literal["read", "net"]
ScoutResearchRecipeProfileKey = Literal[
    "scout_repo_fixture",
    "scout_external_repo",
    "scout_web_docs",
]
ScoutResearchRecipeStatus = Literal["disabled", "ready", "blocked"]

LOCAL_RESEARCH_TOOL_NAMES: tuple[str, ...] = ("FileRead", "Glob", "Grep")
WEB_RESEARCH_TOOL_NAMES: tuple[str, ...] = ("WebSearch", "WebFetch", "KnowledgeSearch")
SOURCE_LEDGER_RESEARCH_TOOL_NAMES: tuple[str, ...] = (
    "SourceLedgerRead",
    "SourceLedgerList",
)
SCOUT_RESEARCH_REPO_FIXTURE_TOOL_NAMES: tuple[str, ...] = (
    "FixtureRepoClone",
    "FixtureRepoOverview",
    "FixtureReferenceRead",
    "FixtureReferenceGrep",
    "FixtureReferenceGlob",
)
SCOUT_RESEARCH_WEB_FIXTURE_TOOL_NAMES: tuple[str, ...] = (
    "FixtureWebSearch",
    "FixtureWebFetch",
)
SCOUT_RESEARCH_FIXTURE_TOOL_NAMES: tuple[str, ...] = (
    *SCOUT_RESEARCH_REPO_FIXTURE_TOOL_NAMES,
    *SCOUT_RESEARCH_WEB_FIXTURE_TOOL_NAMES,
)
SCOUT_RESEARCH_DENIED_CAPABILITIES: tuple[str, ...] = (
    "file write/edit",
    "shell",
    "browser submit",
    "artifact delivery",
    "memory write",
    "workspace mutation",
    "unmanaged external-directory reads",
    "child recursion",
)
_SCOUT_PROMPT_CONTRACT = (
    "Use fixture RepoClone before repository-source inspection, use RepoOverview "
    "before broad search, inspect fixture references with read/grep/glob metadata, "
    "separate verified facts from inference, cite source and evidence refs, and "
    "return uncertainty when a source was not inspected."
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class ResearchToolGrant(BaseModel):
    model_config = _MODEL_CONFIG

    tool_name: str = Field(alias="toolName")
    permission: ResearchToolPermission = "read"
    read_only: bool = Field(default=True, alias="readOnly")
    mutates_workspace: bool = Field(default=False, alias="mutatesWorkspace")
    rationale: str

    @model_validator(mode="after")
    def _validate_readonly_grant(self) -> "ResearchToolGrant":
        if not self.read_only or self.mutates_workspace:
            raise ValueError("research tool grants must be read-only and non-mutating")
        return self


class ResearchAgentSpec(BaseModel):
    model_config = _MODEL_CONFIG

    agent_type: ResearchAgentType = Field(alias="agentType")
    display_name: str = Field(alias="displayName")
    read_only: bool = Field(alias="readOnly")
    spawns_child_agents: bool = Field(alias="spawnsChildAgents")
    tool_grants: tuple[ResearchToolGrant, ...] = Field(default=(), alias="toolGrants")
    min_search_operations: int = Field(default=0, alias="minSearchOperations")
    final_output_contract: str = Field(alias="finalOutputContract")
    final_output_schema: Mapping[str, object] = Field(alias="finalOutputSchema")

    @model_validator(mode="after")
    def _validate_readonly_spec(self) -> "ResearchAgentSpec":
        if not self.read_only and self.tool_grants:
            raise ValueError("research agents with tool grants must be read-only")
        if self.agent_type == "direct" and self.spawns_child_agents:
            raise ValueError("direct research route must not spawn child agents")
        if any(not grant.read_only or grant.mutates_workspace for grant in self.tool_grants):
            raise ValueError("research agent specs may only include read-only grants")
        object.__setattr__(
            self,
            "final_output_schema",
            _freeze_contract_value(self.final_output_schema),
        )
        return self

    @field_serializer("final_output_schema")
    def _serialize_final_output_schema(self, value: Mapping[str, object]) -> object:
        return _thaw_contract_value(value)


class ResearchRouteDecision(BaseModel):
    model_config = _MODEL_CONFIG

    agent_type: ResearchAgentType = Field(alias="agentType")
    route_reason: str = Field(alias="routeReason")
    requires_web_tools: bool = Field(default=False, alias="requiresWebTools")
    matched_signals: tuple[str, ...] = Field(default=(), alias="matchedSignals")
    available_tool_names: tuple[str, ...] = Field(default=(), alias="availableToolNames")


class ResearchMaterializationDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ResearchMaterializationStatus
    agent_type: ResearchAgentType = Field(alias="agentType")
    route_reason: str = Field(alias="routeReason")
    block_reason: ResearchMaterializationBlockReason | None = Field(default=None, alias="blockReason")
    spec: ResearchAgentSpec | None = None
    should_spawn_child_agent: bool = Field(alias="shouldSpawnChildAgent")
    granted_tool_names: tuple[str, ...] = Field(default=(), alias="grantedToolNames")
    adk_tools: tuple[object, ...] = Field(default=(), alias="adkTools")
    attachment_flags: Mapping[str, bool] = Field(
        default_factory=dict,
        alias="attachmentFlags",
    )

    @model_validator(mode="after")
    def _freeze_attachment_flags(self) -> "ResearchMaterializationDecision":
        object.__setattr__(
            self,
            "attachment_flags",
            _freeze_contract_value(self.attachment_flags),
        )
        return self

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, value: Mapping[str, bool]) -> dict[str, bool]:
        return dict(value)


class ScoutResearchToolGrant(FalseOnlyAuthorityModel):
    # C-4 PR-G3: re-parented onto FalseOnlyAuthorityModel. Closes the
    # pre-existing ``model_construct`` leak set of 2 fields
    # (``mutatesWorkspace`` / ``liveExecutionAllowed`` -- raise-to-coerce on
    # validate). Custom ``model_construct`` / ``model_copy`` dropped
    # (``_revalidated_model_copy`` was alias-aware dump-and-revalidate; the
    # kernel's by_alias=True model_copy is equivalent). PRESERVED:
    # ``_validate_scout_grant`` ``@model_validator(mode="after")`` (fixture-
    # name shape guard). ``revalidate_instances="always"`` is dropped
    # (kernel default "never") -- no test depends on this behaviour.
    tool_name: str = Field(alias="toolName")
    permission: ResearchToolPermission = "read"
    read_only: Literal[True] = Field(default=True, alias="readOnly")
    mutates_workspace: Literal[False] = Field(default=False, alias="mutatesWorkspace")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    live_execution_allowed: Literal[False] = Field(
        default=False,
        alias="liveExecutionAllowed",
    )
    rationale: str

    @model_validator(mode="after")
    def _validate_scout_grant(self) -> "ScoutResearchToolGrant":
        if not self.tool_name.startswith("Fixture"):
            raise ValueError("ScoutResearchAgent grants must be fixture-only")
        if self.tool_name not in SCOUT_RESEARCH_FIXTURE_TOOL_NAMES:
            raise ValueError("ScoutResearchAgent grants must use declared fixture tools")
        if not self.rationale.strip():
            raise ValueError("ScoutResearchAgent grant rationale must be non-empty")
        return self


class ScoutResearchAgentProfile(FalseOnlyAuthorityModel):
    # C-4 PR-G3: re-parented onto FalseOnlyAuthorityModel. The kernel handles
    # the 12 ``Literal[False]`` authority/attachment fields uniformly.
    # PRESERVED: ``_validate_profile`` ``@model_validator(mode="after")``
    # (semantic shape guard over tool grants + attachment_flags equality),
    # ``_serialize_attachment_flags`` (non-Literal[False] Mapping field).
    # Custom ``model_construct`` / ``model_copy`` dropped.
    profile_key: Literal["opencode.scout_research_agent"] = Field(
        default="opencode.scout_research_agent",
        alias="profileKey",
    )
    display_name: Literal["ScoutResearchAgent"] = Field(
        default="ScoutResearchAgent",
        alias="displayName",
    )
    tool_grants: tuple[ScoutResearchToolGrant, ...] = Field(alias="toolGrants")
    prompt_contract: Literal[_SCOUT_PROMPT_CONTRACT] = Field(
        default=_SCOUT_PROMPT_CONTRACT,
        alias="promptContract",
    )
    evidence_envelope_contract: Literal["runtime-issued-child-evidence-envelope"] = Field(
        default="runtime-issued-child-evidence-envelope",
        alias="evidenceEnvelopeContract",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(
        default=False,
        alias="functionToolAttached",
    )
    toolhost_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostExecutionAllowed",
    )
    provider_calls_allowed: Literal[False] = Field(
        default=False,
        alias="providerCallsAllowed",
    )
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_output_requires_runtime_envelope: Literal[True] = Field(
        default=True,
        alias="childOutputRequiresRuntimeEnvelope",
    )
    child_summary_is_evidence: Literal[False] = Field(
        default=False,
        alias="childSummaryIsEvidence",
    )
    url_only_citations_allowed: Literal[False] = Field(
        default=False,
        alias="urlOnlyCitationsAllowed",
    )
    raw_source_projection_allowed: Literal[False] = Field(
        default=False,
        alias="rawSourceProjectionAllowed",
    )
    raw_child_output_projection_allowed: Literal[False] = Field(
        default=False,
        alias="rawChildOutputProjectionAllowed",
    )
    adk_tools: tuple[object, ...] = Field(default=(), alias="adkTools")
    attachment_flags: Mapping[str, bool] = Field(default_factory=dict, alias="attachmentFlags")

    @model_validator(mode="after")
    def _validate_profile(self) -> "ScoutResearchAgentProfile":
        names = tuple(grant.tool_name for grant in self.tool_grants)
        if names != SCOUT_RESEARCH_FIXTURE_TOOL_NAMES:
            raise ValueError("ScoutResearchAgent toolGrants must match fixture-only grants")
        if self.adk_tools:
            raise ValueError("ScoutResearchAgent profile must not attach ADK tools")
        expected_flags = _attachment_flags(
            attach_enabled=bool(self.attachment_flags.get("attachEnabled")),
            adk_tools_built=False,
        )
        if dict(self.attachment_flags) != expected_flags:
            raise ValueError("ScoutResearchAgent attachmentFlags must be deterministic")
        object.__setattr__(
            self,
            "attachment_flags",
            _freeze_contract_value(expected_flags),
        )
        return self

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, value: Mapping[str, bool]) -> dict[str, bool]:
        return dict(value)


class ScoutResearchAgentRecipeDecision(FalseOnlyAuthorityModel):
    # C-4 PR-G3: re-parented onto FalseOnlyAuthorityModel (same rationale as
    # ScoutResearchAgentProfile). PRESERVED: ``_validate_decision``
    # ``@model_validator(mode="after")`` (rich semantic guard over status /
    # tool grants / prompt contract / denied capabilities / reason codes),
    # the attachment_flags serializer. Custom ``model_construct`` /
    # ``model_copy`` dropped.
    status: ScoutResearchRecipeStatus
    profile_key: ScoutResearchRecipeProfileKey = Field(alias="profileKey")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    tool_grants: tuple[ScoutResearchToolGrant, ...] = Field(
        default=(),
        alias="toolGrants",
    )
    granted_tool_names: tuple[str, ...] = Field(default=(), alias="grantedToolNames")
    prompt_contract: str = Field(default=_SCOUT_PROMPT_CONTRACT, alias="promptContract")
    denied_capabilities: tuple[str, ...] = Field(
        default=SCOUT_RESEARCH_DENIED_CAPABILITIES,
        alias="deniedCapabilities",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(
        default=False,
        alias="functionToolAttached",
    )
    toolhost_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostExecutionAllowed",
    )
    provider_calls_allowed: Literal[False] = Field(
        default=False,
        alias="providerCallsAllowed",
    )
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_output_requires_runtime_envelope: Literal[True] = Field(
        default=True,
        alias="childOutputRequiresRuntimeEnvelope",
    )
    child_summary_is_evidence: Literal[False] = Field(
        default=False,
        alias="childSummaryIsEvidence",
    )
    url_only_citations_allowed: Literal[False] = Field(
        default=False,
        alias="urlOnlyCitationsAllowed",
    )
    raw_source_projection_allowed: Literal[False] = Field(
        default=False,
        alias="rawSourceProjectionAllowed",
    )
    raw_child_output_projection_allowed: Literal[False] = Field(
        default=False,
        alias="rawChildOutputProjectionAllowed",
    )
    adk_tools: tuple[object, ...] = Field(default=(), alias="adkTools")
    attachment_flags: Mapping[str, bool] = Field(default_factory=dict, alias="attachmentFlags")

    @model_validator(mode="after")
    def _validate_decision(self) -> "ScoutResearchAgentRecipeDecision":
        if self.prompt_contract != _SCOUT_PROMPT_CONTRACT:
            raise ValueError("ScoutResearchAgent promptContract must be deterministic")
        if self.denied_capabilities != SCOUT_RESEARCH_DENIED_CAPABILITIES:
            raise ValueError("ScoutResearchAgent deniedCapabilities must be deterministic")
        if self.status != "ready" and (self.tool_grants or self.granted_tool_names):
            raise ValueError("non-ready ScoutResearchAgent decisions must not grant tools")
        grant_names = tuple(grant.tool_name for grant in self.tool_grants)
        if self.granted_tool_names != grant_names:
            raise ValueError("ScoutResearchAgent grantedToolNames must match toolGrants")
        if self.status == "ready":
            expected_tool_names = _scout_profile_tool_names(self.profile_key)
            if self.granted_tool_names != expected_tool_names:
                raise ValueError("ScoutResearchAgent ready decisions must match profile tool set")
        if self.profile_key == "scout_external_repo" and self.status == "ready":
            raise ValueError("scout_external_repo cannot be ready in fixture-only plan")
        if self.reason_codes not in _allowed_scout_reason_codes(
            status=self.status,
            profile_key=self.profile_key,
        ):
            raise ValueError("ScoutResearchAgent reasonCodes are not allowed for profile status")
        if self.adk_tools:
            raise ValueError("ScoutResearchAgent recipe must not attach ADK tools")
        expected_flags = _attachment_flags(attach_enabled=False, adk_tools_built=False)
        if dict(self.attachment_flags) != expected_flags:
            raise ValueError("ScoutResearchAgent attachmentFlags must remain false")
        object.__setattr__(
            self,
            "attachment_flags",
            _freeze_contract_value(expected_flags),
        )
        return self

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, value: Mapping[str, bool]) -> dict[str, bool]:
        return dict(value)


def materialize_research_agent(
    route: ResearchAgentType | ResearchRouteDecision | str,
    *,
    available_tools: Iterable[object] = (),
    attach_enabled: bool = False,
    registry: object | None = None,
    dispatcher: object | None = None,
    mode: str = "plan",
    tool_context_factory: object | None = None,
) -> ResearchMaterializationDecision:
    route_decision = _coerce_route(route, available_tools=available_tools)
    supplied_tool_names = _available_tool_names(available_tools)
    available_tool_names = supplied_tool_names or route_decision.available_tool_names

    if route_decision.agent_type == "direct":
        return ResearchMaterializationDecision(
            status="direct",
            agentType="direct",
            routeReason=route_decision.route_reason,
            spec=_direct_spec(),
            shouldSpawnChildAgent=False,
            grantedToolNames=(),
            adkTools=(),
            attachmentFlags=_attachment_flags(attach_enabled=attach_enabled, adk_tools_built=False),
        )

    if route_decision.requires_web_tools and not set(available_tool_names).intersection(WEB_RESEARCH_TOOL_NAMES):
        return ResearchMaterializationDecision(
            status="blocked",
            agentType=route_decision.agent_type,
            routeReason=route_decision.route_reason,
            blockReason="missing_web_tools",
            spec=None,
            shouldSpawnChildAgent=False,
            grantedToolNames=(),
            adkTools=(),
            attachmentFlags=_attachment_flags(attach_enabled=attach_enabled, adk_tools_built=False),
        )

    spec = _research_agent_spec(route_decision.agent_type, available_tool_names)
    granted_tool_names = tuple(grant.tool_name for grant in spec.tool_grants)
    adk_tools: tuple[object, ...] = ()
    if attach_enabled:
        if registry is None or dispatcher is None or tool_context_factory is None:
            return ResearchMaterializationDecision(
                status="blocked",
                agentType=route_decision.agent_type,
                routeReason=route_decision.route_reason,
                blockReason="missing_attachment_inputs",
                spec=spec,
                shouldSpawnChildAgent=False,
                grantedToolNames=granted_tool_names,
                adkTools=(),
                attachmentFlags=_attachment_flags(
                    attach_enabled=attach_enabled,
                    adk_tools_built=False,
                ),
            )
        tool_adapter = importlib.import_module(
            "magi_agent.adk_bridge.tool_adapter"
        )
        adk_tools = tuple(
            tool_adapter.build_adk_function_tools_for_granted_names(
                registry,
                dispatcher,
                mode=mode,  # type: ignore[arg-type]
                tool_context_factory=tool_context_factory,  # type: ignore[arg-type]
                granted_tool_names=granted_tool_names,
                attach_enabled=True,
            )
        )

    return ResearchMaterializationDecision(
        status="ready",
        agentType=route_decision.agent_type,
        routeReason=route_decision.route_reason,
        spec=spec,
        shouldSpawnChildAgent=True,
        grantedToolNames=granted_tool_names,
        adkTools=adk_tools,
        attachmentFlags=_attachment_flags(
            attach_enabled=attach_enabled,
            adk_tools_built=bool(adk_tools),
        ),
    )


def materialize_scout_research_profile(
    *,
    available_tools: Iterable[object] = (),
    attach_enabled: bool = False,
    require_fixture_tools: bool = False,
) -> ScoutResearchAgentProfile:
    available_tool_names = _available_tool_names(available_tools)
    grant_names = SCOUT_RESEARCH_FIXTURE_TOOL_NAMES
    if require_fixture_tools:
        available = set(available_tool_names)
        grant_names = tuple(
            name for name in SCOUT_RESEARCH_FIXTURE_TOOL_NAMES if name in available
        )
    grants = tuple(_scout_tool_grant(name) for name in grant_names)
    return ScoutResearchAgentProfile(
        toolGrants=grants,
        adkTools=(),
        attachmentFlags=_attachment_flags(
            attach_enabled=attach_enabled,
            adk_tools_built=False,
        ),
    )


def materialize_scout_research_agent(
    *,
    profile_key: ScoutResearchRecipeProfileKey = "scout_repo_fixture",
    rollout_enabled: bool = False,
    web_provider_boundary_enabled: bool = False,
    available_tools: Iterable[object] = (),
    require_fixture_tools: bool = True,
) -> ScoutResearchAgentRecipeDecision:
    if not rollout_enabled:
        return _scout_recipe_decision(
            status="disabled",
            profile_key=profile_key,
            reason_codes=("rollout_gate_disabled",),
            tool_names=(),
        )

    if profile_key == "scout_external_repo":
        return _scout_recipe_decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("live_network_not_allowed",),
            tool_names=(),
        )

    if profile_key == "scout_web_docs" and not web_provider_boundary_enabled:
        return _scout_recipe_decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("web_provider_boundary_disabled",),
            tool_names=(),
        )

    tool_names = _scout_profile_tool_names(profile_key)
    if require_fixture_tools:
        available = set(_available_tool_names(available_tools))
        if not set(tool_names).issubset(available):
            return _scout_recipe_decision(
                status="blocked",
                profile_key=profile_key,
                reason_codes=("missing_fixture_tools",),
                tool_names=(),
            )

    return _scout_recipe_decision(
        status="ready",
        profile_key=profile_key,
        reason_codes=(
            ("fake_provider_profile_only",)
            if profile_key == "scout_web_docs"
            else ("local_fixture_profile_only",)
        ),
        tool_names=tool_names,
    )


def _coerce_route(
    route: ResearchAgentType | ResearchRouteDecision | str,
    *,
    available_tools: Iterable[object],
) -> ResearchRouteDecision:
    if isinstance(route, ResearchRouteDecision):
        return route
    if route not in {"direct", "explore", "plan", "verifier"}:
        raise ValueError(f"unsupported research route: {route}")
    return ResearchRouteDecision(
        agentType=route,
        routeReason=f"{route}_route",
        availableToolNames=_available_tool_names(available_tools),
    )


def _research_agent_spec(
    agent_type: ResearchAgentType,
    available_tool_names: tuple[str, ...],
) -> ResearchAgentSpec:
    if agent_type == "direct":
        return _direct_spec()
    tool_names = _granted_research_tool_names(agent_type, available_tool_names)
    grants = tuple(_tool_grant(name) for name in tool_names)
    if agent_type == "explore":
        return ResearchAgentSpec(
            agentType="explore",
            displayName="ExploreResearchAgent",
            readOnly=True,
            spawnsChildAgents=True,
            toolGrants=grants,
            minSearchOperations=3,
            finalOutputContract="findings with file/source refs and uncertainty",
            finalOutputSchema={
                "type": "object",
                "required": ("findings", "refs", "uncertainty"),
                "properties": {
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "refs": {"type": "array", "items": {"type": "string"}},
                    "uncertainty": {"type": "string"},
                },
            },
        )
    if agent_type == "plan":
        return ResearchAgentSpec(
            agentType="plan",
            displayName="PlanResearchAgent",
            readOnly=True,
            spawnsChildAgents=True,
            toolGrants=grants,
            minSearchOperations=3,
            finalOutputContract="implementation/research plan with critical files/sources",
            finalOutputSchema={
                "type": "object",
                "required": ("plan", "criticalFiles", "sourceRefs"),
                "properties": {
                    "plan": {"type": "array", "items": {"type": "string"}},
                    "criticalFiles": {"type": "array", "items": {"type": "string"}},
                    "sourceRefs": {"type": "array", "items": {"type": "string"}},
                    "uncertainty": {"type": "string"},
                },
            },
        )
    if agent_type == "verifier":
        return ResearchAgentSpec(
            agentType="verifier",
            displayName="VerifierResearchAgent",
            readOnly=True,
            spawnsChildAgents=True,
            toolGrants=grants,
            minSearchOperations=1,
            finalOutputContract="PASS, FAIL, or PARTIAL with evidence refs",
            finalOutputSchema={
                "type": "object",
                "required": ("status", "evidenceRefs"),
                "properties": {
                    "status": {"type": "string", "enum": ("PASS", "FAIL", "PARTIAL")},
                    "evidenceRefs": {"type": "array", "items": {"type": "string"}},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                },
            },
        )
    raise ValueError(f"unsupported research agent type: {agent_type}")


def _direct_spec() -> ResearchAgentSpec:
    return ResearchAgentSpec(
        agentType="direct",
        displayName="DirectResearchRoute",
        readOnly=True,
        spawnsChildAgents=False,
        toolGrants=(),
        minSearchOperations=0,
        finalOutputContract="answer directly in the parent turn without spawning child agents",
        finalOutputSchema={
            "type": "object",
            "required": ("route",),
            "properties": {"route": {"const": "direct"}},
        },
    )


def _granted_research_tool_names(
    agent_type: ResearchAgentType,
    available_tool_names: tuple[str, ...],
) -> tuple[str, ...]:
    allowed = [*LOCAL_RESEARCH_TOOL_NAMES, *WEB_RESEARCH_TOOL_NAMES]
    if agent_type == "verifier":
        allowed.extend(SOURCE_LEDGER_RESEARCH_TOOL_NAMES)
    available = set(available_tool_names)
    return tuple(name for name in allowed if name in available)


def _tool_grant(tool_name: str) -> ResearchToolGrant:
    return ResearchToolGrant(
        toolName=tool_name,
        permission="net" if tool_name in WEB_RESEARCH_TOOL_NAMES else "read",
        readOnly=True,
        mutatesWorkspace=False,
        rationale="read-only research source acquisition"
        if tool_name in WEB_RESEARCH_TOOL_NAMES
        else "read-only local research inspection",
    )


def _scout_tool_grant(tool_name: str) -> ScoutResearchToolGrant:
    return ScoutResearchToolGrant(
        toolName=tool_name,
        permission="net"
        if tool_name in {"FixtureWebSearch", "FixtureWebFetch"}
        else "read",
        readOnly=True,
        mutatesWorkspace=False,
        fixtureOnly=True,
        liveExecutionAllowed=False,
        rationale="fixture-only external source acquisition metadata"
        if tool_name in {"FixtureRepoClone", "FixtureWebSearch", "FixtureWebFetch"}
        else "fixture-only external source inspection metadata",
    )


def _scout_profile_tool_names(
    profile_key: ScoutResearchRecipeProfileKey,
) -> tuple[str, ...]:
    if profile_key == "scout_repo_fixture":
        return SCOUT_RESEARCH_REPO_FIXTURE_TOOL_NAMES
    if profile_key == "scout_web_docs":
        return SCOUT_RESEARCH_WEB_FIXTURE_TOOL_NAMES
    if profile_key == "scout_external_repo":
        return ()
    raise ValueError(f"unsupported ScoutResearchAgent profile: {profile_key}")


def _allowed_scout_reason_codes(
    *,
    status: ScoutResearchRecipeStatus,
    profile_key: ScoutResearchRecipeProfileKey,
) -> tuple[tuple[str, ...], ...]:
    if status == "disabled":
        return (("rollout_gate_disabled",),)
    if status == "ready":
        if profile_key == "scout_repo_fixture":
            return (("local_fixture_profile_only",),)
        if profile_key == "scout_web_docs":
            return (("fake_provider_profile_only",),)
        return ()
    if profile_key == "scout_external_repo":
        return (("live_network_not_allowed",),)
    if profile_key == "scout_web_docs":
        return (("web_provider_boundary_disabled",), ("missing_fixture_tools",))
    if profile_key == "scout_repo_fixture":
        return (("missing_fixture_tools",),)
    return ()


def _scout_recipe_decision(
    *,
    status: ScoutResearchRecipeStatus,
    profile_key: ScoutResearchRecipeProfileKey,
    reason_codes: tuple[str, ...],
    tool_names: tuple[str, ...],
) -> ScoutResearchAgentRecipeDecision:
    return ScoutResearchAgentRecipeDecision(
        status=status,
        profileKey=profile_key,
        reasonCodes=reason_codes,
        toolGrants=tuple(_scout_tool_grant(name) for name in tool_names),
        grantedToolNames=tool_names,
        promptContract=_SCOUT_PROMPT_CONTRACT,
        deniedCapabilities=SCOUT_RESEARCH_DENIED_CAPABILITIES,
        adkTools=(),
        attachmentFlags=_attachment_flags(attach_enabled=False, adk_tools_built=False),
    )


def _available_tool_names(available_tools: Iterable[object]) -> tuple[str, ...]:
    names: list[str] = []
    for item in available_tools:
        name = _tool_name(item)
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _tool_name(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        raw_name = item.get("name")
        return raw_name if isinstance(raw_name, str) else None
    raw_name = getattr(item, "name", None)
    return raw_name if isinstance(raw_name, str) else None


def _attachment_flags(*, attach_enabled: bool, adk_tools_built: bool) -> dict[str, bool]:
    return {
        "attachEnabled": attach_enabled,
        "adkFunctionToolsBuilt": adk_tools_built,
        "routeAttached": False,
        "productionAttached": False,
        "providerCalled": False,
        "userVisibleOutputAllowed": False,
        "writeMutationAllowed": False,
        "shellExecutionAllowed": False,
    }


def _freeze_contract_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze_contract_value(nested_value)
                for key, nested_value in sorted(value.items(), key=lambda item: str(item[0]))
            }
        )
    if isinstance(value, list | tuple) and not isinstance(value, bytes | bytearray):
        return tuple(_freeze_contract_value(item) for item in value)
    return value


def _thaw_contract_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_contract_value(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_thaw_contract_value(item) for item in value)
    return value


def _alias_updates(model_class: type[BaseModel], update: Mapping[str, Any]) -> dict[str, Any]:
    alias_to_name = {
        field.alias: name
        for name, field in model_class.model_fields.items()
        if field.alias is not None
    }
    return {alias_to_name.get(key, key): value for key, value in update.items()}


def _revalidated_model_copy(
    model: BaseModel,
    *,
    update: Mapping[str, Any] | None,
) -> Any:
    data = model.model_dump(by_alias=False, mode="python", warnings=False)
    if update:
        data.update(_alias_updates(model.__class__, update))
    return model.__class__.model_validate(data)


__all__ = [
    "LOCAL_RESEARCH_TOOL_NAMES",
    "ResearchAgentSpec",
    "ResearchAgentType",
    "ResearchMaterializationDecision",
    "ResearchRouteDecision",
    "ResearchToolGrant",
    "SCOUT_RESEARCH_FIXTURE_TOOL_NAMES",
    "SCOUT_RESEARCH_REPO_FIXTURE_TOOL_NAMES",
    "SCOUT_RESEARCH_WEB_FIXTURE_TOOL_NAMES",
    "ScoutResearchAgentProfile",
    "ScoutResearchAgentRecipeDecision",
    "ScoutResearchRecipeProfileKey",
    "ScoutResearchRecipeStatus",
    "ScoutResearchToolGrant",
    "WEB_RESEARCH_TOOL_NAMES",
    "materialize_research_agent",
    "materialize_scout_research_agent",
    "materialize_scout_research_profile",
]
