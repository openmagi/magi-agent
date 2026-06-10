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
    valid_kinds = {"bool", "str", "int"}
    for spec in FLAGS:
        assert spec.scope in valid_scopes, spec
        assert spec.stage in valid_stages, spec
        assert spec.kind in valid_kinds, spec
        assert spec.summary, f"{spec.name} missing summary"


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
