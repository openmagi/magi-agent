from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.recipes.compiler import RecipeSnapshot, build_recipe_snapshot_id
from magi_agent.recipes.reliability_policy import (
    RecipeReliabilityPolicy,
    RecipeReliabilityPolicyRegistry,
)
from magi_agent.recipes.phase_routing_defaults import (
    CODING_PHASE_CAPABILITY_REQUIREMENTS,
)
from magi_agent.runtime.context_budget import (
    ContextBudgetPlan,
    ContextBudgetPlanner,
    ContextBudgetRequest,
    MemoryMode,
)
from magi_agent.runtime.model_tiers import ModelTierRegistry
from magi_agent.runtime.model_tiers import ModelUsagePhase
from magi_agent.runtime.phase_routing import PhaseRoutingPlan, PhaseRoutingPlanner, PhaseRoutingRequest
from magi_agent.runtime.reliability_budget import ReliabilityBudgetLedger


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class FinalGatePolicyMaterialization(BaseModel):
    model_config = _MODEL_CONFIG

    missing_evidence_action: str = Field(alias="missingEvidenceAction")
    required_evidence: tuple[str, ...] = Field(default=(), alias="requiredEvidence")
    required_validators: tuple[str, ...] = Field(default=(), alias="requiredValidators")


class ReliabilityMaterializationPlan(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_snapshot_id: str = Field(alias="recipeSnapshotId")
    selected_pack_ids: tuple[str, ...] = Field(alias="selectedPackIds")
    model_provider: str = Field(alias="modelProvider")
    model_label: str = Field(alias="modelLabel")
    reliability: RecipeReliabilityPolicy
    phase_routing: PhaseRoutingPlan = Field(alias="phaseRouting")
    budget_ledger: ReliabilityBudgetLedger = Field(alias="budgetLedger")
    context_budget: ContextBudgetPlan = Field(alias="contextBudget")
    final_gate_policy: FinalGatePolicyMaterialization = Field(alias="finalGatePolicy")
    provider_intents: tuple[str, ...] = Field(default=(), alias="providerIntents")
    tool_intents: tuple[str, ...] = Field(default=(), alias="toolIntents")
    channel_intents: tuple[str, ...] = Field(default=(), alias="channelIntents")
    artifact_intents: tuple[str, ...] = Field(default=(), alias="artifactIntents")
    scheduler_intents: tuple[str, ...] = Field(default=(), alias="schedulerIntents")
    evidence_requirements: tuple[str, ...] = Field(default=(), alias="evidenceRequirements")
    approval_gates: tuple[str, ...] = Field(default=(), alias="approvalGates")
    kill_switch_refs: tuple[str, ...] = Field(default=(), alias="killSwitchRefs")
    rollback_refs: tuple[str, ...] = Field(default=(), alias="rollbackRefs")
    materialization_order_refs: tuple[str, ...] = Field(alias="materializationOrderRefs")
    live_attachment_refs: tuple[str, ...] = Field(default=(), alias="liveAttachmentRefs")
    attachment_flags: Mapping[str, bool] = Field(alias="attachmentFlags")


class RecipeMaterializer:
    def __init__(
        self,
        *,
        model_registry: ModelTierRegistry,
        reliability_registry: RecipeReliabilityPolicyRegistry,
    ) -> None:
        self._model_registry = model_registry
        self._reliability_registry = reliability_registry

    @classmethod
    def with_reliability_defaults(cls) -> "RecipeMaterializer":
        return cls(
            model_registry=ModelTierRegistry.with_defaults(),
            reliability_registry=RecipeReliabilityPolicyRegistry.with_defaults(),
        )

    def materialize(
        self,
        snapshot: RecipeSnapshot,
        *,
        modelProvider: str,
        modelLabel: str,
        memoryMode: MemoryMode = "normal",
        budgetUsd: float = 0.05,
    ) -> ReliabilityMaterializationPlan:
        resolved = self._model_registry.resolve(provider=modelProvider, model=modelLabel)
        if "unknown_model_standard_no_elevated_capabilities" in resolved.reason_codes:
            raise ValueError("unknown model cannot materialize reliability policy")
        active_pack_ids = _active_pack_ids(snapshot)
        policy_pack_ids = _policy_pack_ids(snapshot)
        materialized_evidence_refs = _materialized_evidence_refs(snapshot)
        materialized_validator_refs = _materialized_validator_refs(snapshot)
        policies = [
            self._reliability_registry.for_recipe(pack_id, modelTier=resolved.tier)
            for pack_id in policy_pack_ids
            if pack_id in self._reliability_registry.recipe_ids()
        ]
        reliability = _combine_policies(policies, model_tier=resolved.tier)
        policy_recipe_ids = tuple(policy.recipe_id for policy in policies) or (
            reliability.recipe_id,
        )
        phase_routing = PhaseRoutingPlanner(
            model_registry=self._model_registry,
            policy_registry=self._reliability_registry,
            phase_capability_requirements=CODING_PHASE_CAPABILITY_REQUIREMENTS,
        ).plan(
            PhaseRoutingRequest(
                recipeIds=policy_recipe_ids,
                defaultProvider=modelProvider,
                defaultModel=modelLabel,
                phases=_phases_for_packs(active_pack_ids),
                budgetUsd=budgetUsd,
            )
        )
        context_budget = ContextBudgetPlanner.with_defaults().plan(
            ContextBudgetRequest(
                recipeIds=policy_recipe_ids,
                modelTier=resolved.tier,
                phase="final_answer_drafting",
                sourceRefs=tuple(
                    ref.replace("evidence:", "source:", 1)
                    for ref in materialized_evidence_refs[: reliability.max_context_refs]
                    if ref.startswith("evidence:")
                ),
                evidenceRefs=materialized_evidence_refs[: reliability.max_context_refs],
                memoryRefs=("memory-ref:materializer",),
                memoryMode=memoryMode,
            )
        )
        return ReliabilityMaterializationPlan(
            recipeSnapshotId=build_recipe_snapshot_id(active_pack_ids),
            selectedPackIds=active_pack_ids,
            modelProvider=resolved.provider,
            modelLabel=resolved.model,
            reliability=reliability,
            phaseRouting=phase_routing,
            budgetLedger=phase_routing.budget_ledger,
            contextBudget=context_budget,
            finalGatePolicy=FinalGatePolicyMaterialization(
                missingEvidenceAction=reliability.final_answer_without_evidence,
                requiredEvidence=_unique((
                    *reliability.required_evidence,
                    *materialized_evidence_refs,
                )),
                requiredValidators=_unique((
                    *reliability.required_validators,
                    *materialized_validator_refs,
                )),
            ),
            providerIntents=_provider_intents(snapshot),
            toolIntents=_tool_intents(snapshot),
            channelIntents=_channel_intents(snapshot),
            artifactIntents=_artifact_intents(snapshot),
            schedulerIntents=_scheduler_intents(snapshot),
            evidenceRequirements=_evidence_requirements(snapshot, reliability),
            approvalGates=_approval_gates(snapshot),
            killSwitchRefs=_kill_switch_refs(snapshot),
            rollbackRefs=_rollback_refs(snapshot),
            materializationOrderRefs=_materialization_order_refs(),
            liveAttachmentRefs=(),
            attachmentFlags={
                "providerCalled": False,
                "routeAttached": False,
                "adkRunnerInvoked": False,
                "productionWriteAllowed": False,
                "userVisibleOutputAllowed": False,
            },
        )


def _combine_policies(
    policies: list[RecipeReliabilityPolicy],
    *,
    model_tier: str,
) -> RecipeReliabilityPolicy:
    if not policies:
        return RecipeReliabilityPolicyRegistry.with_defaults().for_recipe(
            "openmagi.context-safety",
            modelTier=model_tier,  # type: ignore[arg-type]
        )
    evidence = tuple(sorted({item for policy in policies for item in policy.required_evidence}))
    validators = tuple(sorted({item for policy in policies for item in policy.required_validators}))
    checkpoints = tuple(sorted({item for policy in policies for item in policy.required_checkpoints}))
    data = policies[0].model_dump(by_alias=True, mode="python", warnings=False)
    data.update(
        {
            "recipeId": "+".join(policy.recipe_id for policy in policies),
            "requiredEvidence": evidence,
            "requiredValidators": validators,
            "requiredCheckpoints": checkpoints,
            "contextStrategy": _strictest_context_strategy(policies),
            "maxContextRefs": min(policy.max_context_refs for policy in policies),
            "maxRawInputBytes": min(policy.max_raw_input_bytes for policy in policies),
            "autonomyLevel": "low" if any(policy.autonomy_level == "low" for policy in policies) else "medium",
            "finalAnswerWithoutEvidence": _strictest_missing_evidence_action(policies),
            "maxSotaEscalations": min(policy.max_sota_escalations for policy in policies),
            "maxPatchFiles": _min_optional(policy.max_patch_files for policy in policies),
        }
    )
    return RecipeReliabilityPolicy.model_validate(data)


def _strictest_context_strategy(policies: list[RecipeReliabilityPolicy]) -> str:
    if any(policy.context_strategy == "chunk_refs_only" for policy in policies):
        return "chunk_refs_only"
    if any(policy.context_strategy == "refs_only_with_chunk_summaries" for policy in policies):
        return "refs_only_with_chunk_summaries"
    return "refs_with_summaries"


def _strictest_missing_evidence_action(policies: list[RecipeReliabilityPolicy]) -> str:
    order = {
        "block": 0,
        "insufficient_evidence": 1,
        "repair_required": 2,
        "ask_user": 3,
    }
    return min(
        (policy.final_answer_without_evidence for policy in policies),
        key=lambda item: order[item],
    )


def _min_optional(values) -> int | None:
    concrete = [value for value in values if value is not None]
    return min(concrete) if concrete else None


def _snapshot_materialization_blocked(snapshot: RecipeSnapshot) -> bool:
    if snapshot.composition_policy_metadata.blocked:
        return True
    return snapshot.recipe_selection.admission_blocked


def _active_pack_ids(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    if _snapshot_materialization_blocked(snapshot):
        return ()
    return snapshot.selected_pack_ids


def _policy_pack_ids(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    if not _snapshot_materialization_blocked(snapshot):
        return snapshot.selected_pack_ids
    return snapshot.non_opt_out_pack_ids


def _materialized_evidence_refs(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    if _snapshot_materialization_blocked(snapshot):
        return ()
    return snapshot.evidence_refs


def _materialized_validator_refs(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    if _snapshot_materialization_blocked(snapshot):
        return ()
    return snapshot.validator_refs


def _materialized_approval_gate_refs(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    if not _snapshot_materialization_blocked(snapshot):
        return snapshot.approval_gate_refs
    composition_block_ref = "approval:composition-policy:requires-clarification"
    selection_block_ref = "approval:recipe-selection:blocked"
    refs = []
    if snapshot.composition_policy_metadata.blocked:
        refs.append(composition_block_ref)
    if snapshot.recipe_selection.admission_blocked:
        refs.append(selection_block_ref)
    return _unique(refs)


def _phases_for_packs(pack_ids: tuple[str, ...]) -> tuple[ModelUsagePhase, ...]:
    phases: list[ModelUsagePhase] = [
        "intent_classification",
        "final_answer_drafting",
    ]
    selected = set(pack_ids)
    if selected.intersection(
        {
            "openmagi.web-acquisition",
            "openmagi.document-review",
        }
    ):
        phases.extend(["source_acquisition", "source_extraction"])
    if selected.intersection(
        {
            "openmagi.dev-coding",
            "openmagi.lightweight-scripting",
        }
    ):
        phases.extend(
            [
                "code_search",
                "patch_planning",
                "patch_generation",
                "test_interpretation",
            ]
        )
    if selected.intersection(
        {
            "openmagi.spreadsheet-automation",
            "openmagi.document-review",
        }
    ):
        phases.append("high_risk_review")
    phases.append("final_verification")
    return tuple(dict.fromkeys(phases))


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _provider_intents(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    selected = set(_active_pack_ids(snapshot))
    refs: list[str] = []
    if "openmagi.web-acquisition" in selected:
        refs.extend(
            (
                "provider:web.search",
                "provider:web.fetch",
                "provider:reader.extract",
                "provider:browser.snapshot_fallback",
            )
        )
    if "openmagi.browser-automation" in selected:
        refs.extend(("provider:browser.worker", "provider:browser.screenshot"))
    if "openmagi.memory-agentmemory" in selected:
        refs.extend(("provider:memory.recall", "provider:memory.write-receipt"))
    return _unique(refs)


def _tool_intents(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    refs = [] if _snapshot_materialization_blocked(snapshot) else list(snapshot.tool_refs)
    selected = set(_active_pack_ids(snapshot))
    if "openmagi.artifact-delivery" in selected:
        refs.extend(("tool:FileDeliver", "tool:FileSend"))
    if "openmagi.channel-delivery" in selected:
        refs.extend(("tool:ChannelDispatcher", "tool:NotifyUser"))
    return _unique(refs)


def _channel_intents(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    selected = set(_active_pack_ids(snapshot))
    if "openmagi.channel-delivery" not in selected:
        return ()
    refs = ["channel:dispatcher.push"]
    channel = str(snapshot.resolved_profile.get("channel") or "").casefold()
    intents = set(_profile_intents(snapshot))
    if channel == "telegram" or "telegram" in intents:
        refs.extend(("channel:telegram.send_message", "channel:telegram.send_document"))
    if channel == "discord" or "discord" in intents:
        refs.extend(("channel:discord.send_message", "channel:discord.send_file"))
    if channel in {"web", "app"} or "notify-user" in intents or not channel:
        refs.append("channel:web.push")
    return _unique(refs)


def _artifact_intents(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    if "openmagi.artifact-delivery" not in set(_active_pack_ids(snapshot)):
        return ()
    return ("artifact:prepare-delivery", "artifact:file-deliver", "artifact:file-send")


def _scheduler_intents(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    selected = set(_active_pack_ids(snapshot))
    if "openmagi.scheduled-work" not in selected:
        return ()
    refs = [
        "scheduler:cron.create",
        "scheduler:cron.list",
        "scheduler:cron.update",
        "scheduler:task.wait",
        "scheduler:task.output",
        "scheduler:task.stop",
    ]
    if "notify-user" in set(_profile_intents(snapshot)):
        refs.append("scheduler:notify-user")
    return _unique(refs)


def _evidence_requirements(
    snapshot: RecipeSnapshot,
    reliability: RecipeReliabilityPolicy,
) -> tuple[str, ...]:
    refs = list(_materialized_evidence_refs(snapshot)) + list(reliability.required_evidence)
    selected = set(_active_pack_ids(snapshot))
    if "openmagi.web-acquisition" in selected:
        refs.append("requirement:opened-or-observed-source-proof")
    return _unique(refs)


def _approval_gates(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    refs = list(_materialized_approval_gate_refs(snapshot))
    selected = set(_active_pack_ids(snapshot))
    if {"openmagi.web-acquisition", "openmagi.browser-automation"}.issubset(selected):
        refs.append("approval:web-acquisition:browser-fallback")
    return _unique(refs)


def _kill_switch_refs(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    selected = set(_active_pack_ids(snapshot))
    refs = ["kill-switch:recipe-materializer-live-attachment"]
    if "openmagi.web-acquisition" in selected:
        refs.append("kill-switch:web-acquisition-provider")
    if "openmagi.browser-automation" in selected:
        refs.append("kill-switch:browser-provider")
    if "openmagi.dev-coding" in selected or "openmagi.artifact-delivery" in selected:
        refs.append("kill-switch:toolhost")
    if "openmagi.channel-delivery" in selected:
        refs.append("kill-switch:channel-delivery")
    if "openmagi.scheduled-work" in selected or "openmagi.missions" in selected:
        refs.append("kill-switch:scheduler-runtime")
    return _unique(refs)


def _rollback_refs(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    selected = set(_active_pack_ids(snapshot))
    refs = ["rollback:recipe-materializer-metadata-only"]
    if "openmagi.web-acquisition" in selected or "openmagi.browser-automation" in selected:
        refs.append("rollback:provider-intents-to-metadata-only")
    if "openmagi.artifact-delivery" in selected:
        refs.append("rollback:artifact-delivery-to-ref-only")
    if "openmagi.channel-delivery" in selected:
        refs.append("rollback:channel-delivery-to-no-op")
    if "openmagi.scheduled-work" in selected:
        refs.append("rollback:scheduler-runtime-disabled")
    return _unique(refs)


def _materialization_order_refs() -> tuple[str, ...]:
    return (
        "order:01-hard-safety",
        "order:02-identity-session",
        "order:03-recipe-dependencies",
        "order:04-provider-intents",
        "order:05-tool-intents",
        "order:06-validators",
        "order:07-evidence",
        "order:08-channel-artifact-delivery",
        "order:09-final-output-gate",
    )


def _profile_intents(snapshot: RecipeSnapshot) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("taskType", "task_type", "taskTypes", "task_types", "taskIntent", "task_intent", "taskIntents", "task_intents"):
        raw = snapshot.resolved_profile.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, tuple | list):
            values.extend(str(item) for item in raw)
    return tuple(value.casefold() for value in values)


__all__ = [
    "FinalGatePolicyMaterialization",
    "RecipeMaterializer",
    "ReliabilityMaterializationPlan",
]
