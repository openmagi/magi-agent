from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.harness.policy_state import build_harness_policy_state
from openmagi_core_agent.harness.presets import builtin_preset_by_key, builtin_preset_catalog


EXPECTED_SOURCE_PRECEDENCE = (
    "platform hard safety policy",
    "org policy",
    "bot runtime policy",
    "security-critical Core Pack policy",
    "native plugin policy",
    "user agent.config.yaml",
    "user USER.md",
    "session-level temporary policy",
    "model-suggested plans",
)


def _preset_dump_by_key() -> dict[str, dict[str, object]]:
    state = build_harness_policy_state()
    dumped = state.model_dump(by_alias=True)

    return {preset["key"]: preset for preset in dumped["presets"]}


def test_source_precedence_order_and_model_suggested_non_authoritative() -> None:
    state = build_harness_policy_state()
    dumped = state.model_dump(by_alias=True)

    sources = dumped["sourcePrecedence"]
    assert tuple(source["source"] for source in sources) == EXPECTED_SOURCE_PRECEDENCE
    assert tuple(source["rank"] for source in sources) == tuple(range(1, 10))
    assert all(source["authoritative"] is True for source in sources[:-1])
    assert sources[-1]["source"] == "model-suggested plans"
    assert sources[-1]["authoritative"] is False
    assert sources[-1]["nonAuthoritativeReason"] == "never authoritative over policy"


@pytest.mark.parametrize(
    "key",
    (
        "dangerous-patterns",
        "path-escape",
        "secret-exposure",
        "git-safety",
        "sealed-files",
        "arity-permission",
    ),
)
def test_security_hard_safety_presets_are_not_dashboard_toggleable(key: str) -> None:
    preset = _preset_dump_by_key()[key]

    assert preset["defaultOn"] is True
    assert preset["optOut"] is False
    assert preset["hardSafety"] is True
    assert preset["securityCritical"] is True
    assert preset["dashboardToggleable"] is False


@pytest.mark.parametrize("key", ("answer-quality", "coding-context", "parallel-research"))
def test_non_hard_safety_opt_out_presets_are_dashboard_toggleable(key: str) -> None:
    preset = _preset_dump_by_key()[key]

    assert preset["hardSafety"] is False
    assert preset["securityCritical"] is False
    assert preset["optOut"] is True
    assert preset["dashboardToggleable"] is True


def test_benchmark_verifier_and_coding_context_metadata_surface_with_aliases() -> None:
    presets = _preset_dump_by_key()

    verifier = presets["benchmark-verifier"]
    assert verifier["hookPoints"] == ("beforeCommit",)
    assert verifier["blocking"] is True
    assert verifier["failOpen"] is True
    assert verifier["timeoutMs"] == 65_000
    assert verifier["envGates"] == ("MAGI_PRESET_VERIFIERS",)
    assert "before-commit-verifier" in verifier["contributedHooks"]
    assert "report-only-evidence" in verifier["verifierGates"]
    assert "grounding-required" in verifier["verifierGates"]

    coding_context = presets["coding-context"]
    hooks = {
        contribution["hook"]: contribution
        for contribution in coding_context["hookContributions"]
    }
    assert set(hooks) == {"repo-map", "coding-context", "focus-chain"}
    assert hooks["repo-map"]["hookPoints"] == ("beforeLLMCall",)
    assert hooks["repo-map"]["failOpen"] is True
    assert hooks["repo-map"]["timeoutMs"] == 12_000
    assert hooks["repo-map"]["envGates"] == ("CORE_AGENT_REPO_MAP",)
    assert hooks["repo-map"]["configGates"] == ("repo-map",)
    assert hooks["repo-map"]["runtimeDefaultOn"] is True
    assert "source noted by ledger #816" in " ".join(hooks["repo-map"]["behaviorNotes"])
    assert hooks["focus-chain"]["runtimeDefaultOn"] is False


def test_snapshot_top_level_runtime_attachment_flags_are_false() -> None:
    dumped = build_harness_policy_state().model_dump(by_alias=True)

    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_explicit_empty_presets_catalog_is_respected() -> None:
    state = build_harness_policy_state(presets=())

    assert state.presets == ()


def test_preset_fail_closed_is_inverse_of_known_fail_open() -> None:
    presets = _preset_dump_by_key()

    fail_open_preset = presets["coding-context"]
    assert fail_open_preset["failOpen"] is True
    assert fail_open_preset["failClosed"] is False

    fail_closed_preset = presets["coding-workspace-lock"]
    assert fail_closed_preset["failOpen"] is False
    assert fail_closed_preset["failClosed"] is True


def test_hook_contribution_fail_closed_is_inverse_of_known_fail_open() -> None:
    hooks = {
        contribution["hook"]: contribution
        for contribution in _preset_dump_by_key()["path-escape"]["hookContributions"]
    }

    before_tool_hook = hooks["builtin:resource-boundary"]
    assert before_tool_hook["failOpen"] is False
    assert before_tool_hook["failClosed"] is True

    before_commit_hook = hooks["builtin:resource-boundary-before-commit"]
    assert before_commit_hook["failOpen"] is True
    assert before_commit_hook["failClosed"] is False


def test_policy_state_import_does_not_load_runtime_or_route_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.harness.policy_state")
forbidden_prefixes = (
    "google.adk",
)
forbidden_modules = (
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.hooks.bus",
    "openmagi_core_agent.tools.dispatcher",
)
loaded = [
    module
    for module in sys.modules
    if module.startswith(forbidden_prefixes) or module in forbidden_modules
]
if loaded:
    raise AssertionError(f"policy_state import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_policy_state_is_immutable_and_uses_defensive_catalog_copies() -> None:
    state = build_harness_policy_state()

    with pytest.raises(ValidationError):
        state.presets[0].key = "mutated"

    custom_catalog = builtin_preset_catalog()
    mutated_catalog = (
        custom_catalog[0].model_copy(update={"scope_hints": ("mutated",)}),
        *custom_catalog[1:],
    )
    custom_state = build_harness_policy_state(presets=mutated_catalog)
    global_state = build_harness_policy_state()
    global_catalog_preset = builtin_preset_by_key(custom_catalog[0].key)

    assert custom_state.presets[0].scope_hints == ("mutated",)
    assert global_state.presets[0].scope_hints != ("mutated",)
    assert global_catalog_preset.scope_hints != ("mutated",)
