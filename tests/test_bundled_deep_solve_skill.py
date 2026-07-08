"""Bundled first-party ``deep-solve`` skill tests (U4).

Mirrors ``tests/test_bundled_document_skills.py``: the skill must be bundled,
discoverable as a ``/deep-solve`` command via
``cli/commands/skill_commands.py``, and covered by the package-data globs so it
survives into the wheel/bottle.
"""

from __future__ import annotations

import pathlib
import tomllib
from importlib import resources

from magi_agent.cli.commands.skill_commands import skill_commands

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _package_data_globs() -> list[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["tool"]["setuptools"]["package-data"]["magi_agent"])


def test_deep_solve_skill_is_bundled() -> None:
    bundled_root = resources.files("magi_agent").joinpath("skills").joinpath("bundled")
    skill = bundled_root.joinpath("deep-solve").joinpath("SKILL.md")
    assert skill.is_file()

    text = skill.read_text(encoding="utf-8")
    assert "name: deep-solve" in text
    assert "DeepSolve" in text
    # Trigger conditions (KR + EN) in the frontmatter description.
    assert "올림피아드" in text
    assert "olympiad" in text
    assert "competitive programming" in text
    # Argument guidance: test_command drives ground-truth acceptance.
    assert "test_command" in text
    # Confidence labels documented honestly.
    for label in ("tests_passed", "n_consecutive_clean", "rejected"):
        assert label in text
    # Loop control belongs to the tool, never re-implemented via SpawnAgent.
    assert "SpawnAgent" in text


def test_deep_solve_skill_discoverable_as_bundled_command() -> None:
    discovered = skill_commands("/tmp/no-such-magi-agent-workspace")
    names = {command.name for command in discovered}
    assert "deep-solve" in names


def test_deep_solve_skill_is_packaged() -> None:
    globs = _package_data_globs()
    assert any(
        glob in ("skills/bundled/*/SKILL.md", "skills/bundled/deep-solve/SKILL.md")
        for glob in globs
    )
