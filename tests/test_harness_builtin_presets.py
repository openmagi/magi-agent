from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.harness.presets import (
    PresetCategory,
    builtin_preset_by_key,
    builtin_preset_catalog,
    builtin_preset_keys,
)
from magi_agent.harness.profiles import build_default_profile


REQUIRED_PRESET_KEYS = {
    "answer-quality",
    "completion-evidence",
    "pre-refusal",
    "output-purity",
    "deferral-blocker",
    "fact-grounding",
    "self-claim",
    "resource-existence",
    "claim-citation",
    "deterministic-evidence",
    "coding-verification",
    "coding-context",
    "coding-workspace-lock",
    "coding-child-review",
    "benchmark-verifier",
    "task-contract",
    "goal-progress",
    "task-board-completion",
    "output-delivery",
    "artifact-delivery",
    "response-language",
    "parallel-research",
    "source-authority",
    "memory-continuity",
    "dangerous-patterns",
    "path-escape",
    "secret-exposure",
    "git-safety",
    "sealed-files",
    "arity-permission",
    "autopilot-phase-router",
    "autopilot-interview-gate",
    "autopilot-consensus-gate",
    "autopilot-review-gate",
    "autopilot-qa-gate",
}


def test_builtin_catalog_contains_required_categories_and_keys() -> None:
    catalog = builtin_preset_catalog()

    assert set(builtin_preset_keys()) == REQUIRED_PRESET_KEYS
    assert {preset.category for preset in catalog} == set(PresetCategory)
    assert tuple(preset.key for preset in catalog) == tuple(sorted(REQUIRED_PRESET_KEYS))


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
def test_security_hard_safety_presets_are_default_on_and_not_opt_out(key: str) -> None:
    preset = builtin_preset_by_key(key)

    assert preset.category is PresetCategory.SECURITY
    assert preset.default_on is True
    assert preset.hard_safety is True
    assert preset.security_critical is True
    assert preset.opt_out is False

    with pytest.raises(ValidationError):
        preset.model_copy(update={"opt_out": True})


def test_verifier_and_prompt_injector_deltas_are_represented_as_metadata() -> None:
    verifier = builtin_preset_by_key("benchmark-verifier")
    response_language = builtin_preset_by_key("response-language")
    coding_context = builtin_preset_by_key("coding-context")

    assert verifier.env_gates == ("MAGI_PRESET_VERIFIERS",)
    assert "before-commit-verifier" in verifier.contributed_hooks
    assert verifier.hook_points == ("beforeCommit",)
    assert verifier.blocking is True
    assert verifier.fail_open is True
    assert verifier.timeout_ms == 65_000
    assert "report-only-evidence" in verifier.verifier_gates
    assert "grounding-required" in verifier.verifier_gates

    assert response_language.config_gates == ("response-language-policy",)
    assert "configured-policy" in response_language.scope_hints

    assert coding_context.hook_points == ("beforeLLMCall",)
    assert coding_context.blocking is True
    assert coding_context.fail_open is True
    assert coding_context.timeout_ms == 10_000
    assert {
        timeout.hook: timeout.timeout_ms for timeout in coding_context.hook_timeouts_ms
    } == {
        "repo-map": 12_000,
        "coding-context": 10_000,
        "focus-chain": 2_000,
    }
    assert coding_context.config_gates == ("repo-map", "coding-context", "focus-chain")
    assert coding_context.env_gates == ()


def _hook_contributions_by_name(key: str) -> dict[str, object]:
    preset = builtin_preset_by_key(key)

    return {contribution.hook: contribution for contribution in preset.hook_contributions}


def test_coding_context_represents_focus_chain_gate_per_hook_only() -> None:
    hooks = _hook_contributions_by_name("coding-context")

    assert set(hooks) == {"repo-map", "coding-context", "focus-chain"}
    assert builtin_preset_by_key("coding-context").contributed_hooks == tuple(sorted(hooks))

    repo_map = hooks["repo-map"]
    assert repo_map.hook_points == ("beforeLLMCall",)
    assert repo_map.blocking is True
    assert repo_map.fail_open is True
    assert repo_map.timeout_ms == 12_000
    assert repo_map.env_gates == ("CORE_AGENT_REPO_MAP",)
    assert repo_map.config_gates == ("repo-map",)
    assert repo_map.runtime_default_on is True
    assert "default" in repo_map.behavior_notes

    coding_context = hooks["coding-context"]
    assert coding_context.hook_points == ("beforeLLMCall",)
    assert coding_context.blocking is True
    assert coding_context.fail_open is True
    assert coding_context.timeout_ms == 10_000
    assert coding_context.env_gates == ("CORE_AGENT_CODING_CONTEXT",)
    assert coding_context.config_gates == ("coding-context",)
    assert coding_context.runtime_default_on is True

    focus_chain = hooks["focus-chain"]
    assert focus_chain.hook_points == ("beforeLLMCall",)
    assert focus_chain.blocking is True
    assert focus_chain.fail_open is True
    assert focus_chain.timeout_ms == 2_000
    assert focus_chain.env_gates == ("MAGI_FOCUS_CHAIN",)
    assert focus_chain.config_gates == ("focus-chain",)
    assert focus_chain.runtime_default_on is False


@pytest.mark.parametrize(
    ("key", "expected"),
    (
        (
            "secret-exposure",
            {
                "builtin:secret-exposure-gate": {
                    "hook_points": ("beforeCommit",),
                    "blocking": True,
                    "fail_open": True,
                    "timeout_ms": 500,
                    "env_gates": ("CORE_AGENT_SECRET_EXPOSURE",),
                    "config_gates": (),
                    "runtime_default_on": True,
                    "behavior_notes": ("violation blocks", "internal errors continue"),
                },
            },
        ),
        (
            "arity-permission",
            {
                "builtin:arity-permission-gate": {
                    "hook_points": ("beforeToolUse",),
                    "blocking": True,
                    "fail_open": None,
                    "timeout_ms": 3_000,
                    "env_gates": ("CORE_AGENT_ARITY_PERMISSION",),
                    "config_gates": ("arity_rules",),
                    "runtime_default_on": False,
                    "behavior_notes": ("permission_decision ask/deny",),
                },
            },
        ),
        (
            "sealed-files",
            {
                "builtin:sealed-files:beforeTurnStart": {
                    "hook_points": ("beforeTurnStart",),
                    "blocking": False,
                    "fail_open": True,
                    "timeout_ms": 5_000,
                },
                "builtin:sealed-files": {
                    "hook_points": ("beforeCommit",),
                    "blocking": True,
                    "fail_open": False,
                    "timeout_ms": 5_000,
                },
                "builtin:sealed-files:afterCommit": {
                    "hook_points": ("afterCommit",),
                    "blocking": False,
                    "fail_open": True,
                    "timeout_ms": 5_000,
                },
            },
        ),
        (
            "dangerous-patterns",
            {
                "builtin:dangerous-patterns": {
                    "hook_points": ("beforeToolUse",),
                    "blocking": True,
                    "fail_open": None,
                    "timeout_ms": 3_000,
                    "env_gates": ("CORE_AGENT_DANGEROUS_PATTERNS",),
                    "config_gates": ("dangerous_patterns", "disable_builtin_hooks"),
                    "runtime_default_on": True,
                    "behavior_notes": ("permission_decision ask/deny",),
                },
            },
        ),
        (
            "git-safety",
            {
                "builtin:git-safety-gate": {
                    "hook_points": ("beforeToolUse",),
                    "blocking": True,
                    "fail_open": False,
                    "timeout_ms": 1_000,
                    "env_gates": (),
                    "config_gates": (),
                    "runtime_default_on": True,
                    "behavior_notes": ("blocks destructive git commands",),
                },
            },
        ),
        (
            "path-escape",
            {
                "builtin:resource-boundary": {
                    "hook_points": ("beforeToolUse",),
                    "blocking": True,
                    "fail_open": False,
                    "timeout_ms": 500,
                },
                "builtin:resource-boundary-before-commit": {
                    "hook_points": ("beforeCommit",),
                    "blocking": True,
                    "fail_open": True,
                    "timeout_ms": 2_000,
                    "behavior_notes": ("beforeCommit may fail open after retry exhaustion",),
                },
            },
        ),
    ),
)
def test_security_presets_represent_per_hook_runtime_metadata(
    key: str, expected: dict[str, dict[str, object]]
) -> None:
    preset = builtin_preset_by_key(key)
    hooks = _hook_contributions_by_name(key)

    assert set(hooks) == set(expected)
    assert preset.contributed_hooks == tuple(expected)
    assert preset.default_on is True
    assert preset.opt_out is False
    assert preset.hard_safety is True
    assert preset.security_critical is True

    if key == "sealed-files":
        for hook in hooks.values():
            assert hook.env_gates == ("CORE_AGENT_SEALED_FILES",)
            assert hook.config_gates == (
                "sealed_files",
                "sealed_files_allowlist_turns",
                "disable_builtin_hooks",
            )
            assert hook.runtime_default_on is True
    elif key == "path-escape":
        for hook in hooks.values():
            assert hook.env_gates == ("CORE_AGENT_RESOURCE_BOUNDARY",)
            assert hook.config_gates == ()
            assert hook.runtime_default_on is True

    for hook_name, expected_metadata in expected.items():
        contribution = hooks[hook_name]
        for field, expected_value in expected_metadata.items():
            actual_value = getattr(contribution, field)
            if field == "behavior_notes":
                for note in expected_value:
                    assert note in actual_value
            else:
                assert actual_value == expected_value


def test_catalog_access_returns_defensive_copies() -> None:
    first = builtin_preset_catalog()
    mutated = first[0].model_copy(update={"scope_hints": ("mutated",)})

    second = builtin_preset_catalog()

    assert first is not second
    assert mutated.scope_hints == ("mutated",)
    assert second[0].scope_hints != ("mutated",)
    assert builtin_preset_by_key(first[0].key) is not first[0]


def test_hook_contribution_alias_dump_is_dashboard_compatible() -> None:
    dumped = builtin_preset_by_key("coding-context").model_dump(by_alias=True)
    focus_chain = next(
        hook for hook in dumped["hookContributions"] if hook["hook"] == "focus-chain"
    )

    assert "hookPoints" in focus_chain
    assert "failOpen" in focus_chain
    assert "timeoutMs" in focus_chain
    assert "runtimeDefaultOn" in focus_chain
    assert focus_chain["envGates"] == ("MAGI_FOCUS_CHAIN",)


def test_default_profile_references_builtin_catalog_without_runtime_attachment() -> None:
    profile = build_default_profile()
    catalog_keys = set(builtin_preset_keys())

    assert set(profile.builtin_preset_keys) == catalog_keys
    assert profile.builtin_preset_keys == tuple(sorted(catalog_keys))


def test_preset_catalog_import_does_not_connect_adk_runtime_primitives() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.presets")
forbidden = (
    "google.adk.runners",
    "google.adk.evaluation",
    "google.adk.plugins",
    "magi_agent.adk_bridge.callback_adapter",
    "magi_agent.adk_bridge.runner_adapter",
)
loaded = [module for module in forbidden if module in sys.modules]
if loaded:
    raise AssertionError(f"preset catalog import loaded runtime modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
