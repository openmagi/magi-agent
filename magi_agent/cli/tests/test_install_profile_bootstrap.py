"""Tests for the install profile bootstrap (``~/.magi/profile.env`` → env)."""
from __future__ import annotations

from pathlib import Path

from magi_agent.cli.install_profile_bootstrap import (
    EMBEDDED_DEFAULT_PROFILE,
    apply_install_profile_bootstrap,
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_flags_via_setdefault(tmp_path: Path) -> None:
    profile = _write(
        tmp_path / "profile.env",
        "MAGI_CHILD_RUNNER_LIVE_ENABLED=1\n"
        "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED=1\n",
    )
    env: dict[str, str] = {}

    apply_install_profile_bootstrap(env, profile_path=profile)

    assert env["MAGI_CHILD_RUNNER_LIVE_ENABLED"] == "1"
    assert env["MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED"] == "1"


def test_explicit_env_wins(tmp_path: Path) -> None:
    profile = _write(
        tmp_path / "profile.env", "MAGI_CHILD_RUNNER_LIVE_ENABLED=1\n"
    )
    env = {"MAGI_CHILD_RUNNER_LIVE_ENABLED": "0"}

    apply_install_profile_bootstrap(env, profile_path=profile)

    assert env["MAGI_CHILD_RUNNER_LIVE_ENABLED"] == "0"


def test_missing_file_applies_embedded_defaults(tmp_path: Path) -> None:
    # No profile.env present: the embedded fallback ships the install-default
    # flags so a fresh ``pip install`` still has live subagents on out of the
    # box. (Pre-existing keys must be preserved.)
    env: dict[str, str] = {"PRESERVED": "yes"}

    apply_install_profile_bootstrap(env, profile_path=tmp_path / "absent.env")

    assert env["PRESERVED"] == "yes"
    for key, value in EMBEDDED_DEFAULT_PROFILE.items():
        assert env[key] == value


def test_embedded_default_child_runner_live_enabled() -> None:
    # The specific gate the local-serve dashboard depends on for the Work
    # pane's AGENTS roster (see plugins/native/subagents.py:291).
    assert EMBEDDED_DEFAULT_PROFILE["MAGI_CHILD_RUNNER_LIVE_ENABLED"] == "1"
    assert EMBEDDED_DEFAULT_PROFILE["MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED"] == "1"


def test_explicit_env_wins_over_embedded_default(tmp_path: Path) -> None:
    # A user that explicitly opts out via env must still win — embedded
    # defaults are setdefault, not assignment.
    env = {"MAGI_CHILD_RUNNER_LIVE_ENABLED": "0"}

    apply_install_profile_bootstrap(env, profile_path=tmp_path / "absent.env")

    assert env["MAGI_CHILD_RUNNER_LIVE_ENABLED"] == "0"


def test_profile_file_value_wins_over_embedded_default(tmp_path: Path) -> None:
    # The same precedence applies when the profile.env sets the key.
    profile = _write(
        tmp_path / "profile.env", "MAGI_CHILD_RUNNER_LIVE_ENABLED=0\n"
    )
    env: dict[str, str] = {}

    apply_install_profile_bootstrap(env, profile_path=profile)

    assert env["MAGI_CHILD_RUNNER_LIVE_ENABLED"] == "0"


def test_export_prefix_and_comments_and_blanks(tmp_path: Path) -> None:
    profile = _write(
        tmp_path / "profile.env",
        "# a comment\n"
        "\n"
        "export MAGI_RUNTIME_PROFILE=full\n"
        "   # indented comment\n"
        "export MAGI_CHILD_RUNNER_TOOLSET=readonly\n",
    )
    env: dict[str, str] = {}

    apply_install_profile_bootstrap(env, profile_path=profile)

    assert env["MAGI_RUNTIME_PROFILE"] == "full"
    assert env["MAGI_CHILD_RUNNER_TOOLSET"] == "readonly"


def test_only_magi_keys_applied(tmp_path: Path) -> None:
    profile = _write(
        tmp_path / "profile.env",
        "PATH=/evil/bin\n"
        "HOME=/tmp/evil\n"
        "OTHER_PREFIX_BAR=2\n"
        "MAGI_FOO=1\n",
    )
    env: dict[str, str] = {}

    apply_install_profile_bootstrap(env, profile_path=profile)

    # The MAGI_-prefixed line from the file lands. Non-MAGI lines never do.
    assert env["MAGI_FOO"] == "1"
    assert "PATH" not in env
    assert "HOME" not in env
    assert "OTHER_PREFIX_BAR" not in env
    # And the embedded defaults still seed (file did not set them).
    for key in EMBEDDED_DEFAULT_PROFILE:
        assert key in env


def test_quoted_values_unwrapped(tmp_path: Path) -> None:
    profile = _write(
        tmp_path / "profile.env",
        'MAGI_A="full"\n'
        "MAGI_B='readonly'\n",
    )
    env: dict[str, str] = {}

    apply_install_profile_bootstrap(env, profile_path=profile)

    assert env["MAGI_A"] == "full"
    assert env["MAGI_B"] == "readonly"


def test_malformed_lines_are_skipped_other_lines_applied(tmp_path: Path) -> None:
    profile = _write(
        tmp_path / "profile.env",
        "this line has no equals sign\n"
        "MAGI_GOOD=1\n"
        "=novalue\n",
    )
    env: dict[str, str] = {}

    apply_install_profile_bootstrap(env, profile_path=profile)

    assert env["MAGI_GOOD"] == "1"
    # Embedded defaults still seed alongside file values.
    for key in EMBEDDED_DEFAULT_PROFILE:
        assert key in env


def test_unreadable_path_fails_soft(tmp_path: Path) -> None:
    # A directory at the profile path raises on read; must not propagate.
    # Embedded defaults still apply (file path failure is non-fatal).
    bad = tmp_path / "profile.env"
    bad.mkdir()
    env: dict[str, str] = {"PRESERVED": "yes"}

    apply_install_profile_bootstrap(env, profile_path=bad)

    assert env["PRESERVED"] == "yes"
    for key, value in EMBEDDED_DEFAULT_PROFILE.items():
        assert env[key] == value
