from __future__ import annotations

import os
from pathlib import Path

import pytest

from magi_agent.plugins.native.skills import skill_loader
from magi_agent.tools.context import ToolContext


def _context(root: Path) -> ToolContext:
    return ToolContext(bot_id="bot-test", workspace_root=str(root))


def _write_skill(root: Path, relative_dir: str, name: str, body: str | None = None) -> None:
    skill_dir = root / relative_dir
    skill_dir.mkdir(parents=True)
    skill_body = body if body is not None else f"{name} instructions"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n{skill_body}\n",
        encoding="utf-8",
    )


def test_skill_loader_lists_installed_skills_beyond_previous_cap_in_order(
    tmp_path: Path,
) -> None:
    for index in range(75):
        name = f"bulk-skill-{index:03d}"
        _write_skill(tmp_path, f"skills/{name}", name)

    result = skill_loader({}, _context(tmp_path))

    assert result.status == "ok"
    assert result.output is not None
    installed = [
        path
        for path in result.output["skills"]
        if str(path).startswith("skills/bulk-skill-")
    ]
    assert installed == [
        f"skills/bulk-skill-{index:03d}/SKILL.md" for index in range(75)
    ]
    loaded = {skill["path"]: skill for skill in result.output["loadedSkills"]}
    tail_skill = loaded["skills/bulk-skill-074/SKILL.md"]
    assert "bulk-skill-074 instructions" in tail_skill["body"]


def test_skill_loader_loads_installed_workspace_skill_bodies(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "skills/project-skill",
        "project-skill",
        "Project skill instructions are available by default.",
    )
    _write_skill(
        tmp_path,
        ".magi/skills/user-skill",
        "user-skill",
        "User workspace skill instructions are available by default.",
    )

    result = skill_loader({}, _context(tmp_path))

    assert result.status == "ok"
    assert result.output is not None
    loaded = {skill["path"]: skill for skill in result.output["loadedSkills"]}
    project = loaded["skills/project-skill/SKILL.md"]
    user = loaded[".magi/skills/user-skill/SKILL.md"]
    assert project["source"] == "workspace"
    assert user["source"] == "workspace"
    assert "Project skill instructions" in project["body"]
    assert "User workspace skill instructions" in user["body"]
    assert project["bodyDigest"].startswith("sha256:")
    assert user["bodyDigest"].startswith("sha256:")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_skill_loader_skips_symlinked_escape_and_protected_skill_bodies(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-skill-secret.md"
    outside.write_text("outside token=super-secret-skill-body\n", encoding="utf-8")
    sealed = tmp_path / "AGENTS.md"
    sealed.write_text("sealed operator instructions\n", encoding="utf-8")

    outside_link_dir = tmp_path / "skills" / "outside-link"
    outside_link_dir.mkdir(parents=True)
    (outside_link_dir / "SKILL.md").symlink_to(outside)

    sealed_link_dir = tmp_path / "skills" / "sealed-link"
    sealed_link_dir.mkdir(parents=True)
    (sealed_link_dir / "SKILL.md").symlink_to(sealed)

    result = skill_loader({}, _context(tmp_path))

    assert result.status == "ok"
    assert result.output is not None
    loaded_paths = {skill["path"] for skill in result.output["loadedSkills"]}
    assert "skills/outside-link/SKILL.md" not in loaded_paths
    assert "skills/sealed-link/SKILL.md" not in loaded_paths
    serialized = repr(result.output)
    assert "super-secret-skill-body" not in serialized
    assert "sealed operator instructions" not in serialized
