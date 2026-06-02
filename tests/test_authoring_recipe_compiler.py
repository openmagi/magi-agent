from __future__ import annotations

import copy
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    compile_recipe_pack,
)


FIXTURES = Path(__file__).parent / "fixtures" / "authoring"


def _fixture() -> dict[str, object]:
    return json.loads((FIXTURES / "compile_recipe_pack_success.json").read_text())


def _catalog() -> CompileRecipePackCatalog:
    return CompileRecipePackCatalog(
        connector_refs=("connector.source.readonly",),
        tool_refs=("BrowserLive", "CitationVerify", "FileWrite", "SourceOpen"),
        plugin_refs=("plugin.source-review.readonly",),
        validator_refs=("validator:sourceOpened@1", "validator:quoteExactMatch@1"),
        harness_refs=("harness:authoring-static@1",),
        required_evidence_refs=("openedSourceSnapshot", "quoteDigest"),
        evidence_producer_refs=("evidence:source-opened@1", "evidence:quote-digest@1"),
        approval_authority_refs=("authority:owner-human@1",),
        hard_invariant_refs=("invariant.no-live-execution", "invariant.no-activation"),
        required_hard_invariant_refs=(
            "invariant.no-live-execution",
            "invariant.no-activation",
        ),
    )


def _pack(payload: dict[str, object]) -> dict[str, object]:
    draft = payload["draft"]
    assert isinstance(draft, dict)
    pack = draft["pack"]
    assert isinstance(pack, dict)
    return pack


def _set_nested(payload: dict[str, object], path: tuple[str, ...], value: object) -> None:
    current: dict[str, object] = payload
    for key in path[:-1]:
        nested = current[key]
        assert isinstance(nested, dict)
        current = nested
    current[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    (
        (
            ("draft", "pack", "toolPolicy", "allowedConnectorRefs"),
            ["connector.source.readonly", "connector.unknown"],
            "unknown_connector_ref:connector.unknown",
        ),
        (
            ("draft", "pack", "toolPolicy", "allowedToolRefs"),
            ["SourceOpen", "MysteryTool"],
            "unknown_tool_ref:MysteryTool",
        ),
        (
            ("draft", "pack", "toolPolicy", "allowedPluginRefs"),
            ["plugin.missing"],
            "unknown_plugin_ref:plugin.missing",
        ),
        (
            ("draft", "pack", "validatorPolicy", "validatorRefs"),
            ["validator:missing@1"],
            "unknown_validator_ref:validator:missing@1",
        ),
        (
            ("draft", "pack", "harnessPolicy", "harnessRefs"),
            ["harness:missing@1"],
            "unknown_harness_ref:harness:missing@1",
        ),
        (
            ("draft", "pack", "evidencePolicy", "requiredEvidenceRefs"),
            ["openedSourceSnapshot", "evidenceLabel.unknown"],
            "unknown_required_evidence_ref:evidenceLabel.unknown",
        ),
        (
            ("draft", "pack", "evidencePolicy", "evidenceProducerRefs"),
            ["evidence:missing@1"],
            "unknown_evidence_producer_ref:evidence:missing@1",
        ),
    ),
)
def test_compile_recipe_pack_rejects_unknown_catalog_refs(
    path: tuple[str, ...],
    value: object,
    reason: str,
) -> None:
    payload = _fixture()
    _set_nested(payload, path, value)

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert reason in result.blocked_reasons
    assert result.compiled_snapshot_digest is None
    assert any(diagnostic.code == reason.split(":", maxsplit=1)[0] for diagnostic in result.diagnostics)


def test_compile_recipe_pack_rejects_raw_governed_projection() -> None:
    payload = _fixture()
    projection_policy = _pack(payload)["projectionPolicy"]
    assert isinstance(projection_policy, dict)
    projection_policy["mode"] = "raw_governed"
    projection_policy["rawGovernedProjectionEnabled"] = True

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "raw_governed_projection_disabled" in result.blocked_reasons


def test_compile_recipe_pack_rejects_repair_loop_without_terminal_state() -> None:
    payload = _fixture()
    repair_policy = _pack(payload)["repairPolicy"]
    assert isinstance(repair_policy, dict)
    repair_policy["maxRepairAttempts"] = 2
    repair_policy["terminalStates"] = []

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "repair_terminal_state_missing" in result.blocked_reasons


def test_compile_recipe_pack_rejects_unknown_approval_authority() -> None:
    payload = _fixture()
    approval_policy = _pack(payload)["approvalPolicy"]
    assert isinstance(approval_policy, dict)
    approval_policy["authorityRefs"] = ["authority:unknown@1"]

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "unknown_approval_authority_ref:authority:unknown@1" in result.blocked_reasons


def test_compile_recipe_pack_rejects_disabled_or_log_only_hard_invariants() -> None:
    payload = _fixture()
    _pack(payload)["hardInvariants"] = [
        {
            "invariantId": "invariant.disabled",
            "description": "Disabled invariants are not enforceable.",
            "mode": "disabled",
        },
        {
            "invariantId": "invariant.log-only",
            "description": "Log-only invariants are not enforceable.",
            "mode": "log_only",
        },
    ]

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "hard_invariant_not_enforced:invariant.disabled" in result.blocked_reasons
    assert "hard_invariant_not_enforced:invariant.log-only" in result.blocked_reasons
    assert [item.invariant_id for item in result.hard_invariant_results] == [
        "invariant.disabled",
        "invariant.log-only",
    ]
    assert all(item.ok is False for item in result.hard_invariant_results)


def test_compile_recipe_pack_rejects_unknown_hard_invariant() -> None:
    payload = _fixture()
    _pack(payload)["hardInvariants"] = [
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
        {
            "invariantId": "invariant.spoofed",
            "description": "Unknown invariant should not be accepted.",
            "mode": "enforced",
        },
    ]

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "unknown_hard_invariant_ref:invariant.spoofed" in result.blocked_reasons
    assert any(
        item.invariant_id == "invariant.spoofed" and item.ok is False
        for item in result.hard_invariant_results
    )


def test_compile_recipe_pack_rejects_missing_required_hard_invariant() -> None:
    payload = _fixture()
    _pack(payload)["hardInvariants"] = [
        {
            "invariantId": "invariant.no-live-execution",
            "description": "Draft compilation must remain validation-only.",
            "mode": "enforced",
        },
    ]

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "required_hard_invariant_missing:invariant.no-activation" in result.blocked_reasons


def test_compile_recipe_pack_rejects_invalid_budget_caps() -> None:
    payload = _fixture()
    budget_policy = _pack(payload)["budgetPolicy"]
    assert isinstance(budget_policy, dict)
    budget_policy["maxToolCalls"] = 0

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "budget_cap_invalid:maxToolCalls" in result.blocked_reasons


def test_compile_recipe_pack_requires_bot_owner_session_recipe_builder_scope() -> None:
    payload = _fixture()
    payload["mode"] = "chat"

    result = compile_recipe_pack(payload, catalog=_catalog())

    assert result.ok is False
    assert "invalid_recipe_builder_scope" in result.blocked_reasons
    assert any(diagnostic.code == "invalid_recipe_builder_scope" for diagnostic in result.diagnostics)


def test_compile_recipe_pack_success_produces_stable_deterministic_digests() -> None:
    payload = _fixture()

    result = compile_recipe_pack(payload, catalog=_catalog())
    second_result = compile_recipe_pack(copy.deepcopy(payload), catalog=_catalog())

    assert result.ok is True
    assert result.blocked_reasons == ()
    assert result.compiled_snapshot_digest == second_result.compiled_snapshot_digest
    assert result.effective_policy_snapshot_digest == second_result.effective_policy_snapshot_digest
    assert result.compiled_snapshot_digest == (
        "sha256:e9b3ab542bc86cfb76fbdea08b67707985c61fd8d23d593dda90b6ba80dc1ed8"
    )
    assert result.effective_policy_snapshot_digest == (
        "sha256:67924eb0445a23fce65f4044294591ad4406beff07bee9080539a0c39d95fee0"
    )
    assert all(item.ok for item in result.hard_invariant_results)


def test_compile_recipe_pack_does_not_open_network_or_execute_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_socket(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("compile_recipe_pack must not open network sockets")

    monkeypatch.setattr(socket, "socket", blocked_socket)

    result = compile_recipe_pack(_fixture(), catalog=_catalog())

    assert result.ok is True


def test_compiler_contract_model_copy_revalidates_alias_updates() -> None:
    catalog = _catalog()

    with pytest.raises(ValidationError, match="catalog refs"):
        catalog.model_copy(update={"toolRefs": ("",)})


def test_authoring_compiler_import_stays_runtime_core_and_toolhost_free() -> None:
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
    "magi_agent.recipes.compiler",
    "magi_agent.runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.kernel",
    "magi_agent.transport",
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
# versions; this assertion is about new imports caused by authoring.compiler.

module = importlib.import_module("magi_agent.authoring.compiler")
assert hasattr(module, "compile_recipe_pack")

loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    and name not in baseline
]
if loaded:
    raise AssertionError(f"authoring compiler loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
