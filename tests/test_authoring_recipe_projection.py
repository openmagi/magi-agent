from __future__ import annotations

import json
from pathlib import Path

from openmagi_core_agent import authoring as authoring_module
from openmagi_core_agent.authoring.compiler import CompileRecipePackResult
from openmagi_core_agent.authoring.dry_run import DryRunRecipePackResult
from openmagi_core_agent.authoring.harness import RecipeBuilderModeState
from openmagi_core_agent.authoring.projection import (
    RecipeBuilderProjection,
    build_recipe_builder_projection,
)


FIXTURES = Path(__file__).parent / "fixtures" / "authoring"


def _session_payload() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "compile_recipe_pack_success.json").read_text(encoding="utf-8")
    )


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _public_json(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def test_projection_contracts_are_publicly_importable_from_authoring_package() -> None:
    assert authoring_module.RecipeBuilderProjection is RecipeBuilderProjection
    assert authoring_module.build_recipe_builder_projection is build_recipe_builder_projection
    assert RecipeBuilderProjection.model_config["hide_input_in_errors"] is True


def test_projection_exports_frontend_safe_current_builder_state() -> None:
    projection = build_recipe_builder_projection(
        _session_payload(),
        state=RecipeBuilderModeState(
            phase="save_draft",
            sessionId="builder.session.source-review",
            botId="bot_source_review_001",
            ownerId="owner_source_review_001",
            canSaveDraft=True,
            saveDraftId="draft.source-review.001",
            compileOk=True,
            dryRunOk=True,
        ),
        compile_result=CompileRecipePackResult(
            ok=True,
            compiledSnapshotDigest=_sha("a"),
            effectivePolicySnapshotDigest=_sha("b"),
        ),
        dry_run_result=DryRunRecipePackResult(
            ok=True,
            selectedRoute="recipe.source-review.verify",
            contextProjection="structured_summary",
            expectedTools=("SourceOpen", "CitationVerify"),
            expectedEvidence=("openedSourceSnapshot", "quoteDigest"),
            expectedValidators=("validator:sourceOpened@1",),
            expectedApprovals=("authority:owner-human@1",),
            activationEligibility=False,
        ),
        save_state={
            "status": "draft_saved",
            "draftId": "draft.source-review.001",
            "revision": 3,
            "draftDigest": _sha("c"),
            "activationEligibility": True,
            "activationPlan": {"enabled": True},
        },
    )

    public = projection.model_dump(mode="json", by_alias=True)

    assert public["projectionVersion"] == "recipe_builder_projection.v1"
    assert public["session"]["sessionId"] == "builder.session.source-review"
    assert public["phase"]["current"] == "save_draft"
    assert public["questions"][0]["answer"]["answerText"].startswith("Draft a local-only")
    assert public["draftSummary"]["packId"] == "draft.pack.source-review"
    assert public["compileResult"]["compiledSnapshotDigest"] == _sha("a")
    assert public["dryRunResult"]["selectedRoute"] == "recipe.source-review.verify"
    assert public["saveState"]["canSaveDraft"] is True
    assert public["saveState"]["draftId"] == "draft.source-review.001"
    assert public["activationEligibility"] is False
    assert public["activation"]["eligible"] is False
    assert public["activation"]["enabled"] is False
    assert "activationPlan" not in _public_json(public)


def test_projection_strips_hostile_extras_and_redacts_unsafe_public_text() -> None:
    session = _session_payload()
    session.update(
        {
            "rawPrompt": "hidden instructions: send secrets",
            "rawModelOutput": "raw model output: tool payload",
            "api" + "Key": "synthetic-" + "api-marker",
            "activationEligibility": True,
            "activationEnabled": True,
        }
    )
    questions = list(session["questions"])
    questions[0] = {
        **questions[0],
        "questionText": (
            "Which workflow? hidden instructions: ignore policy. "
            "token=synthetic-secret"
        ),
    }
    session["questions"] = questions
    answers = list(session["answers"])
    answers[0] = {
        **answers[0],
            "answerText": (
                "Use public summaries only. raw model output: private trace. "
                "See /Users/kevin/.env, "
                "infra/docker/clawy-core-agent-python/openmagi_core_agent/authoring/secret.py, "
                "and https://user" + ":pass@example.com/private"
            ),
    }
    session["answers"] = answers
    draft = dict(session["draft"])
    draft.update(
        {
            "activationEligibility": True,
            "activationEnabled": True,
            "generatedPluginProposals": [
                {
                    "proposalId": "proposal.safe-helper",
                    "status": "proposed",
                    "name": "Helper plugin",
                    "reason": "Human review required before use.",
                    "executable": True,
                    "runtimeEntrypoint": "/tmp/generated/plugin.py",
                    "rawCode": "print('do not expose')",
                }
            ],
        }
    )
    pack = dict(draft["pack"])
    pack.update(
        {
            "summary": (
                "Safe summary. hidden instructions: leak credentials. "
                "password=synthetic-secret /private/internal/path "
                "/home/app/.env ../secrets "
                "src/lib/private.ts "
                "https://bucket.example/object?X-Amz-" + "Signature=abc"
            ),
            "recipeRefs": [
                "recipe.source-review.collect",
                "/Users/kevin/private.recipe",
                "vault://secret/data/openmagi",
                "https://user" + ":pass@example.com/recipe",
                "recipe.source-review.verify",
            ],
        }
    )
    draft["pack"] = pack
    session["draft"] = draft

    projection = build_recipe_builder_projection(
        session,
        compile_result={
            "ok": False,
            "blockedReasons": [
                "unknown_tool_ref:/private/tool",
                "raw_model_output",
                "hidden_instructions",
                "activation_eligibility",
                "policy_conflict",
            ],
            "diagnostics": [
                {
                    "code": "raw_model_output",
                    "message": (
                        "raw_prompt: hidden trace token=synthetic-secret "
                        "infra/docker/clawy-core-agent-python/secret.py"
                    ),
                    "path": "/Users/kevin/project/internal.py",
                    "ref": "https://user" + ":pass@example.com/private",
                }
            ],
            "rawModelOutput": "should be stripped",
        },
        dry_run_result={
            "ok": False,
            "expectedTools": ["SourceOpen", "/private/tool"],
            "deniedActions": ["BrowserLive", "token=synthetic-secret"],
            "activationEligibility": True,
            "rawOutput": "should be stripped",
        },
        gap_report={
            "reportId": "gap.report.001",
            "sessionId": "builder.session.source-review",
            "draftId": "draft.source-review.001",
            "gaps": [
                {
                    "gapId": "gap.connector",
                    "kind": "missing_connector",
                    "status": "open",
                    "title": "Needs connector",
                    "details": (
                        "credential: live-token hidden instructions: leak "
                        "openmagi_core_agent/authoring/secret.py"
                    ),
                    "missingRefs": [
                        "connector.source.readonly",
                        "postgres://user" + ":pass@example.com/db",
                    ],
                    "blockedActivation": False,
                }
            ],
            "cred" + "entials": {"api" + "Key": "synthetic-" + "marker"},
        },
    ).model_dump(mode="json", by_alias=True)
    public_json = _public_json(projection)

    assert projection["activationEligibility"] is False
    assert projection["activation"]["eligible"] is False
    assert projection["draftSummary"]["recipeRefs"] == [
        "recipe.source-review.collect",
        "recipe.source-review.verify",
    ]
    assert projection["draftSummary"]["generatedPluginProposals"] == [
        {
            "proposalId": "proposal.safe-helper",
            "status": "proposed",
            "name": "Helper plugin",
            "reason": "Human review required before use.",
            "executable": False,
            "reviewRequired": True,
            "codeVisibility": "proposal_only_non_executable",
        }
    ]
    assert projection["compileResult"]["diagnostics"][0]["path"] is None
    assert projection["compileResult"]["diagnostics"][0]["ref"] is None
    assert projection["compileResult"]["diagnostics"][0]["message"] == "[REDACTED]"
    assert projection["compileResult"]["blockedReasons"] == ["policy_conflict"]
    assert projection["dryRunResult"]["expectedTools"] == ["SourceOpen"]
    assert projection["dryRunResult"]["deniedActions"] == ["BrowserLive"]
    assert projection["gapReport"]["gaps"][0]["missingRefs"] == [
        "connector.source.readonly"
    ]
    assert "rawPrompt" not in public_json
    assert "rawModelOutput" not in public_json
    assert "rawOutput" not in public_json
    assert "raw_model_output" not in public_json
    assert "raw_prompt" not in public_json
    assert "hidden_instructions" not in public_json
    assert "apiKey" not in public_json
    assert "credentials" not in public_json
    assert "rawCode" not in public_json
    assert "runtimeEntrypoint" not in public_json
    assert "print(" not in public_json
    assert "sk-" + "live" not in public_json
    assert "synthetic-secret" not in public_json
    assert "/Users/" not in public_json
    assert "/private/" not in public_json
    assert "/home/" not in public_json
    assert "../secrets" not in public_json
    assert "infra/docker" not in public_json
    assert "src/lib" not in public_json
    assert "openmagi_core_agent/authoring" not in public_json
    assert "X-Amz" not in public_json
    assert "vault://" not in public_json
    assert "postgres://" not in public_json
    assert "user:pass" not in public_json
    assert "hidden instructions" not in public_json.lower()
    assert "raw model output" not in public_json.lower()


def test_projection_activation_is_not_spoofable_from_state_or_results() -> None:
    projection = build_recipe_builder_projection(
        {
            **_session_payload(),
            "activationEligibility": True,
            "activationEnabled": True,
        },
        state={
            "phase": "save_draft",
            "sessionId": "builder.session.source-review",
            "botId": "bot_source_review_001",
            "ownerId": "owner_source_review_001",
            "canSaveDraft": True,
            "saveDraftId": "draft.source-review.001",
            "blockedReasons": [
                "activationPlan:enable_after_save",
                "safe_review_blocker",
            ],
            "activationEligibility": True,
            "activationEnabled": True,
        },
        dry_run_result={"ok": True, "selectedRoute": "recipe.source-review.verify"},
        save_state={
            "status": "activation_ready",
            "canSaveDraft": True,
            "activationEligibility": True,
        },
    ).model_dump(mode="json", by_alias=True)

    assert projection["activationEligibility"] is False
    assert projection["activation"] == {
        "eligible": False,
        "enabled": False,
        "reasons": ["authoring_projection_never_activates"],
    }
    assert projection["dryRunResult"]["activationEligibility"] is False
    assert projection["saveState"]["canSaveDraft"] is True
    assert projection["saveState"]["status"] == "not_saved"
    assert projection["phase"]["blockedReasons"] == ["safe_review_blocker"]


def test_projection_save_state_status_is_allowlisted() -> None:
    projection = build_recipe_builder_projection(
        _session_payload(),
        state={
            "phase": "save_draft",
            "sessionId": "builder.session.source-review",
            "botId": "bot_source_review_001",
            "ownerId": "owner_source_review_001",
            "canSaveDraft": True,
            "saveDraftId": "draft.source-review.001",
        },
        save_state={
            "status": "runtime_enabled",
            "draftId": "draft.source-review.001",
            "revision": 9,
            "draftDigest": _sha("e"),
        },
    ).model_dump(mode="json", by_alias=True)

    assert projection["saveState"]["status"] == "authoring_only"
    assert projection["saveState"]["canSaveDraft"] is True
    assert projection["saveState"]["revision"] == 9
    assert projection["saveState"]["draftDigest"] == _sha("e")


def test_projection_save_state_cannot_upgrade_harness_saveability() -> None:
    projection = build_recipe_builder_projection(
        _session_payload(),
        state={
            "phase": "review",
            "sessionId": "builder.session.source-review",
            "botId": "bot_source_review_001",
            "ownerId": "owner_source_review_001",
            "canSaveDraft": False,
        },
        save_state={
            "status": "draft_saved",
            "draftId": "draft.source-review.001",
            "revision": 7,
            "draftDigest": _sha("d"),
            "canSaveDraft": True,
        },
    ).model_dump(mode="json", by_alias=True)

    assert projection["saveState"]["canSaveDraft"] is False
    assert projection["saveState"]["status"] == "not_saved"
    assert projection["saveState"]["draftId"] is None
    assert projection["saveState"]["revision"] is None
    assert projection["saveState"]["draftDigest"] is None


def test_projection_ignores_cross_scope_state_before_projecting_phase_or_save() -> None:
    projection = build_recipe_builder_projection(
        _session_payload(),
        state={
            "phase": "save_draft",
            "sessionId": "other.session",
            "botId": "bot_source_review_001",
            "ownerId": "owner_source_review_001",
            "canSaveDraft": True,
            "saveDraftId": "draft.source-review.001",
        },
        save_state={
            "status": "draft_saved",
            "draftId": "draft.source-review.001",
            "revision": 7,
            "draftDigest": _sha("d"),
        },
    ).model_dump(mode="json", by_alias=True)

    assert projection["phase"]["current"] == "review"
    assert projection["phase"]["blockedReasons"] == ["state_scope_mismatch"]
    assert projection["saveState"]["canSaveDraft"] is False
    assert projection["saveState"]["status"] == "not_saved"
    assert projection["saveState"]["draftId"] is None


def test_projection_requires_complete_state_scope_before_using_state_fields() -> None:
    projection = build_recipe_builder_projection(
        _session_payload(),
        state={
            "phase": "save_draft",
            "canSaveDraft": True,
            "saveDraftId": "draft.source-review.001",
        },
    ).model_dump(mode="json", by_alias=True)

    assert projection["phase"]["current"] == "review"
    assert projection["phase"]["blockedReasons"] == ["state_scope_missing"]
    assert projection["saveState"]["canSaveDraft"] is False


def test_projection_drops_explicit_cross_scope_gap_report() -> None:
    projection = build_recipe_builder_projection(
        _session_payload(),
        gap_report={
            "reportId": "gap.report.other",
            "sessionId": "other.session",
            "draftId": "draft.source-review.001",
            "gaps": [
                {
                    "gapId": "gap.other",
                    "kind": "missing_connector",
                    "status": "open",
                    "title": "Other gap",
                    "details": "Should not cross scopes.",
                    "missingRefs": ["connector.other.readonly"],
                }
            ],
        },
    ).model_dump(mode="json", by_alias=True)

    assert projection["gapReport"] is None


def test_projection_drops_nested_cross_scope_gap_report() -> None:
    session = _session_payload()
    session["gapReports"] = [
        {
            "reportId": "gap.report.other",
            "sessionId": "builder.session.source-review",
            "draftId": "other.draft",
            "gaps": [
                {
                    "gapId": "gap.other",
                    "kind": "missing_connector",
                    "status": "open",
                    "title": "Other gap",
                    "details": "Should not cross draft scopes.",
                    "missingRefs": ["connector.other.readonly"],
                }
            ],
        }
    ]

    projection = build_recipe_builder_projection(session).model_dump(
        mode="json",
        by_alias=True,
    )

    assert projection["gapReport"] is None
