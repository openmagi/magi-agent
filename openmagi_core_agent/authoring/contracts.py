from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


BuilderPhaseStatus: TypeAlias = Literal["pending", "in_progress", "completed", "blocked"]
BuilderAnswerKind: TypeAlias = Literal["free_text", "single_select", "multi_select", "connector_ref"]
RecipePackDraftStatus: TypeAlias = Literal["draft", "review", "blocked"]
RecipePackVersionStatus: TypeAlias = Literal["candidate", "reviewed", "rejected", "archived"]
GeneratedPluginProposalStatus: TypeAlias = Literal["proposed", "blocked", "rejected"]
BuilderGapKind: TypeAlias = Literal["missing_connector", "missing_capability", "policy_conflict"]
BuilderGapStatus: TypeAlias = Literal["open", "resolved", "deferred"]
BuilderReviewDecision: TypeAlias = Literal["approved_for_review", "needs_revision", "blocked"]
DraftHardInvariantMode: TypeAlias = Literal["enforced", "disabled", "log_only"]
AuthoringToolName: TypeAlias = Literal[
    "ask_question",
    "record_answer",
    "save_draft",
    "report_gap",
    "propose_generated_plugin",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    hide_input_in_errors=True,
)

_RAW_IO_FIELD_NAMES = {
    "rawprompt",
    "rawmodelprompt",
    "prompttojson",
    "rawoutput",
    "rawmodeloutput",
    "modelrawoutput",
}
_RAW_CODE_FIELD_NAMES = {
    "rawcode",
    "generatedcode",
    "executablecode",
}
_RAW_CREDENTIAL_FIELD_NAMES = {
    "apikey",
    "apitoken",
    "authtoken",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "credential",
    "credentials",
    "rawcredential",
    "rawcredentials",
    "password",
    "privatekey",
    "secret",
    "secrettoken",
    "token",
}
_SEPARATE_AGENT_IDENTITY_FIELD_NAMES = {
    "agentid",
    "builderagentid",
    "builderagentidentity",
}
_DEFAULT_AUTHORING_TOOL_ALLOWLIST: tuple[AuthoringToolName, ...] = (
    "ask_question",
    "record_answer",
    "save_draft",
    "report_gap",
    "propose_generated_plugin",
)

_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._-]{12,}|sk-(?:live|test)-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+)"
)
_RAW_MODEL_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"raw\s*model\s*output|raw\s*output|raw\s*prompt|"
    r"hidden\s+instructions?|hidden\s+transcript|"
    r"chain\s+of\s+thought|tool\s+result\s+payload"
    r")\b\s*:?\s*[^.!?\n]*(?:[.!?])?"
)
_DIGEST_PREFIX = "sha256:"


class _AuthoringModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for authoring contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _reject_unsafe_input_fields(cls, data: object) -> object:
        _reject_unsafe_field_names(data)
        return data

    @field_validator(
        "title",
        "summary",
        "name",
        "reason",
        "details",
        "question_text",
        "answer_text",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def _redact_public_text_fields(cls, value: object) -> object:
        if isinstance(value, str):
            return _redact_unsafe_text(value)
        return value


class DraftHarnessPolicy(_AuthoringModel):
    harness_refs: tuple[str, ...] = Field(default=(), alias="harnessRefs")
    allow_model_calls: StrictBool = Field(default=False, alias="allowModelCalls")
    allow_live_execution: StrictBool = Field(default=False, alias="allowLiveExecution")
    allow_workspace_mutation: StrictBool = Field(default=False, alias="allowWorkspaceMutation")
    allow_memory_write: StrictBool = Field(default=False, alias="allowMemoryWrite")
    allow_external_delivery: StrictBool = Field(default=False, alias="allowExternalDelivery")
    allow_schedule_mutation: StrictBool = Field(default=False, alias="allowScheduleMutation")

    @field_validator("harness_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "harness refs")
        return value

    @model_validator(mode="after")
    def _require_default_off(self) -> DraftHarnessPolicy:
        _reject_true(self.allow_model_calls, "allowModelCalls")
        _reject_true(self.allow_live_execution, "allowLiveExecution")
        _reject_true(self.allow_workspace_mutation, "allowWorkspaceMutation")
        _reject_true(self.allow_memory_write, "allowMemoryWrite")
        _reject_true(self.allow_external_delivery, "allowExternalDelivery")
        _reject_true(self.allow_schedule_mutation, "allowScheduleMutation")
        return self


class DraftToolPolicy(_AuthoringModel):
    allowed_connector_refs: tuple[str, ...] = Field(default=(), alias="allowedConnectorRefs")
    allowed_tool_refs: tuple[str, ...] = Field(default=(), alias="allowedToolRefs")
    allowed_plugin_refs: tuple[str, ...] = Field(default=(), alias="allowedPluginRefs")
    denied_tool_refs: tuple[str, ...] = Field(default=(), alias="deniedToolRefs")
    generated_plugin_execution_allowed: StrictBool = Field(
        default=False, alias="generatedPluginExecutionAllowed"
    )
    allow_live_connectors: StrictBool = Field(default=False, alias="allowLiveConnectors")
    connector_credential_reads_allowed: StrictBool = Field(
        default=False, alias="connectorCredentialReadsAllowed"
    )
    connector_credentials_exposed: StrictBool = Field(
        default=False, alias="connectorCredentialsExposed"
    )

    @field_validator(
        "allowed_connector_refs",
        "allowed_tool_refs",
        "allowed_plugin_refs",
        "denied_tool_refs",
    )
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "tool policy refs")
        return value

    @model_validator(mode="after")
    def _require_non_executable_tools(self) -> DraftToolPolicy:
        _reject_true(self.generated_plugin_execution_allowed, "generatedPluginExecutionAllowed")
        _reject_true(self.allow_live_connectors, "allowLiveConnectors")
        _reject_true(
            self.connector_credential_reads_allowed,
            "connectorCredentialReadsAllowed",
        )
        _reject_true(self.connector_credentials_exposed, "connectorCredentialsExposed")
        conflict = set(self.allowed_tool_refs).intersection(self.denied_tool_refs)
        if conflict:
            raise ValueError("allowedToolRefs cannot overlap deniedToolRefs")
        return self


class DraftEvidencePolicy(_AuthoringModel):
    required_evidence_refs: tuple[str, ...] = Field(default=(), alias="requiredEvidenceRefs")
    evidence_producer_refs: tuple[str, ...] = Field(default=(), alias="evidenceProducerRefs")
    capture_model_io: StrictBool = Field(default=False, alias="captureModelIo")

    @field_validator("required_evidence_refs", "evidence_producer_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "required evidence refs")
        return value

    @model_validator(mode="after")
    def _reject_model_io_capture(self) -> DraftEvidencePolicy:
        _reject_true(self.capture_model_io, "captureModelIo")
        return self


class DraftValidatorPolicy(_AuthoringModel):
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")

    @field_validator("validator_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "validator refs")
        return value


class DraftApprovalPolicy(_AuthoringModel):
    requires_human_review: StrictBool = Field(default=True, alias="requiresHumanReview")
    authority_refs: tuple[str, ...] = Field(
        default=("authority:owner-human@1",), alias="authorityRefs"
    )
    allow_auto_activation: StrictBool = Field(default=False, alias="allowAutoActivation")

    @field_validator("authority_refs")
    @classmethod
    def _validate_authority_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "approval authority refs")
        return value

    @model_validator(mode="after")
    def _reject_auto_activation(self) -> DraftApprovalPolicy:
        _reject_true(self.allow_auto_activation, "allowAutoActivation")
        return self


class DraftProjectionPolicy(_AuthoringModel):
    mode: Literal["structured_summary", "review_packet", "raw_governed"] = "structured_summary"
    redact_unsafe_text: StrictBool = Field(default=True, alias="redactUnsafeText")
    expose_model_io: StrictBool = Field(default=False, alias="exposeModelIo")
    raw_governed_projection_enabled: StrictBool = Field(
        default=False, alias="rawGovernedProjectionEnabled"
    )

    @model_validator(mode="after")
    def _reject_model_io_projection(self) -> DraftProjectionPolicy:
        _reject_true(self.expose_model_io, "exposeModelIo")
        if not self.redact_unsafe_text:
            raise ValueError("redactUnsafeText cannot be false in authoring contracts")
        return self


class DraftRepairPolicy(_AuthoringModel):
    max_repair_attempts: int = Field(default=0, ge=0, le=3, alias="maxRepairAttempts")
    terminal_states: tuple[str, ...] = Field(default=(), alias="terminalStates")
    allow_runtime_repair: StrictBool = Field(default=False, alias="allowRuntimeRepair")

    @field_validator("terminal_states")
    @classmethod
    def _validate_terminal_states(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "repair terminal states")
        return value

    @model_validator(mode="after")
    def _reject_runtime_repair(self) -> DraftRepairPolicy:
        _reject_true(self.allow_runtime_repair, "allowRuntimeRepair")
        return self


class DraftBudgetPolicy(_AuthoringModel):
    max_tool_calls: int = Field(default=16, ge=0, le=100, alias="maxToolCalls")
    max_validator_calls: int = Field(default=16, ge=0, le=100, alias="maxValidatorCalls")
    max_repair_attempts: int = Field(default=3, ge=0, le=3, alias="maxRepairAttempts")


class DraftHardInvariant(_AuthoringModel):
    invariant_id: str = Field(alias="invariantId")
    description: str
    mode: DraftHardInvariantMode = "enforced"

    @field_validator("invariant_id", "description")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "hard invariant fields")


class GeneratedPluginProposal(_AuthoringModel):
    proposal_id: str = Field(alias="proposalId")
    status: GeneratedPluginProposalStatus
    name: str
    reason: str
    executable: StrictBool = False
    runtime_entrypoint: str | None = Field(default=None, alias="runtimeEntrypoint")
    review_required: StrictBool = Field(default=True, alias="reviewRequired")

    @field_validator("proposal_id", "name", "reason")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "generated plugin proposal fields")

    @model_validator(mode="after")
    def _require_non_executable(self) -> GeneratedPluginProposal:
        _reject_true(self.executable, "executable")
        if self.runtime_entrypoint is not None:
            raise ValueError("runtimeEntrypoint must be absent for generated plugin proposals")
        if not self.review_required:
            raise ValueError("reviewRequired cannot be false for generated plugin proposals")
        return self


class DraftRecipePack(_AuthoringModel):
    pack_id: str = Field(alias="packId")
    title: str
    summary: str
    recipe_refs: tuple[str, ...] = Field(alias="recipeRefs")
    harness_policy: DraftHarnessPolicy = Field(default_factory=DraftHarnessPolicy, alias="harnessPolicy")
    tool_policy: DraftToolPolicy = Field(default_factory=DraftToolPolicy, alias="toolPolicy")
    evidence_policy: DraftEvidencePolicy = Field(
        default_factory=DraftEvidencePolicy, alias="evidencePolicy"
    )
    validator_policy: DraftValidatorPolicy = Field(
        default_factory=DraftValidatorPolicy, alias="validatorPolicy"
    )
    approval_policy: DraftApprovalPolicy = Field(
        default_factory=DraftApprovalPolicy, alias="approvalPolicy"
    )
    projection_policy: DraftProjectionPolicy = Field(
        default_factory=DraftProjectionPolicy, alias="projectionPolicy"
    )
    repair_policy: DraftRepairPolicy = Field(default_factory=DraftRepairPolicy, alias="repairPolicy")
    budget_policy: DraftBudgetPolicy = Field(default_factory=DraftBudgetPolicy, alias="budgetPolicy")
    hard_invariants: tuple[DraftHardInvariant, ...] = Field(
        default=(), alias="hardInvariants"
    )

    @field_validator("pack_id", "title", "summary")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "draft recipe pack fields")

    @field_validator("recipe_refs")
    @classmethod
    def _validate_recipe_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("recipeRefs must include at least one draft recipe ref")
        _require_non_empty_strings(value, "recipe refs")
        return value


class RecipePackDraft(_AuthoringModel):
    draft_id: str = Field(alias="draftId")
    bot_id: str = Field(alias="botId")
    owner_id: str = Field(alias="ownerId")
    authoring_session_id: str = Field(alias="authoringSessionId")
    status: RecipePackDraftStatus
    pack: DraftRecipePack
    save_target: Literal["current_bot_draft_store"] = Field(
        default="current_bot_draft_store", alias="saveTarget"
    )
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    activation_eligibility: StrictBool = Field(default=False, alias="activationEligibility")
    generated_plugin_proposals: tuple[GeneratedPluginProposal, ...] = Field(
        default=(), alias="generatedPluginProposals"
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_active_status(cls, data: object) -> object:
        if isinstance(data, Mapping) and data.get("status") == "active":
            raise ValueError("draft status cannot be active")
        return data

    @field_validator("draft_id", "bot_id", "owner_id", "authoring_session_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "recipe pack draft fields")

    @model_validator(mode="after")
    def _require_default_off(self) -> RecipePackDraft:
        _reject_true(self.activation_enabled, "activationEnabled")
        _reject_true(self.activation_eligibility, "activationEligibility")
        return self


class RecipePackVersion(_AuthoringModel):
    pack_id: str = Field(alias="packId")
    version: str
    source_draft_id: str = Field(alias="sourceDraftId")
    status: RecipePackVersionStatus
    source_digest: str = Field(alias="sourceDigest")
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")

    @model_validator(mode="before")
    @classmethod
    def _reject_active_status(cls, data: object) -> object:
        if isinstance(data, Mapping) and data.get("status") == "active":
            raise ValueError("recipe pack version status cannot be active in PR1")
        return data

    @field_validator("pack_id", "version", "source_draft_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "recipe pack version fields")

    @field_validator("source_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "sourceDigest")

    @model_validator(mode="after")
    def _require_default_off(self) -> RecipePackVersion:
        _reject_true(self.activation_enabled, "activationEnabled")
        return self


class BuilderPhase(_AuthoringModel):
    phase_id: str = Field(alias="phaseId")
    title: str
    status: BuilderPhaseStatus
    question_ids: tuple[str, ...] = Field(default=(), alias="questionIds")

    @field_validator("phase_id", "title")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "builder phase fields")

    @field_validator("question_ids")
    @classmethod
    def _validate_question_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "question ids")
        return value


class BuilderQuestion(_AuthoringModel):
    question_id: str = Field(alias="questionId")
    phase_id: str = Field(alias="phaseId")
    question_text: str = Field(alias="questionText")
    required: StrictBool = True
    answer_kind: BuilderAnswerKind = Field(default="free_text", alias="answerKind")

    @field_validator("question_id", "phase_id", "question_text")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "builder question fields")


class BuilderAnswer(_AuthoringModel):
    question_id: str = Field(alias="questionId")
    answer_text: str | None = Field(default=None, alias="answerText")
    selected_refs: tuple[str, ...] = Field(default=(), alias="selectedRefs")

    @field_validator("question_id")
    @classmethod
    def _reject_empty_question_id(cls, value: str) -> str:
        return _require_non_empty(value, "builder answer questionId")

    @field_validator("selected_refs")
    @classmethod
    def _validate_selected_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "selected refs")
        return value

    @model_validator(mode="after")
    def _require_answer_payload(self) -> BuilderAnswer:
        if self.answer_text is None and not self.selected_refs:
            raise ValueError("builder answer requires answerText or selectedRefs")
        return self


class BuilderGap(_AuthoringModel):
    gap_id: str = Field(alias="gapId")
    kind: BuilderGapKind
    status: BuilderGapStatus
    title: str
    details: str
    missing_refs: tuple[str, ...] = Field(default=(), alias="missingRefs")
    blocked_activation: StrictBool = Field(default=True, alias="blockedActivation")

    @field_validator("gap_id", "title", "details")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "builder gap fields")

    @field_validator("missing_refs")
    @classmethod
    def _validate_missing_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "missing refs")
        return value

    @model_validator(mode="after")
    def _require_blocked_activation_for_missing_or_open_gap(self) -> BuilderGap:
        if not self.blocked_activation:
            raise ValueError("blockedActivation cannot be false for PR1 authoring gaps")
        return self


class BuilderGapReport(_AuthoringModel):
    report_id: str = Field(alias="reportId")
    session_id: str = Field(alias="sessionId")
    draft_id: str = Field(alias="draftId")
    local_only: StrictBool = Field(default=True, alias="localOnly")
    non_production: StrictBool = Field(default=True, alias="nonProduction")
    gaps: tuple[BuilderGap, ...]

    @field_validator("report_id", "session_id", "draft_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "builder gap report fields")

    @model_validator(mode="after")
    def _require_fixture_only(self) -> BuilderGapReport:
        _reject_false(self.local_only, "localOnly")
        _reject_false(self.non_production, "nonProduction")
        return self


class BuilderReviewSummary(_AuthoringModel):
    review_id: str = Field(alias="reviewId")
    draft_id: str = Field(alias="draftId")
    decision: BuilderReviewDecision
    notes: tuple[str, ...] = ()
    activation_ready: StrictBool = Field(default=False, alias="activationReady")

    @field_validator("review_id", "draft_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "builder review summary fields")

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "review notes")
        return tuple(_redact_unsafe_text(item) for item in value)

    @model_validator(mode="after")
    def _require_not_activation_ready(self) -> BuilderReviewSummary:
        _reject_true(self.activation_ready, "activationReady")
        return self


class EvalFixtureSet(_AuthoringModel):
    fixture_set_id: str = Field(alias="fixtureSetId")
    draft_id: str = Field(alias="draftId")
    local_only: StrictBool = Field(default=True, alias="localOnly")
    non_production: StrictBool = Field(default=True, alias="nonProduction")
    scenario_refs: tuple[str, ...] = Field(default=(), alias="scenarioRefs")
    expected_gap_refs: tuple[str, ...] = Field(default=(), alias="expectedGapRefs")

    @field_validator("fixture_set_id", "draft_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "eval fixture set fields")

    @field_validator("scenario_refs", "expected_gap_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "eval fixture refs")
        return value

    @model_validator(mode="after")
    def _require_local_fixture(self) -> EvalFixtureSet:
        _reject_false(self.local_only, "localOnly")
        _reject_false(self.non_production, "nonProduction")
        return self


class RecipeBuilderSession(_AuthoringModel):
    session_id: str = Field(alias="sessionId")
    bot_id: str = Field(alias="botId")
    owner_id: str = Field(alias="ownerId")
    mode: Literal["recipe_builder"] = "recipe_builder"
    temporary: StrictBool = True
    separate_agent_identity: StrictBool = Field(default=False, alias="separateAgentIdentity")
    activation_eligibility: StrictBool = Field(default=False, alias="activationEligibility")
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    authoring_tool_allowlist: tuple[AuthoringToolName, ...] = Field(
        default=_DEFAULT_AUTHORING_TOOL_ALLOWLIST,
        alias="authoringToolAllowlist",
    )
    title: str
    current_phase: str = Field(alias="currentPhase")
    phases: tuple[BuilderPhase, ...]
    questions: tuple[BuilderQuestion, ...]
    answers: tuple[BuilderAnswer, ...] = ()
    draft: RecipePackDraft | None = None
    gap_reports: tuple[BuilderGapReport, ...] = Field(default=(), alias="gapReports")
    review_summary: BuilderReviewSummary | None = Field(default=None, alias="reviewSummary")

    @field_validator("session_id", "bot_id", "owner_id", "title", "current_phase")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "recipe builder session fields")

    @field_validator("authoring_tool_allowlist")
    @classmethod
    def _reject_empty_tool_allowlist(
        cls, value: tuple[AuthoringToolName, ...]
    ) -> tuple[AuthoringToolName, ...]:
        if not value:
            raise ValueError("authoringToolAllowlist must include authoring tools")
        return value

    @model_validator(mode="after")
    def _require_mode_scope_and_default_off(self) -> RecipeBuilderSession:
        _reject_false(self.temporary, "temporary")
        _reject_true(self.separate_agent_identity, "separateAgentIdentity")
        _reject_true(self.activation_eligibility, "activationEligibility")
        _reject_true(self.activation_enabled, "activationEnabled")
        phase_ids = {phase.phase_id for phase in self.phases}
        if self.current_phase not in phase_ids:
            raise ValueError("currentPhase must reference a declared BuilderPhase")
        if self.draft is not None:
            if self.draft.bot_id != self.bot_id:
                raise ValueError("draft botId must match recipe builder session botId")
            if self.draft.owner_id != self.owner_id:
                raise ValueError("draft ownerId must match recipe builder session ownerId")
            if self.draft.authoring_session_id != self.session_id:
                raise ValueError(
                    "draft authoringSessionId must match recipe builder session sessionId"
                )
        return self


BuilderAgentSession = RecipeBuilderSession


class AuthoringToolScope(_AuthoringModel):
    bot_id: str = Field(alias="botId")
    owner_id: str = Field(alias="ownerId")
    session_id: str = Field(alias="sessionId")
    mode: Literal["recipe_builder"] = "recipe_builder"

    @field_validator("bot_id", "owner_id", "session_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "tool scope")


AuthoringToolSession = RecipeBuilderSession | AuthoringToolScope


def _authoring_tool_scope_ids(scope: AuthoringToolSession) -> tuple[str, str, str]:
    return (scope.bot_id, scope.owner_id, scope.session_id)


def _reject_unsafe_field_names(value: object) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            if isinstance(raw_key, str):
                normalized = _normalize_field_name(raw_key)
                if normalized in _SEPARATE_AGENT_IDENTITY_FIELD_NAMES:
                    raise ValueError("separate Builder Agent identity fields are not accepted")
                if normalized in _RAW_CREDENTIAL_FIELD_NAMES:
                    raise ValueError("raw credential fields are not accepted")
                if normalized in _RAW_IO_FIELD_NAMES:
                    raise ValueError("raw prompt/output fields are not accepted")
                if normalized in _RAW_CODE_FIELD_NAMES:
                    raise ValueError("raw generated code fields are not accepted")
            _reject_unsafe_field_names(nested)
        return

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _reject_unsafe_field_names(nested)


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _redact_unsafe_text(value: str) -> str:
    redacted = _RAW_MODEL_TEXT_RE.sub("[REDACTED]", value)
    return _SECRET_TEXT_RE.sub("[REDACTED]", redacted)


def _reject_true(value: bool, alias: str) -> None:
    if value:
        raise ValueError(f"{alias} cannot be true in PR1 authoring contracts")


def _reject_false(value: bool, alias: str) -> None:
    if not value:
        raise ValueError(f"{alias} cannot be false in authoring fixtures")


def _require_non_empty(value: str, field_label: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_label} must be non-empty")
    return value


def _require_non_empty_strings(values: tuple[str, ...], field_label: str) -> None:
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field_label} must contain non-empty strings")


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value
