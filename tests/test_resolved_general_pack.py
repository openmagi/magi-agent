"""TDD tests for PR1 — general harness pack.

These tests validate that:
1. Resolving for agent_role="general" produces a ResolvedHarnessPresetState with a
   `.general` pack that has the expected tools and permissionDefaults.
2. The coding/research/verification packs for their own roles remain byte-for-byte
   unchanged (non-regression).
"""
from __future__ import annotations

from magi_agent.harness.engine import HarnessEngine, HarnessResolutionRequest
from magi_agent.harness.resolved import (
    ResolvedHarnessPack,
    build_default_resolved_harness_state,
)

# ---------------------------------------------------------------------------
# Expected constants for the general pack
# ---------------------------------------------------------------------------

_EXPECTED_GENERAL_TOOLS = frozenset(
    {
        # read tier (from workspace_read / reasoning / metadata presets)
        "FileRead",
        # web tier (automation.research)
        "WebSearch",
        "WebFetch",
        # shell tier (gated — declaration only)
        "GeneralAutomationShellRequest",
        # spreadsheet tier (automation.office — gated)
        "CSVRead",
        "SpreadsheetPreview",
        # browser tier (automation.browser-inspect / browser-act — gated)
        "BrowserAction",
    }
)

_EXPECTED_PERMISSION_DEFAULTS = (
    "write_requires_approval",
    "external_directory_requires_approval",
)


# ---------------------------------------------------------------------------
# GREEN: general pack is present and has the right shape
# ---------------------------------------------------------------------------


def test_general_role_resolves_general_pack() -> None:
    state = build_default_resolved_harness_state(agent_role="general")

    assert hasattr(state, "general"), "ResolvedHarnessPresetState must have a 'general' field"
    assert isinstance(state.general, ResolvedHarnessPack)
    assert state.general.enabled is True
    assert state.general.components["hooks"] == ()
    assert state.general.components["childAgent"] == ()
    assert state.general.opt_out_allowed == ()


def test_general_pack_contains_expected_tools() -> None:
    state = build_default_resolved_harness_state(agent_role="general")
    tools = set(state.general.components["tools"])

    assert tools == _EXPECTED_GENERAL_TOOLS


def test_general_pack_permission_defaults_reflect_approval_posture() -> None:
    state = build_default_resolved_harness_state(agent_role="general")
    perms = state.general.components["permissionDefaults"]

    assert set(perms) == set(_EXPECTED_PERMISSION_DEFAULTS)


def test_general_pack_source_is_builtin() -> None:
    state = build_default_resolved_harness_state(agent_role="general")

    assert state.general.source == "builtin"


# ---------------------------------------------------------------------------
# effective_harness_packs for general role
# ---------------------------------------------------------------------------


def test_general_main_run_includes_general_in_effective_packs() -> None:
    state = build_default_resolved_harness_state(agent_role="general")

    assert "general" in state.effective_harness_packs
    assert "hard-safety" in state.effective_harness_packs


def test_general_child_run_selects_only_general_and_hard_safety() -> None:
    state = build_default_resolved_harness_state(agent_role="general", spawn_depth=1)

    assert state.effective_harness_packs == ("general", "hard-safety")


def test_general_main_run_effective_packs_are_complete() -> None:
    """Main general run should include general + coding + research + verification + hard-safety."""
    state = build_default_resolved_harness_state(agent_role="general")

    assert state.effective_harness_packs == ("general", "coding", "research", "verification", "hard-safety")


# ---------------------------------------------------------------------------
# Non-regression: coding/research/verification packs unchanged
# ---------------------------------------------------------------------------


def test_coding_pack_is_unchanged_for_coding_role() -> None:
    coding_state = build_default_resolved_harness_state(agent_role="coding")

    assert coding_state.coding.components["tools"] == ("FileRead", "FileEdit", "PatchApply")
    assert coding_state.coding.components["hooks"] == ("coding-verification", "completion-evidence")
    assert coding_state.coding.components["childAgent"] == ("coding-child-review",)
    assert coding_state.coding.components["permissionDefaults"] == ("write_requires_act",)
    assert "tddRequired" in coding_state.coding.opt_out_allowed
    assert "childReview" in coding_state.coding.opt_out_allowed
    assert coding_state.coding.enabled is True


def test_research_pack_is_unchanged_for_research_role() -> None:
    research_state = build_default_resolved_harness_state(agent_role="research")

    assert research_state.research.components["tools"] == (
        "WebSearch",
        "WebFetch",
        "KnowledgeSearch",
    )
    assert research_state.research.components["hooks"] == (
        "source-authority",
        "claim-citation",
        "fact-grounding",
    )
    assert research_state.research.components["ledgers"] == ("source-ledger",)
    assert research_state.research.components["delivery"] == ("citation-required",)
    assert "citationRequired" in research_state.research.opt_out_allowed
    assert "factGrounding" in research_state.research.opt_out_allowed
    assert research_state.research.enabled is True


def test_verification_pack_is_unchanged() -> None:
    state = build_default_resolved_harness_state(agent_role="general")

    assert state.verification.components["verifierGates"] == (
        "answer-quality",
        "self-claim",
        "deterministic-evidence",
    )
    assert "answerQuality" in state.verification.opt_out_allowed
    assert state.verification.enabled is True


def test_hard_safety_pack_is_unchanged() -> None:
    state = build_default_resolved_harness_state(agent_role="general")

    assert "permission-arbiter" in state.hard_safety.protected_gates
    assert "path-safety" in state.hard_safety.protected_gates
    assert "secret-safety" in state.hard_safety.protected_gates
    assert "sealed-file-policy" in state.hard_safety.protected_gates
    assert "git-safety" in state.hard_safety.protected_gates
    assert state.hard_safety.opt_out is False


def test_coding_child_run_packs_unchanged() -> None:
    coding_child = build_default_resolved_harness_state(agent_role="coding", spawn_depth=1)

    assert coding_child.effective_harness_packs == ("coding", "hard-safety")


def test_research_child_run_packs_unchanged() -> None:
    research_child = build_default_resolved_harness_state(agent_role="research", spawn_depth=1)

    assert research_child.effective_harness_packs == ("research", "hard-safety")


# ---------------------------------------------------------------------------
# HarnessEngine path
# ---------------------------------------------------------------------------


def test_harness_engine_resolves_general_pack_via_engine() -> None:
    _, state = HarnessEngine().resolve(HarnessResolutionRequest(agent_role="general"))

    assert hasattr(state, "general")
    assert state.general.enabled is True
    assert "general" in state.effective_harness_packs
