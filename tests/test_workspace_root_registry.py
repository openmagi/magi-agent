"""Tests for I-4 follow-up: workspace-root + local-chat-route registry parity.

Per the I-4 plan (``docs/plans/2026-06-18-magi-agent-oss-main-remediation/
ws-I-config-quality.md`` §I-4) the workspace-root flags + the local chat-route
gate were read inline in ``magi_agent/transport/chat_routes.py`` via raw
``os.environ.get(...)`` calls. The migration:

1. Registers each env name in :mod:`magi_agent.config.flags` with the
   appropriate kind/scope:

   * ``MAGI_AGENT_LOCAL_CHAT_ROUTE`` — public ``kind="bool"`` (legacy default
     was the string ``"off"``; FlagSpec default ``False`` resolves the unset
     env to the same falsey state).
   * ``MAGI_AGENT_WORKSPACE`` — public ``kind="str"`` (default ``""``).
   * ``CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT`` — hosted
     ``kind="str"`` (default ``""``).
   * ``CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT`` — hosted
     ``kind="str"`` (default ``""``).

2. Replaces the inline ``os.environ.get(...)`` reads with
   :func:`magi_agent.config.flags.flag_bool` /
   :func:`magi_agent.config.flags.flag_str` calls. The ``or os.getcwd()`` /
   ``or Path.cwd()`` fallback semantics are preserved byte-identical so a
   non-empty env wins, an empty or unset env falls back to cwd.

Parity coverage
---------------
The string flags pin three rows each: an unset env, an empty-string env, and
a populated env. The bool flag reuses the standard 13-input strict-truthy
parity table from the I-1 batch migrations (see
``magi_agent/config/tests/test_flag_migration_parity.py``).

The workspace-root tests do NOT exercise the live ``Path.cwd()`` fallback
because that introduces test-process cwd coupling; the parity assertions
target only the ``flag_str`` typed reader so the registry contract is loud
without depending on the test harness's working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from magi_agent.config.flags import FLAGS_BY_NAME, flag_bool, flag_profile_bool, flag_str
from magi_agent.transport.chat_routes import (
    _gate1a_workspace_root,
    _gate5b_full_toolhost_workspace_root,
    _local_chat_route_enabled,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_local_chat_route_is_registered_as_public_bool() -> None:
    spec = FLAGS_BY_NAME["MAGI_AGENT_LOCAL_CHAT_ROUTE"]
    assert spec.kind == "bool"
    assert spec.scope == "public"
    assert spec.default is False


def test_workspace_flag_is_registered_as_public_str() -> None:
    spec = FLAGS_BY_NAME["MAGI_AGENT_WORKSPACE"]
    assert spec.kind == "str"
    assert spec.scope == "public"
    assert spec.default == ""


@pytest.mark.parametrize(
    "name",
    [
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
        "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT",
    ],
)
def test_hosted_workspace_root_flags_are_registered_as_hosted_str(name: str) -> None:
    """Both hosted workspace-root env names register with hosted scope.

    Hosted-only means the public env-reference generator excludes them; pinning
    the scope here so a rename cannot silently widen them to ``public`` and
    leak a hosted-runtime path into the self-host docs.
    """
    spec = FLAGS_BY_NAME[name]
    assert spec.kind == "str"
    assert spec.scope == "hosted"
    assert spec.default == ""


# ---------------------------------------------------------------------------
# MAGI_AGENT_LOCAL_CHAT_ROUTE — strict-truthy parity (same 13-input table as
# the I-1 batch migrations).
# ---------------------------------------------------------------------------

_LOCAL_CHAT_ROUTE_PARITY: tuple[tuple[str | None, bool], ...] = (
    (None, False),       # unset
    ("1", True),
    ("true", True),
    ("on", True),
    ("yes", True),
    ("TRUE", True),
    ("Yes", True),
    ("0", False),
    ("false", False),
    ("off", False),
    ("", False),
    ("garbage", False),  # strict opt-in: unknown stays False
    ("  on  ", True),    # whitespace + case-fold
)


@pytest.mark.parametrize(("raw", "expected"), _LOCAL_CHAT_ROUTE_PARITY)
def test_local_chat_route_enabled_parity(raw: str | None, expected: bool) -> None:
    """Helper resolves identical to the legacy inline ``=on``-class check.

    Legacy form (pre-PR): ``os.environ.get("MAGI_AGENT_LOCAL_CHAT_ROUTE",
    "off").strip().lower() in {"1","true","yes","on"}``. New form delegates to
    ``flag_bool`` whose ``_truthy.is_true`` parser accepts the same set after
    trim+lower — byte-identical for every input.
    """
    env: dict[str, str] = (
        {} if raw is None else {"MAGI_AGENT_LOCAL_CHAT_ROUTE": raw}
    )
    assert _local_chat_route_enabled(env) is expected


def test_local_chat_route_helper_delegates_to_flag_bool() -> None:
    """The helper must match the registry typed reader byte-for-byte.

    Pins the single-decision-point invariant: any divergence between the
    helper and ``flag_bool`` would mean another reading path silently
    reintroduced an inline truthy check.
    """
    for raw, expected in _LOCAL_CHAT_ROUTE_PARITY:
        env: dict[str, str] = (
            {} if raw is None else {"MAGI_AGENT_LOCAL_CHAT_ROUTE": raw}
        )
        assert (
            _local_chat_route_enabled(env)
            is flag_bool("MAGI_AGENT_LOCAL_CHAT_ROUTE", env=env)
            is expected
        )


# ---------------------------------------------------------------------------
# Workspace-root string-flag parity (3 rows × 3 flags).
# ---------------------------------------------------------------------------


_STRING_FLAG_NAMES: tuple[str, ...] = (
    "MAGI_AGENT_WORKSPACE",
    "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
    "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT",
)


@pytest.mark.parametrize("name", _STRING_FLAG_NAMES)
def test_string_flag_unset_resolves_to_empty_default(name: str) -> None:
    """Unset env reads the FlagSpec default (empty string)."""
    assert flag_str(name, env={}) == ""


@pytest.mark.parametrize("name", _STRING_FLAG_NAMES)
def test_string_flag_empty_string_passes_through(name: str) -> None:
    """An explicit empty-string env value reads as ``""``.

    Critical for byte-identical fallback semantics in
    ``transport/chat_routes.py``: the callers post-process with ``or
    os.getcwd()`` / ``or Path.cwd()``, so ``flag_str`` returning ``""`` must
    not be confused with the populated case below.
    """
    assert flag_str(name, env={name: ""}) == ""


@pytest.mark.parametrize("name", _STRING_FLAG_NAMES)
def test_string_flag_populated_value_passes_through(name: str) -> None:
    """A populated env value reads verbatim through ``flag_str``."""
    assert flag_str(name, env={name: "/srv/workspace"}) == "/srv/workspace"


# ---------------------------------------------------------------------------
# Workspace-root callable parity (``or cwd`` fallback preserved).
# ---------------------------------------------------------------------------


def test_gate5b_workspace_root_falls_back_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset env → ``Path.cwd()`` (preserved byte-identical fallback)."""
    monkeypatch.delenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", raising=False
    )
    assert _gate5b_full_toolhost_workspace_root() == Path.cwd()


def test_gate5b_workspace_root_uses_populated_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A populated env value wins over the cwd fallback."""
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )
    assert _gate5b_full_toolhost_workspace_root() == tmp_path


def test_gate5b_workspace_root_empty_env_falls_back_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit empty-string env value falls back to cwd."""
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", ""
    )
    assert _gate5b_full_toolhost_workspace_root() == Path.cwd()


def test_gate1a_workspace_root_falls_back_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset env → ``Path.cwd()`` (preserved byte-identical fallback)."""
    monkeypatch.delenv(
        "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", raising=False
    )
    assert _gate1a_workspace_root() == Path.cwd()


def test_gate1a_workspace_root_uses_populated_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A populated env value wins over the cwd fallback."""
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", str(tmp_path)
    )
    assert _gate1a_workspace_root() == tmp_path


def test_gate1a_workspace_root_empty_env_falls_back_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit empty-string env value falls back to cwd."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", "")
    assert _gate1a_workspace_root() == Path.cwd()


# ---------------------------------------------------------------------------
# MAGI_MAX_STEPS_BRAKE_ENABLED — registry membership + flag_profile_bool parity.
# ---------------------------------------------------------------------------

# Profile-aware default-ON semantics under a non-safe (default) profile:
# unset/unrecognized fall back to the profile default (ON), an explicit truthy
# value stays ON, and an explicit falsy value (incl. empty string) forces OFF.
_MAX_STEPS_BRAKE_PROFILE_PARITY: tuple[tuple[str | None, bool], ...] = (
    (None, True),         # unset -> profile default ON
    ("1", True),
    ("true", True),
    ("on", True),
    ("yes", True),
    ("TRUE", True),
    ("Yes", True),
    ("0", False),
    ("false", False),
    ("off", False),
    ("", False),
    ("garbage", True),    # unrecognized -> profile default ON (not strict-OFF)
    ("  on  ", True),     # whitespace + case-fold
)


def test_max_steps_brake_is_registered_as_public_profile_bool() -> None:
    """Pin the registration so a future rename cannot silently downgrade kind.

    Promoted to a profile-aware default-ON flag (``profile_bool``): ON under
    the full/non-safe profile, OFF under the safe-family.

    Coordination note: H-9 audit flags ``MaxStepsBrakeControl`` as an inert
    no-op (max_iterations=0). If H-9 deletes the seam the FlagSpec should be
    removed in the same PR — this test will start failing on KeyError, which
    is the desired loud signal that the cleanup is incomplete.
    """
    spec = FLAGS_BY_NAME["MAGI_MAX_STEPS_BRAKE_ENABLED"]
    assert spec.kind == "profile_bool"
    assert spec.scope == "public"
    assert spec.default is None


@pytest.mark.parametrize(("raw", "expected"), _MAX_STEPS_BRAKE_PROFILE_PARITY)
def test_max_steps_brake_flag_profile_bool_parity(raw: str | None, expected: bool) -> None:
    """The ``flag_profile_bool`` consumer resolves each input under the default
    (non-safe) profile per the profile-aware default-ON convention."""
    env: dict[str, str] = (
        {} if raw is None else {"MAGI_MAX_STEPS_BRAKE_ENABLED": raw}
    )
    assert flag_profile_bool("MAGI_MAX_STEPS_BRAKE_ENABLED", env=env) is expected


def test_default_env_resolves_workspace_helpers_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: a fully-scrubbed live env keeps both workspace helpers at cwd.

    Mirrors the I-1 batch migration's ``test_default_env_resolves_to_false``
    spot check: the convenience ``env=None`` path on ``flag_str`` returns the
    registry default ``""``, so the callers' ``or Path.cwd()`` keeps the
    historic behaviour live without a populated env.
    """
    monkeypatch.delenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", raising=False
    )
    monkeypatch.delenv(
        "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", raising=False
    )
    monkeypatch.delenv("MAGI_AGENT_WORKSPACE", raising=False)
    assert _gate5b_full_toolhost_workspace_root() == Path.cwd()
    assert _gate1a_workspace_root() == Path.cwd()
    # flag_str(env=None) reads the live process env which monkeypatch scrubbed.
    assert flag_str("MAGI_AGENT_WORKSPACE") == ""
    # And os.environ does not carry any of the three keys post-scrub.
    for name in _STRING_FLAG_NAMES:
        assert name not in os.environ
