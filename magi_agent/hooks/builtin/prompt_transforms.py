"""Built-in ``beforeSystemPrompt`` prompt-transform presets (PR3, Track 16).

Three opt-in, additive transforms that read the assembled system-prompt
sections from :attr:`HookContext.prompt_sections` and return a NEW section list
(existing sections + one new section):

1. :func:`language_preference_transform` -- inject a "Respond in {language}."
   section derived deterministically from ``context.locale``.
2. :func:`project_context_transform` -- inject the contents of
   ``.opencode/context.md`` from the workspace root, if present.
3. :func:`model_capability_transform` -- inject a short capability note for
   Claude/Opus models (extended thinking) based on ``context.agent_model``.

All three are **disabled by default** (``enabled=False``), **fail-open**
(``failOpen=True``), and **opt-out allowed** (``optOut=True``), so they never
fire unless an operator explicitly opts in. They are ``executionType="handler"``
hooks (real Python logic, run synchronously via :meth:`HookBus.run`).

Design notes (signals + caps):
  * Language signal: ``context.locale`` only. If absent, we return ``continue``
    and never guess (the runtime has no recent-message text on HookContext).
    The locale's primary subtag is mapped to a human-readable language name via
    a small deterministic table, falling back to the raw locale string.
  * Workspace root: resolved the same way the gate1a read-only tools resolve it
    -- ``CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT`` env var, else
    the current working directory. ``.opencode/context.md`` is read relative to
    that root, guarded by existence + a size cap (truncated with a note).
  * Model signal: ``context.agent_model`` substring ``"claude"`` (case
    -insensitive) gates the extended-thinking note.

Rule 3 (immutability): handlers treat ``context.prompt_sections`` as an
immutable tuple and never mutate it; they build a new list.
"""
from __future__ import annotations

import os
from pathlib import Path

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.tools.manifest import ToolSource

__all__ = [
    "language_preference_transform",
    "language_preference_transform_manifest",
    "project_context_transform",
    "project_context_transform_manifest",
    "model_capability_transform",
    "model_capability_transform_manifest",
]

_BUILTIN_SOURCE = ToolSource(kind="builtin", package="magi_agent.hooks.builtin")

# Maximum bytes of ``.opencode/context.md`` injected into the prompt. Files
# larger than this are truncated with an explicit note so a huge project file
# cannot blow up the prompt.
_PROJECT_CONTEXT_MAX_CHARS = 8_000

# Deterministic primary-subtag -> language-name table. Unmapped subtags fall
# back to the raw locale string (still deterministic).
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
}


def _existing_sections(context: HookContext) -> tuple[str, ...] | None:
    sections = context.prompt_sections
    if not sections:
        return None
    return tuple(sections)


def _language_name(locale: str) -> str:
    primary = locale.strip().replace("_", "-").split("-", 1)[0].lower()
    return _LANGUAGE_NAMES.get(primary, locale.strip())


# ---------------------------------------------------------------------------
# 1. language_preference_transform
# ---------------------------------------------------------------------------


def language_preference_transform(context: HookContext) -> HookResult | None:
    """Inject a "Respond in {language}." section derived from ``context.locale``.

    Returns ``continue`` (None) when there is no locale signal or no assembled
    sections to extend -- never guesses a language.
    """
    sections = _existing_sections(context)
    if sections is None:
        return None
    locale = context.locale
    if not locale or not locale.strip():
        return None
    language = _language_name(locale)
    section = f"Respond in {language}."
    return HookResult(action="replace", value=[*sections, section])


def language_preference_transform_manifest() -> HookManifest:
    """Manifest for :func:`language_preference_transform` (disabled by default)."""
    return HookManifest(
        name="builtin:language-preference-transform",
        point=HookPoint.BEFORE_SYSTEM_PROMPT,
        description="Injects a 'Respond in {language}.' section derived from the locale.",
        source=_BUILTIN_SOURCE,
        executionType="handler",
        enabled=False,
        failOpen=True,
        priority=50,
        optOut=True,
    )


# ---------------------------------------------------------------------------
# 2. project_context_transform
# ---------------------------------------------------------------------------


def _workspace_root() -> Path:
    configured = os.environ.get(
        "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT"
    )
    if configured:
        return Path(configured)
    return Path.cwd()


def project_context_transform(context: HookContext) -> HookResult | None:
    """Inject ``.opencode/context.md`` (if present) as an extra context section.

    The workspace root is resolved from
    ``CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT`` (else CWD), the
    same signal the gate1a read-only tools use. The file is read with an
    existence guard and a size cap; oversized content is truncated with a note.
    Returns ``continue`` (None) when the file is absent/unreadable or there are
    no sections to extend.
    """
    sections = _existing_sections(context)
    if sections is None:
        return None

    context_path = _workspace_root() / ".opencode" / "context.md"
    try:
        if not context_path.is_file():
            return None
        raw = context_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # fail-open: any IO error means no project-context section.
        return None

    if not raw.strip():
        return None

    if len(raw) > _PROJECT_CONTEXT_MAX_CHARS:
        body = raw[:_PROJECT_CONTEXT_MAX_CHARS]
        body = f"{body}\n\n[project context truncated to {_PROJECT_CONTEXT_MAX_CHARS} chars]"
    else:
        body = raw

    section = f"## Project Context (.opencode/context.md)\n{body}"
    return HookResult(action="replace", value=[*sections, section])


def project_context_transform_manifest() -> HookManifest:
    """Manifest for :func:`project_context_transform` (disabled by default)."""
    return HookManifest(
        name="builtin:project-context-transform",
        point=HookPoint.BEFORE_SYSTEM_PROMPT,
        description="Injects .opencode/context.md as an extra project-context section.",
        source=_BUILTIN_SOURCE,
        executionType="handler",
        enabled=False,
        failOpen=True,
        priority=50,
        optOut=True,
    )


# ---------------------------------------------------------------------------
# 3. model_capability_transform
# ---------------------------------------------------------------------------


def model_capability_transform(context: HookContext) -> HookResult | None:
    """Inject a short extended-thinking note for Claude/Opus models.

    Reads ``context.agent_model``; if it indicates a Claude model (case
    -insensitive substring ``"claude"``), appends a brief capability note.
    Returns ``continue`` (None) otherwise.
    """
    sections = _existing_sections(context)
    if sections is None:
        return None
    model = context.agent_model
    if not model or "claude" not in model.lower():
        return None
    section = (
        "Model capability note: this Claude model supports extended thinking. "
        "Use it to reason through complex steps before acting, then respond concisely."
    )
    return HookResult(action="replace", value=[*sections, section])


def model_capability_transform_manifest() -> HookManifest:
    """Manifest for :func:`model_capability_transform` (disabled by default)."""
    return HookManifest(
        name="builtin:model-capability-transform",
        point=HookPoint.BEFORE_SYSTEM_PROMPT,
        description="Injects a Claude extended-thinking capability note based on the model.",
        source=_BUILTIN_SOURCE,
        executionType="handler",
        enabled=False,
        failOpen=True,
        priority=50,
        optOut=True,
    )
