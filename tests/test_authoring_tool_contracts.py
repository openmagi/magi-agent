from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.authoring import (
    CompileRecipePack,
    DryRunRecipePack,
    DraftEvalFixtures,
    DraftHarnessPolicyTool,
    DraftRecipePackTool,
    GenerateActivationPlan,
    GenerateGapReport,
    InspectConnectorAvailability,
    InspectHarnessRegistry,
    InspectPluginCatalog,
    InspectRecipeRegistry,
    InspectToolCatalog,
    InspectValidatorRegistry,
    ReadMagiDocs,
    SaveRecipePackDraft,
)
from openmagi_core_agent.authoring import tool_contracts as tool_contracts_module
from openmagi_core_agent.authoring.contracts import (
    DraftRecipePack as DraftRecipePackData,
    RecipeBuilderSession,
    RecipePackDraft,
)
from openmagi_core_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    CompileRecipePackResult,
)
from openmagi_core_agent.authoring.dry_run import (
    DryRunRecipePackCatalog,
    DryRunRecipePackRequest,
    DryRunRecipePackResult,
)
from openmagi_core_agent.authoring.tool_contracts import (
    DraftEvalFixtures as DraftEvalFixturesContract,
    DraftHarnessPolicy as DraftHarnessPolicyContract,
    DraftRecipePack as DraftRecipePackContract,
    GenerateGapReport as GenerateGapReportContract,
    SaveRecipePackDraft as SaveRecipePackDraftContract,
    run_compile_recipe_pack,
    run_dry_run_recipe_pack,
    run_generate_activation_plan,
)

FIXTURES = Path(__file__).parent / "fixtures" / "authoring"


def _session_payload() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "compile_recipe_pack_success.json").read_text(encoding="utf-8")
    )


def _scope_payload() -> dict[str, object]:
    payload = _session_payload()
    return {
        "botId": payload["botId"],
        "ownerId": payload["ownerId"],
        "sessionId": payload["sessionId"],
        "mode": "recipe_builder",
    }


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    (
        "tool_type",
        "extras",
    ),
    (
        (ReadMagiDocs, {"query": "search recipe docs"}),
        (InspectRecipeRegistry, {}),
        (InspectToolCatalog, {}),
        (InspectPluginCatalog, {}),
        (InspectConnectorAvailability, {}),
        (InspectValidatorRegistry, {}),
        (InspectHarnessRegistry, {}),
    ),
)
def test_tool_contracts_accept_recipe_builder_scope(
    tool_type: object,
    extras: dict[str, object],
) -> None:
    session = RecipeBuilderSession.model_validate(_session_payload())
    scope_only = _scope_payload()

    with_session = tool_type.model_validate({"scope": session, **extras})
    with_scope = tool_type.model_validate({"scope": scope_only, **extras})

    assert with_session.scope.bot_id == session.bot_id
    assert with_session.scope.owner_id == session.owner_id
    assert with_scope.scope.session_id == session.session_id
    assert with_session.local_only is True
    assert with_scope.non_production is True
    assert with_session.scope_digest.startswith("sha256:")


def test_tool_contracts_require_scope_digest_to_match_scope() -> None:
    scope = _scope_payload()
    request = {
        "scope": scope,
        "scopeDigest": "sha256:" + "1" * 64,
        "query": "search recipe docs",
    }

    with pytest.raises(ValidationError, match="scopeDigest must match scope"):
        ReadMagiDocs.model_validate(request)


def test_catalog_contracts_require_digest_for_provided_references() -> None:
    scope = _scope_payload()
    bad = {
        "scope": scope,
        "references": ("SourceOpen",),
        "catalogDigest": "sha256:" + "0" * 64,
    }

    with pytest.raises(ValidationError, match="catalogDigest must match catalog references"):
        InspectToolCatalog.model_validate(bad)


def test_tools_return_public_safe_digest_metadata() -> None:
    scope = _scope_payload()
    doc_digest = "sha256:" + "a" * 64

    docs = ReadMagiDocs.model_validate(
        {
            "scope": scope,
            "docs": (
                {
                    "docId": "docs.authoring.recipe-builder-mode",
                    "digest": doc_digest,
                    "title": "Recipe Builder Mode",
                    "summary": "Public authoring metadata only.",
                },
            ),
        }
    )
    catalog = InspectRecipeRegistry.model_validate({"scope": scope})

    dumped_doc = docs.model_dump(by_alias=True)["docs"][0]
    assert dumped_doc["digest"] == doc_digest
    assert "content" not in dumped_doc
    assert catalog.catalog_digest.startswith("sha256:")

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReadMagiDocs.model_validate(
            {
                "scope": scope,
                "docs": (
                    {
                        "docId": "docs.authoring.recipe-builder-mode",
                        "digest": doc_digest,
                        "title": "Recipe Builder Mode",
                        "summary": "Public authoring metadata only.",
                        "content": "raw docs are not exposed by this contract",
                    },
                ),
            }
        )


def test_tool_metadata_rejects_private_paths() -> None:
    scope = _scope_payload()

    with pytest.raises(ValidationError, match="private paths"):
        InspectToolCatalog.model_validate(
            {"scope": scope, "references": ("/Users/kevin/private-tool",)}
        )

    with pytest.raises(ValidationError, match="private paths"):
        ReadMagiDocs.model_validate(
            {
                "scope": scope,
                "docs": (
                    {
                        "docId": "file:/Users/kevin/private-doc",
                        "digest": "sha256:" + "b" * 64,
                        "title": "Private doc",
                        "summary": "Should not validate.",
                    },
                ),
            }
        )


def test_tool_metadata_rejects_credential_and_raw_model_markers() -> None:
    scope = _scope_payload()

    with pytest.raises(ValidationError, match="credentials or raw model data"):
        InspectConnectorAvailability.model_validate(
            {"scope": scope, "references": ("connector.example?token=secret-value",)}
        )

    with pytest.raises(ValidationError, match="credentials or raw model data"):
        ReadMagiDocs.model_validate(
            {
                "scope": scope,
                "docs": (
                    {
                        "docId": "doc.example?apiKey=secret-value",
                        "digest": "sha256:" + "c" * 64,
                        "title": "Credential doc",
                        "summary": "Should not validate.",
                    },
                ),
            }
        )

    with pytest.raises(ValidationError, match="credentials or raw model data"):
        ReadMagiDocs.model_validate(
            {"scope": scope, "query": "raw model output from previous turn"}
        )


def test_tool_contract_catalog_digest_defaults_from_references() -> None:
    scope = _scope_payload()
    defaulted = InspectToolCatalog.model_validate({"scope": scope})

    assert defaulted.references == (
        "SourceOpen",
        "CitationVerify",
        "BrowserLive",
        "FileWrite",
    )
    assert defaulted.catalog_digest.startswith("sha256:")
    assert len(defaulted.catalog_digest.removeprefix("sha256:")) == 64


def test_draft_harness_policy_tool_stays_default_off() -> None:
    valid = DraftHarnessPolicyContract.model_validate(
        {
            "scope": _scope_payload(),
            "draftId": "draft.source-review.001",
            "harnessPolicy": {"harnessRefs": ("harness:authoring-static@1",)},
        }
    )

    assert valid.harness_policy.allow_model_calls is False
    assert valid.harness_policy.allow_live_execution is False
    assert valid.activation_eligibility is False

    with pytest.raises(ValidationError, match="allowModelCalls"):
        DraftHarnessPolicyContract.model_validate(
            {
                "scope": _scope_payload(),
                "harnessPolicy": {"allowModelCalls": True},
            }
        )


def test_draft_recipe_pack_tool_is_draft_only_and_keeps_plugin_proposals_non_executable() -> None:
    draft_payload = _fixture("blocked_generated_plugin_proposal.json")
    scope = {
        "botId": draft_payload["botId"],
        "ownerId": draft_payload["ownerId"],
        "sessionId": draft_payload["authoringSessionId"],
        "mode": "recipe_builder",
    }

    drafted = DraftRecipePackContract.model_validate(
        {
            "scope": scope,
            "draft": draft_payload,
        }
    )

    assert drafted.draft_only is True
    assert drafted.activation_enabled is False
    assert drafted.activation_eligibility is False
    assert drafted.draft.status == "blocked"
    assert drafted.generated_plugin_proposals[0].executable is False
    assert drafted.generated_plugin_proposals[0].runtime_entrypoint is None

    executable_payload = json.loads(json.dumps(draft_payload))
    executable_payload["generatedPluginProposals"][0]["executable"] = True
    with pytest.raises(ValidationError, match="executable"):
        DraftRecipePackContract.model_validate(
            {
                "scope": scope,
                "draft": executable_payload,
            }
        )

    with pytest.raises(ValidationError, match="draftOnly"):
        DraftRecipePackContract.model_validate(
            {
                "scope": scope,
                "draft": draft_payload,
                "draftOnly": False,
            }
        )


def test_generate_gap_report_is_local_and_scope_bound() -> None:
    report_payload = _fixture("missing_connector_capability_gap.json")
    session_payload = _fixture("backoffice_reconciliation_builder_session.json")
    scope = {
        "botId": session_payload["botId"],
        "ownerId": session_payload["ownerId"],
        "sessionId": session_payload["sessionId"],
        "mode": "recipe_builder",
    }

    report = GenerateGapReportContract.model_validate(
        {
            "scope": scope,
            "draftId": report_payload["draftId"],
            "report": report_payload,
        }
    )

    assert report.report is not None
    assert report.report.local_only is True
    assert report.report.non_production is True
    assert report.activation_eligibility is False
    assert report.report.gaps[0].blocked_activation is True

    bad_report = dict(report_payload)
    bad_report["sessionId"] = "builder.session.other"
    with pytest.raises(ValidationError, match="gap report sessionId"):
        GenerateGapReportContract.model_validate(
            {
                "scope": scope,
                "draftId": report_payload["draftId"],
                "report": bad_report,
            }
        )


def test_save_recipe_draft_enforces_current_bot_scope() -> None:
    payload = _session_payload()
    draft_payload = payload["draft"]
    assert isinstance(draft_payload, dict)
    scope = _scope_payload()

    valid = SaveRecipePackDraftContract.model_validate(
        {
            "scope": scope,
            "draft": draft_payload,
            "draftId": draft_payload["draftId"],
        }
    )

    assert valid.saved_scope == "current_bot_draft_store"
    assert valid.draft_id == draft_payload["draftId"]
    assert valid.activation_eligibility is False

    bad_bot = dict(scope)
    bad_bot["botId"] = "bot_other"
    with pytest.raises(ValidationError, match="scope botId"):
        SaveRecipePackDraftContract.model_validate(
            {"scope": bad_bot, "draft": draft_payload, "draftId": draft_payload["draftId"]}
        )

    bad_session = dict(scope)
    bad_session["sessionId"] = "builder.session.other"
    with pytest.raises(ValidationError, match="scope sessionId"):
        SaveRecipePackDraftContract.model_validate(
            {"scope": bad_session, "draft": draft_payload, "draftId": draft_payload["draftId"]}
        )

    with pytest.raises(ValidationError, match="savedScope"):
        SaveRecipePackDraftContract.model_validate(
            {
                "scope": scope,
                "draft": draft_payload,
                "draftId": draft_payload["draftId"],
                "savedScope": "staged_draft_store",
            }
        )


def test_draft_eval_fixtures_syncs_and_rejects_secret_fields() -> None:
    payload = _session_payload()
    draft = RecipePackDraft.model_validate(payload["draft"])

    validated = DraftEvalFixturesContract.model_validate(
        {
            "scope": _scope_payload(),
            "draftId": draft.draft_id,
            "fixtures": (
                {
                    "fixtureSetId": "eval.source-review.001",
                    "draftId": draft.draft_id,
                    "scenarioRefs": ("scenario.source-review.001",),
                    "expectedGapRefs": ("gap.source-review.001",),
                },
            ),
        }
    )

    assert validated.fixtures[0].draft_id == draft.draft_id
    assert validated.scope_digest.startswith("sha256:")

    with pytest.raises(ValidationError, match="fixture draftId must match request draftId"):
        DraftEvalFixturesContract.model_validate(
            {
                "scope": _scope_payload(),
                "draftId": draft.draft_id,
                "fixtures": (
                    {
                        "fixtureSetId": "eval.source-review.001",
                        "draftId": "draft.other",
                        "scenarioRefs": ("scenario.source-review.001",),
                        "expectedGapRefs": ("gap.source-review.001",),
                    },
                ),
            }
        )

    with pytest.raises(ValidationError, match="raw credential"):
        DraftEvalFixturesContract.model_validate(
            {
                "scope": _scope_payload(),
                "draftId": draft.draft_id,
                "fixtures": (
                    {
                        "fixtureSetId": "eval.source-review.001",
                        "draftId": draft.draft_id,
                        "scenarioRefs": ("scenario.source-review.001",),
                        "expectedGapRefs": ("gap.source-review.001",),
                        "rawCredential": "secret-value",
                    },
                ),
            }
        )


def test_run_compile_recipe_pack_delegates_to_authoring_compiler(monkeypatch) -> None:
    session = RecipeBuilderSession.model_validate(_session_payload())
    called: dict[str, object] = {}

    def fake_compile(
        scope: RecipeBuilderSession,
        catalog: CompileRecipePackCatalog | None = None,
    ) -> CompileRecipePackResult:
        called["scope"] = scope
        called["catalog"] = catalog
        return CompileRecipePackResult(ok=False, blockedReasons=("fake-block",))

    monkeypatch.setattr(tool_contracts_module, "compile_recipe_pack", fake_compile)

    result = run_compile_recipe_pack(
        {
            "scope": session.model_dump(by_alias=True),
            "catalog": CompileRecipePackCatalog.default(),
        }
    )

    assert result.result is not None
    assert result.result.blocked_reasons == ("fake-block",)
    assert isinstance(called["scope"], RecipeBuilderSession)
    assert called["scope"].bot_id == session.bot_id
    assert isinstance(called["catalog"], CompileRecipePackCatalog)


def test_compile_dry_run_and_activation_plan_require_full_builder_session_scope() -> None:
    with pytest.raises(ValidationError, match="RecipeBuilderSession scope with draft"):
        CompileRecipePack.model_validate({"scope": _scope_payload()})

    with pytest.raises(ValidationError, match="RecipeBuilderSession scope with draft"):
        DryRunRecipePack.model_validate({"scope": _scope_payload()})

    with pytest.raises(ValidationError, match="RecipeBuilderSession scope with draft"):
        GenerateActivationPlan.model_validate(
            {
                "scope": _scope_payload(),
                "draft": _session_payload()["draft"],
            }
        )


def test_run_dry_run_recipe_pack_delegates_to_authoring_dry_run(monkeypatch) -> None:
    session = RecipeBuilderSession.model_validate(_session_payload())
    called: dict[str, object] = {}

    def fake_run(
        scope: RecipeBuilderSession,
        request: DryRunRecipePackRequest | None = None,
        catalog: DryRunRecipePackCatalog | None = None,
        config=None,
    ) -> DryRunRecipePackResult:
        called["scope"] = scope
        called["request"] = request
        called["catalog"] = catalog
        called["config"] = config
        return DryRunRecipePackResult(
            ok=True,
            selectedRoute="recipe.source-review.verify",
            contextProjection="structured_summary",
            expectedTools=(),
            expectedEvidence=(),
            expectedValidators=(),
            expectedApprovals=(),
            predictedTerminalStates=("resolved",),
            deniedActions=(),
            warnings=(),
            activationEligibility=False,
        )

    monkeypatch.setattr(tool_contracts_module, "dry_run_recipe_pack", fake_run)

    result = run_dry_run_recipe_pack(
        {
            "scope": session.model_dump(by_alias=True),
            "request": {
                "requestedRouteRef": "recipe.source-review.verify",
                "governedTask": True,
            },
            "catalog": DryRunRecipePackCatalog(routeRefs=("recipe.source-review.verify",)),
            "config": {"noMatchTerminalState": "ask_user_for_route"},
        }
    )

    assert result.result is not None
    assert result.result.ok is True
    assert isinstance(called["scope"], RecipeBuilderSession)
    assert called["request"] is not None
    assert called["request"].requested_route_ref == "recipe.source-review.verify"


def test_run_generate_activation_plan_is_non_activating(monkeypatch) -> None:
    payload = _session_payload()
    session = RecipeBuilderSession.model_validate(payload)
    draft_payload = payload["draft"]
    assert isinstance(draft_payload, dict)
    compile_calls = 0

    def fake_compile(scope: object, catalog: object | None = None) -> CompileRecipePackResult:
        nonlocal compile_calls
        compile_calls += 1
        return CompileRecipePackResult(
            ok=True,
            compiledSnapshotDigest="sha256:" + "1" * 64,
            effectivePolicySnapshotDigest="sha256:" + "2" * 64,
            blockedReasons=(),
            diagnostics=(),
            warnings=(),
            hardInvariantResults=(),
        )

    monkeypatch.setattr(tool_contracts_module, "compile_recipe_pack", fake_compile)

    plan = run_generate_activation_plan(
        {
            "scope": session.model_dump(by_alias=True),
            "draft": draft_payload,
        }
    )

    assert compile_calls == 1
    assert plan.can_activate is False
    assert plan.activation_eligibility is False
    assert plan.activation_plan == ()
    assert "activation is intentionally disabled in authoring mode" in plan.blockers
    assert plan.compiled_snapshot_digest == "sha256:" + "1" * 64

    with pytest.raises(ValidationError, match="canActivate"):
        run_generate_activation_plan(
            {
                "scope": session.model_dump(by_alias=True),
                "draft": draft_payload,
                "canActivate": True,
            }
        )


def test_run_generate_activation_plan_rejects_scope_session_mismatch() -> None:
    payload = _session_payload()
    session = RecipeBuilderSession.model_validate(payload)
    draft_payload = json.loads(json.dumps(payload["draft"]))
    draft_payload["authoringSessionId"] = "builder.session.other"
    with pytest.raises(ValidationError, match="scope sessionId"):
        run_generate_activation_plan(
            {
                "scope": session.model_dump(by_alias=True),
                "draft": draft_payload,
            }
        )


def test_run_generate_activation_plan_rejects_same_scope_different_draft() -> None:
    payload = _session_payload()
    session = RecipeBuilderSession.model_validate(payload)
    altered_draft = json.loads(json.dumps(payload["draft"]))
    altered_draft["pack"]["toolPolicy"]["allowedToolRefs"] = ["UnknownTool"]

    with pytest.raises(ValidationError, match="draft must match RecipeBuilderSession draft"):
        run_generate_activation_plan(
            {
                "scope": session.model_dump(by_alias=True),
                "draft": altered_draft,
            }
        )


def test_authoring_tool_contract_import_is_runtime_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.authoring.tool_contracts")

forbidden_exact = (
    "google.adk",
    "openai",
    "openmagi_core_agent.runtime",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.host",
    "openmagi_core_agent.tools.toolhost",
    "google.generativeai",
    "requests",
    "httpx",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
]
if loaded:
    raise AssertionError(f"tool_contracts imported forbidden modules: {loaded}")
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_tool_contracts_are_publicly_importable_from_authoring_package() -> None:
    from openmagi_core_agent import authoring

    assert authoring.ReadMagiDocs is ReadMagiDocs
    assert authoring.InspectRecipeRegistry is InspectRecipeRegistry
    assert authoring.InspectToolCatalog is InspectToolCatalog
    assert authoring.InspectPluginCatalog is InspectPluginCatalog
    assert authoring.InspectConnectorAvailability is InspectConnectorAvailability
    assert authoring.InspectValidatorRegistry is InspectValidatorRegistry
    assert authoring.InspectHarnessRegistry is InspectHarnessRegistry
    assert authoring.DraftHarnessPolicyTool is DraftHarnessPolicyTool
    assert authoring.DraftRecipePackTool is DraftRecipePackTool
    assert authoring.DraftEvalFixtures is DraftEvalFixtures
    assert authoring.GenerateGapReport is GenerateGapReport
    assert authoring.SaveRecipePackDraft is SaveRecipePackDraft
    assert authoring.CompileRecipePack is CompileRecipePack
    assert authoring.DryRunRecipePack is DryRunRecipePack
    assert authoring.GenerateActivationPlan is GenerateActivationPlan
    assert authoring.DraftRecipePack is DraftRecipePackData
    assert "BuilderAgentSession" not in authoring.__all__
    assert not hasattr(authoring, "BuilderAgentSession")


def test_authoring_package_exports_tool_contracts_lazily() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            r'''
import importlib
import sys

import openmagi_core_agent.authoring.compiler

if "openmagi_core_agent.authoring.tool_contracts" in sys.modules:
    raise AssertionError("tool_contracts should remain unloaded after compiler import")

from openmagi_core_agent import authoring

_ = authoring.CompileRecipePack
_ = authoring.DraftRecipePackTool
_ = authoring.DraftHarnessPolicyTool
_ = authoring.DraftEvalFixtures
_ = authoring.GenerateGapReport
_ = authoring.GenerateActivationPlan

if "openmagi_core_agent.authoring.tool_contracts" not in sys.modules:
    raise AssertionError("tool_contracts should load when accessing tool contract attribute")

importlib.reload(importlib.import_module("openmagi_core_agent.authoring.compiler"))

if "openmagi_core_agent.authoring.tool_contracts" in sys.modules:
    # keep boundary tests behavior: it was allowed to be imported later via tools
    pass
''',
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
