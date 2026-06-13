"""Verify the committed full-ON dogfood profile turns every intended governance/
harness module ON — WITHOUT relying on any repo code-default flip.

This test LOADS ``scripts/dogfood-full-on.env`` (the operator-sourceable profile
for BOTH the local CLI and the canary/hosted serve) and asserts each listed flag
parses to its enabled/enforce/block value through the SAME canonical readers the
runtime uses (``config.flags.flag_bool``/``flag_profile_bool`` and the
``config.env`` / ``research.live_audit`` / ``memory.config`` resolvers).

Hermetic: the profile is parsed into a plain dict and injected via the readers'
``env=`` parameter. ``os.environ`` is never mutated, so this test neither needs
nor depends on the process environment and cannot leak into other tests. With
the profile NOT loaded the very same readers return their default-OFF values
(asserted in ``test_unset_env_is_default_off_for_strict_gates``), proving the
profile is pure CONFIG, not a code-default change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.config import env as cfg_env
from magi_agent.config.flags import flag_bool, flag_profile_bool, get_flag
from magi_agent.memory.config import resolve_memory_config
from magi_agent.research.live_audit import research_governance_mode

_PROFILE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "dogfood-full-on.env"
)


def _load_profile(path: Path = _PROFILE_PATH) -> dict[str, str]:
    """Parse ``export KEY=VALUE`` lines from the dogfood env file.

    Mirrors what ``set -a; source <file>`` would export, but as a plain dict so
    the assertions stay hermetic (no os.environ mutation). Ignores blank lines
    and ``#`` comments.
    """
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


@pytest.fixture(scope="module")
def profile() -> dict[str, str]:
    loaded = _load_profile()
    assert loaded, "dogfood profile parsed to an empty mapping"
    return loaded


# ---------------------------------------------------------------------------
# Sanity: the file exists and sets the profile selector.
# ---------------------------------------------------------------------------
def test_profile_file_exists() -> None:
    assert _PROFILE_PATH.is_file(), f"missing dogfood profile at {_PROFILE_PATH}"


def test_profile_selector_is_full(profile: dict[str, str]) -> None:
    # "full" must NOT be a safe profile (else profile-aware gates resolve OFF).
    from magi_agent.runtime.local_defaults import SAFE_RUNTIME_PROFILES

    assert profile.get("MAGI_RUNTIME_PROFILE") == "full"
    assert "full" not in SAFE_RUNTIME_PROFILES


def test_profile_enables_gate5b_governance_for_canary(profile: dict[str, str]) -> None:
    # The line that makes the canary/hosted serve run the same governance as the
    # local CLI (G1). Without it governance is cli/engine-only.
    assert profile.get("MAGI_GATE5B_GOVERNANCE_ENABLED") == "1"
    assert cfg_env.is_gate5b_governance_enabled(profile) is True


# ---------------------------------------------------------------------------
# Strict opt-in bool gates (kind="bool", default-OFF): each MUST read True under
# the profile via the canonical flag_bool reader.
# ---------------------------------------------------------------------------
_STRICT_BOOL_FLAGS_ENABLED = (
    "MAGI_GATE5B_GOVERNANCE_ENABLED",
    "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED",
    "MAGI_GA_DELIVERABLE_GATE_ENABLED",
    "MAGI_EGRESS_GATE_ENABLED",
    "MAGI_FACTS_REPLAN_ENABLED",
    "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED",
    "MAGI_RESEARCH_FACT_GUIDANCE_ENABLED",
    "MAGI_CROSS_VERIFY_ENABLED",
    "MAGI_STEP_DECOMPOSITION_ENABLED",
    "MAGI_DEEP_WEB_RESEARCH_ENABLED",
    "MAGI_BROWSER_TOOL_ENABLED",
    "MAGI_CODE_ACTION_ENABLED",
    "MAGI_DEFERRED_TOOLS_ENABLED",
    "MAGI_HEADTAIL_TRUNCATION_ENABLED",
    "MAGI_FILE_DELIVERY_LIVE_ENABLED",
    "MAGI_DOCUMENT_QA_ENABLED",
    "MAGI_GOAL_LOOP_ENABLED",
    "MAGI_OBSERVABILITY_ENABLED",
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
    "MAGI_CODING_REPAIR_LOOP_ENABLED",
    "MAGI_COMPUTE_VIA_CODE_ENABLED",
    "MAGI_FORMAT_ADHERENCE_ENABLED",
    "MAGI_MULTI_FILE_JOIN_ENABLED",
    "MAGI_LEARNING_ENABLED",
    "MAGI_LEARNING_LIVE_ENABLED",
    "MAGI_LEARNING_INJECTION_ENABLED",
    "MAGI_LEARNING_REFLECTION_ENABLED",
    # Memory subsystem master + registered sub-flags (registry kind="bool").
    "MAGI_MEMORY_ENABLED",
    "MAGI_MEMORY_WRITE_ENABLED",
    "MAGI_MEMORY_RECALL_ENABLED",
    "MAGI_MEMORY_COMPACTION_ENABLED",
    "MAGI_MEMORY_PROJECTION_ENABLED",
    "MAGI_MEMORY_QMD_LIVE_ENABLED",
    "MAGI_MEMORY_MODE_ROUTING_ENABLED",
)


@pytest.mark.parametrize("name", _STRICT_BOOL_FLAGS_ENABLED)
def test_strict_bool_flag_enabled_under_profile(
    profile: dict[str, str], name: str
) -> None:
    spec = get_flag(name)  # raises if the name is not a registered flag
    assert spec.kind == "bool", f"{name} is kind {spec.kind!r}, expected 'bool'"
    # Profile must set it explicitly (strict gates are NOT profile-default-ON).
    assert name in profile, f"{name} missing from dogfood profile"
    assert flag_bool(name, env=profile) is True


# ---------------------------------------------------------------------------
# Profile-aware default-ON gates (kind="profile_bool"): MUST read True under the
# full profile via flag_profile_bool. (These would also be ON with the value
# unset under MAGI_RUNTIME_PROFILE=full; the profile reaffirms them explicitly.)
# ---------------------------------------------------------------------------
_PROFILE_BOOL_FLAGS_ENABLED = (
    "MAGI_EDIT_FUZZY_MATCH_ENABLED",
    "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED",
    "MAGI_LSP_DIAGNOSTICS_ENABLED",
    "MAGI_RIPGREP_ENABLED",
    "MAGI_APPLY_PATCH_ENABLED",
    "MAGI_LOOP_GUARD_ENABLED",
    "MAGI_ERROR_RECOVERY_ENABLED",
    "MAGI_OUTPUT_CONTINUATION_ENABLED",
    "MAGI_CONTEXT_COMPACTION_ENABLED",
    "MAGI_SELF_INTROSPECTION_ENABLED",
    "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED",
    "MAGI_EVIDENCE_COMPLETION_GATE_ENABLED",
)


@pytest.mark.parametrize("name", _PROFILE_BOOL_FLAGS_ENABLED)
def test_profile_bool_flag_enabled_under_profile(
    profile: dict[str, str], name: str
) -> None:
    spec = get_flag(name)
    assert spec.kind == "profile_bool", (
        f"{name} is kind {spec.kind!r}, expected 'profile_bool'"
    )
    assert flag_profile_bool(name, env=profile) is True


# ---------------------------------------------------------------------------
# String / mode gates resolve to their REAL-governance value (enforce / block),
# not merely "on".
# ---------------------------------------------------------------------------
def test_research_governance_mode_is_enforce(profile: dict[str, str]) -> None:
    assert profile.get("MAGI_RESEARCH_GOVERNANCE_MODE") == "enforce"
    assert research_governance_mode(profile) == "enforce"


def test_document_authoring_coverage_is_block(profile: dict[str, str]) -> None:
    # Real governance: a hard block (not advisory/off) on failed DocumentCoverage.
    assert cfg_env.resolve_document_authoring_coverage_mode(profile) == "block"
    assert cfg_env.is_document_authoring_coverage_enabled(profile) is True


# ---------------------------------------------------------------------------
# Resolvers that read the profile holistically (env helpers + memory resolver).
# ---------------------------------------------------------------------------
def test_env_helper_resolvers_enabled_under_profile(profile: dict[str, str]) -> None:
    assert cfg_env.is_egress_gate_enabled(profile) is True
    assert cfg_env.is_step_decomposition_enabled(profile) is True
    assert cfg_env.parse_fact_grounding_verification_enabled(profile) is True
    assert cfg_env.parse_ga_deliverable_gate_enabled(profile) is True
    assert cfg_env.parse_evidence_completion_gate_enabled(profile) is True
    assert cfg_env.general_automation_live_enabled(profile) is True
    assert cfg_env.compute_via_code_enabled(profile) is True
    assert cfg_env.parse_trusted_local_shell_enabled(profile) is True


def test_memory_subsystem_fully_resolved_on(profile: dict[str, str]) -> None:
    cfg = resolve_memory_config(env=profile)
    # Master on -> the whole subsystem activates.
    assert cfg.master_enabled is True
    assert cfg.write_enabled is True
    assert cfg.recall_enabled is True
    assert cfg.compaction_enabled is True
    assert cfg.projection_enabled is True
    # Master-on opt-ins the operator set explicitly (these stay OFF otherwise).
    assert cfg.vector_search is True
    assert cfg.prefer_local_search is True


# ---------------------------------------------------------------------------
# The profile is CONFIG, not a code-default flip: the SAME readers return
# default-OFF when the profile is not loaded (empty env). If any strict gate
# defaulted ON in code, this would fail — guarding the "tests stay green" promise.
# ---------------------------------------------------------------------------
def test_unset_env_is_default_off_for_strict_gates() -> None:
    empty: dict[str, str] = {}
    # A representative spread of the strict opt-in gates the profile turns on.
    assert cfg_env.is_gate5b_governance_enabled(empty) is False
    assert cfg_env.parse_fact_grounding_verification_enabled(empty) is False
    assert cfg_env.parse_ga_deliverable_gate_enabled(empty) is False
    assert cfg_env.is_egress_gate_enabled(empty) is False
    assert flag_bool("MAGI_FACTS_REPLAN_ENABLED", env=empty) is False
    assert flag_bool("MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED", env=empty) is False
    assert flag_bool("MAGI_DEEP_WEB_RESEARCH_ENABLED", env=empty) is False
    # Mode/string gates default to the inert value.
    assert research_governance_mode(empty) == "off"
    assert cfg_env.resolve_document_authoring_coverage_mode(empty) == "off"
    # Memory master defaults OFF in code -> whole subsystem inert.
    assert resolve_memory_config(env=empty).master_enabled is False


def test_every_profile_listed_registry_flag_is_enabled(
    profile: dict[str, str],
) -> None:
    """Cross-check: every flag in the profile that is in the canonical registry
    and is a boolean kind must resolve enabled. This catches a future profile
    edit that sets a registry bool flag to a falsy value by mistake.
    """
    from magi_agent.config.flags import FLAGS_BY_NAME

    # Flags registered as kind="bool" in the registry but whose REAL runtime
    # semantics are a multi-state mode resolved by a dedicated helper, not the
    # strict-truthy flag_bool reader. The dogfood profile sets these to a
    # non-truthy mode word ("block") on purpose; their enabled-ness is asserted
    # by their own mode test (test_document_authoring_coverage_is_block), so the
    # generic flag_bool cross-check would mis-flag them.
    _MODE_RESOLVED_BOOL_FLAGS = frozenset({"MAGI_DOCUMENT_AUTHORING_COVERAGE"})

    checked = 0
    for name, value in profile.items():
        spec = FLAGS_BY_NAME.get(name)
        if spec is None:
            continue
        if name in _MODE_RESOLVED_BOOL_FLAGS:
            continue
        if spec.kind == "bool":
            assert flag_bool(name, env=profile) is True, (
                f"{name}={value!r} did not resolve True"
            )
            checked += 1
        elif spec.kind == "profile_bool":
            assert flag_profile_bool(name, env=profile) is True, (
                f"{name}={value!r} did not resolve True"
            )
            checked += 1
    # Guard against a silently-empty cross-check (e.g. a parser regression).
    assert checked >= 20
