"""Unit tests for the canonical flag registry + reader (``config/flags.py``).

This is the PR2 foundation: a single-source registry (``FLAGS``) plus typed
reader helpers (``flag_bool``/``flag_str``/``flag_int``). The migration of the
~154 raw call sites and the env-reference auto-generation are explicitly out of
scope for this PR (see ``docs/plans/2026-06-09-magi-oss-full-activation/
15-flag-governance.md`` PR2/PR3/PR4).
"""

from __future__ import annotations

import pytest

from magi_agent.config import flags
from magi_agent.config.flags import (
    FLAGS,
    FlagScope,
    FlagSpec,
    Stage,
    flag_bool,
    flag_int,
    flag_profile_bool,
    flag_str,
    get_flag,
)


# ---------------------------------------------------------------------------
# Registry shape / invariants
# ---------------------------------------------------------------------------
def test_flags_registry_is_a_tuple_of_flagspec() -> None:
    assert isinstance(FLAGS, tuple)
    assert all(isinstance(spec, FlagSpec) for spec in FLAGS)


def test_flags_registry_has_at_least_thirty_entries() -> None:
    # PR2 completion definition: FLAGS >= 30 (high-value operator subset).
    assert len(FLAGS) >= 30


def test_flag_names_are_unique() -> None:
    names = [spec.name for spec in FLAGS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], f"duplicate flag names: {duplicates}"


def test_flag_names_use_known_prefix() -> None:
    for spec in FLAGS:
        assert spec.name.startswith(("MAGI_", "CORE_AGENT_")), spec.name


def test_flag_specs_are_frozen() -> None:
    spec = FLAGS[0]
    with pytest.raises(Exception):
        spec.name = "MAGI_MUTATED"  # type: ignore[misc]


def test_flag_scope_and_stage_values_are_valid() -> None:
    valid_scopes = {"public", "hosted", "internal", "dev"}
    valid_stages = {"stage1", "stage2", "stage3"}
    valid_kinds = {"bool", "str", "int", "profile_bool"}
    for spec in FLAGS:
        assert spec.scope in valid_scopes, spec
        assert spec.stage in valid_stages, spec
        assert spec.kind in valid_kinds, spec
        assert spec.summary, f"{spec.name} missing summary"


# ---------------------------------------------------------------------------
# Profile-aware default-ON flags must NOT be modelled as flat truthy bools.
#
# The spec (15-flag-governance.md PR2 risk) requires that flags read via
# env._runtime_feature_enabled (profile-aware default-ON: ON in the full
# runtime profile, OFF under MAGI_RUNTIME_PROFILE=safe|eval) are preserved as a
# DISTINCT kind, not flattened to a strict-truthy bool/registry-default. These
# tests pin that the registry metadata matches real env.py behaviour so the
# later env-reference (PR4) and stage-table (PR5) generators do not
# misrepresent flags to operators.
# ---------------------------------------------------------------------------
# All flags env.py reads via _runtime_feature_enabled (profile-aware default-ON).
PROFILE_AWARE_DEFAULT_ON_FLAGS = (
    "MAGI_LSP_DIAGNOSTICS_ENABLED",
    "MAGI_RIPGREP_ENABLED",
    "MAGI_APPLY_PATCH_ENABLED",
    "MAGI_LOOP_GUARD_ENABLED",
    "MAGI_ERROR_RECOVERY_ENABLED",
    "MAGI_OUTPUT_CONTINUATION_ENABLED",
    "MAGI_CONTEXT_COMPACTION_ENABLED",
    "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED",
    "MAGI_EVIDENCE_COMPLETION_GATE_ENABLED",
    "MAGI_SELF_INTROSPECTION_ENABLED",
    "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED",
    "MAGI_EDIT_FUZZY_MATCH_ENABLED",
)


def test_profile_aware_flags_registered_as_profile_bool_kind() -> None:
    for name in PROFILE_AWARE_DEFAULT_ON_FLAGS:
        spec = get_flag(name)
        assert spec.kind == "profile_bool", (
            f"{name} is read via env._runtime_feature_enabled (profile-aware "
            f"default-ON) but is registered as kind={spec.kind!r}; flattening it "
            "to a strict-truthy bool misrepresents the default to operators"
        )


def test_profile_bool_flags_cannot_be_read_as_strict_bool() -> None:
    # A profile_bool flag must NOT be readable through flag_bool — that path
    # would silently impose strict-truthy / registry-default semantics.
    with pytest.raises(TypeError):
        flag_bool("MAGI_LSP_DIAGNOSTICS_ENABLED", env={})


def test_flag_profile_bool_default_on_in_full_profile() -> None:
    # Unset + no safe profile => default ON (matches _runtime_feature_enabled).
    assert flag_profile_bool("MAGI_LSP_DIAGNOSTICS_ENABLED", env={}) is True


@pytest.mark.parametrize("profile", ["safe", "eval", "off", "minimal", "conservative"])
def test_flag_profile_bool_off_under_safe_profiles(profile: str) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    assert flag_profile_bool("MAGI_LSP_DIAGNOSTICS_ENABLED", env=env) is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_flag_profile_bool_explicit_false_wins(value: str) -> None:
    env = {"MAGI_LSP_DIAGNOSTICS_ENABLED": value}
    assert flag_profile_bool("MAGI_LSP_DIAGNOSTICS_ENABLED", env=env) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_flag_profile_bool_explicit_true_wins_under_safe_profile(value: str) -> None:
    env = {"MAGI_LSP_DIAGNOSTICS_ENABLED": value, "MAGI_RUNTIME_PROFILE": "safe"}
    assert flag_profile_bool("MAGI_LSP_DIAGNOSTICS_ENABLED", env=env) is True


def test_flag_profile_bool_rejects_plain_bool_kind() -> None:
    # The strict default-OFF egress gate is a plain bool, not profile-aware.
    with pytest.raises(TypeError):
        flag_profile_bool("MAGI_EGRESS_GATE_ENABLED", env={})


def test_flag_profile_bool_matches_env_runtime_feature_enabled() -> None:
    # The registry reader must be byte-identical to the env.py source of truth
    # for every profile-aware flag and a representative set of environments.
    from magi_agent.config import env as env_module

    envs = (
        {},
        {"MAGI_RUNTIME_PROFILE": "safe"},
        {"MAGI_RUNTIME_PROFILE": "eval"},
        {"MAGI_LSP_DIAGNOSTICS_ENABLED": "0"},
        {"MAGI_LSP_DIAGNOSTICS_ENABLED": "1", "MAGI_RUNTIME_PROFILE": "safe"},
        {"MAGI_LSP_DIAGNOSTICS_ENABLED": "garbage"},
    )
    for env in envs:
        expected = env_module._runtime_feature_enabled(env, "MAGI_LSP_DIAGNOSTICS_ENABLED")
        assert (
            flag_profile_bool("MAGI_LSP_DIAGNOSTICS_ENABLED", env=env) is expected
        ), env


def test_master_memory_flag_is_registered_as_public() -> None:
    spec = get_flag("MAGI_MEMORY_ENABLED")
    assert spec.scope == "public"
    assert spec.kind == "bool"


def test_get_flag_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_flag("MAGI_DEFINITELY_NOT_A_REGISTERED_FLAG")


# ---------------------------------------------------------------------------
# flag_bool truthy / falsey parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", " on ", "On"])
def test_flag_bool_truthy_values(value: str) -> None:
    env = {"MAGI_EGRESS_GATE_ENABLED": value}
    assert flag_bool("MAGI_EGRESS_GATE_ENABLED", env=env) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "garbage"])
def test_flag_bool_falsey_values(value: str) -> None:
    env = {"MAGI_EGRESS_GATE_ENABLED": value}
    assert flag_bool("MAGI_EGRESS_GATE_ENABLED", env=env) is False


def test_flag_bool_uses_registry_default_when_absent() -> None:
    # MAGI_EGRESS_GATE_ENABLED registry default is False (strict opt-in).
    assert flag_bool("MAGI_EGRESS_GATE_ENABLED", env={}) is False


def test_flag_bool_unknown_flag_raises() -> None:
    with pytest.raises(KeyError):
        flag_bool("MAGI_UNREGISTERED", env={})


def test_flag_bool_rejects_non_bool_kind() -> None:
    # flag_str-typed entries must not be read as bool.
    str_specs = [s for s in FLAGS if s.kind == "str"]
    if not str_specs:
        pytest.skip("no str-kind flags registered")
    with pytest.raises(TypeError):
        flag_bool(str_specs[0].name, env={})


# ---------------------------------------------------------------------------
# flag_str / flag_int
# ---------------------------------------------------------------------------
def test_flag_str_returns_value_or_default() -> None:
    spec = next(s for s in FLAGS if s.kind == "str")
    assert flag_str(spec.name, env={spec.name: "custom"}) == "custom"
    assert flag_str(spec.name, env={}) == spec.default


def test_flag_int_parses_value() -> None:
    spec = next((s for s in FLAGS if s.kind == "int"), None)
    if spec is None:
        pytest.skip("no int-kind flags registered")
    assert flag_int(spec.name, env={spec.name: "42"}) == 42
    assert flag_int(spec.name, env={}) == spec.default


def test_flag_int_invalid_falls_back_to_default() -> None:
    spec = next((s for s in FLAGS if s.kind == "int"), None)
    if spec is None:
        pytest.skip("no int-kind flags registered")
    assert flag_int(spec.name, env={spec.name: "notanint"}) == spec.default


# ---------------------------------------------------------------------------
# env injection defaults to os.environ
# ---------------------------------------------------------------------------
def test_flag_bool_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    assert flag_bool("MAGI_EGRESS_GATE_ENABLED") is True
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "0")
    assert flag_bool("MAGI_EGRESS_GATE_ENABLED") is False


# ---------------------------------------------------------------------------
# env.py delegation: behaviour stays byte-identical
# ---------------------------------------------------------------------------
def test_env_helper_delegation_matches_flag_bool() -> None:
    from magi_agent.config import env as env_module

    for value, expected in [
        ({"MAGI_EGRESS_GATE_ENABLED": "1"}, True),
        ({"MAGI_EGRESS_GATE_ENABLED": "0"}, False),
        ({}, False),
    ]:
        assert env_module.is_egress_gate_enabled(value) is expected
        assert (
            env_module.is_egress_gate_enabled(value)
            is flag_bool("MAGI_EGRESS_GATE_ENABLED", env=value)
        )


def test_flagscope_and_stage_are_string_literal_aliases() -> None:
    # Pure type-alias sanity: importable and usable as annotations.
    assert FlagScope is not None
    assert Stage is not None
