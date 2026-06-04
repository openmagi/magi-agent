"""Track 19 PR7 — blocking GA ``question`` tool (extends existing GA pack).

RED-first tests for the model-callable clarifying-question tool added to the
EXISTING general-automation pack. The tool BLOCKS the turn (returns a
``pending_control_request`` / ``needs_approval`` result) carrying an
``approval_required`` control projection that holds the question + options, and
RESUMES on the user's reply via the existing control/resume-ref machinery.

Invariants exercised:

* flag-ON + ``general`` role → blocking pending_control_request + an
  ``approval_required`` control projection carrying the question/options digest;
* the control has ``executionAllowed`` False and a resume ref;
* a reply via the existing resume flow resumes (resume-ref linkage asserted);
* flag-OFF / non-general → tool inert / not surfaced (no behavior change);
* no raw secret in the projection (digest/label-safe).
"""
from __future__ import annotations

import pytest

from magi_agent.harness.general_automation.control_projection import (
    GeneralAutomationControlProjection,
)
from magi_agent.harness.general_automation.question_tool import (
    GeneralAutomationQuestion,
    GeneralAutomationQuestionOption,
    GeneralAutomationQuestionOutcome,
    classify_general_automation_question,
    general_automation_question_handler,
    general_automation_question_manifest,
    resume_general_automation_question,
)
from magi_agent.runtime.control import ControlRequestStore
from magi_agent.tools.context import ToolContext


_FLAG = "MAGI_GA_LIVE_ENABLED"


def _general_context() -> ToolContext:
    return ToolContext(
        botId="bot-1",
        sessionKey="sess-1",
        turnId="turn-1",
        workspaceRoot="/workspace",
        executionContract={"agentRole": "general"},
    )


def _coding_context() -> ToolContext:
    return ToolContext(
        botId="bot-1",
        sessionKey="sess-1",
        turnId="turn-1",
        workspaceRoot="/workspace",
        executionContract={"agentRole": "coding"},
    )


def _question_args() -> dict[str, object]:
    return {
        "header": "Pick a deployment target",
        "question": "Which environment should I deploy to?",
        "options": [
            {"label": "Staging", "description": "Safe rehearsal cluster"},
            {"label": "Production", "description": "Live fleet"},
        ],
        "multiple": False,
    }


def _secret_question_args() -> dict[str, object]:
    return {
        "header": "Confirm credentials",
        "question": "Use api_key=sk-supersecretvalue1234567890 for the call?",
        "options": [
            {"label": "Yes", "description": "token=ghp_anothersecret9876543210"},
        ],
        "multiple": False,
    }


# ---------------------------------------------------------------------------
# (a) flag-ON + general: blocking pending_control_request + approval projection
# ---------------------------------------------------------------------------


def test_question_model_accepts_options_multiple_and_freetext() -> None:
    question = GeneralAutomationQuestion(
        header="Pick a deployment target",
        question="Which environment should I deploy to?",
        options=(
            GeneralAutomationQuestionOption(label="Staging", description="rehearsal"),
            GeneralAutomationQuestionOption(label="Production", description="live"),
        ),
        multiple=True,
    )
    # An implicit free-text option always exists alongside the structured options.
    assert question.free_text_allowed is True
    assert len(question.options) == 2


def test_handler_flag_on_general_blocks_with_approval_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLAG, "1")
    result = general_automation_question_handler(_question_args(), _general_context())

    # Blocking: the turn does not proceed — it returns a pending control request.
    assert result.status == "needs_approval"
    projection = result.metadata.get("controlProjection")
    assert isinstance(projection, dict)
    assert projection["controlType"] == "approval_required"
    assert result.metadata.get("pendingControlRequest") is True
    assert result.metadata.get("generalAutomationQuestion") is True


def test_classify_flag_on_general_carries_question_options() -> None:
    outcome = classify_general_automation_question(
        GeneralAutomationQuestion(
            header="Pick a deployment target",
            question="Which environment should I deploy to?",
            options=(
                GeneralAutomationQuestionOption(label="Staging"),
                GeneralAutomationQuestionOption(label="Production"),
            ),
            multiple=False,
        ),
        _general_context(),
        env={_FLAG: "1"},
    )
    assert isinstance(outcome, GeneralAutomationQuestionOutcome)
    assert outcome.active is True
    assert outcome.decision == "ask"
    assert isinstance(outcome.control_projection, GeneralAutomationControlProjection)
    # The labels are surfaced (label-safe) so the control/evidence trail records
    # what was asked, while the question text itself is only digested.
    assert outcome.option_labels == ("Staging", "Production")


# ---------------------------------------------------------------------------
# (b) executionAllowed False + resume ref
# ---------------------------------------------------------------------------


def test_control_projection_execution_disallowed_with_resume_ref() -> None:
    outcome = classify_general_automation_question(
        GeneralAutomationQuestion(
            header="Pick a target",
            question="Which one?",
            options=(GeneralAutomationQuestionOption(label="A"),),
        ),
        _general_context(),
        env={_FLAG: "1"},
    )
    projection = outcome.control_projection
    assert projection is not None
    assert projection.execution_allowed is False
    assert projection.authority_flags.approval_bypassed is False
    assert projection.resume_ref is not None
    assert projection.resume_ref.startswith("resume:general-automation-question:")


# ---------------------------------------------------------------------------
# (c) reply via existing resume flow resumes (resume-ref linkage)
# ---------------------------------------------------------------------------


def test_reply_resumes_via_existing_control_store_resume_ref() -> None:
    outcome = classify_general_automation_question(
        GeneralAutomationQuestion(
            header="Pick a target",
            question="Which one?",
            options=(
                GeneralAutomationQuestionOption(label="Staging"),
                GeneralAutomationQuestionOption(label="Production"),
            ),
        ),
        _general_context(),
        env={_FLAG: "1"},
    )
    projection = outcome.control_projection
    assert projection is not None

    store = ControlRequestStore()
    resume = resume_general_automation_question(
        outcome,
        store=store,
        session_key="sess-1",
        turn_id="turn-1",
        answer="Staging",
        now=1_000,
        timeout_ms=60_000,
    )

    # The reply resolves the SAME control identified by the projection's resume
    # ref — that is the resume linkage.
    assert resume.resume_ref == projection.resume_ref
    record = store.get_terminal(resume.request_id)
    assert record is not None
    assert record.kind == "user_question"
    assert record.decision == "answered"
    assert record.answer == "Staging"


def test_resume_is_idempotent_on_same_resume_ref() -> None:
    outcome = classify_general_automation_question(
        GeneralAutomationQuestion(
            header="Pick a target",
            question="Which one?",
            options=(GeneralAutomationQuestionOption(label="A"),),
        ),
        _general_context(),
        env={_FLAG: "1"},
    )
    store = ControlRequestStore()
    first = resume_general_automation_question(
        outcome, store=store, session_key="s", turn_id="t", answer="A", now=1, timeout_ms=10
    )
    second = resume_general_automation_question(
        outcome, store=store, session_key="s", turn_id="t", answer="A", now=2, timeout_ms=10
    )
    assert first.request_id == second.request_id
    assert second.resume_ref == first.resume_ref


# ---------------------------------------------------------------------------
# (d) flag-OFF / non-general → tool inert / not surfaced
# ---------------------------------------------------------------------------


def test_classify_flag_off_is_inert() -> None:
    outcome = classify_general_automation_question(
        GeneralAutomationQuestion(header="h", question="q"),
        _general_context(),
        env={},
    )
    assert outcome.active is False
    assert outcome.decision == "allow"
    assert outcome.control_projection is None


def test_classify_non_general_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    outcome = classify_general_automation_question(
        GeneralAutomationQuestion(header="h", question="q"),
        _coding_context(),
        env={_FLAG: "1"},
    )
    assert outcome.active is False
    assert outcome.decision == "allow"
    assert outcome.control_projection is None


def test_handler_flag_off_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    result = general_automation_question_handler(_question_args(), _general_context())
    # Inert: no pending control request is produced; the tool is effectively a
    # no-op / blocked passthrough so flag-OFF behaves like main.
    assert result.status == "blocked"
    assert "controlProjection" not in result.metadata
    assert result.metadata.get("pendingControlRequest") is not True


def test_handler_non_general_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    result = general_automation_question_handler(_question_args(), _coding_context())
    assert result.status == "blocked"
    assert "controlProjection" not in result.metadata


# ---------------------------------------------------------------------------
# (e) no raw secret in the projection
# ---------------------------------------------------------------------------


def test_no_raw_secret_in_projection_or_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLAG, "1")
    result = general_automation_question_handler(
        _secret_question_args(), _general_context()
    )
    assert result.status == "needs_approval"
    blob = repr(result.metadata)
    assert "sk-supersecretvalue1234567890" not in blob
    assert "ghp_anothersecret9876543210" not in blob


# ---------------------------------------------------------------------------
# manifest + registration surface (extends existing GA pack)
# ---------------------------------------------------------------------------


def test_manifest_is_meta_and_disabled_by_default() -> None:
    manifest = general_automation_question_manifest()
    assert manifest.permission == "meta"
    assert manifest.mutates_workspace is False
    assert manifest.dangerous is False
    # Inert by default at the manifest level; the live flag gate is the authority.
    assert manifest.enabled_by_default is False


def test_general_pack_surfaces_question_tool() -> None:
    from magi_agent.harness.resolved import build_default_resolved_harness_state

    state = build_default_resolved_harness_state(agent_role="general")
    tools = state.general.components["tools"]
    assert general_automation_question_manifest().name in tools


def test_preset_files_role_exposes_question_category() -> None:
    from magi_agent.recipes.first_party.general_automation.presets import (
        get_general_automation_preset,
    )

    preset = get_general_automation_preset("automation.plan")
    assert "user_question" in preset.tool_categories
