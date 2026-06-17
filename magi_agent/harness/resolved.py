from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Self, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.evidence.rollout import EvidenceRolloutMetadata
from magi_agent.harness.general_automation.constraint_reinjection import (
    GA_CONSTRAINT_REINJECTION_HOOK_NAME,
)
from magi_agent.harness.general_automation.question_tool import (
    GENERAL_AUTOMATION_QUESTION_TOOL_NAME,
)
from magi_agent.harness.general_automation.recipe_disclosure import (
    LOAD_GA_RECIPE_TOOL_NAME,
)
from magi_agent.evidence.types import (
    EvidenceContractScopeMetadata,
    EvidenceEnforcement,
    EvidenceSpawnDepthRange,
)
from magi_agent.harness.evidence_scope import (
    EvidenceContractScope,
    EvidenceScopeContext,
    EvidenceScopeDecision,
    RunOn,
    resolve_evidence_scope,
)
from magi_agent.harness.kernel_roles import (
    FIRST_PARTY_AGENT_ROLE_IDS,
    known_agent_role_ids,
)

if TYPE_CHECKING:
    from magi_agent.hooks.manifest import HookManifest
    from magi_agent.hooks.scope import HookScopeContext


PresetSource = Literal["builtin", "native-plugin", "custom-plugin", "config"]
EvidenceSkipReason = Literal[
    "agent_role_mismatch",
    "run_on_mismatch",
    "spawn_depth_mismatch",
    "opted_out",
]
EvidenceRolloutMode = Literal["audit", "block_final_answer"]
EvidenceVerdictReadinessStatus = Literal["not_evaluated"]


_RESOLVED_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)
_ResolvedModelT = TypeVar("_ResolvedModelT", bound=BaseModel)


def _validate_agent_role_value(value: str) -> str:
    """Accept a first-party role, or — when the kernel role-provides flag is ON —
    a discovered ``ext.<name>`` role. With the flag OFF this admits exactly the
    three first-party roles, so validation is byte-identical to the prior
    ``AgentRole`` Literal.
    """

    if value not in known_agent_role_ids():
        raise ValueError(f"unknown agent role: {value!r}")
    return value


class _ResolvedAgentRoleInput(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    agent_role: str = Field(alias="agentRole")

    @field_validator("agent_role")
    @classmethod
    def _check_agent_role(cls, value: str) -> str:
        return _validate_agent_role_value(value)


class _ResolvedHarnessModel(BaseModel):
    model_config = _RESOLVED_MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update(
                {
                    alias_to_name.get(key, key): _copy_update_value_for_validation(value)
                    for key, value in update.items()
                }
            )
        return self.__class__.model_validate(data)


def _copy_update_value_for_validation(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=False, mode="python", warnings=False)
    if isinstance(value, tuple):
        return tuple(_copy_update_value_for_validation(item) for item in value)
    if isinstance(value, list):
        return [_copy_update_value_for_validation(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _copy_update_value_for_validation(nested)
            for key, nested in value.items()
        }
    return value


def _revalidate_nested_model(
    value: _ResolvedModelT,
    model_type: type[_ResolvedModelT],
) -> _ResolvedModelT:
    if isinstance(value, model_type):
        return model_type.model_validate(
            value.model_dump(by_alias=False, mode="python", warnings=False)
        )
    return value


class ResolvedHarnessPack(_ResolvedHarnessModel):
    enabled: bool
    source: PresetSource = "builtin"
    components: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    opt_out_allowed: tuple[str, ...] = Field(default=(), alias="optOutAllowed")

    @field_validator("components")
    @classmethod
    def _freeze_components(
        cls,
        value: Mapping[str, tuple[str, ...]],
    ) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType({key: tuple(items) for key, items in value.items()})

    @field_serializer("components")
    def _serialize_components(
        self,
        value: Mapping[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        return dict(value)


class ResolvedHardSafety(_ResolvedHarnessModel):
    protected_gates: tuple[str, ...] = Field(alias="protectedGates")
    opt_out: bool = Field(default=False, alias="optOut")


class SkippedEvidenceContract(_ResolvedHarnessModel):
    contract_id: str = Field(alias="contractId")
    reason: EvidenceSkipReason
    effective_enforcement: Literal["off"] = Field(default="off", alias="effectiveEnforcement")


class EvidenceVerdictReadinessMetadata(_ResolvedHarnessModel):
    status: EvidenceVerdictReadinessStatus = "not_evaluated"
    ready_contract_ids: tuple[str, ...] = Field(default=(), alias="readyContractIds")
    pending_contract_ids: tuple[str, ...] = Field(default=(), alias="pendingContractIds")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @model_validator(mode="before")
    @classmethod
    def _reject_attached_input(cls, data: object) -> object:
        if isinstance(data, Mapping) and (
            data.get("traffic_attached")
            or data.get("trafficAttached")
            or data.get("execution_attached")
            or data.get("executionAttached")
        ):
            raise ValueError("evidence verdict readiness metadata must stay traffic-free")
        return data


class ResolvedEvidenceContractSnapshot(_ResolvedHarnessModel):
    contract_id: str = Field(alias="contractId")
    applies: bool
    effective_enforcement: EvidenceEnforcement = Field(alias="effectiveEnforcement")
    enforcement_enabled: bool = Field(alias="enforcementEnabled")
    opt_out_applied: bool = Field(alias="optOutApplied")
    hard_safety: bool = Field(alias="hardSafety")
    failure_channel: Literal["evidence_contract"] = Field(
        default="evidence_contract",
        alias="failureChannel",
    )
    research_citation_gate: Literal[False] = Field(
        default=False,
        alias="researchCitationGate",
    )
    skip_reason: EvidenceSkipReason | None = Field(default=None, alias="skipReason")
    rollout: EvidenceRolloutMetadata
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @model_validator(mode="before")
    @classmethod
    def _reject_attached_input(cls, data: object) -> object:
        if isinstance(data, Mapping) and (
            data.get("traffic_attached")
            or data.get("trafficAttached")
            or data.get("execution_attached")
            or data.get("executionAttached")
        ):
            raise ValueError("resolved evidence snapshots must stay traffic-free")
        return data

    @model_validator(mode="after")
    def _validate_snapshot_policy(self) -> Self:
        if self.research_citation_gate:
            raise ValueError("resolved evidence snapshots must not attach research citation gates")
        if self.enforcement_enabled != (self.effective_enforcement != "off"):
            raise ValueError("enforcementEnabled must match effectiveEnforcement")
        if not self.applies and self.effective_enforcement != "off":
            raise ValueError("effectiveEnforcement must be off when applies=false")
        if self.opt_out_applied and self.effective_enforcement != "off":
            raise ValueError("optOutApplied requires effectiveEnforcement=off")
        if self.opt_out_applied and self.hard_safety:
            raise ValueError("optOutApplied cannot be true for hard-safety evidence")
        return self

    @field_validator("rollout")
    @classmethod
    def _revalidate_rollout(
        cls,
        value: EvidenceRolloutMetadata,
    ) -> EvidenceRolloutMetadata:
        return _revalidate_nested_model(value, EvidenceRolloutMetadata)


class ResolvedHarnessPresetState(_ResolvedHarnessModel):
    profile_name: str = Field(alias="profileName")
    run_on: RunOn = Field(default="main", alias="runOn")
    agent_role: str = Field(default="general", alias="agentRole")
    spawn_depth: int = Field(default=0, alias="spawnDepth")
    general: ResolvedHarnessPack
    coding: ResolvedHarnessPack
    research: ResolvedHarnessPack
    verification: ResolvedHarnessPack
    hard_safety: ResolvedHardSafety = Field(alias="hardSafety")
    effective_hooks: tuple[str, ...] = Field(default=(), alias="effectiveHooks")
    effective_harness_packs: tuple[str, ...] = Field(default=(), alias="effectiveHarnessPacks")
    skipped_by_scope: tuple[str, ...] = Field(default=(), alias="skippedByScope")
    evidence_contracts: tuple[ResolvedEvidenceContractSnapshot, ...] = Field(
        default=(),
        alias="evidenceContracts",
    )
    effective_evidence_contracts: tuple[str, ...] = Field(
        default=(),
        alias="effectiveEvidenceContracts",
    )
    skipped_evidence_contracts: tuple[SkippedEvidenceContract, ...] = Field(
        default=(),
        alias="skippedEvidenceContracts",
    )
    evidence_verdict_readiness: EvidenceVerdictReadinessMetadata = Field(
        default_factory=EvidenceVerdictReadinessMetadata,
        alias="evidenceVerdictReadiness",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @model_validator(mode="before")
    @classmethod
    def _reject_attached_input(cls, data: object) -> object:
        if isinstance(data, Mapping) and (
            data.get("traffic_attached")
            or data.get("trafficAttached")
            or data.get("execution_attached")
            or data.get("executionAttached")
        ):
            raise ValueError("resolved harness state must stay traffic-free")
        return data

    @model_validator(mode="after")
    def _validate_run_depth_pair(self) -> Self:
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main runs must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child runs must use spawnDepth greater than 0")
        return self

    @field_validator("evidence_contracts")
    @classmethod
    def _revalidate_evidence_contracts(
        cls,
        value: tuple[ResolvedEvidenceContractSnapshot, ...],
    ) -> tuple[ResolvedEvidenceContractSnapshot, ...]:
        return tuple(
            _revalidate_nested_model(snapshot, ResolvedEvidenceContractSnapshot)
            for snapshot in value
        )

    @field_validator("evidence_verdict_readiness")
    @classmethod
    def _revalidate_evidence_verdict_readiness(
        cls,
        value: EvidenceVerdictReadinessMetadata,
    ) -> EvidenceVerdictReadinessMetadata:
        return _revalidate_nested_model(value, EvidenceVerdictReadinessMetadata)

    @field_validator("agent_role")
    @classmethod
    def _check_agent_role(cls, value: str) -> str:
        return _validate_agent_role_value(value)

    def hook_scope_context(self) -> HookScopeContext:
        from magi_agent.hooks.scope import HookScopeContext

        return HookScopeContext(
            run_on=self.run_on,
            agent_role=self.agent_role,
            spawn_depth=self.spawn_depth,
        )


def build_default_resolved_harness_state(
    *,
    agent_role: str = "general",
    spawn_depth: int = 0,
    run_on: RunOn | None = None,
    evidence_contracts: tuple[EvidenceContractScope, ...] = (),
    opted_out_evidence_contract_ids: tuple[str, ...] = (),
    evidence_rollout_mode: EvidenceRolloutMode = "audit",
) -> ResolvedHarnessPresetState:
    resolved_agent_role = _validate_resolved_agent_role(agent_role)
    resolved_run_on: RunOn = run_on or ("child" if spawn_depth > 0 else "main")
    (
        evidence_snapshots,
        effective_evidence_contracts,
        skipped_evidence_contracts,
        evidence_verdict_readiness,
    ) = resolve_evidence_contract_snapshots(
        evidence_contracts,
        agent_role=resolved_agent_role,
        run_on=resolved_run_on,
        spawn_depth=spawn_depth,
        opted_out_contract_ids=opted_out_evidence_contract_ids,
        rollout_mode=evidence_rollout_mode,
    )

    return ResolvedHarnessPresetState(
        profile_name="openmagi-opinionated",
        run_on=resolved_run_on,
        agent_role=resolved_agent_role,
        spawn_depth=spawn_depth,
        general=ResolvedHarnessPack(
            enabled=True,
            components={
                "tools": (
                    "FileRead",
                    "WebSearch",
                    "WebFetch",
                    "GeneralAutomationShellRequest",
                    "CSVRead",
                    "SpreadsheetPreview",
                    "BrowserAction",
                    # PR7: blocking clarifying-question tool (declaration only —
                    # the handler is flag-gated and inert by default; see
                    # harness/general_automation/question_tool).
                    GENERAL_AUTOMATION_QUESTION_TOOL_NAME,
                    # PR8: on-demand recipe/playbook load tool (declaration only
                    # — the handler is flag-gated and inert by default; its
                    # tool-result is compaction-protected; see
                    # harness/general_automation/recipe_disclosure).
                    LOAD_GA_RECIPE_TOOL_NAME,
                ),
                # GA-scoped hooks (PR6): per-turn constraint re-injection.
                # Declaration only — the handler is flag-gated and inert by
                # default (see harness/general_automation/constraint_reinjection).
                "hooks": (GA_CONSTRAINT_REINJECTION_HOOK_NAME,),
                "childAgent": (),
                "permissionDefaults": (
                    "write_requires_approval",
                    "external_directory_requires_approval",
                ),
            },
        ),
        coding=ResolvedHarnessPack(
            enabled=True,
            components={
                "tools": ("FileRead", "FileEdit", "PatchApply"),
                "hooks": ("coding-verification", "completion-evidence"),
                "childAgent": ("coding-child-review",),
                "permissionDefaults": ("write_requires_act",),
            },
            opt_out_allowed=("tddRequired", "childReview", "benchmarkVerifier", "contextInjector"),
        ),
        research=ResolvedHarnessPack(
            enabled=True,
            components={
                "tools": ("WebSearch", "WebFetch", "KnowledgeSearch"),
                "hooks": ("source-authority", "claim-citation", "fact-grounding"),
                "ledgers": ("source-ledger",),
                "delivery": ("citation-required",),
            },
            opt_out_allowed=("citationRequired", "factGrounding", "parallelResearch", "claimCitation"),
        ),
        verification=ResolvedHarnessPack(
            enabled=True,
            components={
                "verifierGates": ("answer-quality", "self-claim", "deterministic-evidence"),
            },
            opt_out_allowed=("answerQuality", "selfClaim", "deterministicEvidence"),
        ),
        hard_safety=ResolvedHardSafety(
            protected_gates=(
                "permission-arbiter",
                "path-safety",
                "secret-safety",
                "sealed-file-policy",
                "git-safety",
            ),
        ),
        effective_harness_packs=_default_effective_harness_packs(
            run_on=resolved_run_on,
            agent_role=resolved_agent_role,
        ),
        evidence_contracts=evidence_snapshots,
        effective_evidence_contracts=effective_evidence_contracts,
        skipped_evidence_contracts=skipped_evidence_contracts,
        evidence_verdict_readiness=evidence_verdict_readiness,
    )


def _validate_resolved_agent_role(agent_role: str) -> str:
    return _ResolvedAgentRoleInput(agentRole=agent_role).agent_role


def _default_effective_harness_packs(*, run_on: RunOn, agent_role: str) -> tuple[str, ...]:
    if run_on == "child":
        # Data-driven over the kernel role registry: a known role (first-party, or
        # an external ext.<name> role when the flag is ON) gets its own scope
        # bucket; hard-safety is ALWAYS appended so an external role can never
        # shed it.
        if agent_role in known_agent_role_ids():
            return (agent_role, "hard-safety")
        return ("hard-safety",)
    return ("general", "coding", "research", "verification", "hard-safety")


def resolve_scoped_harness_hooks(
    hooks: tuple[HookManifest, ...],
    state: ResolvedHarnessPresetState,
) -> tuple[tuple[HookManifest, ...], ResolvedHarnessPresetState]:
    context = state.hook_scope_context()
    selected: list[HookManifest] = []
    skipped_by_scope: list[str] = []

    for hook in hooks:
        if hook.security_critical or hook.scope.applies_to(context):
            selected.append(hook)
        else:
            skipped_by_scope.append(hook.name)

    resolved_state = state.model_copy(
        update={
            "effective_hooks": tuple(hook.name for hook in selected),
            "skipped_by_scope": tuple(skipped_by_scope),
        }
    )
    return tuple(selected), resolved_state


def filter_hooks_for_harness(
    hooks: tuple[HookManifest, ...],
    state: ResolvedHarnessPresetState,
) -> tuple[HookManifest, ...]:
    selected, _ = resolve_scoped_harness_hooks(hooks, state)
    return selected


def resolve_evidence_contract_snapshots(
    evidence_contracts: tuple[EvidenceContractScope, ...],
    *,
    agent_role: str,
    run_on: RunOn,
    spawn_depth: int,
    opted_out_contract_ids: tuple[str, ...] = (),
    rollout_mode: EvidenceRolloutMode = "audit",
) -> tuple[
    tuple[ResolvedEvidenceContractSnapshot, ...],
    tuple[str, ...],
    tuple[SkippedEvidenceContract, ...],
    EvidenceVerdictReadinessMetadata,
]:
    if not evidence_contracts:
        return (
            (),
            (),
            (),
            EvidenceVerdictReadinessMetadata(),
        )

    context = _build_evidence_scope_context(
        agent_role=agent_role,
        run_on=run_on,
        spawn_depth=spawn_depth,
    )
    snapshots: list[ResolvedEvidenceContractSnapshot] = []
    effective_contract_ids: list[str] = []
    skipped_contracts: list[SkippedEvidenceContract] = []

    for contract in evidence_contracts:
        validated_contract = EvidenceContractScope.model_validate(
            contract.model_dump(by_alias=True, warnings=False)
        )
        decision = _resolve_evidence_decision(
            validated_contract,
            context,
            opted_out_contract_ids=opted_out_contract_ids,
        )
        skip_reason = _evidence_skip_reason(
            validated_contract,
            context,
            decision,
            opted_out_contract_ids=opted_out_contract_ids,
        )
        rollout = EvidenceRolloutMetadata(
            mode=rollout_mode,
            auditBeforeBlock=validated_contract.audit_before_block,
            blockModeEnabledForLiveTraffic=False,
            scope=_scope_metadata_for_contract(validated_contract),
        )
        snapshots.append(
            ResolvedEvidenceContractSnapshot(
                contractId=validated_contract.contract_id,
                applies=decision.applies,
                effectiveEnforcement=decision.effective_enforcement,
                enforcementEnabled=decision.enforcement_enabled,
                optOutApplied=decision.opt_out_applied,
                hardSafety=decision.hard_safety,
                failureChannel=decision.failure_channel,
                researchCitationGate=False,
                skipReason=skip_reason,
                rollout=rollout,
                trafficAttached=False,
                executionAttached=False,
            )
        )
        if skip_reason is None:
            if decision.enforcement_enabled:
                effective_contract_ids.append(validated_contract.contract_id)
            continue
        skipped_contracts.append(
            SkippedEvidenceContract(
                contractId=validated_contract.contract_id,
                reason=skip_reason,
            )
        )

    return (
        tuple(snapshots),
        tuple(effective_contract_ids),
        tuple(skipped_contracts),
        EvidenceVerdictReadinessMetadata(
            readyContractIds=(),
            pendingContractIds=tuple(effective_contract_ids),
            trafficAttached=False,
            executionAttached=False,
        ),
    )


def _build_evidence_scope_context(
    *,
    agent_role: str,
    run_on: RunOn,
    spawn_depth: int,
) -> EvidenceScopeContext | None:
    # Contained scope (PR2): evidence contracts stay first-party-role-scoped. An
    # external ext.<name> role gets no scope context, so it matches no first-party
    # contract (none declare ext roles) — the same skip outcome as a first-party
    # role a contract does not target. Hard-safety GATES are unconditional
    # (protected_gates + the always-appended "hard-safety" pack), so this is not
    # an enforcement escape. Widening contracts to ext roles is a separate change.
    if agent_role not in FIRST_PARTY_AGENT_ROLE_IDS:
        return None
    return EvidenceScopeContext(
        agentRole=agent_role,
        runOn=run_on,
        spawnDepth=spawn_depth,
    )


def _resolve_evidence_decision(
    contract: EvidenceContractScope,
    context: EvidenceScopeContext | None,
    *,
    opted_out_contract_ids: tuple[str, ...],
) -> EvidenceScopeDecision:
    if context is None:
        return EvidenceScopeDecision(
            contractId=contract.contract_id,
            applies=False,
            effectiveEnforcement="off",
            enforcementEnabled=False,
            optOutApplied=False,
            hardSafety=contract.hard_safety,
            failureChannel=contract.failure_channel,
            researchCitationGate=False,
            trafficAttached=False,
            executionAttached=False,
        )
    return resolve_evidence_scope(
        contract,
        context,
        opted_out_contract_ids=opted_out_contract_ids,
    )


def _evidence_skip_reason(
    contract: EvidenceContractScope,
    context: EvidenceScopeContext | None,
    decision: EvidenceScopeDecision,
    *,
    opted_out_contract_ids: tuple[str, ...],
) -> EvidenceSkipReason | None:
    if decision.opt_out_applied:
        return "opted_out"
    if decision.applies:
        return None
    if context is None or context.agent_role not in contract.agent_roles:
        return "agent_role_mismatch"
    if context.run_on not in contract.run_on:
        return "run_on_mismatch"
    return "spawn_depth_mismatch"


def _scope_metadata_for_contract(
    contract: EvidenceContractScope,
) -> EvidenceContractScopeMetadata:
    return EvidenceContractScopeMetadata(
        agentRoles=contract.agent_roles,
        runOn=contract.run_on,
        spawnDepth=EvidenceSpawnDepthRange(
            minDepth=contract.spawn_depth.min_depth,
            maxDepth=contract.spawn_depth.max_depth,
        ),
        enforcement=contract.enforcement,
        auditBeforeBlock=contract.audit_before_block,
        optOutAllowed=contract.opt_out_allowed,
        hardSafety=contract.hard_safety,
        failureChannel=contract.failure_channel,
        trafficAttached=False,
        executionAttached=False,
    )
