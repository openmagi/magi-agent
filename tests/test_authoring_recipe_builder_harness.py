from __future__ import annotations

import inspect
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from openmagi_core_agent.authoring.compiler import CompileRecipePackResult
from openmagi_core_agent.authoring.dry_run import DryRunRecipePackResult
from openmagi_core_agent.authoring.harness import (
    RecipeBuilderModeConfig,
    RecipeBuilderModeState,
    advance_recipe_builder_mode,
)
from openmagi_core_agent.authoring.tool_contracts import SaveRecipePackDraft


FIXTURES = Path(__file__).parent / "fixtures" / "authoring"


def _fixture() -> dict[str, object]:
    return json.loads((FIXTURES / "compile_recipe_pack_success.json").read_text())


def _named_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def _interview_session(*, answered: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "sessionId": "builder.session.interview",
        "botId": "bot_recipe_mode_001",
        "ownerId": "owner_recipe_mode_001",
        "mode": "recipe_builder",
        "temporary": True,
        "separateAgentIdentity": False,
        "activationEligibility": False,
        "activationEnabled": False,
        "title": "Recipe Builder Mode",
        "currentPhase": "deep_interview",
        "phases": [
            {
                "phaseId": "deep_interview",
                "title": "Deep interview",
                "status": "in_progress",
                "questionIds": ["question.objective"],
            },
        ],
        "questions": [
            {
                "questionId": "question.objective",
                "phaseId": "deep_interview",
                "questionText": "What workflow should the current bot author?",
                "required": True,
                "answerKind": "free_text",
            },
        ],
        "answers": [],
    }
    if answered:
        payload["answers"] = [
            {
                "questionId": "question.objective",
                "answerText": "Author a local-only recipe pack for this bot.",
            },
        ]
    return payload


def _compile_result(ok: bool, *blocked_reasons: str) -> CompileRecipePackResult:
    return CompileRecipePackResult(
        ok=ok,
        compiledSnapshotDigest=(
            "sha256:" + "a" * 64 if ok else None
        ),
        effectivePolicySnapshotDigest=(
            "sha256:" + "b" * 64 if ok else None
        ),
        blockedReasons=blocked_reasons,
    )


def _dry_run_result(ok: bool, *denied_actions: str) -> DryRunRecipePackResult:
    return DryRunRecipePackResult(
        ok=ok,
        selectedRoute="recipe.source-review.verify" if ok else None,
        deniedActions=denied_actions,
        activationEligibility=False,
    )


def test_user_request_enters_deep_interview() -> None:
    state = advance_recipe_builder_mode(_interview_session(), event="user_request")

    assert state.phase == "deep_interview"
    assert state.bot_id == "bot_recipe_mode_001"
    assert state.owner_id == "owner_recipe_mode_001"
    assert state.session_id == "builder.session.interview"
    assert state.mode_temporary is True
    assert state.separate_agent_identity is False
    assert state.activation_eligibility is False


def test_unanswered_required_question_blocks_draft() -> None:
    state = advance_recipe_builder_mode(_interview_session(), event="draft_requested")

    assert state.phase == "blocked"
    assert state.required_question_ids == ("question.objective",)
    assert state.unanswered_required_question_ids == ("question.objective",)
    assert "unanswered_required_question:question.objective" in state.blocked_reasons
    assert state.can_save_draft is False


def test_answered_draft_request_enters_draft_recipe_pack() -> None:
    state = advance_recipe_builder_mode(
        _interview_session(answered=True),
        event="draft_requested",
    )

    assert state.phase == "draft_recipe_pack"
    assert state.unanswered_required_question_ids == ()


def test_compile_failure_triggers_bounded_repair() -> None:
    state = advance_recipe_builder_mode(
        _fixture(),
        event="compile_finished",
        compile_result=_compile_result(False, "unknown_tool_ref:MysteryTool"),
        config=RecipeBuilderModeConfig(maxRepairAttempts=2),
    )

    assert state.phase == "repair"
    assert state.repair_attempts == 1
    assert state.max_repair_attempts == 2
    assert "compile_failed:unknown_tool_ref:MysteryTool" in state.blocked_reasons
    assert state.can_save_draft is False


def test_repeated_compile_failure_transitions_to_blocked() -> None:
    previous = RecipeBuilderModeState(
        phase="repair",
        sessionId="builder.session.source-review",
        botId="bot_source_review_001",
        ownerId="owner_source_review_001",
        repairAttempts=1,
        maxRepairAttempts=1,
        blockedReasons=("compile_failed:unknown_tool_ref:MysteryTool",),
    )

    state = advance_recipe_builder_mode(
        _fixture(),
        event="compile_finished",
        previous_state=previous,
        compile_result=_compile_result(False, "unknown_tool_ref:MysteryTool"),
        config=RecipeBuilderModeConfig(maxRepairAttempts=1),
    )

    assert state.phase == "blocked"
    assert state.repair_attempts == 1
    assert "repair_attempts_exhausted" in state.blocked_reasons


def test_previous_state_scope_mismatch_blocks_without_reusing_repair_counter() -> None:
    previous = RecipeBuilderModeState(
        phase="repair",
        sessionId="builder.session.other",
        botId="bot_other",
        ownerId="owner_other",
        repairAttempts=1,
        maxRepairAttempts=1,
    )

    state = advance_recipe_builder_mode(
        _fixture(),
        event="compile_finished",
        previous_state=previous,
        compile_result=_compile_result(False, "unknown_tool_ref:MysteryTool"),
        config=RecipeBuilderModeConfig(maxRepairAttempts=1),
    )

    assert state.phase == "blocked"
    assert state.repair_attempts == 0
    assert state.bot_id == "bot_source_review_001"
    assert "previous_state_scope_mismatch" in state.blocked_reasons
    assert "repair_attempts_exhausted" not in state.blocked_reasons


def test_dry_run_failure_triggers_repair_then_blocks_when_exhausted() -> None:
    repair = advance_recipe_builder_mode(
        _fixture(),
        event="dry_run_finished",
        dry_run_result=_dry_run_result(False, "no_route_match"),
        config=RecipeBuilderModeConfig(maxRepairAttempts=1),
    )
    blocked = advance_recipe_builder_mode(
        _fixture(),
        event="dry_run_finished",
        previous_state=repair,
        dry_run_result=_dry_run_result(False, "no_route_match"),
        config=RecipeBuilderModeConfig(maxRepairAttempts=1),
    )

    assert repair.phase == "repair"
    assert "dry_run_failed:no_route_match" in repair.blocked_reasons
    assert blocked.phase == "blocked"
    assert "repair_attempts_exhausted" in blocked.blocked_reasons


def test_save_draft_requires_configured_compile_and_dry_run_gates() -> None:
    compile_only = advance_recipe_builder_mode(
        _fixture(),
        event="save_requested",
        compile_result=_compile_result(True),
        config=RecipeBuilderModeConfig(requireCompile=True, requireDryRun=True),
    )
    dry_run_failed = advance_recipe_builder_mode(
        _fixture(),
        event="save_requested",
        compile_result=_compile_result(True),
        dry_run_result=_dry_run_result(False, "no_route_match"),
        config=RecipeBuilderModeConfig(requireCompile=True, requireDryRun=True),
    )

    assert compile_only.phase == "blocked"
    assert "dry_run_gate_required" in compile_only.blocked_reasons
    assert dry_run_failed.phase == "blocked"
    assert "dry_run_gate_failed" in dry_run_failed.blocked_reasons
    assert compile_only.can_save_draft is False
    assert dry_run_failed.can_save_draft is False


def test_save_draft_requires_current_session_draft() -> None:
    state = advance_recipe_builder_mode(
        _interview_session(answered=True),
        event="save_requested",
        compile_result=_compile_result(True),
        dry_run_result=_dry_run_result(True),
    )

    assert state.phase == "blocked"
    assert "missing_recipe_pack_draft" in state.blocked_reasons
    assert state.can_save_draft is False


def test_save_draft_requires_validated_save_contract_and_nontrivial_gate_evidence() -> None:
    session = _fixture()
    save_draft = SaveRecipePackDraft.model_validate(
        {"scope": session, "draft": session["draft"]}
    )

    no_save_contract = advance_recipe_builder_mode(
        session,
        event="save_requested",
        compile_result=_compile_result(True),
        dry_run_result=_dry_run_result(True),
    )
    forged_pass_results = advance_recipe_builder_mode(
        session,
        event="save_requested",
        compile_result={"ok": True},
        dry_run_result={"ok": True},
        save_draft=save_draft,
    )

    assert no_save_contract.phase == "blocked"
    assert "save_draft_contract_required" in no_save_contract.blocked_reasons
    assert no_save_contract.can_save_draft is False
    assert forged_pass_results.phase == "blocked"
    assert "compile_gate_missing_snapshot_digest" in forged_pass_results.blocked_reasons
    assert "compile_gate_missing_policy_digest" in forged_pass_results.blocked_reasons
    assert "dry_run_gate_missing_route" in forged_pass_results.blocked_reasons
    assert forged_pass_results.can_save_draft is False


def test_save_draft_contract_must_match_current_session_draft() -> None:
    session = _fixture()
    other_draft = _named_fixture("blocked_generated_plugin_proposal.json")
    other_scope = {
        "botId": other_draft["botId"],
        "ownerId": other_draft["ownerId"],
        "sessionId": other_draft["authoringSessionId"],
        "mode": "recipe_builder",
    }
    other_save_draft = SaveRecipePackDraft.model_validate(
        {"scope": other_scope, "draft": other_draft}
    )

    state = advance_recipe_builder_mode(
        session,
        event="save_requested",
        compile_result=_compile_result(True),
        dry_run_result=_dry_run_result(True),
        save_draft=other_save_draft,
    )

    assert state.phase == "blocked"
    assert "save_draft_contract_mismatch" in state.blocked_reasons
    assert state.can_save_draft is False


def test_save_draft_allowed_after_passing_compile_and_dry_run_without_activation() -> None:
    session = _fixture()
    save_draft = SaveRecipePackDraft.model_validate(
        {"scope": session, "draft": session["draft"]}
    )

    state = advance_recipe_builder_mode(
        session,
        event="save_requested",
        compile_result=_compile_result(True),
        dry_run_result=_dry_run_result(True),
        save_draft=save_draft,
    )

    assert state.phase == "save_draft"
    assert state.can_save_draft is True
    assert state.save_draft_id == "draft.source-review.001"
    assert state.activation_eligibility is False
    assert state.activation_enabled is False


def test_harness_cannot_operate_without_bot_owner_session_scope_or_create_identity() -> None:
    missing_bot = _interview_session(answered=True)
    missing_bot.pop("botId")

    state = advance_recipe_builder_mode(missing_bot, event="user_request")

    assert state.phase == "blocked"
    assert state.bot_id is None
    assert state.owner_id is None
    assert state.session_id is None
    assert state.separate_agent_identity is False
    assert "invalid_recipe_builder_scope" in state.blocked_reasons


def test_activation_is_denied_for_every_harness_projection() -> None:
    for event in (
        "user_request",
        "answers_complete",
        "docs_and_catalogs_inspected",
        "draft_ready",
        "compile_finished",
        "dry_run_finished",
        "review_finished",
        "gap_reported",
        "save_requested",
    ):
        state = advance_recipe_builder_mode(
            _fixture(),
            event=event,
            compile_result=_compile_result(True),
            dry_run_result=_dry_run_result(True),
        )
        assert state.activation_eligibility is False
        assert state.activation_enabled is False


def test_generic_harness_core_has_no_domain_specific_hard_coded_verticals() -> None:
    import openmagi_core_agent.authoring.harness as harness

    source = inspect.getsource(harness).lower()

    assert "finance" not in source
    assert "research" not in source
    assert "backoffice" not in source


def test_harness_does_not_call_network_subprocess_or_runtime_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked_socket(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("Recipe Builder Mode harness must not open network sockets")

    def blocked_subprocess(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("Recipe Builder Mode harness must not execute subprocesses")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(subprocess, "run", blocked_subprocess)
    session = _fixture()
    save_draft = SaveRecipePackDraft.model_validate(
        {"scope": session, "draft": session["draft"]}
    )

    state = advance_recipe_builder_mode(
        session,
        event="save_requested",
        compile_result=_compile_result(True),
        dry_run_result=_dry_run_result(True),
        save_draft=save_draft,
    )

    assert state.phase == "save_draft"


def test_authoring_harness_import_stays_runtime_core_toolhost_adk_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.runtime",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.kernel",
    "openmagi_core_agent.transport",
    "openai",
    "google.genai",
    "requests",
    "httpx",
    "urllib",
)
baseline = {
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
}

module = importlib.import_module("openmagi_core_agent.authoring.harness")
assert hasattr(module, "advance_recipe_builder_mode")

loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    and name not in baseline
]
if loaded:
    raise AssertionError(f"authoring harness loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
