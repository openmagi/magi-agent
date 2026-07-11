"""Control-plane behavior toggles: store persistence + env projection.

Closes the gap where the dashboard Customize tab could not turn off the
in-context "Facts Survey" (and sibling control-plane nudges): they are gated
purely on ``MAGI_*_ENABLED`` env flags that the ``lab`` / dogfood profiles seed
ON, with no user-facing override seam. These tests pin the contract that an
explicit toggle in ``customize.json`` wins over the profile seed.
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.customize.control_plane_overrides import (
    CONTROL_PLANE_BEHAVIORS,
    apply_control_plane_overrides_to_env,
    control_plane_behavior_catalog,
)
from magi_agent.customize.store import (
    DEFAULT_OVERRIDES,
    load_overrides,
    set_control_plane_override,
)

FACTS_ENV = "MAGI_FACTS_REPLAN_ENABLED"


# --------------------------------------------------------------------------- #
# Catalog                                                                      #
# --------------------------------------------------------------------------- #
def test_facts_replan_is_in_catalog_mapped_to_its_flag() -> None:
    by_id = {b.id: b for b in CONTROL_PLANE_BEHAVIORS}
    assert "facts-replan" in by_id
    assert by_id["facts-replan"].env_var == FACTS_ENV


def test_catalog_excludes_hard_safety_flags() -> None:
    # Defense-in-depth: a user must not be able to walk back a safety/governance
    # obligation through this behavior seam.
    env_vars = {b.env_var for b in CONTROL_PLANE_BEHAVIORS}
    for forbidden in (
        "MAGI_EGRESS_GATE_ENABLED",
        "MAGI_GATE5B_GOVERNANCE_ENABLED",
        "MAGI_SERVE_EVIDENCE_ENABLED",
        "MAGI_KERNEL_RECIPE_PACKS_ENABLED",
    ):
        assert forbidden not in env_vars


def test_catalog_is_serializable_with_label_and_description() -> None:
    cat = control_plane_behavior_catalog({})
    entry = next(e for e in cat if e["id"] == "facts-replan")
    assert entry["env_var"] == FACTS_ENV
    assert entry["label"] and entry["description"]


def test_catalog_enabled_reflects_env_truthiness() -> None:
    on = next(
        e
        for e in control_plane_behavior_catalog({FACTS_ENV: "1"})
        if e["id"] == "facts-replan"
    )
    off = next(
        e
        for e in control_plane_behavior_catalog({FACTS_ENV: "0"})
        if e["id"] == "facts-replan"
    )
    assert on["enabled"] is True
    assert off["enabled"] is False


# --------------------------------------------------------------------------- #
# Env projection — precedence is the whole point                              #
# --------------------------------------------------------------------------- #
def test_explicit_false_overrides_profile_seed() -> None:
    # Simulate the lab/dogfood seed having turned the flag ON.
    env = {FACTS_ENV: "1"}
    apply_control_plane_overrides_to_env(env, {"control_plane": {"facts-replan": False}})
    assert env[FACTS_ENV] == "0"


def test_explicit_true_overrides_a_disabled_seed() -> None:
    env = {FACTS_ENV: "0"}
    apply_control_plane_overrides_to_env(env, {"control_plane": {"facts-replan": True}})
    assert env[FACTS_ENV] == "1"


def test_absent_behavior_leaves_env_untouched() -> None:
    # Tri-state: no key → byte-identical to before (the profile seed stands).
    env = {FACTS_ENV: "1"}
    apply_control_plane_overrides_to_env(env, {"control_plane": {}})
    assert env[FACTS_ENV] == "1"


def test_empty_overrides_is_a_noop() -> None:
    env = {FACTS_ENV: "1"}
    apply_control_plane_overrides_to_env(env, None)
    apply_control_plane_overrides_to_env(env, {})
    assert env[FACTS_ENV] == "1"


def test_unknown_behavior_id_never_touches_env() -> None:
    env = {FACTS_ENV: "1", "MAGI_EGRESS_GATE_ENABLED": "1"}
    apply_control_plane_overrides_to_env(
        env,
        {"control_plane": {"made-up": False, "MAGI_EGRESS_GATE_ENABLED": False}},
    )
    # Neither the unknown id nor a flag-shaped id leaks through.
    assert env[FACTS_ENV] == "1"
    assert env["MAGI_EGRESS_GATE_ENABLED"] == "1"


def test_non_bool_value_is_ignored() -> None:
    env = {FACTS_ENV: "1"}
    apply_control_plane_overrides_to_env(env, {"control_plane": {"facts-replan": "0"}})
    assert env[FACTS_ENV] == "1"


def test_malformed_overrides_fail_soft() -> None:
    env = {FACTS_ENV: "1"}
    # control_plane not a mapping → no-op, no raise.
    apply_control_plane_overrides_to_env(env, {"control_plane": ["facts-replan"]})
    assert env[FACTS_ENV] == "1"


# --------------------------------------------------------------------------- #
# Store                                                                         #
# --------------------------------------------------------------------------- #
def test_default_overrides_has_empty_control_plane_section() -> None:
    assert DEFAULT_OVERRIDES["control_plane"] == {}


def test_set_control_plane_override_roundtrips_and_retains_false(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    out = set_control_plane_override("facts-replan", False, path=p)
    assert out["control_plane"]["facts-replan"] is False
    # Persisted (RETAINED on disable so the opt-out survives a restart).
    assert load_overrides(p)["control_plane"]["facts-replan"] is False


def test_normalize_drops_non_bool_control_plane_entries(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    p.write_text(
        '{"control_plane": {"facts-replan": false, "goal-loop": "yes", "x": 1}}',
        encoding="utf-8",
    )
    cp = load_overrides(p)["control_plane"]
    assert cp == {"facts-replan": False}


def test_store_to_env_end_to_end(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    set_control_plane_override("facts-replan", False, path=p)
    overrides = load_overrides(p)
    env = {FACTS_ENV: "1"}
    apply_control_plane_overrides_to_env(env, overrides)
    assert env[FACTS_ENV] == "0"


def test_goal_loop_toggle_off_also_pins_legacy_goal_nudge() -> None:
    # F1-B: turning "Goal nudge" (goal-loop) OFF must ALSO disable the legacy
    # goal-nudge, so no ambient re-invocation family stays live (the response-
    # duplication root cause: goal-loop OFF used to REVIVE the legacy nudge).
    env = {"MAGI_GOAL_LOOP_ENABLED": "1", "MAGI_GOAL_NUDGE_ENABLED": "1"}
    apply_control_plane_overrides_to_env(env, {"control_plane": {"goal-loop": False}})
    assert env["MAGI_GOAL_LOOP_ENABLED"] == "0"
    assert env["MAGI_GOAL_NUDGE_ENABLED"] == "0"


def test_goal_loop_toggle_on_pins_both() -> None:
    env: dict[str, str] = {}
    apply_control_plane_overrides_to_env(env, {"control_plane": {"goal-loop": True}})
    assert env["MAGI_GOAL_LOOP_ENABLED"] == "1"
    assert env["MAGI_GOAL_NUDGE_ENABLED"] == "1"


def test_goal_nudge_flag_is_strict_default_off() -> None:
    # F1-B: the legacy nudge no longer defaults ON under lab; it fires ONLY on an
    # explicit MAGI_GOAL_NUDGE_ENABLED=1.
    from magi_agent.config.env import is_goal_nudge_enabled

    assert is_goal_nudge_enabled({}) is False
    assert is_goal_nudge_enabled({"MAGI_RUNTIME_PROFILE": "lab"}) is False
    assert is_goal_nudge_enabled({"MAGI_GOAL_NUDGE_ENABLED": "1"}) is True
