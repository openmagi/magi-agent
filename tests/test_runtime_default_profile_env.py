"""The DEFAULT runtime profile (no ``MAGI_RUNTIME_PROFILE`` set) is now the
experimental ``lab`` tier.

``apply_runtime_profile_defaults`` is the single dispatch entry point shared by
``magi`` (cli/app.py) and ``magi-agent serve`` (main.py). Before lab-graduation
an unset profile resolved to the leaner ``full`` overlay and the experimental
flat flags stayed OFF unless the operator pinned ``MAGI_RUNTIME_PROFILE=lab``.
These tests pin the new contract: unset == lab, while explicit ``full`` / the
safe tiers / ``eval`` are unchanged, and per-flag walk-back still wins. Registry
defaults in ``config/flags.py`` are untouched (a library/test import never calls
the dispatcher), so a fresh import stays byte-identical default-OFF.
"""

from __future__ import annotations

from magi_agent.config.flags import flag_bool
from magi_agent.runtime.local_defaults import (
    EVAL_RUNTIME_ENV_DEFAULTS,
    LAB_EXPERIMENTAL_FLAGS,
    apply_runtime_profile_defaults,
)

# A strict experimental flag that the leaner ``full`` overlay does NOT seed, so
# it is a clean discriminator between the default (lab) and explicit ``full``.
_LAB_ONLY_FLAG = "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED"


def test_unset_profile_defaults_to_lab_tier() -> None:
    env: dict[str, str] = {}
    apply_runtime_profile_defaults(env)
    assert env["MAGI_RUNTIME_PROFILE"] == "lab"
    for name in LAB_EXPERIMENTAL_FLAGS:
        assert flag_bool(name, env=env) is True, name


def test_explicit_full_keeps_leaner_tier_experimental_off() -> None:
    env = {"MAGI_RUNTIME_PROFILE": "full"}
    apply_runtime_profile_defaults(env)
    assert env["MAGI_RUNTIME_PROFILE"] == "full"
    # The lab-only experimental flag stays OFF under the explicit full tier.
    assert flag_bool(_LAB_ONLY_FLAG, env=env) is False


def test_explicit_lab_is_identical_to_default() -> None:
    default_env: dict[str, str] = {}
    apply_runtime_profile_defaults(default_env)
    lab_env = {"MAGI_RUNTIME_PROFILE": "lab"}
    apply_runtime_profile_defaults(lab_env)
    assert default_env == lab_env


def test_safe_profiles_stay_conservative_under_dispatch() -> None:
    for profile in ("safe", "minimal", "off", "conservative"):
        env = {"MAGI_RUNTIME_PROFILE": profile}
        apply_runtime_profile_defaults(env)
        assert env["MAGI_RUNTIME_PROFILE"] == profile, profile
        for name in LAB_EXPERIMENTAL_FLAGS:
            assert flag_bool(name, env=env) is False, f"{profile}:{name}"


def test_eval_profile_routes_to_eval_seed() -> None:
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    apply_runtime_profile_defaults(env)
    assert env["MAGI_RUNTIME_PROFILE"] == "eval"
    # eval applies its own seed (e.g. its autonomy flag) and NOT the lab tier.
    assert env.get("MAGI_EVAL_AUTONOMY_ENABLED") == EVAL_RUNTIME_ENV_DEFAULTS["MAGI_EVAL_AUTONOMY_ENABLED"]


def test_explicit_flag_off_wins_over_default_lab_tier() -> None:
    for name in LAB_EXPERIMENTAL_FLAGS:
        env = {name: "0"}
        apply_runtime_profile_defaults(env)
        assert flag_bool(name, env=env) is False, name
