"""Tests for the install profile bootstrap (``~/.magi/profile.env`` → env)."""
from __future__ import annotations

from pathlib import Path

from magi_agent.cli.install_profile_bootstrap import (
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


def test_missing_file_is_noop(tmp_path: Path) -> None:
    env = {"PRESERVED": "yes"}

    apply_install_profile_bootstrap(env, profile_path=tmp_path / "absent.env")

    assert env == {"PRESERVED": "yes"}


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


def test_only_magi_and_core_agent_keys_applied(tmp_path: Path) -> None:
    profile = _write(
        tmp_path / "profile.env",
        "PATH=/evil/bin\n"
        "HOME=/tmp/evil\n"
        "MAGI_FOO=1\n"
        "CORE_AGENT_BAR=2\n",
    )
    env: dict[str, str] = {}

    apply_install_profile_bootstrap(env, profile_path=profile)

    assert env == {"MAGI_FOO": "1", "CORE_AGENT_BAR": "2"}


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

    assert env == {"MAGI_GOOD": "1"}


def test_unreadable_path_fails_soft(tmp_path: Path) -> None:
    # A directory at the profile path raises on read; must not propagate.
    bad = tmp_path / "profile.env"
    bad.mkdir()
    env = {"PRESERVED": "yes"}

    apply_install_profile_bootstrap(env, profile_path=bad)

    assert env == {"PRESERVED": "yes"}
