from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.authoring import contracts as authoring_contracts
from openmagi_core_agent.authoring.contracts import (
    BuilderAgentSession,
    BuilderAnswer,
    BuilderGap,
    BuilderGapReport,
    BuilderPhase,
    BuilderQuestion,
    BuilderReviewSummary,
    DraftApprovalPolicy,
    DraftEvidencePolicy,
    DraftHarnessPolicy,
    DraftProjectionPolicy,
    DraftRecipePack,
    DraftRepairPolicy,
    DraftToolPolicy,
    DraftValidatorPolicy,
    EvalFixtureSet,
    GeneratedPluginProposal,
    RecipePackDraft,
    RecipePackVersion,
    RecipeBuilderSession,
)


FIXTURES = Path(__file__).parent / "fixtures" / "authoring"
BOT_ID = "bot_recipe_builder_mode_001"
OWNER_ID = "owner_recipe_builder_mode_001"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _minimal_pack() -> DraftRecipePack:
    return DraftRecipePack(
        packId="draft.pack.finance-research",
        title="Finance research analyst",
        summary="Authoring-only draft for cited market research workflows.",
        recipeRefs=("recipe.finance.source-triage",),
        harnessPolicy=DraftHarnessPolicy(),
        toolPolicy=DraftToolPolicy(
            allowedConnectorRefs=("connector.market-data.readonly",),
            allowedToolRefs=("SourceOpen", "CitationVerify"),
            deniedToolRefs=("Bash", "FileWrite"),
        ),
        evidencePolicy=DraftEvidencePolicy(
            requiredEvidenceRefs=("openedSourceSnapshot", "quoteDigest")
        ),
        validatorPolicy=DraftValidatorPolicy(
            validatorRefs=("validator:sourceOpened@1", "validator:quoteExactMatch@1")
        ),
        approvalPolicy=DraftApprovalPolicy(),
        projectionPolicy=DraftProjectionPolicy(),
        repairPolicy=DraftRepairPolicy(maxRepairAttempts=1),
    )


def _minimal_draft(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "draftId": "draft.finance-research.001",
        "botId": BOT_ID,
        "ownerId": OWNER_ID,
        "authoringSessionId": "builder.session.finance-research",
        "status": "draft",
        "pack": _minimal_pack().model_dump(by_alias=True),
    }
    payload.update(overrides)
    return payload


def test_all_pr1_authoring_contracts_are_publicly_importable() -> None:
    contract_types = (
        BuilderAgentSession,
        BuilderPhase,
        BuilderQuestion,
        BuilderAnswer,
        RecipePackDraft,
        RecipePackVersion,
        DraftRecipePack,
        DraftHarnessPolicy,
        DraftToolPolicy,
        DraftEvidencePolicy,
        DraftValidatorPolicy,
        DraftApprovalPolicy,
        DraftProjectionPolicy,
        DraftRepairPolicy,
        EvalFixtureSet,
        BuilderGapReport,
        BuilderReviewSummary,
        RecipeBuilderSession,
    )

    assert all(contract_type.__name__ for contract_type in contract_types)
    assert authoring_contracts.BuilderAgentSession is authoring_contracts.RecipeBuilderSession


def test_recipe_builder_session_is_bot_scoped_temporary_mode_not_separate_agent() -> None:
    session = authoring_contracts.RecipeBuilderSession(
        sessionId="builder.session.mode",
        botId=BOT_ID,
        ownerId=OWNER_ID,
        title="Recipe builder mode session",
        currentPhase="intake",
        phases=(
            {
                "phaseId": "intake",
                "title": "Intake",
                "status": "in_progress",
                "questionIds": ("question.mode.objective",),
            },
        ),
        questions=(
            {
                "questionId": "question.mode.objective",
                "phaseId": "intake",
                "questionText": "What should the current bot draft?",
            },
        ),
        draft=_minimal_draft(
            draftId="draft.mode.001",
            authoringSessionId="builder.session.mode",
        ),
    )

    assert session.mode == "recipe_builder"
    assert session.bot_id == BOT_ID
    assert session.owner_id == OWNER_ID
    assert session.temporary is True
    assert session.separate_agent_identity is False
    assert session.activation_eligibility is False
    assert session.authoring_tool_allowlist == (
        "ask_question",
        "record_answer",
        "save_draft",
        "report_gap",
        "propose_generated_plugin",
    )
    assert session.draft is not None
    assert session.draft.bot_id == BOT_ID
    assert session.draft.owner_id == OWNER_ID
    assert session.draft.save_target == "current_bot_draft_store"
    assert session.model_dump(by_alias=True)["separateAgentIdentity"] is False


def test_recipe_builder_session_requires_bot_and_owner_scope() -> None:
    base: dict[str, object] = {
        "sessionId": "builder.session.scope",
        "botId": BOT_ID,
        "ownerId": OWNER_ID,
        "title": "Scoped recipe builder mode",
        "currentPhase": "intake",
        "phases": (
            {
                "phaseId": "intake",
                "title": "Intake",
                "status": "in_progress",
            },
        ),
        "questions": (),
    }

    missing_bot = dict(base)
    missing_bot.pop("botId")
    with pytest.raises(ValidationError, match="botId"):
        authoring_contracts.RecipeBuilderSession.model_validate(missing_bot)

    missing_owner = dict(base)
    missing_owner.pop("ownerId")
    with pytest.raises(ValidationError, match="ownerId"):
        authoring_contracts.RecipeBuilderSession.model_validate(missing_owner)


def test_recipe_builder_mode_rejects_separate_agent_identity_inputs() -> None:
    base: dict[str, object] = {
        "sessionId": "builder.session.identity",
        "botId": BOT_ID,
        "ownerId": OWNER_ID,
        "title": "Scoped recipe builder mode",
        "currentPhase": "intake",
        "phases": (
            {
                "phaseId": "intake",
                "title": "Intake",
                "status": "in_progress",
            },
        ),
        "questions": (),
    }

    with pytest.raises(ValidationError, match="separate Builder Agent identity"):
        authoring_contracts.RecipeBuilderSession.model_validate(
            {**base, "agentId": "agent.separate.not-allowed"}
        )

    with pytest.raises(ValidationError, match="separate Builder Agent identity"):
        authoring_contracts.RecipeBuilderSession.model_validate(
            {**base, "builderAgentId": "builder-agent.not-allowed"}
        )

    with pytest.raises(ValidationError, match="separateAgentIdentity"):
        authoring_contracts.RecipeBuilderSession.model_validate(
            {**base, "separateAgentIdentity": True}
        )


def test_recipe_builder_session_tool_allowlist_is_authoring_only() -> None:
    with pytest.raises(ValidationError, match="authoringToolAllowlist"):
        authoring_contracts.RecipeBuilderSession(
            sessionId="builder.session.tools",
            botId=BOT_ID,
            ownerId=OWNER_ID,
            title="Invalid tool allowlist",
            currentPhase="intake",
            phases=(
                {
                    "phaseId": "intake",
                    "title": "Intake",
                    "status": "in_progress",
                },
            ),
            questions=(),
            authoringToolAllowlist=("save_draft", "BrowserLive"),
        )


def test_recipe_pack_draft_requires_current_bot_save_scope_and_denies_activation() -> None:
    with pytest.raises(ValidationError, match="botId"):
        RecipePackDraft.model_validate(
            _minimal_draft(botId=None)
        )

    with pytest.raises(ValidationError, match="ownerId"):
        RecipePackDraft.model_validate(
            _minimal_draft(ownerId=None)
        )

    with pytest.raises(ValidationError, match="saveTarget"):
        RecipePackDraft.model_validate(
            _minimal_draft(saveTarget="global_recipe_registry")
        )

    with pytest.raises(ValidationError, match="activationEligibility"):
        RecipePackDraft.model_validate(
            _minimal_draft(activationEligibility=True)
        )


def test_recipe_builder_session_draft_scope_must_match_current_bot_and_owner() -> None:
    with pytest.raises(ValidationError, match="draft botId"):
        authoring_contracts.RecipeBuilderSession(
            sessionId="builder.session.scope-mismatch",
            botId=BOT_ID,
            ownerId=OWNER_ID,
            title="Scope mismatch",
            currentPhase="intake",
            phases=(
                {
                    "phaseId": "intake",
                    "title": "Intake",
                    "status": "in_progress",
                },
            ),
            questions=(),
            draft=_minimal_draft(botId="bot_other"),
        )

    with pytest.raises(ValidationError, match="draft ownerId"):
        authoring_contracts.RecipeBuilderSession(
            sessionId="builder.session.owner-mismatch",
            botId=BOT_ID,
            ownerId=OWNER_ID,
            title="Owner mismatch",
            currentPhase="intake",
            phases=(
                {
                    "phaseId": "intake",
                    "title": "Intake",
                    "status": "in_progress",
                },
            ),
            questions=(),
            draft=_minimal_draft(ownerId="owner_other"),
        )


def test_recipe_builder_session_draft_session_id_must_match_current_session() -> None:
    with pytest.raises(ValidationError, match="draft authoringSessionId"):
        authoring_contracts.RecipeBuilderSession(
            sessionId="builder.session.current",
            botId=BOT_ID,
            ownerId=OWNER_ID,
            title="Session mismatch",
            currentPhase="intake",
            phases=(
                {
                    "phaseId": "intake",
                    "title": "Intake",
                    "status": "in_progress",
                },
            ),
            questions=(),
            draft=_minimal_draft(authoringSessionId="builder.session.other"),
        )


def test_tool_policy_denies_connector_credential_exposure() -> None:
    with pytest.raises(ValidationError, match="connectorCredentialReadsAllowed"):
        DraftToolPolicy.model_validate({"connectorCredentialReadsAllowed": True})

    with pytest.raises(ValidationError, match="connectorCredentialsExposed"):
        DraftToolPolicy.model_validate({"connectorCredentialsExposed": True})


def test_authoring_contracts_reject_raw_credential_and_raw_io_fields() -> None:
    credential_field = "api" + "Key"
    with pytest.raises(ValidationError, match="raw credential"):
        BuilderAnswer.model_validate(
            {
                "questionId": "question.finance.connector",
                "answerText": "use the readonly connector",
                credential_field: "placeholder",
            }
        )

    with pytest.raises(ValidationError, match="raw prompt/output"):
        RecipePackDraft.model_validate(
            _minimal_draft(rawPrompt="compile this into a live recipe")
        )

    with pytest.raises(ValidationError, match="raw credential"):
        DraftToolPolicy.model_validate(
            {
                "allowedConnectorRefs": ("connector.crm.readonly",),
                "rawCredential": "crm-token-not-allowed",
            }
        )


def test_authoring_answers_redact_secret_like_text_values() -> None:
    answer = BuilderAnswer(
        questionId="question.finance.connector",
        answerText="Use Authorization: Bearer sk-live-1234567890abcdef for the vendor.",
    )

    assert "sk-live-1234567890abcdef" not in answer.answer_text
    assert "[REDACTED]" in answer.answer_text
    assert answer.model_dump(by_alias=True)["answerText"] == answer.answer_text


def test_authoring_public_text_fields_redact_secret_like_values() -> None:
    pack = DraftRecipePack(
        packId="draft.pack.secret-redaction",
        title="Secret redaction",
        summary="Vendor token: sk-live-summarysecret123456 must not leak.",
        recipeRefs=("recipe.secret-redaction",),
    )
    proposal = GeneratedPluginProposal(
        proposalId="proposal.secret-redaction",
        status="blocked",
        name="Secret redaction proposal",
        reason="Authorization: Bearer proposal-secret-token-123456 should be redacted.",
    )
    gap = BuilderGap(
        gapId="gap.secret-redaction",
        kind="missing_connector",
        status="open",
        title="Secret redaction gap",
        details="password=gap-secret-value should be redacted.",
        missingRefs=("connector.secret-redaction.readonly",),
    )
    question = BuilderQuestion(
        questionId="question.secret-redaction",
        phaseId="intake",
        questionText="Use apiKey=question-secret-value only as redaction input.",
    )

    assert "sk-live-summarysecret123456" not in pack.summary
    assert "proposal-secret-token-123456" not in proposal.reason
    assert "gap-secret-value" not in gap.details
    assert "question-secret-value" not in question.question_text
    assert pack.summary.count("[REDACTED]") == 1
    assert proposal.reason.count("[REDACTED]") == 1
    assert gap.details.count("[REDACTED]") == 1
    assert question.question_text.count("[REDACTED]") == 1


def test_authoring_public_text_fields_redact_raw_model_output_markers() -> None:
    answer = BuilderAnswer(
        questionId="question.raw-output",
        answerText="rawModelOutput: chain of thought and tool result payload",
    )
    question = BuilderQuestion(
        questionId="question.raw-output",
        phaseId="intake",
        questionText="raw output: full hidden model transcript",
    )
    pack = DraftRecipePack(
        packId="draft.pack.raw-output",
        title="Raw output redaction",
        summary="rawPrompt: hidden instructions for the model",
        recipeRefs=("recipe.raw-output",),
    )
    gap = BuilderGap(
        gapId="gap.raw-output",
        kind="policy_conflict",
        status="deferred",
        title="Raw output gap",
        details="raw model output: tool result payload should never serialize",
    )
    proposal = GeneratedPluginProposal(
        proposalId="proposal.raw-output",
        status="blocked",
        name="Raw output proposal",
        reason="hidden transcript includes chain of thought",
    )

    public_texts = (
        answer.answer_text or "",
        question.question_text,
        pack.summary,
        gap.details,
        proposal.reason,
    )
    forbidden_fragments = (
        "rawModelOutput",
        "raw output",
        "rawPrompt",
        "raw model output",
        "hidden instructions",
        "hidden transcript",
        "chain of thought",
        "tool result payload",
    )

    for text in public_texts:
        assert "[REDACTED]" in text
        for fragment in forbidden_fragments:
            assert fragment.lower() not in text.lower()


def test_authoring_public_text_fields_redact_raw_prompt_output_spelling_variants() -> None:
    answer = BuilderAnswer(
        questionId="question.raw-spelling",
        answerText="rawOutput full hidden transcript",
    )
    question = BuilderQuestion(
        questionId="question.raw-spelling",
        phaseId="intake",
        questionText="raw prompt contains hidden instructions",
    )

    assert "rawOutput" not in (answer.answer_text or "")
    assert "hidden transcript" not in (answer.answer_text or "").lower()
    assert "raw prompt" not in question.question_text.lower()
    assert "hidden instructions" not in question.question_text.lower()
    assert "[REDACTED]" in (answer.answer_text or "")
    assert "[REDACTED]" in question.question_text


def test_draft_activation_defaults_false_and_cannot_be_enabled_in_pr1() -> None:
    draft = RecipePackDraft.model_validate(_minimal_draft())

    assert draft.activation_enabled is False
    assert draft.pack.harness_policy.allow_model_calls is False
    assert draft.pack.harness_policy.allow_live_execution is False
    assert draft.pack.harness_policy.allow_workspace_mutation is False
    assert draft.pack.harness_policy.allow_memory_write is False
    assert draft.pack.harness_policy.allow_external_delivery is False
    assert draft.pack.harness_policy.allow_schedule_mutation is False

    with pytest.raises(ValidationError, match="activationEnabled"):
        RecipePackDraft.model_validate(_minimal_draft(activationEnabled=True))

    with pytest.raises(ValidationError, match="allowModelCalls"):
        DraftHarnessPolicy.model_validate({"allowModelCalls": True})


def test_generated_plugin_proposal_is_non_executable() -> None:
    draft = RecipePackDraft.model_validate(_fixture("blocked_generated_plugin_proposal.json"))

    assert draft.status == "blocked"
    assert len(draft.generated_plugin_proposals) == 1
    proposal = draft.generated_plugin_proposals[0]
    assert proposal.status == "blocked"
    assert proposal.executable is False
    assert proposal.runtime_entrypoint is None
    assert "rawCode" not in proposal.model_dump(by_alias=True)

    with pytest.raises(ValidationError, match="executable"):
        GeneratedPluginProposal(
            proposalId="proposal.generated.unreviewed",
            status="proposed",
            name="Unreviewed generated connector",
            reason="Needs sandbox and human review before any executable materialization.",
            executable=True,
        )


def test_model_construct_cannot_bypass_authoring_safety_invariants() -> None:
    with pytest.raises(TypeError, match="model_construct"):
        RecipePackDraft.model_construct(
            draft_id="draft.bypass",
            authoring_session_id="builder.session.bypass",
            status="active",
            pack=_minimal_pack(),
            activation_enabled=True,
        )

    with pytest.raises(TypeError, match="model_construct"):
        GeneratedPluginProposal.model_construct(
            proposal_id="proposal.bypass",
            status="proposed",
            name="Bypass proposal",
            reason="Bypass attempt",
            executable=True,
            runtime_entrypoint="generated.module:run",
        )


def test_model_copy_update_cannot_bypass_authoring_safety_invariants() -> None:
    draft = RecipePackDraft.model_validate(_minimal_draft())
    proposal = GeneratedPluginProposal(
        proposalId="proposal.copy-bypass",
        status="blocked",
        name="Copy bypass proposal",
        reason="Copy update must revalidate.",
    )

    with pytest.raises(ValidationError, match="active"):
        draft.model_copy(update={"status": "active"})

    with pytest.raises(ValidationError, match="activationEligibility"):
        draft.model_copy(update={"activationEligibility": True})

    with pytest.raises(ValidationError, match="executable"):
        proposal.model_copy(
            update={
                "executable": True,
                "runtimeEntrypoint": "generated.module:run",
            }
        )


def test_draft_status_cannot_be_active() -> None:
    with pytest.raises(ValidationError, match="active"):
        RecipePackDraft.model_validate(_minimal_draft(status="active"))


def test_missing_or_open_gaps_cannot_clear_blocked_activation() -> None:
    with pytest.raises(ValidationError, match="blockedActivation"):
        BuilderGap(
            gapId="gap.missing-connector",
            kind="missing_connector",
            status="open",
            title="Missing connector",
            details="Readonly connector has not been approved.",
            missingRefs=("connector.erp.readonly",),
            blockedActivation=False,
        )

    with pytest.raises(ValidationError, match="blockedActivation"):
        BuilderGap(
            gapId="gap.missing-capability",
            kind="missing_capability",
            status="resolved",
            title="Missing capability",
            details="Capability remains absent from the authoring contract.",
            missingRefs=("capability.reconcile.readonly",),
            blockedActivation=False,
        )

    with pytest.raises(ValidationError, match="blockedActivation"):
        BuilderGap(
            gapId="gap.open-policy",
            kind="policy_conflict",
            status="open",
            title="Open policy conflict",
            details="Open gaps must still block activation.",
            blockedActivation=False,
        )

    with pytest.raises(ValidationError, match="blockedActivation"):
        BuilderGap(
            gapId="gap.deferred-policy",
            kind="policy_conflict",
            status="deferred",
            title="Deferred policy conflict",
            details="Deferred policy conflicts cannot fail open in PR1.",
            blockedActivation=False,
        )


def test_authoring_fixtures_validate_as_local_non_production_contracts() -> None:
    finance = RecipeBuilderSession.model_validate(
        _fixture("finance_research_builder_session.json")
    )
    backoffice = RecipeBuilderSession.model_validate(
        _fixture("backoffice_reconciliation_builder_session.json")
    )
    gap_report = BuilderGapReport.model_validate(
        _fixture("missing_connector_capability_gap.json")
    )

    assert finance.activation_enabled is False
    assert finance.activation_eligibility is False
    assert finance.mode == "recipe_builder"
    assert finance.separate_agent_identity is False
    assert finance.temporary is True
    assert finance.current_phase == "draft_constraints"
    assert isinstance(finance.phases, tuple)
    assert isinstance(finance.answers, tuple)
    assert finance.draft is not None
    assert finance.draft.bot_id == finance.bot_id
    assert finance.draft.owner_id == finance.owner_id
    assert finance.draft.activation_eligibility is False
    assert finance.draft.save_target == "current_bot_draft_store"
    assert finance.draft.pack.tool_policy.allowed_connector_refs == (
        "connector.sec-filings.readonly",
        "connector.market-data.readonly",
    )

    assert backoffice.activation_enabled is False
    assert backoffice.draft is not None
    assert "FileWrite" in backoffice.draft.pack.tool_policy.denied_tool_refs

    assert gap_report.local_only is True
    assert gap_report.non_production is True
    assert gap_report.gaps[0].kind == "missing_capability"
    assert gap_report.gaps[0].status == "open"


def test_review_summary_and_eval_fixture_set_stay_authoring_only() -> None:
    review = BuilderReviewSummary(
        reviewId="review.finance-research.001",
        draftId="draft.finance-research.001",
        decision="needs_revision",
        notes=("Missing readonly market-data connector approval.",),
        activationReady=False,
    )
    fixtures = EvalFixtureSet(
        fixtureSetId="eval.authoring.finance-research",
        draftId="draft.finance-research.001",
        localOnly=True,
        nonProduction=True,
        scenarioRefs=("fixture.finance.baseline",),
        expectedGapRefs=("gap.missing-market-data-connector",),
    )
    version = RecipePackVersion(
        packId="pack.finance-research",
        version="0.1.0",
        sourceDraftId="draft.finance-research.001",
        status="candidate",
        sourceDigest="sha256:" + "1" * 64,
        activationEnabled=False,
    )

    assert review.activation_ready is False
    assert fixtures.local_only is True
    assert fixtures.non_production is True
    assert version.activation_enabled is False
    assert version.source_digest == "sha256:" + "1" * 64
