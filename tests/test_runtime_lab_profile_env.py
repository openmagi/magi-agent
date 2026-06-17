"""Invariants for the ``lab`` (experimental) runtime profile.

``MAGI_RUNTIME_PROFILE=lab`` is a single opt-in dogfood tier that turns ON the
full experimental flat-flag set on top of the local-full overlay, WITHOUT
touching the ``config/flags.py`` registry defaults. These tests pin the four
critical invariants:

1. ``lab`` -> every experimental flat flag resolves ON (via the canonical
   ``flag_bool`` registry reader).
2. ``safe``/``eval``/``minimal``/``conservative`` profiles are UNCHANGED: the
   experimental flags stay OFF (the lab seed is not applied).
3. An explicit ``MAGI_X=0`` still wins over the lab seed (per-flag walk-back).
4. ``lab`` is a strict superset of the local-full overlay (lab == full + extras).
"""

from __future__ import annotations

from magi_agent.config.flags import FLAGS, flag_bool
from magi_agent.runtime.local_defaults import (
    EVAL_RUNTIME_ENV_DEFAULTS,
    LAB_EXPERIMENTAL_FLAGS,
    LAB_RUNTIME_ENV_DEFAULTS,
    LOCAL_FULL_RUNTIME_ENV_DEFAULTS,
    SAFE_RUNTIME_PROFILES,
    apply_lab_runtime_defaults,
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)

_BY_NAME = {spec.name: spec for spec in FLAGS}


def test_lab_experimental_flags_are_registered_strict_bool_default_off() -> None:
    # Guard against drift: every flag the lab seed forces ON must be a flat
    # strict-truthy ``_b`` flag whose registry default is OFF, otherwise the
    # "registry defaults unchanged" claim is meaningless.
    for name in LAB_EXPERIMENTAL_FLAGS:
        spec = _BY_NAME.get(name)
        assert spec is not None, f"{name} missing from FLAGS registry"
        assert spec.kind == "bool", f"{name} is not a flat bool flag"
        assert bool(spec.default) is False, f"{name} registry default is not OFF"


def test_lab_profile_enables_all_experimental_flags() -> None:
    env: dict[str, str] = {}
    apply_lab_runtime_defaults(env)

    assert env["MAGI_RUNTIME_PROFILE"] == "lab"
    for name in LAB_EXPERIMENTAL_FLAGS:
        assert flag_bool(name, env=env) is True, name


def test_lab_profile_is_superset_of_local_full_overlay() -> None:
    full_env: dict[str, str] = {}
    apply_local_full_runtime_defaults(full_env)

    lab_env: dict[str, str] = {}
    apply_lab_runtime_defaults(lab_env)

    # Everything the full overlay seeds must also be present under lab (except
    # the profile identity, which lab overrides to "lab").
    for key, value in LOCAL_FULL_RUNTIME_ENV_DEFAULTS.items():
        if key == "MAGI_RUNTIME_PROFILE":
            continue
        assert lab_env.get(key) == value, key
    assert lab_env["MAGI_RUNTIME_PROFILE"] == "lab"


def test_safe_profiles_do_not_enable_experimental_flags() -> None:
    # Apply the lab seed under each safe profile pre-set; the local-full overlay
    # is a no-op under safe profiles, so none of the experimental flags get
    # seeded and they all resolve OFF.
    for profile in sorted(SAFE_RUNTIME_PROFILES):
        env = {"MAGI_RUNTIME_PROFILE": profile}
        apply_lab_runtime_defaults(env)
        assert env["MAGI_RUNTIME_PROFILE"] == profile, profile
        for name in LAB_EXPERIMENTAL_FLAGS:
            # safe profiles never inherit the lab seed; the flag stays unset and
            # the canonical strict-truthy reader resolves OFF.
            assert flag_bool(name, env=env) is False, f"{profile}:{name}"


def test_eval_profile_unchanged_by_lab_seed() -> None:
    # The eval profile is selected/applied independently (cli/app.py routes
    # ``eval`` to apply_local_eval_runtime_defaults, never the lab applier). It
    # legitimately enables a few of these flags on its own (e.g.
    # MAGI_EDIT_RETRY_REFLECTION_ENABLED); the invariant is that eval resolves
    # EXACTLY to its own seed and the lab tier never widens it. Any lab flag NOT
    # in eval's own defaults must stay OFF.
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    apply_local_eval_runtime_defaults(env)
    eval_on = {k for k, v in EVAL_RUNTIME_ENV_DEFAULTS.items() if v in ("1", "true")}
    for name in LAB_EXPERIMENTAL_FLAGS:
        if name in eval_on:
            continue
        assert flag_bool(name, env=env) is False, name


def test_explicit_flag_off_overrides_lab_seed() -> None:
    # Per-flag walk-back: an explicit MAGI_X=0 set before the seed wins because
    # the seed is setdefault-based.
    for name in LAB_EXPERIMENTAL_FLAGS:
        env = {name: "0"}
        apply_lab_runtime_defaults(env)
        assert env[name] == "0", name
        assert flag_bool(name, env=env) is False, name


def test_lab_seed_is_setdefault_and_preserves_explicit_profile_env() -> None:
    # An operator who explicitly pins MAGI_RUNTIME_PROFILE keeps their value;
    # the lab applier only fills it when unset.
    env = {"MAGI_RUNTIME_PROFILE": "lab", "MAGI_MEMORY_ENABLED": "0"}
    apply_lab_runtime_defaults(env)
    assert env["MAGI_RUNTIME_PROFILE"] == "lab"
    assert env["MAGI_MEMORY_ENABLED"] == "0"


def test_lab_defaults_mapping_matches_experimental_flag_list() -> None:
    seeded = {k for k in LAB_RUNTIME_ENV_DEFAULTS if k != "MAGI_RUNTIME_PROFILE"}
    assert seeded == set(LAB_EXPERIMENTAL_FLAGS)
    assert all(LAB_RUNTIME_ENV_DEFAULTS[name] == "1" for name in LAB_EXPERIMENTAL_FLAGS)
