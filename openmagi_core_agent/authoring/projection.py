from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from openmagi_core_agent.authoring.compiler import (
    CompileRecipePackDiagnostic,
    CompileRecipePackResult,
    HardInvariantResult,
)
from openmagi_core_agent.authoring.contracts import (
    BuilderAnswer,
    BuilderGap,
    BuilderGapReport,
    BuilderPhase,
    BuilderQuestion,
    GeneratedPluginProposal,
    RecipeBuilderSession,
    RecipePackDraft,
)
from openmagi_core_agent.authoring.dry_run import (
    DryRunRecipePackResult,
    DryRunRecipePackWarning,
)
from openmagi_core_agent.authoring.harness import RecipeBuilderModeState


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    hide_input_in_errors=True,
)
_PROJECTION_VERSION = "recipe_builder_projection.v1"
_ACTIVATION_NEVER_REASONS = ("authoring_projection_never_activates",)
_SAVE_STATUS_ALLOWLIST = frozenset(
    {"not_saved", "draft_saved", "save_failed", "authoring_only"}
)
_PRIVATE_REF_PREFIXES = (
    "/",
    "~",
    ".",
    "file:",
    "s3:",
    "gs:",
    "gcs:",
    "postgres:",
    "postgresql:",
    "supabase:",
    "vault:",
)
_URI_USERINFO_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/?#\s]*:[^/?#\s]*@")
_SIGNED_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|^)(?:x-amz-signature|x-amz-credential|x-goog-signature|"
    r"x-goog-credential|signature|sig|access_key|accesskey)="
)
_SIGNED_URL_TEXT_RE = re.compile(
    r"(?i)https?://[^\s,;)]+[?&](?:x-amz-signature|x-amz-credential|"
    r"x-goog-signature|x-goog-credential|signature|sig|access_key|accesskey)="
    r"[^\s,;)]*"
)
_UNSAFE_REF_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._-]{8,}|"
    r"(?:api[_-]?key|apikey|token|secret|password|credential)\s*[:=?&]\s*[^\s,;]+|"
    r"raw[\s_-]*(?:model[\s_-]*)?(?:prompt|output)|"
    r"hidden[\s_-]*instructions?|hidden[\s_-]*transcript|chain[\s_-]*of[\s_-]*thought|"
    r"tool\s+result\s+payload|"
    r"activation[\s_-]*(?:plan|ready|eligible|eligibility|enabled)|"
    r"activation(?:plan|ready|eligible|eligibility|enabled))"
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._-]{8,}|sk-(?:live|test)-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|apikey|token|secret|password|credential)\s*[:=?&]\s*[^\s,;]+)"
)
_RAW_MODEL_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"raw[\s_-]*model[\s_-]*output|raw[\s_-]*output|raw[\s_-]*prompt|"
    r"hidden[\s_-]*instructions?|hidden[\s_-]*transcript|"
    r"chain[\s_-]*of[\s_-]*thought|tool[\s_-]*result[\s_-]*payload"
    r")\b\s*:?\s*[^.!?\n]*(?:[.!?])?"
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"https?://[^/?#\s]*:[^/?#\s]*@[^\s,;)]+|"
    r"(?:file|vault|postgres|postgresql|supabase|gcs|s3|gs)://[^\s,;)]+|"
    r"(?:/Users|/home|/root|/workspace|/app|/private|/var|/tmp|/etc|/opt|/srv|/mnt|~)"
    r"/[^\s,;)]+|"
    r"(?:\.\.?/)[^\s,;)]+|"
    r"(?:infra|apps|src|scripts|supabase|memory|outputs|tests|openmagi_core_agent|"
    r"\.claude|\.worktrees)/[^\s,;)]+|"
    r"[A-Za-z]:\\[^\s,;)]+"
    r")"
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class _ProjectionModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for authoring projection contracts")

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


class ProjectionSessionSummary(_ProjectionModel):
    session_id: str = Field(alias="sessionId")
    bot_id: str = Field(alias="botId")
    owner_id: str = Field(alias="ownerId")
    title: str
    temporary: bool
    mode: Literal["recipe_builder"] = "recipe_builder"


class ProjectionPhase(_ProjectionModel):
    phase_id: str = Field(alias="phaseId")
    title: str
    status: str
    question_ids: tuple[str, ...] = Field(default=(), alias="questionIds")


class ProjectionPhaseState(_ProjectionModel):
    current: str
    session_current_phase: str = Field(alias="sessionCurrentPhase")
    phases: tuple[ProjectionPhase, ...]
    blocked_reasons: tuple[str, ...] = Field(default=(), alias="blockedReasons")
    required_question_ids: tuple[str, ...] = Field(default=(), alias="requiredQuestionIds")
    unanswered_required_question_ids: tuple[str, ...] = Field(
        default=(),
        alias="unansweredRequiredQuestionIds",
    )


class ProjectionQuestionAnswer(_ProjectionModel):
    answered: bool
    answer_text: str | None = Field(default=None, alias="answerText")
    selected_refs: tuple[str, ...] = Field(default=(), alias="selectedRefs")


class ProjectionQuestion(_ProjectionModel):
    question_id: str = Field(alias="questionId")
    phase_id: str = Field(alias="phaseId")
    question_text: str = Field(alias="questionText")
    required: bool
    answer_kind: str = Field(alias="answerKind")
    answer: ProjectionQuestionAnswer


class ProjectionGeneratedPluginProposal(_ProjectionModel):
    proposal_id: str = Field(alias="proposalId")
    status: str
    name: str
    reason: str
    executable: Literal[False] = False
    review_required: Literal[True] = Field(default=True, alias="reviewRequired")
    code_visibility: Literal["proposal_only_non_executable"] = Field(
        default="proposal_only_non_executable",
        alias="codeVisibility",
    )


class ProjectionDraftSummary(_ProjectionModel):
    draft_id: str = Field(alias="draftId")
    status: str
    save_target: str = Field(alias="saveTarget")
    pack_id: str = Field(alias="packId")
    title: str
    summary: str
    recipe_refs: tuple[str, ...] = Field(default=(), alias="recipeRefs")
    generated_plugin_proposals: tuple[ProjectionGeneratedPluginProposal, ...] = Field(
        default=(),
        alias="generatedPluginProposals",
    )


class ProjectionDiagnostic(_ProjectionModel):
    code: str
    message: str
    severity: str = "error"
    path: str | None = None
    ref: str | None = None


class ProjectionHardInvariantResult(_ProjectionModel):
    invariant_id: str = Field(alias="invariantId")
    ok: bool
    mode: str
    message: str


class ProjectionCompileResult(_ProjectionModel):
    ok: bool
    compiled_snapshot_digest: str | None = Field(default=None, alias="compiledSnapshotDigest")
    effective_policy_snapshot_digest: str | None = Field(
        default=None,
        alias="effectivePolicySnapshotDigest",
    )
    diagnostics: tuple[ProjectionDiagnostic, ...] = ()
    warnings: tuple[ProjectionDiagnostic, ...] = ()
    blocked_reasons: tuple[str, ...] = Field(default=(), alias="blockedReasons")
    hard_invariant_results: tuple[ProjectionHardInvariantResult, ...] = Field(
        default=(),
        alias="hardInvariantResults",
    )


class ProjectionDryRunResult(_ProjectionModel):
    ok: bool
    selected_route: str | None = Field(default=None, alias="selectedRoute")
    context_projection: str | None = Field(default=None, alias="contextProjection")
    expected_tools: tuple[str, ...] = Field(default=(), alias="expectedTools")
    expected_evidence: tuple[str, ...] = Field(default=(), alias="expectedEvidence")
    expected_validators: tuple[str, ...] = Field(default=(), alias="expectedValidators")
    expected_approvals: tuple[str, ...] = Field(default=(), alias="expectedApprovals")
    predicted_terminal_states: tuple[str, ...] = Field(
        default=(),
        alias="predictedTerminalStates",
    )
    denied_actions: tuple[str, ...] = Field(default=(), alias="deniedActions")
    warnings: tuple[ProjectionDiagnostic, ...] = ()
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )


class ProjectionGap(_ProjectionModel):
    gap_id: str = Field(alias="gapId")
    kind: str
    status: str
    title: str
    details: str
    missing_refs: tuple[str, ...] = Field(default=(), alias="missingRefs")
    blocked_activation: Literal[True] = Field(default=True, alias="blockedActivation")


class ProjectionGapReport(_ProjectionModel):
    report_id: str = Field(alias="reportId")
    session_id: str = Field(alias="sessionId")
    draft_id: str = Field(alias="draftId")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    non_production: Literal[True] = Field(default=True, alias="nonProduction")
    gaps: tuple[ProjectionGap, ...]


class ProjectionSaveState(_ProjectionModel):
    status: Literal["not_saved", "draft_saved", "save_failed", "authoring_only"]
    can_save_draft: bool = Field(default=False, alias="canSaveDraft")
    draft_id: str | None = Field(default=None, alias="draftId")
    revision: int | None = None
    draft_digest: str | None = Field(default=None, alias="draftDigest")


class ProjectionActivationState(_ProjectionModel):
    eligible: Literal[False] = False
    enabled: Literal[False] = False
    reasons: tuple[str, ...] = _ACTIVATION_NEVER_REASONS


class RecipeBuilderProjection(_ProjectionModel):
    projection_version: Literal["recipe_builder_projection.v1"] = Field(
        default=_PROJECTION_VERSION,
        alias="projectionVersion",
    )
    session: ProjectionSessionSummary
    phase: ProjectionPhaseState
    questions: tuple[ProjectionQuestion, ...]
    draft_summary: ProjectionDraftSummary | None = Field(
        default=None,
        alias="draftSummary",
    )
    compile_result: ProjectionCompileResult | None = Field(
        default=None,
        alias="compileResult",
    )
    dry_run_result: ProjectionDryRunResult | None = Field(
        default=None,
        alias="dryRunResult",
    )
    gap_report: ProjectionGapReport | None = Field(default=None, alias="gapReport")
    save_state: ProjectionSaveState = Field(alias="saveState")
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )
    activation: ProjectionActivationState = Field(default_factory=ProjectionActivationState)


def build_recipe_builder_projection(
    session: RecipeBuilderSession | Mapping[str, object],
    *,
    state: RecipeBuilderModeState | Mapping[str, object] | None = None,
    compile_result: CompileRecipePackResult | Mapping[str, object] | None = None,
    dry_run_result: DryRunRecipePackResult | Mapping[str, object] | None = None,
    gap_report: BuilderGapReport | Mapping[str, object] | None = None,
    save_state: Mapping[str, object] | None = None,
) -> RecipeBuilderProjection:
    builder_session = _coerce_session(session)
    builder_state = _coerce_state(state)
    coerced_compile = _coerce_compile_result(compile_result)
    coerced_dry_run = _coerce_dry_run_result(dry_run_result)
    explicit_gap_report = _coerce_gap_report(gap_report)
    coerced_gap_report = (
        _trusted_gap_report(builder_session, explicit_gap_report)
        if explicit_gap_report is not None
        else _latest_gap_report(builder_session)
    )
    state_scope_issue = _state_scope_issue(builder_session, builder_state)
    trusted_state = None if state_scope_issue is not None else builder_state

    return RecipeBuilderProjection(
        session=_project_session(builder_session),
        phase=_project_phase(
            builder_session,
            trusted_state,
            state_scope_issue=state_scope_issue,
        ),
        questions=_project_questions(builder_session),
        draftSummary=_project_draft_summary(builder_session.draft),
        compileResult=_project_compile_result(coerced_compile),
        dryRunResult=_project_dry_run_result(coerced_dry_run),
        gapReport=_project_gap_report(coerced_gap_report),
        saveState=_project_save_state(
            session=builder_session,
            builder_state=trusted_state,
            save_state=save_state,
        ),
        activationEligibility=False,
        activation=ProjectionActivationState(),
    )


def _coerce_session(session: RecipeBuilderSession | Mapping[str, object]) -> RecipeBuilderSession:
    if isinstance(session, RecipeBuilderSession):
        return session
    if not isinstance(session, Mapping):
        raise TypeError("session must be a RecipeBuilderSession or mapping")
    return RecipeBuilderSession.model_validate(_sanitize_session_payload(session))


def _coerce_state(
    state: RecipeBuilderModeState | Mapping[str, object] | None,
) -> RecipeBuilderModeState | None:
    if state is None or isinstance(state, RecipeBuilderModeState):
        return state
    return RecipeBuilderModeState.model_validate(_sanitize_state_payload(state))


def _coerce_compile_result(
    result: CompileRecipePackResult | Mapping[str, object] | None,
) -> CompileRecipePackResult | None:
    if result is None or isinstance(result, CompileRecipePackResult):
        return result
    return CompileRecipePackResult.model_validate(_sanitize_compile_payload(result))


def _coerce_dry_run_result(
    result: DryRunRecipePackResult | Mapping[str, object] | None,
) -> DryRunRecipePackResult | None:
    if result is None or isinstance(result, DryRunRecipePackResult):
        return result
    return DryRunRecipePackResult.model_validate(_sanitize_dry_run_payload(result))


def _coerce_gap_report(
    gap_report: BuilderGapReport | Mapping[str, object] | None,
) -> BuilderGapReport | None:
    if gap_report is None or isinstance(gap_report, BuilderGapReport):
        return gap_report
    return BuilderGapReport.model_validate(_sanitize_gap_report_payload(gap_report))


def _project_session(session: RecipeBuilderSession) -> ProjectionSessionSummary:
    return ProjectionSessionSummary(
        sessionId=_safe_text(session.session_id),
        botId=_safe_text(session.bot_id),
        ownerId=_safe_text(session.owner_id),
        title=_safe_text(session.title),
        temporary=session.temporary,
    )


def _project_phase(
    session: RecipeBuilderSession,
    state: RecipeBuilderModeState | None,
    *,
    state_scope_issue: str | None = None,
) -> ProjectionPhaseState:
    blocked_reasons = (
        (state_scope_issue,) if state_scope_issue is not None else ()
    ) + (state.blocked_reasons if state is not None else ())
    return ProjectionPhaseState(
        current=_safe_text(state.phase if state is not None else session.current_phase),
        sessionCurrentPhase=_safe_text(session.current_phase),
        phases=tuple(_project_declared_phase(phase) for phase in session.phases),
        blockedReasons=_safe_refs(blocked_reasons),
        requiredQuestionIds=_safe_refs(state.required_question_ids if state is not None else ()),
        unansweredRequiredQuestionIds=_safe_refs(
            state.unanswered_required_question_ids if state is not None else ()
        ),
    )


def _project_declared_phase(phase: BuilderPhase) -> ProjectionPhase:
    return ProjectionPhase(
        phaseId=_safe_text(phase.phase_id),
        title=_safe_text(phase.title),
        status=_safe_text(phase.status),
        questionIds=_safe_refs(phase.question_ids),
    )


def _project_questions(session: RecipeBuilderSession) -> tuple[ProjectionQuestion, ...]:
    answers_by_question = {answer.question_id: answer for answer in session.answers}
    return tuple(
        _project_question(question, answers_by_question.get(question.question_id))
        for question in session.questions
    )


def _project_question(
    question: BuilderQuestion,
    answer: BuilderAnswer | None,
) -> ProjectionQuestion:
    answer_text = (
        None
        if answer is None or answer.answer_text is None
        else _safe_text(answer.answer_text)
    )
    selected_refs = _safe_refs(answer.selected_refs if answer is not None else ())
    return ProjectionQuestion(
        questionId=_safe_text(question.question_id),
        phaseId=_safe_text(question.phase_id),
        questionText=_safe_text(question.question_text),
        required=question.required,
        answerKind=_safe_text(question.answer_kind),
        answer=ProjectionQuestionAnswer(
            answered=bool((answer_text and answer_text.strip()) or selected_refs),
            answerText=answer_text,
            selectedRefs=selected_refs,
        ),
    )


def _project_draft_summary(draft: RecipePackDraft | None) -> ProjectionDraftSummary | None:
    if draft is None:
        return None
    pack = draft.pack
    return ProjectionDraftSummary(
        draftId=_safe_text(draft.draft_id),
        status=_safe_text(draft.status),
        saveTarget=_safe_text(draft.save_target),
        packId=_safe_text(pack.pack_id),
        title=_safe_text(pack.title),
        summary=_safe_text(pack.summary),
        recipeRefs=_safe_refs(pack.recipe_refs),
        generatedPluginProposals=tuple(
            _project_generated_plugin_proposal(proposal)
            for proposal in draft.generated_plugin_proposals
        ),
    )


def _project_generated_plugin_proposal(
    proposal: GeneratedPluginProposal,
) -> ProjectionGeneratedPluginProposal:
    return ProjectionGeneratedPluginProposal(
        proposalId=_safe_text(proposal.proposal_id),
        status=_safe_text(proposal.status),
        name=_safe_text(proposal.name),
        reason=_safe_text(proposal.reason),
        executable=False,
        reviewRequired=True,
        codeVisibility="proposal_only_non_executable",
    )


def _project_compile_result(
    result: CompileRecipePackResult | None,
) -> ProjectionCompileResult | None:
    if result is None:
        return None
    return ProjectionCompileResult(
        ok=result.ok,
        compiledSnapshotDigest=_safe_digest(result.compiled_snapshot_digest),
        effectivePolicySnapshotDigest=_safe_digest(result.effective_policy_snapshot_digest),
        diagnostics=tuple(_project_compile_diagnostic(item) for item in result.diagnostics),
        warnings=tuple(_project_compile_diagnostic(item) for item in result.warnings),
        blockedReasons=_safe_refs(result.blocked_reasons),
        hardInvariantResults=tuple(
            _project_hard_invariant_result(item) for item in result.hard_invariant_results
        ),
    )


def _project_compile_diagnostic(
    diagnostic: CompileRecipePackDiagnostic | DryRunRecipePackWarning,
) -> ProjectionDiagnostic:
    return ProjectionDiagnostic(
        code=_safe_text(diagnostic.code),
        message=_safe_text(diagnostic.message),
        severity=_safe_text(getattr(diagnostic, "severity", "warning")),
        path=_safe_ref_or_none(diagnostic.path),
        ref=_safe_ref_or_none(diagnostic.ref),
    )


def _project_hard_invariant_result(
    result: HardInvariantResult,
) -> ProjectionHardInvariantResult:
    return ProjectionHardInvariantResult(
        invariantId=_safe_text(result.invariant_id),
        ok=result.ok,
        mode=_safe_text(result.mode),
        message=_safe_text(result.message),
    )


def _project_dry_run_result(
    result: DryRunRecipePackResult | None,
) -> ProjectionDryRunResult | None:
    if result is None:
        return None
    return ProjectionDryRunResult(
        ok=result.ok,
        selectedRoute=_safe_ref_or_none(result.selected_route),
        contextProjection=_safe_ref_or_none(result.context_projection),
        expectedTools=_safe_refs(result.expected_tools),
        expectedEvidence=_safe_refs(result.expected_evidence),
        expectedValidators=_safe_refs(result.expected_validators),
        expectedApprovals=_safe_refs(result.expected_approvals),
        predictedTerminalStates=_safe_refs(result.predicted_terminal_states),
        deniedActions=_safe_refs(result.denied_actions),
        warnings=tuple(_project_compile_diagnostic(item) for item in result.warnings),
        activationEligibility=False,
    )


def _latest_gap_report(session: RecipeBuilderSession) -> BuilderGapReport | None:
    for report in reversed(session.gap_reports):
        trusted = _trusted_gap_report(session, report)
        if trusted is not None:
            return trusted
    return None


def _project_gap_report(report: BuilderGapReport | None) -> ProjectionGapReport | None:
    if report is None:
        return None
    return ProjectionGapReport(
        reportId=_safe_text(report.report_id),
        sessionId=_safe_text(report.session_id),
        draftId=_safe_text(report.draft_id),
        localOnly=True,
        nonProduction=True,
        gaps=tuple(_project_gap(gap) for gap in report.gaps),
    )


def _project_gap(gap: BuilderGap) -> ProjectionGap:
    return ProjectionGap(
        gapId=_safe_text(gap.gap_id),
        kind=_safe_text(gap.kind),
        status=_safe_text(gap.status),
        title=_safe_text(gap.title),
        details=_safe_text(gap.details),
        missingRefs=_safe_refs(gap.missing_refs),
        blockedActivation=True,
    )


def _project_save_state(
    *,
    session: RecipeBuilderSession,
    builder_state: RecipeBuilderModeState | None,
    save_state: Mapping[str, object] | None,
) -> ProjectionSaveState:
    can_save_draft = builder_state.can_save_draft if builder_state is not None else False
    expected_draft_id = session.draft.draft_id if session.draft is not None else None
    draft_id = _safe_ref_or_none(builder_state.save_draft_id) if builder_state is not None else None
    if expected_draft_id is None or draft_id != expected_draft_id:
        draft_id = None
        can_save_draft = False
    status = "not_saved"
    revision = None
    draft_digest = None

    if save_state is not None:
        raw_draft_id = save_state.get("draftId", save_state.get("draft_id"))
        save_draft_id = _safe_ref_or_none(raw_draft_id)
        trusted_save_state = (
            can_save_draft
            and expected_draft_id is not None
            and save_draft_id == expected_draft_id
        )
        if trusted_save_state:
            draft_id = save_draft_id
            raw_status = save_state.get("status")
            status = _safe_save_status(raw_status)
            raw_revision = save_state.get("revision")
            if isinstance(raw_revision, int) and raw_revision >= 1:
                revision = raw_revision
            raw_digest = save_state.get("draftDigest", save_state.get("draft_digest"))
            if isinstance(raw_digest, str):
                draft_digest = _safe_digest(raw_digest)

    return ProjectionSaveState(
        status=status,
        canSaveDraft=can_save_draft,
        draftId=_safe_ref_or_none(draft_id),
        revision=revision,
        draftDigest=draft_digest,
    )


def _sanitize_session_payload(data: Mapping[str, object]) -> dict[str, object]:
    sanitized = _pick(
        data,
        (
            "sessionId",
            "botId",
            "ownerId",
            "mode",
            "temporary",
            "authoringToolAllowlist",
            "title",
            "currentPhase",
            "reviewSummary",
        ),
    )
    sanitized["separateAgentIdentity"] = False
    sanitized["activationEligibility"] = False
    sanitized["activationEnabled"] = False
    sanitized["phases"] = tuple(
        _sanitize_phase_payload(item) for item in _items(data.get("phases"))
    )
    sanitized["questions"] = tuple(
        _sanitize_question_payload(item) for item in _items(data.get("questions"))
    )
    sanitized["answers"] = tuple(
        _sanitize_answer_payload(item) for item in _items(data.get("answers"))
    )
    if isinstance(data.get("draft"), Mapping):
        sanitized["draft"] = _sanitize_draft_payload(data["draft"])
    if data.get("gapReports") is not None:
        sanitized["gapReports"] = tuple(
            _sanitize_gap_report_payload(item)
            for item in _items(data.get("gapReports"))
            if isinstance(item, Mapping)
        )
    return sanitized


def _sanitize_phase_payload(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return _pick(value, ("phaseId", "title", "status", "questionIds"))


def _sanitize_question_payload(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return _pick(value, ("questionId", "phaseId", "questionText", "required", "answerKind"))


def _sanitize_answer_payload(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return _pick(value, ("questionId", "answerText", "selectedRefs"))


def _sanitize_draft_payload(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    sanitized = _pick(
        value,
        ("draftId", "botId", "ownerId", "authoringSessionId", "status", "saveTarget"),
    )
    sanitized["activationEligibility"] = False
    sanitized["activationEnabled"] = False
    if isinstance(value.get("pack"), Mapping):
        sanitized["pack"] = _sanitize_pack_payload(value["pack"])
    proposals = [
        _sanitize_plugin_proposal_payload(item)
        for item in _items(value.get("generatedPluginProposals"))
        if isinstance(item, Mapping)
    ]
    if proposals:
        sanitized["generatedPluginProposals"] = tuple(proposals)
    return sanitized


def _sanitize_pack_payload(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    sanitized = _pick(
        value,
        (
            "packId",
            "title",
            "summary",
            "recipeRefs",
            "harnessPolicy",
            "toolPolicy",
            "evidencePolicy",
            "validatorPolicy",
            "approvalPolicy",
            "projectionPolicy",
            "repairPolicy",
            "budgetPolicy",
            "hardInvariants",
        ),
    )
    if isinstance(sanitized.get("harnessPolicy"), Mapping):
        sanitized["harnessPolicy"] = {
            **sanitized["harnessPolicy"],
            "allowModelCalls": False,
            "allowLiveExecution": False,
            "allowWorkspaceMutation": False,
            "allowMemoryWrite": False,
            "allowExternalDelivery": False,
            "allowScheduleMutation": False,
        }
    if isinstance(sanitized.get("toolPolicy"), Mapping):
        sanitized["toolPolicy"] = {
            **sanitized["toolPolicy"],
            "generatedPluginExecutionAllowed": False,
            "allowLiveConnectors": False,
            "connectorCredentialReadsAllowed": False,
            "connectorCredentialsExposed": False,
        }
    if isinstance(sanitized.get("evidencePolicy"), Mapping):
        sanitized["evidencePolicy"] = {
            **sanitized["evidencePolicy"],
            "captureModelIo": False,
        }
    if isinstance(sanitized.get("approvalPolicy"), Mapping):
        sanitized["approvalPolicy"] = {
            **sanitized["approvalPolicy"],
            "allowAutoActivation": False,
        }
    if isinstance(sanitized.get("projectionPolicy"), Mapping):
        sanitized["projectionPolicy"] = {
            **sanitized["projectionPolicy"],
            "redactUnsafeText": True,
            "exposeModelIo": False,
            "rawGovernedProjectionEnabled": False,
        }
    return sanitized


def _sanitize_plugin_proposal_payload(value: Mapping[str, object]) -> dict[str, object]:
    sanitized = _pick(value, ("proposalId", "status", "name", "reason", "reviewRequired"))
    sanitized["executable"] = False
    sanitized["reviewRequired"] = True
    return sanitized


def _sanitize_state_payload(value: Mapping[str, object]) -> dict[str, object]:
    sanitized = _pick(
        value,
        (
            "phase",
            "sessionId",
            "botId",
            "ownerId",
            "modeTemporary",
            "repairAttempts",
            "maxRepairAttempts",
            "requiredQuestionIds",
            "unansweredRequiredQuestionIds",
            "blockedReasons",
            "compileOk",
            "dryRunOk",
            "canSaveDraft",
            "saveDraftId",
            "gapReportId",
        ),
    )
    sanitized["separateAgentIdentity"] = False
    sanitized["activationEligibility"] = False
    sanitized["activationEnabled"] = False
    return sanitized


def _sanitize_compile_payload(value: Mapping[str, object]) -> dict[str, object]:
    sanitized = _pick(
        value,
        (
            "ok",
            "compiledSnapshotDigest",
            "effectivePolicySnapshotDigest",
            "blockedReasons",
        ),
    )
    sanitized["diagnostics"] = tuple(
        _sanitize_diagnostic_payload(item)
        for item in _items(value.get("diagnostics"))
        if isinstance(item, Mapping)
    )
    sanitized["warnings"] = tuple(
        _sanitize_diagnostic_payload(item)
        for item in _items(value.get("warnings"))
        if isinstance(item, Mapping)
    )
    sanitized["hardInvariantResults"] = tuple(
        _pick(item, ("invariantId", "ok", "mode", "message"))
        for item in _items(value.get("hardInvariantResults"))
        if isinstance(item, Mapping)
    )
    return sanitized


def _sanitize_dry_run_payload(value: Mapping[str, object]) -> dict[str, object]:
    sanitized = _pick(
        value,
        (
            "ok",
            "selectedRoute",
            "contextProjection",
            "expectedTools",
            "expectedEvidence",
            "expectedValidators",
            "expectedApprovals",
            "predictedTerminalStates",
            "deniedActions",
        ),
    )
    sanitized["activationEligibility"] = False
    sanitized["warnings"] = tuple(
        _sanitize_diagnostic_payload(item)
        for item in _items(value.get("warnings"))
        if isinstance(item, Mapping)
    )
    return sanitized


def _sanitize_gap_report_payload(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    sanitized = _pick(value, ("reportId", "sessionId", "draftId"))
    sanitized["localOnly"] = True
    sanitized["nonProduction"] = True
    sanitized["gaps"] = tuple(
        _sanitize_gap_payload(item)
        for item in _items(value.get("gaps"))
        if isinstance(item, Mapping)
    )
    return sanitized


def _sanitize_gap_payload(value: Mapping[str, object]) -> dict[str, object]:
    sanitized = _pick(value, ("gapId", "kind", "status", "title", "details", "missingRefs"))
    sanitized["blockedActivation"] = True
    return sanitized


def _sanitize_diagnostic_payload(value: Mapping[str, object]) -> dict[str, object]:
    return _pick(value, ("code", "message", "severity", "path", "ref"))


def _safe_text(value: object) -> str:
    if not isinstance(value, str):
        value = str(value)
    redacted = _SIGNED_URL_TEXT_RE.sub("[REDACTED]", value)
    redacted = _PRIVATE_TEXT_RE.sub("[REDACTED]", redacted)
    redacted = _RAW_MODEL_TEXT_RE.sub("[REDACTED]", redacted)
    redacted = _SECRET_TEXT_RE.sub("[REDACTED]", redacted)
    return redacted.strip()


def _safe_refs(values: Sequence[str] | tuple[str, ...]) -> tuple[str, ...]:
    safe: list[str] = []
    for value in values:
        public = _safe_ref_or_none(value)
        if public is not None:
            safe.append(public)
    return tuple(safe)


def _safe_ref_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    public = value.strip()
    if not public:
        return None
    lowered = public.lower()
    if _DIGEST_RE.fullmatch(public):
        return public
    if (
        lowered.startswith(_PRIVATE_REF_PREFIXES)
        or "\\" in public
        or "/" in public
        or (len(public) > 2 and public[1] == ":" and public[2] in {"/", "\\"})
    ):
        return None
    if _URI_USERINFO_RE.search(public) or _SIGNED_QUERY_RE.search(public):
        return None
    if _UNSAFE_REF_RE.search(public):
        return None
    redacted = _safe_text(public)
    if redacted != public:
        return None
    return public


def _safe_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _DIGEST_RE.fullmatch(value):
        return value
    return None


def _safe_save_status(value: object) -> Literal[
    "not_saved",
    "draft_saved",
    "save_failed",
    "authoring_only",
]:
    if not isinstance(value, str) or not value.strip():
        return "not_saved"
    status = _safe_ref_or_none(value)
    if status in _SAVE_STATUS_ALLOWLIST:
        return status  # type: ignore[return-value]
    return "authoring_only"


def _pick(value: Mapping[str, object], keys: tuple[str, ...]) -> dict[str, object]:
    return {key: value[key] for key in keys if key in value}


def _items(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(value)
    return ()


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _state_scope_issue(
    session: RecipeBuilderSession,
    state: RecipeBuilderModeState | None,
) -> str | None:
    if state is None:
        return None
    if state.session_id is None or state.bot_id is None or state.owner_id is None:
        return "state_scope_missing"
    if (
        state.session_id != session.session_id
        or state.bot_id != session.bot_id
        or state.owner_id != session.owner_id
    ):
        return "state_scope_mismatch"
    return None


def _trusted_gap_report(
    session: RecipeBuilderSession,
    report: BuilderGapReport | None,
) -> BuilderGapReport | None:
    if report is None or session.draft is None:
        return None
    if report.session_id != session.session_id:
        return None
    if report.draft_id != session.draft.draft_id:
        return None
    return report
