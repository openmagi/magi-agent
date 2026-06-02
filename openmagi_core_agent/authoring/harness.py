from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openmagi_core_agent.authoring.compiler import CompileRecipePackResult
from openmagi_core_agent.authoring.contracts import (
    BuilderGapReport,
    RecipeBuilderSession,
)
from openmagi_core_agent.authoring.dry_run import DryRunRecipePackResult
from openmagi_core_agent.authoring.tool_contracts import SaveRecipePackDraft


RecipeBuilderModePhase = Literal[
    "deep_interview",
    "inspect_docs_and_catalogs",
    "draft_recipe_pack",
    "compile",
    "dry_run",
    "repair",
    "review",
    "gap_report",
    "save_draft",
    "blocked",
]
RecipeBuilderModeEvent = Literal[
    "user_request",
    "answers_complete",
    "docs_and_catalogs_inspected",
    "draft_requested",
    "draft_ready",
    "compile_finished",
    "dry_run_finished",
    "review_finished",
    "gap_reported",
    "save_requested",
]

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class _HarnessModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for authoring harness contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class RecipeBuilderModeConfig(_HarnessModel):
    max_repair_attempts: int = Field(default=1, ge=0, le=3, alias="maxRepairAttempts")
    require_compile: bool = Field(default=True, alias="requireCompile")
    require_dry_run: bool = Field(default=True, alias="requireDryRun")


class RecipeBuilderModeState(_HarnessModel):
    phase: RecipeBuilderModePhase
    session_id: str | None = Field(default=None, alias="sessionId")
    bot_id: str | None = Field(default=None, alias="botId")
    owner_id: str | None = Field(default=None, alias="ownerId")
    mode_temporary: bool = Field(default=True, alias="modeTemporary")
    separate_agent_identity: Literal[False] = Field(
        default=False,
        alias="separateAgentIdentity",
    )
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )
    activation_enabled: Literal[False] = Field(default=False, alias="activationEnabled")
    repair_attempts: int = Field(default=0, ge=0, le=3, alias="repairAttempts")
    max_repair_attempts: int = Field(default=1, ge=0, le=3, alias="maxRepairAttempts")
    required_question_ids: tuple[str, ...] = Field(
        default=(),
        alias="requiredQuestionIds",
    )
    unanswered_required_question_ids: tuple[str, ...] = Field(
        default=(),
        alias="unansweredRequiredQuestionIds",
    )
    blocked_reasons: tuple[str, ...] = Field(default=(), alias="blockedReasons")
    compile_ok: bool | None = Field(default=None, alias="compileOk")
    dry_run_ok: bool | None = Field(default=None, alias="dryRunOk")
    can_save_draft: bool = Field(default=False, alias="canSaveDraft")
    save_draft_id: str | None = Field(default=None, alias="saveDraftId")
    gap_report_id: str | None = Field(default=None, alias="gapReportId")


def advance_recipe_builder_mode(
    session: RecipeBuilderSession | Mapping[str, object],
    *,
    event: RecipeBuilderModeEvent,
    previous_state: RecipeBuilderModeState | Mapping[str, object] | None = None,
    config: RecipeBuilderModeConfig | Mapping[str, object] | None = None,
    compile_result: CompileRecipePackResult | Mapping[str, object] | None = None,
    dry_run_result: DryRunRecipePackResult | Mapping[str, object] | None = None,
    gap_report: BuilderGapReport | Mapping[str, object] | None = None,
    save_draft: SaveRecipePackDraft | Mapping[str, object] | None = None,
) -> RecipeBuilderModeState:
    mode_config = _coerce_config(config)
    prior = _coerce_previous_state(previous_state)
    builder_session = _coerce_session(session)
    if builder_session is None:
        return RecipeBuilderModeState(
            phase="blocked",
            maxRepairAttempts=mode_config.max_repair_attempts,
            blockedReasons=("invalid_recipe_builder_scope",),
        )

    required_question_ids = _required_question_ids(builder_session)
    unanswered_question_ids = _unanswered_required_question_ids(builder_session)
    if prior is not None and not _previous_state_matches_session(prior, builder_session):
        common_without_prior = _common_state(
            builder_session,
            mode_config=mode_config,
            prior=None,
            required_question_ids=required_question_ids,
            unanswered_question_ids=unanswered_question_ids,
        )
        return RecipeBuilderModeState(
            phase="blocked",
            **common_without_prior,
            blockedReasons=("previous_state_scope_mismatch",),
        )

    common = _common_state(
        builder_session,
        mode_config=mode_config,
        prior=prior,
        required_question_ids=required_question_ids,
        unanswered_question_ids=unanswered_question_ids,
    )

    if event == "user_request":
        return RecipeBuilderModeState(phase="deep_interview", **common)

    if event in {"draft_requested", "draft_ready"} and unanswered_question_ids:
        return RecipeBuilderModeState(
            phase="blocked",
            **common,
            blockedReasons=_unanswered_reasons(unanswered_question_ids),
        )

    if event == "draft_requested":
        return RecipeBuilderModeState(phase="draft_recipe_pack", **common)

    if event == "answers_complete":
        if unanswered_question_ids:
            return RecipeBuilderModeState(
                phase="deep_interview",
                **common,
                blockedReasons=_unanswered_reasons(unanswered_question_ids),
            )
        return RecipeBuilderModeState(phase="inspect_docs_and_catalogs", **common)

    if event == "docs_and_catalogs_inspected":
        return RecipeBuilderModeState(phase="draft_recipe_pack", **common)

    if event == "draft_ready":
        return RecipeBuilderModeState(phase="compile", **common)

    if event == "compile_finished":
        result = _coerce_compile_result(compile_result)
        if result is None:
            return RecipeBuilderModeState(phase="compile", **common)
        if result.ok:
            return RecipeBuilderModeState(
                phase="dry_run" if mode_config.require_dry_run else "review",
                **common,
                compileOk=True,
            )
        return _repair_or_block(
            common,
            prior=prior,
            mode_config=mode_config,
            reason_prefix="compile_failed",
            reasons=result.blocked_reasons or ("compile_not_ok",),
            compile_ok=False,
        )

    if event == "dry_run_finished":
        result = _coerce_dry_run_result(dry_run_result)
        if result is None:
            return RecipeBuilderModeState(phase="dry_run", **common)
        if result.ok:
            return RecipeBuilderModeState(phase="review", **common, dryRunOk=True)
        return _repair_or_block(
            common,
            prior=prior,
            mode_config=mode_config,
            reason_prefix="dry_run_failed",
            reasons=result.denied_actions or ("dry_run_not_ok",),
            dry_run_ok=False,
        )

    if event == "review_finished":
        return RecipeBuilderModeState(phase="gap_report", **common)

    if event == "gap_reported":
        report = _coerce_gap_report(gap_report)
        if report is None:
            return RecipeBuilderModeState(phase="gap_report", **common)
        return RecipeBuilderModeState(
            phase="gap_report",
            **common,
            gapReportId=report.report_id,
            blockedReasons=_gap_reasons(report),
        )

    if event == "save_requested":
        return _save_projection(
            builder_session,
            common,
            mode_config=mode_config,
            compile_result=_coerce_compile_result(compile_result),
            dry_run_result=_coerce_dry_run_result(dry_run_result),
            save_draft=_coerce_save_draft(save_draft),
        )

    return RecipeBuilderModeState(phase="blocked", **common, blockedReasons=("unknown_event",))


def _coerce_config(
    config: RecipeBuilderModeConfig | Mapping[str, object] | None,
) -> RecipeBuilderModeConfig:
    if isinstance(config, RecipeBuilderModeConfig):
        return config
    return RecipeBuilderModeConfig.model_validate(config or {})


def _coerce_previous_state(
    previous_state: RecipeBuilderModeState | Mapping[str, object] | None,
) -> RecipeBuilderModeState | None:
    if previous_state is None or isinstance(previous_state, RecipeBuilderModeState):
        return previous_state
    return RecipeBuilderModeState.model_validate(previous_state)


def _coerce_session(
    session: RecipeBuilderSession | Mapping[str, object],
) -> RecipeBuilderSession | None:
    if isinstance(session, RecipeBuilderSession):
        return session
    try:
        return RecipeBuilderSession.model_validate(session)
    except ValidationError:
        return None


def _coerce_compile_result(
    result: CompileRecipePackResult | Mapping[str, object] | None,
) -> CompileRecipePackResult | None:
    if result is None or isinstance(result, CompileRecipePackResult):
        return result
    return CompileRecipePackResult.model_validate(result)


def _coerce_dry_run_result(
    result: DryRunRecipePackResult | Mapping[str, object] | None,
) -> DryRunRecipePackResult | None:
    if result is None or isinstance(result, DryRunRecipePackResult):
        return result
    return DryRunRecipePackResult.model_validate(result)


def _coerce_gap_report(
    gap_report: BuilderGapReport | Mapping[str, object] | None,
) -> BuilderGapReport | None:
    if gap_report is None or isinstance(gap_report, BuilderGapReport):
        return gap_report
    return BuilderGapReport.model_validate(gap_report)


def _coerce_save_draft(
    save_draft: SaveRecipePackDraft | Mapping[str, object] | None,
) -> SaveRecipePackDraft | None:
    if save_draft is None or isinstance(save_draft, SaveRecipePackDraft):
        return save_draft
    return SaveRecipePackDraft.model_validate(save_draft)


def _previous_state_matches_session(
    previous_state: RecipeBuilderModeState,
    session: RecipeBuilderSession,
) -> bool:
    return (
        previous_state.session_id == session.session_id
        and previous_state.bot_id == session.bot_id
        and previous_state.owner_id == session.owner_id
    )


def _common_state(
    session: RecipeBuilderSession,
    *,
    mode_config: RecipeBuilderModeConfig,
    prior: RecipeBuilderModeState | None,
    required_question_ids: tuple[str, ...],
    unanswered_question_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "sessionId": session.session_id,
        "botId": session.bot_id,
        "ownerId": session.owner_id,
        "modeTemporary": session.temporary,
        "repairAttempts": prior.repair_attempts if prior is not None else 0,
        "maxRepairAttempts": mode_config.max_repair_attempts,
        "requiredQuestionIds": required_question_ids,
        "unansweredRequiredQuestionIds": unanswered_question_ids,
        "activationEligibility": False,
        "activationEnabled": False,
        "separateAgentIdentity": False,
    }


def _required_question_ids(session: RecipeBuilderSession) -> tuple[str, ...]:
    return tuple(question.question_id for question in session.questions if question.required)


def _unanswered_required_question_ids(session: RecipeBuilderSession) -> tuple[str, ...]:
    answered = {
        answer.question_id
        for answer in session.answers
        if (answer.answer_text is not None and answer.answer_text.strip())
        or bool(answer.selected_refs)
    }
    return tuple(
        question.question_id
        for question in session.questions
        if question.required and question.question_id not in answered
    )


def _unanswered_reasons(question_ids: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"unanswered_required_question:{question_id}" for question_id in question_ids)


def _repair_or_block(
    common: dict[str, object],
    *,
    prior: RecipeBuilderModeState | None,
    mode_config: RecipeBuilderModeConfig,
    reason_prefix: str,
    reasons: tuple[str, ...],
    compile_ok: bool | None = None,
    dry_run_ok: bool | None = None,
) -> RecipeBuilderModeState:
    current_attempts = prior.repair_attempts if prior is not None else 0
    blocked_reasons = tuple(f"{reason_prefix}:{reason}" for reason in reasons)
    if current_attempts < mode_config.max_repair_attempts:
        return RecipeBuilderModeState.model_validate(
            {
                **common,
                "phase": "repair",
                "repairAttempts": current_attempts + 1,
                "blockedReasons": blocked_reasons,
                "compileOk": compile_ok,
                "dryRunOk": dry_run_ok,
            }
        )
    return RecipeBuilderModeState.model_validate(
        {
            **common,
            "phase": "blocked",
            "repairAttempts": current_attempts,
            "blockedReasons": blocked_reasons + ("repair_attempts_exhausted",),
            "compileOk": compile_ok,
            "dryRunOk": dry_run_ok,
        }
    )


def _gap_reasons(report: BuilderGapReport) -> tuple[str, ...]:
    return tuple(
        f"gap:{gap.kind}:{gap.gap_id}"
        for gap in report.gaps
        if gap.status in {"open", "deferred"}
    )


def _save_projection(
    session: RecipeBuilderSession,
    common: dict[str, object],
    *,
    mode_config: RecipeBuilderModeConfig,
    compile_result: CompileRecipePackResult | None,
    dry_run_result: DryRunRecipePackResult | None,
    save_draft: SaveRecipePackDraft | None,
) -> RecipeBuilderModeState:
    blockers = list(
        _save_gate_blockers(
            mode_config=mode_config,
            compile_result=compile_result,
            dry_run_result=dry_run_result,
        )
    )
    if save_draft is None:
        blockers.append("save_draft_contract_required")

    if session.draft is None:
        blockers.append("missing_recipe_pack_draft")
    elif save_draft is not None and save_draft.draft != session.draft:
        blockers.append("save_draft_contract_mismatch")

    if blockers:
        return RecipeBuilderModeState(
            phase="blocked",
            **common,
            blockedReasons=tuple(blockers),
        )

    assert session.draft is not None
    assert save_draft is not None
    return RecipeBuilderModeState(
        phase="save_draft",
        **common,
        compileOk=compile_result.ok if compile_result is not None else None,
        dryRunOk=dry_run_result.ok if dry_run_result is not None else None,
        canSaveDraft=True,
        saveDraftId=save_draft.draft_id,
    )


def _save_gate_blockers(
    *,
    mode_config: RecipeBuilderModeConfig,
    compile_result: CompileRecipePackResult | None,
    dry_run_result: DryRunRecipePackResult | None,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if mode_config.require_compile:
        if compile_result is None:
            blockers.append("compile_gate_required")
        elif not compile_result.ok:
            blockers.append("compile_gate_failed")
        else:
            if compile_result.compiled_snapshot_digest is None:
                blockers.append("compile_gate_missing_snapshot_digest")
            if compile_result.effective_policy_snapshot_digest is None:
                blockers.append("compile_gate_missing_policy_digest")
    if mode_config.require_dry_run:
        if dry_run_result is None:
            blockers.append("dry_run_gate_required")
        elif not dry_run_result.ok:
            blockers.append("dry_run_gate_failed")
        else:
            if dry_run_result.selected_route is None:
                blockers.append("dry_run_gate_missing_route")
            if dry_run_result.denied_actions:
                blockers.append("dry_run_gate_denied_actions")
    return tuple(blockers)


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key
