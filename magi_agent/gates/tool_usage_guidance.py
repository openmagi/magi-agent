"""Per-tool usage guidance appended to gate5b ADK tool descriptions (D1).

Most gate5b tools ship the generic fallback docstring ("Gate 5B selected full
toolhost {name} tool."), giving the model zero routing signal between
overlapping tools (Bash-vs-Grep, FileWrite-vs-FileEdit, ...). This registry
holds lean "Use when / Do NOT use when" blocks for the highest-confusion
tools; ``apply_usage_guidance`` appends them behind the default-OFF
``MAGI_TOOL_USAGE_GUIDANCE_ENABLED`` flag.

Contract: flag OFF / unregistered tool / any error -> the description is
returned unchanged (byte-identical, fail-open). Entries are capped at 600
chars and must contain at least one explicit negative ("Do NOT") rule —
enforced by tests.
"""
from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "TOOL_USAGE_GUIDANCE",
    "apply_usage_guidance",
]

TOOL_USAGE_GUIDANCE: dict[str, str] = {
    "WebSearch": (
        "Use when: facts that change over time (prices, versions, current "
        "events, current officeholders) or any name/product you cannot "
        "confidently place. Do NOT use for stable knowledge (algorithms, "
        "historical facts, language syntax) or for workspace content — use "
        "Grep/FileRead for local code. One focused query beats several vague "
        "ones; stop when the answer is found."
    ),
    "WebFetch": (
        "Use when: reading the full content of a specific URL you already "
        "have (from WebSearch results or the user). Do NOT use to discover "
        "pages — run WebSearch first. Do NOT use for local files — use "
        "FileRead."
    ),
    "Bash": (
        "Use when: shell-only operations — running tests/builds, git, "
        "process management, pipes. Do NOT use to read files (FileRead), "
        "search file contents (Grep), find files by name (Glob), or edit "
        "files (FileEdit/FileWrite); the dedicated tools are faster and "
        "safer than cat/sed/echo."
    ),
    "Grep": (
        "Use when: finding WHERE a symbol or string occurs across files. "
        "Do NOT use to read a known file (FileRead) or to find files by "
        "name pattern (Glob). Example: 'where is build_system_prompt "
        "called' -> Grep; 'show me message_builder.py' -> FileRead."
    ),
    "Glob": (
        "Use when: finding files by NAME pattern (e.g. '**/*_test.py'). "
        "Do NOT use to search file CONTENTS — that is Grep."
    ),
    "FileRead": (
        "Use when: reading a known file path. Do NOT use cat via Bash. For "
        "very large files, read a line range instead of the whole file."
    ),
    "FileEdit": (
        "Use when: targeted in-place edits to an existing file (exact "
        "old/new replacement). Read the file first so the match is exact. "
        "Do NOT rewrite a whole file for a small change — surgical edits "
        "preserve the rest."
    ),
    "FileWrite": (
        "Use when: creating a new file, or fully replacing one whose entire "
        "content you intend to control. Do NOT use for small modifications "
        "to existing files — use FileEdit."
    ),
    "SpawnAgent": (
        "Use when: an independent subtask benefits from a fresh context "
        "(parallel research, isolated implementation). Give the child a "
        "self-contained brief. To run the child on a specific model, pass BOTH "
        "`provider` AND `model` exactly matching one of the routes listed below. "
        "Do NOT invent model names or pass `model` without its matching "
        "`provider` (rejected as child_model_route_unknown). Pass `taskTitle` "
        "(a SHORT public-safe label, <= 64 chars, shown to the user as the "
        "chip label) — keep it descriptive but free of private prompt content. "
        "Do NOT spawn for work you can finish directly in fewer steps."
    ),
    "AskUserQuestion": (
        "Use when: a genuine decision blocks progress and the answer is not "
        "derivable from the task, code, or conversation. Do NOT ask to "
        "confirm things the task already specifies, and do NOT ask multiple "
        "rounds when one round of focused options suffices."
    ),
}


def apply_usage_guidance(
    name: str,
    description: str,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return *description* with registry guidance appended when the flag is ON.

    Fail-open: flag OFF, tool not in the registry, or any error -> the
    original *description* unchanged.
    """
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            is_tool_usage_guidance_enabled,
        )

        if not is_tool_usage_guidance_enabled(env):
            return description
        guidance = TOOL_USAGE_GUIDANCE.get(name)
        if not guidance:
            return description
        result = f"{description}\n\n{guidance}"
        if name == "SpawnAgent":
            routes = _spawn_agent_routes_line(env)
            if routes:
                result = f"{result}\n\n{routes}"
        return result
    except Exception:  # noqa: BLE001
        return description


def _spawn_agent_routes_line(env: Mapping[str, str] | None) -> str:
    """Dynamic ``provider:model`` route list a child spawn may target.

    Union of the built-in ``ModelTierRegistry`` and the operator's deployment
    route allowlist — the two sources :func:`_validate_route` accepts — so the
    model requests routes that actually pass validation instead of guessing.
    Fail-soft: any error returns ``""`` (no line appended).
    """
    try:
        import os  # noqa: PLC0415

        from magi_agent.runtime.model_tiers import (  # noqa: PLC0415
            available_child_model_routes,
        )

        source_env = env if env is not None else os.environ
        routes = available_child_model_routes(source_env)
        if not routes:
            return ""
        return (
            "Available model routes (pass provider + model exactly; omitting "
            f"provider defaults to anthropic): {', '.join(routes)}."
        )
    except Exception:  # noqa: BLE001
        return ""
