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


def test_pr1_overlay_scope_is_observability_only():
    # PR1 must not pull in sibling control flags (those are PR2/PR3/PR4).
    env = {HOSTED_DEPLOYMENT_ENV: "hosted", "MAGI_CONTROL_STAGE": "full"}
    apply_hosted_runtime_defaults(env)
    for sibling in (
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_SELF_REVIEW_ENABLED",
        "MAGI_SELF_INTROSPECTION_ENABLED",
        "MAGI_CODING_REPAIR_LOOP_ENABLED",
        "MAGI_DOCUMENT_AUTHORING_COVERAGE",
    ):
        assert sibling not in env, sibling
