"""Pre-built LLM hook preset manifests for common safety checks.

Three opt-in presets:

1. ``bash_safety_classifier`` -- beforeToolUse hook that classifies Bash
   commands for safety (read-only vs destructive).
2. ``edit_scope_classifier`` -- beforeToolUse hook that checks whether file
   edits stay within the scope requested by the user.
3. ``response_quality_gate`` -- beforeCommit hook that verifies the response
   fully answers the user's question before committing.

All presets are **disabled by default** (``enabled=False``) and **fail-open**
(``fail_open=True``) so they never block unless explicitly opted in.
"""
from __future__ import annotations

from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.tools.manifest import ToolSource

__all__ = [
    "bash_safety_classifier",
    "edit_scope_classifier",
    "response_quality_gate",
]

_BUILTIN_SOURCE = ToolSource(kind="builtin", package="magi_agent.hooks.builtin")

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_BASH_SAFETY_PROMPT = """\
You are a safety classifier for shell commands executed by an AI agent.

## Command
```
{toolInput}
```

## Context
- Bot: {botId}
- Channel: {channel}
- Hook event: {hookEvent}

## Instructions
Classify this Bash command as safe or unsafe.

Safe commands: read-only operations (ls, cat, grep, find, head, tail, wc, \
echo, pwd, date, git status, git log, git diff, npm test, npm run lint, \
python -m pytest).

Unsafe commands: file deletion (rm, rmdir), permission changes (chmod, chown), \
disk writes to system paths, network exfiltration (curl POST with secrets), \
process killing (kill, pkill), package installation without explicit approval, \
any command using sudo.

Respond with exactly one keyword on the first line:
- ALLOW -- if the command is safe
- DENY -- if the command is destructive or dangerous
- ASK -- if the command is ambiguous and needs user confirmation

Follow the keyword with a brief reason.\
"""

_EDIT_SCOPE_PROMPT = """\
You are a scope classifier for file edit operations by an AI agent.

## Edit Details
- File: {toolInput}
- Bot: {botId}
- Hook event: {hookEvent}

## Instructions
Determine whether this file edit stays within the scope of the user's request.

Out-of-scope indicators:
- Editing files unrelated to the current task
- Modifying configuration files (package.json, tsconfig, .env) without explicit request
- Changing test files when only production code was requested
- Touching files outside the project workspace

Respond with exactly one keyword on the first line:
- ALLOW -- the edit is within scope
- DENY -- the edit is clearly outside the requested scope
- ASK -- the scope is ambiguous; ask the user

Follow the keyword with a brief reason.\
"""

_RESPONSE_QUALITY_PROMPT = """\
You are a quality gate for AI agent responses before they are committed.

## Response Summary
{hookEvent}

## Context
- Bot: {botId}
- Channel: {channel}

## Instructions
Evaluate whether the response fully and correctly answers the user's question.

Quality failures:
- Response is empty or only contains filler phrases
- Response contradicts the user's request
- Response claims completion but no evidence of work done
- Response defers work ("I'll do this later") without justification
- Response contains obvious hallucinated file paths or tool names

Respond with exactly one keyword on the first line:
- ALLOW -- the response adequately answers the question
- DENY -- the response is clearly inadequate or wrong
- ASK -- the quality is borderline; flag for review

Follow the keyword with a brief reason.\
"""


# ---------------------------------------------------------------------------
# Preset factory functions
# ---------------------------------------------------------------------------


def bash_safety_classifier() -> HookManifest:
    """Return a beforeToolUse hook that classifies Bash commands for safety."""
    return HookManifest(
        name="builtin:bash-safety-classifier",
        point=HookPoint.BEFORE_TOOL_USE,
        description="Classifies Bash commands as safe, unsafe, or ambiguous via LLM.",
        source=_BUILTIN_SOURCE,
        executionType="llm",
        promptTemplate=_BASH_SAFETY_PROMPT,
        enabled=False,
        failOpen=True,
        priority=50,
        optOut=True,
    )


def edit_scope_classifier() -> HookManifest:
    """Return a beforeToolUse hook that checks file edits stay within scope."""
    return HookManifest(
        name="builtin:edit-scope-classifier",
        point=HookPoint.BEFORE_TOOL_USE,
        description="Checks whether file edits stay within the user-requested scope via LLM.",
        source=_BUILTIN_SOURCE,
        executionType="llm",
        promptTemplate=_EDIT_SCOPE_PROMPT,
        enabled=False,
        failOpen=True,
        priority=50,
        optOut=True,
    )


def response_quality_gate() -> HookManifest:
    """Return a beforeCommit hook that checks response quality."""
    return HookManifest(
        name="builtin:response-quality-gate",
        point=HookPoint.BEFORE_COMMIT,
        description="Verifies the response fully answers the user's question via LLM.",
        source=_BUILTIN_SOURCE,
        executionType="llm",
        promptTemplate=_RESPONSE_QUALITY_PROMPT,
        enabled=False,
        failOpen=True,
        priority=50,
        optOut=True,
    )
