"""Bundled first-party document authoring skill parity tests."""

from __future__ import annotations

import pathlib
import tomllib
from importlib import resources

from magi_agent.cli.commands.skill_commands import skill_commands

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _package_data_globs() -> list[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["tool"]["setuptools"]["package-data"]["magi_agent"])


def test_document_writer_and_hwpx_skills_are_bundled() -> None:
    bundled_root = resources.files("magi_agent").joinpath("skills").joinpath("bundled")

    document_writer = bundled_root.joinpath("document-writer").joinpath("SKILL.md")
    hwpx = bundled_root.joinpath("hwpx").joinpath("SKILL.md")

    assert document_writer.is_file()
    assert hwpx.is_file()

    document_text = document_writer.read_text(encoding="utf-8")
    hwpx_text = hwpx.read_text(encoding="utf-8")

    assert "name: document-writer" in document_text
    assert "DocumentWrite" in document_text
    for fmt in ("md", "txt", "html", "pdf", "docx", "hwpx"):
        assert fmt in document_text

    assert "name: hwpx" in hwpx_text
    assert 'DocumentWrite(format="hwpx")' in hwpx_text
    assert "FileDeliver" in hwpx_text


def test_document_skills_are_discoverable_as_bundled_commands() -> None:
    discovered = skill_commands("/tmp/no-such-magi-agent-workspace")
    names = {command.name for command in discovered}

    assert "document-writer" in names
    assert "hwpx" in names


def test_document_skills_and_hwpx_runtime_are_packaged() -> None:
    globs = _package_data_globs()

    assert any(glob == "skills/bundled/*/SKILL.md" for glob in globs) or any(
        glob == "skills/bundled/document-writer/SKILL.md" for glob in globs
    )
    assert any(glob == "skills/bundled/*/SKILL.md" for glob in globs) or any(
        glob == "skills/bundled/hwpx/SKILL.md" for glob in globs
    )
    assert any(glob.startswith("tools/document_write/hwpx_runtime/") for glob in globs)
