from __future__ import annotations

import builtins
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from magi_agent.authoring.dry_run import (
    DryRunRecipePackCatalog,
    DryRunRecipePackConfig,
    DryRunRecipePackRequest,
    dry_run_recipe_pack,
)


FIXTURES = Path(__file__).parent / "fixtures" / "authoring"


def _fixture() -> dict[str, object]:
    return json.loads((FIXTURES / "compile_recipe_pack_success.json").read_text())


def _catalog() -> DryRunRecipePackCatalog:
    return DryRunRecipePackCatalog(
        routeRefs=("recipe.source-review.collect", "recipe.source-review.verify"),
        generalChatRouteRef="route.general_chat",
    )


def _request(**overrides: object) -> DryRunRecipePackRequest:
    payload: dict[str, object] = {
        "requestedRouteRef": "recipe.source-review.verify",
        "governedTask": True,
    }
    payload.update(overrides)
    return DryRunRecipePackRequest.model_validate(payload)


def test_dry_run_projects_authoring_policy_without_domain_hardcoding() -> None:
    payload = _fixture()
    result = dry_run_recipe_pack(payload, request=_request(), catalog=_catalog())

    assert result.ok is True
    assert result.selected_route == "recipe.source-review.verify"
    assert result.context_projection == "structured_summary"
    assert result.expected_tools == ("SourceOpen", "CitationVerify")
    assert result.expected_evidence == ("openedSourceSnapshot", "quoteDigest")
    assert result.expected_validators == (
        "validator:sourceOpened@1",
        "validator:quoteExactMatch@1",
    )
    assert result.expected_approvals == ("authority:owner-human@1",)
    assert result.predicted_terminal_states == ("resolved", "blocked")
    assert result.denied_actions == ("BrowserLive", "FileWrite")
    assert result.activation_eligibility is False


def test_dry_run_projection_is_driven_by_pack_policy_not_source_review_names() -> None:
    payload = _fixture()
    pack = payload["draft"]["pack"]
    pack["recipeRefs"] = ["recipe.operations.reconcile"]
    pack["toolPolicy"]["allowedToolRefs"] = ["CalculatorTool"]
    pack["toolPolicy"]["deniedToolRefs"] = ["ExternalDelivery"]
    pack["evidencePolicy"]["requiredEvidenceRefs"] = ["calculationReceipt"]
    pack["validatorPolicy"]["validatorRefs"] = ["validator:calculationReceipt@1"]
    pack["approvalPolicy"]["authorityRefs"] = ["authority:ops-reviewer@1"]
    pack["repairPolicy"]["terminalStates"] = ["balanced", "needs_review"]
    pack["harnessPolicy"]["harnessRefs"] = ["harness:ops-static@1"]
    pack["toolPolicy"]["allowedConnectorRefs"] = ["connector.ledger.readonly"]
    pack["toolPolicy"]["allowedPluginRefs"] = ["plugin.ops-reconcile.readonly"]
    pack["evidencePolicy"]["evidenceProducerRefs"] = ["evidence:calculation-receipt@1"]
    pack["hardInvariants"] = [
        {
            "invariantId": "invariant.no-live-execution",
            "description": "Draft compilation must remain validation-only.",
            "mode": "enforced",
        },
        {
            "invariantId": "invariant.no-activation",
            "description": "Compilation must not activate or save the draft.",
            "mode": "enforced",
        },
    ]

    result = dry_run_recipe_pack(
        payload,
        request=DryRunRecipePackRequest(
            requestedRouteRef="recipe.operations.reconcile",
            governedTask=True,
        ),
        catalog=DryRunRecipePackCatalog(
            routeRefs=("recipe.operations.reconcile",),
            compilerCatalog={
                "connectorRefs": ("connector.ledger.readonly",),
                "toolRefs": ("CalculatorTool", "ExternalDelivery"),
                "pluginRefs": ("plugin.ops-reconcile.readonly",),
                "validatorRefs": ("validator:calculationReceipt@1",),
                "harnessRefs": ("harness:ops-static@1",),
                "requiredEvidenceRefs": ("calculationReceipt",),
                "evidenceProducerRefs": ("evidence:calculation-receipt@1",),
                "approvalAuthorityRefs": ("authority:ops-reviewer@1",),
                "hardInvariantRefs": (
                    "invariant.no-live-execution",
                    "invariant.no-activation",
                ),
                "requiredHardInvariantRefs": (
                    "invariant.no-live-execution",
                    "invariant.no-activation",
                ),
            },
        ),
    )

    assert result.ok is True
    assert result.selected_route == "recipe.operations.reconcile"
    assert result.expected_tools == ("CalculatorTool",)
    assert result.expected_evidence == ("calculationReceipt",)
    assert result.expected_validators == ("validator:calculationReceipt@1",)
    assert result.expected_approvals == ("authority:ops-reviewer@1",)
    assert result.predicted_terminal_states == ("balanced", "needs_review")
    assert result.denied_actions == ("ExternalDelivery",)


def test_dry_run_does_not_call_models_execute_tools_or_open_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked_socket(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("dry_run_recipe_pack must not open network sockets")

    def blocked_subprocess(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("dry_run_recipe_pack must not execute subprocesses")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(subprocess, "run", blocked_subprocess)

    result = dry_run_recipe_pack(_fixture(), request=_request(), catalog=_catalog())

    assert result.ok is True


def test_dry_run_performs_no_file_side_effect_after_fixture_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = builtins.open

    def blocked_write(path: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        if any(flag in mode for flag in ("w", "a", "+", "x")):
            raise AssertionError(f"dry_run_recipe_pack must not mutate files: {path}")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", blocked_write)

    result = dry_run_recipe_pack(_fixture(), request=_request(), catalog=_catalog())

    assert result.ok is True


def test_no_route_match_returns_configured_terminal_state_without_execution() -> None:
    result = dry_run_recipe_pack(
        _fixture(),
        request=_request(requestedRouteRef="recipe.missing"),
        catalog=_catalog(),
        config=DryRunRecipePackConfig(noMatchTerminalState="ask_user_for_route"),
    )

    assert result.ok is False
    assert result.selected_route is None
    assert result.predicted_terminal_states == ("ask_user_for_route",)
    assert "no_route_match" in result.denied_actions


def test_governed_task_cannot_fall_back_to_general_chat() -> None:
    result = dry_run_recipe_pack(
        _fixture(),
        request=_request(requestedRouteRef="route.general_chat", governedTask=True),
        catalog=_catalog(),
        config=DryRunRecipePackConfig(noMatchTerminalState="ask_user_for_route"),
    )

    assert result.ok is False
    assert result.selected_route is None
    assert result.predicted_terminal_states == ("ask_user_for_route",)
    assert "general_chat_fallback_denied" in result.denied_actions
    assert any("governed task" in warning.message for warning in result.warnings)


def test_activation_is_never_implied_by_successful_dry_run() -> None:
    result = dry_run_recipe_pack(_fixture(), request=_request(), catalog=_catalog())

    assert result.ok is True
    assert result.activation_eligibility is False
    assert result.model_dump(by_alias=True)["activationEligibility"] is False


@pytest.mark.parametrize("missing_field", ("botId", "ownerId", "sessionId"))
def test_bot_owner_session_recipe_builder_scope_is_required(missing_field: str) -> None:
    payload = _fixture()
    payload.pop(missing_field)

    result = dry_run_recipe_pack(payload, request=_request(), catalog=_catalog())

    assert result.ok is False
    assert "invalid_recipe_builder_scope" in result.denied_actions
    assert result.activation_eligibility is False


def test_authoring_dry_run_import_stays_runtime_core_toolhost_adk_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.kernel",
    "magi_agent.transport",
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
# Python/uv bootstrap may preload stdlib modules such as urllib on some
# versions; this assertion is about new imports caused by authoring.dry_run.

module = importlib.import_module("magi_agent.authoring.dry_run")
assert hasattr(module, "dry_run_recipe_pack")

loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    and name not in baseline
]
if loaded:
    raise AssertionError(f"authoring dry-run loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
