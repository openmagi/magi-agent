from __future__ import annotations

from importlib import resources
from pathlib import Path

from magi_agent.plugins.native._common import digest, ok_result, workspace_root
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

_MAX_LOADED_BUNDLED_SKILLS = 20
_MAX_SKILL_BODY_CHARS = 64_000


def skill_loader(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    root = workspace_root(context)
    candidates = _skill_candidates(root)
    loaded_skills = _load_bundled_skill_bodies(candidates)
    return ok_result(
        "SkillLoader",
        {
            "skills": candidates,
            "skillCount": len(candidates),
            "loadedSkills": loaded_skills,
            "loadedSkillCount": len(loaded_skills),
        },
    )


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


def _load_bundled_skill_bodies(candidates: list[str]) -> list[dict[str, object]]:
    try:
        skills_root = resources.files("magi_agent").joinpath("skills")
    except (FileNotFoundError, ModuleNotFoundError):
        return []

    loaded: list[dict[str, object]] = []
    for relative in candidates:
        if not relative.startswith("bundled/") or not relative.endswith("/SKILL.md"):
            continue
        try:
            resource = skills_root.joinpath(*relative.split("/"))
            if not resource.is_file():
                continue
            body = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError, OSError):
            continue
        if len(body) > _MAX_SKILL_BODY_CHARS:
            body = body[:_MAX_SKILL_BODY_CHARS]
        loaded.append(
            {
                "path": relative,
                "source": "bundled",
                "body": body,
                "bodyDigest": digest(body),
            }
        )
        if len(loaded) >= _MAX_LOADED_BUNDLED_SKILLS:
            break
    return loaded
