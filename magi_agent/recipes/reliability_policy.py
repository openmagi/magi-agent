from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.runtime.model_tiers import ModelTier


CheapModelSafeMode: TypeAlias = Literal[
    "chunk_refs_only",
    "small_patch_review",
    "approval_gated_actions",
    "deterministic_evidence_required",
]
ReliabilityRequirement: TypeAlias = Literal[
    "source_ledger",
    "fact_grounding",
    "citation_support",
    "test_or_not_run_reason",
    "fresh_review",
    "calculation_evidence",
    "approval_required",
    "redaction_audit",
    "public_redaction",
    "no_production_attachment",
]
ContextStrategy: TypeAlias = Literal[
    "chunk_refs_only",
    "refs_only_with_chunk_summaries",
    "refs_with_summaries",
]
AutonomyLevel: TypeAlias = Literal["low", "medium", "high"]
MissingEvidenceAction: TypeAlias = Literal[
    "insufficient_evidence",
    "repair_required",
    "ask_user",
    "block",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_HARD_EVIDENCE = ("redaction_audit",)
_HARD_VALIDATORS = ("no_production_attachment", "public_redaction")


class RecipeReliabilityPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_id: str = Field(alias="recipeId")
    model_tier: ModelTier = Field(alias="modelTier")
    minimum_model_tier: ModelTier = Field(default="standard", alias="minimumModelTier")
    preferred_model_tier: ModelTier = Field(default="standard", alias="preferredModelTier")
    context_strategy: ContextStrategy = Field(default="refs_with_summaries", alias="contextStrategy")
    max_context_refs: int = Field(default=12, ge=0, alias="maxContextRefs")
    max_raw_input_bytes: int = Field(default=16_384, ge=0, alias="maxRawInputBytes")
    required_evidence: tuple[str, ...] = Field(default=(), alias="requiredEvidence")
    required_validators: tuple[str, ...] = Field(default=(), alias="requiredValidators")
    required_checkpoints: tuple[str, ...] = Field(default=(), alias="requiredCheckpoints")
    autonomy_level: AutonomyLevel = Field(default="medium", alias="autonomyLevel")
    final_answer_without_evidence: MissingEvidenceAction = Field(
        default="repair_required",
        alias="finalAnswerWithoutEvidence",
    )
    sota_escalation_allowed: bool = Field(default=False, alias="sotaEscalationAllowed")
    sota_escalation_reasons: tuple[str, ...] = Field(
        default=(),
        alias="sotaEscalationReasons",
    )
    max_sota_escalations: int = Field(default=0, ge=0, alias="maxSotaEscalations")
    max_patch_files: int | None = Field(default=None, ge=0, alias="maxPatchFiles")

    @field_validator(
        "required_evidence",
        "required_validators",
        "required_checkpoints",
        "sota_escalation_reasons",
    )
    @classmethod
    def _sort_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(dict.fromkeys(str(item) for item in value if str(item))))


class RecipeReliabilityPolicyRegistry:
    def __init__(self, base_requirements: Mapping[str, Mapping[str, object]]) -> None:
        self._base_requirements = {
            str(recipe_id): dict(config)
            for recipe_id, config in sorted(base_requirements.items(), key=lambda item: item[0])
        }

    @classmethod
    def with_defaults(cls) -> Self:
        return cls(
            {
                "openmagi.context-safety": {
                    "requiredEvidence": ("redaction_audit",),
                    "requiredValidators": ("no_production_attachment", "public_redaction"),
                    "requiredCheckpoints": ("side_effect_deny_by_default",),
                    "finalAnswerWithoutEvidence": "repair_required",
                },
                "openmagi.evidence": {
                    "requiredEvidence": ("runtime_evidence_record",),
                    "requiredValidators": ("no_raw_evidence_payload",),
                    "requiredCheckpoints": ("audit_ref_only",),
                    "finalAnswerWithoutEvidence": "repair_required",
                },
                "openmagi.web-acquisition": {
                    "requiredEvidence": ("source_ledger",),
                    "requiredValidators": ("source_quality", "no_auth_bypass"),
                    "requiredCheckpoints": ("domain_policy",),
                    "finalAnswerWithoutEvidence": "insufficient_evidence",
                },
                "openmagi.research": {
                    "requiredEvidence": ("citation_support", "source_ledger"),
                    "requiredValidators": ("citation_support", "fact_grounding"),
                    "requiredCheckpoints": ("end_of_task_evidence_check",),
                    "finalAnswerWithoutEvidence": "insufficient_evidence",
                    "sotaEscalationReasons": ("final_verification",),
                    "maxSotaEscalations": 1,
                },
                "openmagi.dev-coding": {
                    "requiredEvidence": ("git_diff", "test_or_not_run_reason"),
                    "requiredValidators": ("tdd_verification",),
                    "requiredCheckpoints": ("fresh_review",),
                    "maxPatchFiles": 2,
                },
                "openmagi.office-automation": {
                    "requiredEvidence": ("office_preview",),
                    "requiredValidators": ("preview_before_write",),
                    "requiredCheckpoints": ("write_or_send_approval",),
                    "finalAnswerWithoutEvidence": "repair_required",
                },
                "openmagi.artifact-delivery": {
                    "requiredEvidence": ("artifact_delivery_ref",),
                    "requiredValidators": ("no_raw_path_leakage", "redacted_preview_only"),
                    "requiredCheckpoints": ("delivery_ack_metadata", "sanitized_artifact_ref"),
                    "finalAnswerWithoutEvidence": "block",
                },
                "openmagi.spreadsheet-automation": {
                    "requiredEvidence": ("calculation_evidence", "spreadsheet_preview"),
                    "requiredValidators": ("formula_recalc", "preview_before_write"),
                    "requiredCheckpoints": ("spreadsheet_write_approval",),
                    "finalAnswerWithoutEvidence": "repair_required",
                },
                "openmagi.browser-automation": {
                    "requiredEvidence": ("browser_observation_ref",),
                    "requiredValidators": ("browser_action_plan", "no_private_browser_data"),
                    "requiredCheckpoints": ("external_action_approval",),
                    "finalAnswerWithoutEvidence": "ask_user",
                },
                "openmagi.agent-methodology": {
                    "requiredEvidence": ("approved_plan", "verification_record"),
                    "requiredValidators": ("plan_before_act", "verification_before_completion"),
                    "requiredCheckpoints": ("review_gate", "tdd_cycle"),
                    "finalAnswerWithoutEvidence": "repair_required",
                },
                "openmagi.superpowers-compat": {
                    "requiredEvidence": ("approved_plan", "verification_record"),
                    "requiredValidators": ("plan_before_act", "verification_before_completion"),
                    "requiredCheckpoints": ("review_gate", "tdd_cycle"),
                    "finalAnswerWithoutEvidence": "repair_required",
                },
                "openmagi.memory-agentmemory": {
                    "requiredEvidence": ("memory_source_authority",),
                    "requiredValidators": ("private_memory_redaction",),
                    "requiredCheckpoints": ("memory_mode_check",),
                    "finalAnswerWithoutEvidence": "ask_user",
                },
                "openmagi.missions": {
                    "requiredEvidence": ("mission_progress_ref",),
                    "requiredValidators": ("budget_envelope", "stop_condition"),
                    "requiredCheckpoints": ("pause_resume_cancel_control",),
                    "finalAnswerWithoutEvidence": "ask_user",
                },
                "openmagi.document-review": {
                    "requiredEvidence": ("document_finding", "source_ledger"),
                    "requiredValidators": ("citation_support",),
                    "requiredCheckpoints": ("tracked_change_or_comment_approval",),
                    "finalAnswerWithoutEvidence": "insufficient_evidence",
                },
                "openmagi.lightweight-scripting": {
                    "requiredEvidence": ("script_dry_run", "test_or_not_run_reason"),
                    "requiredValidators": ("small_script_plan",),
                    "requiredCheckpoints": ("workspace_mutation_approval",),
                    "finalAnswerWithoutEvidence": "repair_required",
                },
            }
        )

    def recipe_ids(self) -> tuple[str, ...]:
        return tuple(self._base_requirements)

    def for_recipe(
        self,
        recipe_id: str,
        *,
        modelTier: ModelTier = "standard",
        model_tier: ModelTier | None = None,
    ) -> RecipeReliabilityPolicy:
        tier = model_tier or modelTier
        if recipe_id not in self._base_requirements:
            raise KeyError(f"unknown recipe reliability policy: {recipe_id}")
        base = dict(self._base_requirements[recipe_id])
        evidence = _merge_requirements(_HARD_EVIDENCE, base.get("requiredEvidence", ()))
        validators = _merge_requirements(_HARD_VALIDATORS, base.get("requiredValidators", ()))
        checkpoints = _merge_requirements((), base.get("requiredCheckpoints", ()))

        policy_data: dict[str, object] = {
            "recipeId": recipe_id,
            "modelTier": tier,
            "minimumModelTier": "standard",
            "preferredModelTier": "standard",
            "contextStrategy": "refs_with_summaries",
            "maxContextRefs": 12,
            "maxRawInputBytes": 16_384,
            "requiredEvidence": evidence,
            "requiredValidators": validators,
            "requiredCheckpoints": checkpoints,
            "autonomyLevel": "medium",
            "finalAnswerWithoutEvidence": base.get(
                "finalAnswerWithoutEvidence",
                "repair_required",
            ),
            "sotaEscalationAllowed": False,
            "sotaEscalationReasons": tuple(base.get("sotaEscalationReasons", ())),
            "maxSotaEscalations": int(base.get("maxSotaEscalations", 0)),
            "maxPatchFiles": base.get("maxPatchFiles"),
        }
        if tier == "cheap":
            policy_data.update(
                {
                    "contextStrategy": (
                        "chunk_refs_only"
                        if recipe_id == "openmagi.research"
                        else "refs_only_with_chunk_summaries"
                    ),
                    "maxContextRefs": 6,
                    "maxRawInputBytes": 8_192,
                    "autonomyLevel": "low",
                    "sotaEscalationAllowed": bool(policy_data["sotaEscalationReasons"]),
                }
            )
            if recipe_id == "openmagi.dev-coding":
                policy_data["maxPatchFiles"] = 1
        elif tier == "sota":
            policy_data.update(
                {
                    "preferredModelTier": "sota",
                    "maxContextRefs": 18,
                    "maxRawInputBytes": 24_576,
                    "sotaEscalationAllowed": bool(policy_data["sotaEscalationReasons"]),
                }
            )
        return RecipeReliabilityPolicy.model_validate(policy_data)


def _merge_requirements(
    required: tuple[str, ...],
    configured: object,
) -> tuple[str, ...]:
    items: list[str] = list(required)
    if isinstance(configured, str):
        items.append(configured)
    elif isinstance(configured, tuple | list | set):
        items.extend(str(item) for item in configured)
    return tuple(sorted(dict.fromkeys(item for item in items if item)))


__all__ = [
    "AutonomyLevel",
    "CheapModelSafeMode",
    "ContextStrategy",
    "MissingEvidenceAction",
    "RecipeReliabilityPolicy",
    "RecipeReliabilityPolicyRegistry",
    "ReliabilityRequirement",
]
