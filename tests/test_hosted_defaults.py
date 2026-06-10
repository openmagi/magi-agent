"""Tests for the hosted-deployment control-stage overlay (doc 14 PR1).

PR1 scope: skeleton + C8 observability only. The overlay is keyed on
``MAGI_CONTROL_STAGE`` (off|resilience|full|hardgate) and only applies when the
deployment is explicitly marked hosted (``MAGI_DEPLOYMENT=hosted``). It uses
``setdefault`` semantics so explicit operator env always wins, and it must never
touch the local-bot or eval runtime paths.
"""

from __future__ import annotations

from magi_agent.runtime.hosted_defaults import (
    HOSTED_DEPLOYMENT_ENV,
    apply_hosted_runtime_defaults,
    is_hosted_deployment,
    resolve_control_stage,
)


def test_resolve_control_stage_defaults_to_off():
    assert resolve_control_stage({}) == "off"
    assert resolve_control_stage({"MAGI_CONTROL_STAGE": ""}) == "off"


def test_resolve_control_stage_normalizes_case_and_whitespace():
    assert resolve_control_stage({"MAGI_CONTROL_STAGE": " Full "}) == "full"
    assert resolve_control_stage({"MAGI_CONTROL_STAGE": "RESILIENCE"}) == "resilience"


def test_resolve_control_stage_unknown_falls_back_to_off():
    # Unknown stage names must fail safe (no controls flipped).
    assert resolve_control_stage({"MAGI_CONTROL_STAGE": "bogus"}) == "off"


def test_is_hosted_deployment_requires_explicit_marker():
    assert is_hosted_deployment({}) is False
    assert is_hosted_deployment({"MAGI_DEPLOYMENT": "local"}) is False
    assert is_hosted_deployment({"MAGI_DEPLOYMENT": "hosted"}) is True
    assert is_hosted_deployment({"MAGI_DEPLOYMENT": "HOSTED"}) is True


def test_overlay_noop_when_not_hosted():
    env = {"MAGI_CONTROL_STAGE": "full"}
    apply_hosted_runtime_defaults(env)
    # Not hosted -> overlay must not set anything.
    assert "MAGI_OBSERVABILITY_ENABLED" not in env
    assert "MAGI_OBS_HOME" not in env


def test_stage_off_sets_no_observability_keys():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    assert "MAGI_OBSERVABILITY_ENABLED" not in env
    assert "MAGI_OBS_HOME" not in env


def test_stage_off_is_byte_identical_default():
    # Default stage (unset) == off: hosted env with no stage stays untouched.
    env = {HOSTED_DEPLOYMENT_ENV: "hosted"}
    apply_hosted_runtime_defaults(env)
    assert env == {HOSTED_DEPLOYMENT_ENV: "hosted"}


def test_stage_full_enables_observability_on_pvc():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "full"}
    apply_hosted_runtime_defaults(env)
    assert env["MAGI_OBSERVABILITY_ENABLED"] == "1"
    # Observability DB must land on the hosted PVC subPath, not read-only cwd.
    assert env["MAGI_OBS_HOME"] == "/workspace/.openmagi"


def test_explicit_env_always_wins_setdefault_semantics():
    env = {
        HOSTED_DEPLOYMENT_ENV: "hosted",
        "MAGI_CONTROL_STAGE": "full",
        "MAGI_OBSERVABILITY_ENABLED": "0",
        "MAGI_OBS_HOME": "/custom/path",
    }
    apply_hosted_runtime_defaults(env)
    assert env["MAGI_OBSERVABILITY_ENABLED"] == "0"
    assert env["MAGI_OBS_HOME"] == "/custom/path"


def test_overlay_scope_excludes_other_clusters():
    # The control-stage overlay must NOT pull in C11 coding-repair/doc-coverage
    # flags — those belong to a sibling PR (14-PR4). C9 MemoryWrite real-write
    # also stays out (its persistence ties to the held memory master, 01-PR5).
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "hardgate"}
    apply_hosted_runtime_defaults(env)
    for sibling in (
        "MAGI_CODING_REPAIR_LOOP_ENABLED",
        "MAGI_DOCUMENT_AUTHORING_COVERAGE",
        "MAGI_MEMORY_WRITE_ENABLED",
    ):
        assert sibling not in env, sibling


# --- PR2 (C3): six ControlPlane controls wired into the stage overlay ---

RESILIENCE_C3_FLAGS = (
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
    "MAGI_LOOP_GUARD_ENABLED",
    "MAGI_ERROR_RECOVERY_ENABLED",
    "MAGI_MAX_STEPS_BRAKE_ENABLED",
)
FULL_C3_FLAGS = ("MAGI_CONTEXT_COMPACTION_ENABLED", "MAGI_SELF_REVIEW_ENABLED")


def test_stage_off_sets_no_c3_controls():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    for flag in (*RESILIENCE_C3_FLAGS, *FULL_C3_FLAGS):
        assert flag not in env, flag


def test_stage_resilience_enables_four_resilience_controls():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    for flag in RESILIENCE_C3_FLAGS:
        assert env[flag] == "1", flag
    # resilience must NOT pull in the non-resilience C3 controls.
    for flag in FULL_C3_FLAGS:
        assert flag not in env, flag
    assert "MAGI_SELF_REVIEW_SHADOW" not in env


def test_stage_full_adds_compaction_and_shadow_self_review():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "full"}
    apply_hosted_runtime_defaults(env)
    # full is additive over resilience.
    for flag in RESILIENCE_C3_FLAGS:
        assert env[flag] == "1", flag
    assert env["MAGI_CONTEXT_COMPACTION_ENABLED"] == "1"
    # self-review is shadow-first on hosted: enabled, but SHADOW stays on so it
    # only observes (no live candidate generation) until hardgate.
    assert env["MAGI_SELF_REVIEW_ENABLED"] == "1"
    assert env["MAGI_SELF_REVIEW_SHADOW"] == "1"


def test_stage_hardgate_flips_self_review_to_live():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "hardgate"}
    apply_hosted_runtime_defaults(env)
    for flag in (*RESILIENCE_C3_FLAGS, *FULL_C3_FLAGS):
        assert env[flag] == "1", flag
    # hardgate promotes self-review from shadow to live.
    assert env["MAGI_SELF_REVIEW_SHADOW"] == "0"


def test_explicit_c3_flag_wins_over_stage():
    env = {
        HOSTED_DEPLOYMENT_ENV: "hosted",
        "MAGI_CONTROL_STAGE": "resilience",
        "MAGI_LOOP_GUARD_ENABLED": "0",
    }
    apply_hosted_runtime_defaults(env)
    assert env["MAGI_LOOP_GUARD_ENABLED"] == "0"


def test_c3_controls_register_in_build_default_plane():
    # End-to-end contract: the overlay env actually drives ControlPlane
    # registration (control_plane.py reads these flags).
    from magi_agent.adk_bridge.control_plane import build_default_plane

    env: dict[str, str] = {
        HOSTED_DEPLOYMENT_ENV: "hosted",
        "MAGI_CONTROL_STAGE": "resilience",
    }
    apply_hosted_runtime_defaults(env)
    plane = build_default_plane(env)
    # At least the resilience-family controls must register from the overlay.
    assert len(plane._controls) >= 1


# --- PR6 (C9): InspectSelfEvidence + MemoryWrite wired into the stage overlay ---
#
# InspectSelfEvidence (read-only introspection) is low-risk and is exposed at
# the ``full`` stage and above via MAGI_SELF_INTROSPECTION_ENABLED. MemoryWrite
# real persistence ties to the held memory master (01-PR5), so the overlay must
# keep it default-OFF at every stage — never set MAGI_MEMORY_WRITE_ENABLED.

SELF_INTROSPECTION_FLAG = "MAGI_SELF_INTROSPECTION_ENABLED"
MEMORY_WRITE_FLAG = "MAGI_MEMORY_WRITE_ENABLED"


def test_stage_off_sets_no_introspection_flag():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    assert SELF_INTROSPECTION_FLAG not in env


def test_stage_resilience_does_not_enable_introspection():
    # Introspection is a ``full``-stage capability — resilience stays minimal.
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    assert SELF_INTROSPECTION_FLAG not in env


def test_stage_full_enables_introspection():
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "full"}
    apply_hosted_runtime_defaults(env)
    assert env[SELF_INTROSPECTION_FLAG] == "1"


def test_stage_hardgate_keeps_introspection_on():
    # hardgate is additive over full.
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "hardgate"}
    apply_hosted_runtime_defaults(env)
    assert env[SELF_INTROSPECTION_FLAG] == "1"


def test_memory_write_stays_off_at_every_stage():
    # MemoryWrite real persistence is held behind the memory master (01-PR5).
    # No stage may flip MAGI_MEMORY_WRITE_ENABLED on.
    for stage in ("off", "resilience", "full", "hardgate"):
        env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": stage}
        apply_hosted_runtime_defaults(env)
        assert MEMORY_WRITE_FLAG not in env, stage


def test_explicit_introspection_flag_wins_over_stage():
    env = {
        HOSTED_DEPLOYMENT_ENV: "hosted",
        "MAGI_CONTROL_STAGE": "full",
        SELF_INTROSPECTION_FLAG: "0",
    }
    apply_hosted_runtime_defaults(env)
    assert env[SELF_INTROSPECTION_FLAG] == "0"
