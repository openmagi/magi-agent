from __future__ import annotations

from importlib import resources
from pathlib import Path

from magi_agent.plugins.native._common import digest, ok_result, workspace_root
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


def skill_loader(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    root = workspace_root(context)
    candidates = _skill_candidates(root)
    return ok_result("SkillLoader", {"skills": candidates, "skillCount": len(candidates)})


def skill_runtime_hooks(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    hooks = ("beforeModelCall", "afterToolCall", "beforeCommit", "afterTurnEnd")
    return ok_result("SkillRuntimeHooks", {"hooks": hooks, "hookDigest": digest(hooks)})


def external_tool_loader(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return ok_result(
        "ExternalToolLoader",
        {
            "status": "metadata_only",
            "executionAttached": False,
            "toolAuthority": "first_party_policy_required",
        },
    )


def _skill_candidates(root: Path) -> list[str]:
    skills: list[str] = []
    skills.extend(_bundled_skill_candidates())
    for base in (root / "skills", root / ".magi" / "skills", root / "docs" / "superpowers"):
        if not base.exists():
            continue
        for skill in sorted(base.rglob("SKILL.md"))[:50]:
            try:
                skills.append(skill.relative_to(root).as_posix())
            except ValueError:
                skills.append(skill.name)
    return skills


def _bundled_skill_candidates() -> list[str]:
    try:
        skills_root = resources.files("magi_agent").joinpath("skills")
    except (FileNotFoundError, ModuleNotFoundError):
        return []
    bundled_root = skills_root.joinpath("bundled")
    if not bundled_root.is_dir():
        return []
    return sorted(
        skill.relative_to(skills_root).as_posix()
        for skill in bundled_root.rglob("SKILL.md")
    )[:50]
