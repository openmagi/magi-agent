from __future__ import annotations

from importlib import resources
from pathlib import Path

from magi_agent.plugins.native._common import (
    digest,
    ok_result,
    protected_workspace_path_reason,
    workspace_root,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

_MAX_SKILL_BODY_CHARS = 64_000
_WORKSPACE_SKILL_BASES = ("skills", ".magi/skills", "docs/superpowers")


def skill_loader(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    root = workspace_root(context)
    candidates = _skill_candidates(root)
    loaded_skills = _load_skill_bodies(candidates, root)
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
    for relative_base in _WORKSPACE_SKILL_BASES:
        base = root / relative_base
        if not base.is_dir():
            continue
        if _workspace_read_path_reason(root, relative_base) != "":
            continue
        for skill in sorted(base.rglob("SKILL.md")):
            relative = _workspace_skill_relative(root, skill)
            if relative is not None:
                skills.append(relative)
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
    )


def _load_skill_bodies(candidates: list[str], root: Path) -> list[dict[str, object]]:
    loaded: list[dict[str, object]] = []
    for relative in candidates:
        if relative.startswith("bundled/"):
            body = _read_bundled_skill_body(relative)
        else:
            body = _read_workspace_skill_body(root, relative)
        if body is None:
            continue
        loaded.append(body)
    return loaded


def load_bundled_skill_body(name_or_path: str) -> dict[str, object] | None:
    """Return a bundled SKILL.md body by safe skill name or bundled path.

    This is a read-only instruction loader. It never executes skill content and
    only resolves package-bundled ``SKILL.md`` files.
    """

    relative = _resolve_bundled_skill_relative(name_or_path)
    if relative is None:
        return None
    return _read_bundled_skill_body(relative)


def _resolve_bundled_skill_relative(name_or_path: str) -> str | None:
    requested = name_or_path.strip()
    if not requested:
        return None
    normalized = requested.strip("/")
    candidates = _bundled_skill_candidates()

    if normalized.startswith("bundled/") and normalized.endswith("/SKILL.md"):
        return normalized if normalized in candidates else None

    safe_name = normalized
    if safe_name.endswith("/SKILL.md"):
        safe_name = Path(safe_name).parent.name
    else:
        safe_name = Path(safe_name).name
    if not safe_name or safe_name in {".", ".."}:
        return None

    expected = f"bundled/superpowers/{safe_name}/SKILL.md"
    if expected in candidates:
        return expected

    for relative in candidates:
        if Path(relative).parent.name == safe_name:
            return relative
    return None


def _read_bundled_skill_body(relative: str) -> dict[str, object] | None:
    if not relative.startswith("bundled/") or not relative.endswith("/SKILL.md"):
        return None
    try:
        skills_root = resources.files("magi_agent").joinpath("skills")
        resource = skills_root.joinpath(*relative.split("/"))
        if not resource.is_file():
            return None
        body = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, UnicodeDecodeError, OSError):
        return None
    if len(body) > _MAX_SKILL_BODY_CHARS:
        body = body[:_MAX_SKILL_BODY_CHARS]
    return {
        "path": relative,
        "source": "bundled",
        "body": body,
        "bodyDigest": digest(body),
    }


def _workspace_skill_relative(root: Path, skill: Path) -> str | None:
    try:
        relative = skill.relative_to(root).as_posix()
    except ValueError:
        return None
    if not relative.endswith("/SKILL.md"):
        return None
    if _workspace_read_path_reason(root, relative) != "":
        return None
    return relative


def _read_workspace_skill_body(root: Path, relative: str) -> dict[str, object] | None:
    if not relative.endswith("/SKILL.md"):
        return None
    if _workspace_read_path_reason(root, relative) != "":
        return None
    path = (root / relative).resolve()
    try:
        body = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return None
    if len(body) > _MAX_SKILL_BODY_CHARS:
        body = body[:_MAX_SKILL_BODY_CHARS]
    return {
        "path": relative,
        "source": "workspace",
        "body": body,
        "bodyDigest": digest(body),
    }


def _workspace_read_path_reason(root: Path, relative: str) -> str:
    normalized = str(Path(relative.replace("\\", "/")).as_posix())
    normalized = "" if normalized == "." else normalized
    if not normalized or normalized.startswith("/") or normalized == "..":
        return "path_traversal_blocked"
    if normalized.startswith("../") or "/../" in f"/{normalized}/":
        return "path_traversal_blocked"
    reason = protected_workspace_path_reason(normalized, mutating=False)
    if reason:
        return reason
    try:
        candidate = (root / normalized).resolve()
        resolved_relative = candidate.relative_to(root).as_posix()
    except (OSError, ValueError):
        return "path_traversal_blocked"
    return protected_workspace_path_reason(resolved_relative, mutating=False)
