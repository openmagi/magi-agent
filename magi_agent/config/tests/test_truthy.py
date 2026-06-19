"""Unit tests for ``magi_agent.config._truthy`` — the dependency-free leaf
that owns the canonical truthy convention + profile-default resolution shared
by ``config/env.py`` and ``config/flags.py``.

These tests guard against semantic drift while the leaf is extracted (I-3) and
during subsequent flag-migration sweeps (I-1, I-2). They exercise the public
helpers' behavior against the live convention (truthy / falsey / profile
matrix) and assert the constants are frozen, sharable, and identical to the
historic ``env.py`` private aliases.
"""

from __future__ import annotations

import pytest

from magi_agent.config import _truthy
from magi_agent.config._truthy import (
    FALSE_VALUES,
    RUNTIME_PROFILE_ENV,
    SAFE_RUNTIME_PROFILES,
    TRUE_VALUES,
    env_bool,
    env_bool_default_true,
    is_true,
    runtime_feature_enabled,
    runtime_profile_default_enabled,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_true_values_set_matches_historic_convention() -> None:
    assert TRUE_VALUES == frozenset({"1", "true", "yes", "on"})
    assert isinstance(TRUE_VALUES, frozenset)


def test_false_values_set_matches_historic_convention() -> None:
    # NOTE: empty string lives in the FALSE set — historic env.py convention.
    assert FALSE_VALUES == frozenset({"0", "false", "no", "off", ""})
    assert isinstance(FALSE_VALUES, frozenset)


def test_true_and_false_sets_are_disjoint() -> None:
    assert TRUE_VALUES.isdisjoint(FALSE_VALUES)


def test_runtime_profile_env_name_is_pinned() -> None:
    # The hosted/local runtime profile env var is part of the public contract.
    assert RUNTIME_PROFILE_ENV == "MAGI_RUNTIME_PROFILE"


def test_safe_runtime_profiles_match_historic_set() -> None:
    assert SAFE_RUNTIME_PROFILES == frozenset(
        {"safe", "off", "minimal", "conservative", "eval"}
    )


# ---------------------------------------------------------------------------
# is_true — table of edge cases (matches historic env._is_true semantics)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "1",
        "true",
        "TRUE",
        "True",
        "yes",
        "YES",
        "on",
        "ON",
        " 1 ",
        "\t1\n",
        " true ",
        " yEs ",
    ],
)
def test_is_true_recognises_canonical_truthy_values(value: str) -> None:
    assert is_true(value) is True


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        "0",
        "false",
        "FALSE",
        "no",
        "off",
        "OFF",
        "maybe",
        "2",
        "enable",  # not in TRUE_VALUES — historic strict-truthy parse
        "enabled",
    ],
)
def test_is_true_rejects_everything_else(value: str | None) -> None:
    assert is_true(value) is False


# ---------------------------------------------------------------------------
# env_bool — single allowlist-semantics reader
# ---------------------------------------------------------------------------
def test_env_bool_returns_default_when_unset() -> None:
    assert env_bool({}, "MAGI_FOO") is False
    assert env_bool({}, "MAGI_FOO", default=True) is True


def test_env_bool_returns_true_for_explicit_truthy() -> None:
    assert env_bool({"MAGI_FOO": "1"}, "MAGI_FOO") is True
    assert env_bool({"MAGI_FOO": "true"}, "MAGI_FOO") is True
    assert env_bool({"MAGI_FOO": "YES"}, "MAGI_FOO") is True


def test_env_bool_returns_false_for_explicit_non_truthy_when_default_false() -> None:
    # Non-truthy explicit values — including unknown ones — fall to is_true(False)
    # under the allowlist convention. Default is irrelevant once a value is set.
    assert env_bool({"MAGI_FOO": "0"}, "MAGI_FOO") is False
    assert env_bool({"MAGI_FOO": "false"}, "MAGI_FOO") is False
    assert env_bool({"MAGI_FOO": "off"}, "MAGI_FOO") is False
    assert env_bool({"MAGI_FOO": ""}, "MAGI_FOO") is False
    assert env_bool({"MAGI_FOO": "unknown"}, "MAGI_FOO") is False


def test_env_bool_with_default_true_still_honors_explicit_falsey() -> None:
    # Explicit value is present, so default is ignored. Unknown -> not truthy
    # -> False, even if default=True.
    assert env_bool({"MAGI_FOO": "0"}, "MAGI_FOO", default=True) is False
    assert env_bool({"MAGI_FOO": "unknown"}, "MAGI_FOO", default=True) is False


# ---------------------------------------------------------------------------
# runtime_profile_default_enabled — profile gating
# ---------------------------------------------------------------------------
def test_runtime_profile_default_enabled_unset_is_full_on() -> None:
    assert runtime_profile_default_enabled({}) is True


@pytest.mark.parametrize(
    "profile",
    ["safe", "off", "minimal", "conservative", "eval", "SAFE", " safe ", " EVAL\n"],
)
def test_runtime_profile_default_enabled_is_off_under_safe_profiles(
    profile: str,
) -> None:
    assert runtime_profile_default_enabled({RUNTIME_PROFILE_ENV: profile}) is False


@pytest.mark.parametrize("profile", ["full", "local", "lab", "production", "anything"])
def test_runtime_profile_default_enabled_is_on_outside_safe_set(
    profile: str,
) -> None:
    assert runtime_profile_default_enabled({RUNTIME_PROFILE_ENV: profile}) is True


# ---------------------------------------------------------------------------
# runtime_feature_enabled — profile-aware default-ON reader
# ---------------------------------------------------------------------------
def test_runtime_feature_enabled_unset_full_profile_is_on() -> None:
    assert runtime_feature_enabled({}, "MAGI_FOO") is True


def test_runtime_feature_enabled_unset_safe_profile_is_off() -> None:
    assert (
        runtime_feature_enabled({RUNTIME_PROFILE_ENV: "safe"}, "MAGI_FOO") is False
    )
    assert (
        runtime_feature_enabled({RUNTIME_PROFILE_ENV: "eval"}, "MAGI_FOO") is False
    )


def test_runtime_feature_enabled_explicit_truthy_wins_even_in_safe_profile() -> None:
    assert (
        runtime_feature_enabled(
            {RUNTIME_PROFILE_ENV: "safe", "MAGI_FOO": "1"}, "MAGI_FOO"
        )
        is True
    )
    assert (
        runtime_feature_enabled(
            {RUNTIME_PROFILE_ENV: "eval", "MAGI_FOO": "true"}, "MAGI_FOO"
        )
        is True
    )


def test_runtime_feature_enabled_explicit_falsey_wins_in_full_profile() -> None:
    assert runtime_feature_enabled({"MAGI_FOO": "0"}, "MAGI_FOO") is False
    assert runtime_feature_enabled({"MAGI_FOO": "false"}, "MAGI_FOO") is False
    assert runtime_feature_enabled({"MAGI_FOO": "off"}, "MAGI_FOO") is False
    assert runtime_feature_enabled({"MAGI_FOO": ""}, "MAGI_FOO") is False


def test_runtime_feature_enabled_unknown_value_falls_back_to_profile_default() -> None:
    # Unknown value (neither truthy nor falsey) -> profile default applies.
    assert runtime_feature_enabled({"MAGI_FOO": "maybe"}, "MAGI_FOO") is True
    assert (
        runtime_feature_enabled(
            {RUNTIME_PROFILE_ENV: "safe", "MAGI_FOO": "maybe"}, "MAGI_FOO"
        )
        is False
    )


# ---------------------------------------------------------------------------
# env_bool_default_true — present-truthy or present-unknown -> True, only
# explicit falsey -> False. (None also -> True; this models a default-ON env
# helper used by ``native_receipts_honest`` and friends.)
# ---------------------------------------------------------------------------
def test_env_bool_default_true_with_none_returns_true() -> None:
    assert env_bool_default_true(None) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_env_bool_default_true_returns_false_on_explicit_falsey(value: str) -> None:
    assert env_bool_default_true(value) is False


@pytest.mark.parametrize(
    "value", ["1", "true", "yes", "on", "anything", " mystery "]
)
def test_env_bool_default_true_returns_true_on_anything_else(value: str) -> None:
    assert env_bool_default_true(value) is True


# ---------------------------------------------------------------------------
# Leaf purity — _truthy exports nothing magi_agent-shaped at runtime
# ---------------------------------------------------------------------------
def test_truthy_module_has_no_magi_agent_attributes() -> None:
    """Runtime tie-back to the AST cycle test.

    ``tests/test_config_import_acyclic.py`` is the structural enforcer for the
    "dependency-free leaf" contract — it scans the source for ``magi_agent``
    imports. This test is the runtime tie-back: nothing in the leaf's module
    dict points back into the ``magi_agent`` package, so even reflection
    cannot reach the cycle that I-3 broke.
    """
    own_module = "magi_agent.config._truthy"
    for name, value in vars(_truthy).items():
        if name.startswith("__"):
            continue
        mod = getattr(value, "__module__", None) or ""
        if not mod.startswith("magi_agent."):
            continue
        # The leaf itself is allowed to expose its own callables; anything else
        # under ``magi_agent.*`` would have been re-exported from a sibling and
        # would smuggle the cycle back in.
        assert mod == own_module, (
            f"_truthy.{name} resolves into {mod!r}; leaf must be dependency-free."
        )
