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


def test_c3_overlay_scope_excludes_other_clusters():
    # PR2 (C3) wires the six ControlPlane controls only. It must NOT pull in
    # C9 introspection/memory-write or C11 coding-repair/doc-coverage flags —
    # those belong to sibling PRs (14-PR3/PR6).
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "hardgate"}
    apply_hosted_runtime_defaults(env)
    for sibling in (
        "MAGI_SELF_INTROSPECTION_ENABLED",
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
