"""Shared sanitization helpers for hook executors.

All hook executors (command, http, …) send a sanitized JSON payload to the
external destination. Centralising the logic here ensures consistent redaction
rules across executor types.

Sanitisation rules
------------------
- Absolute POSIX paths containing a username component → ``<redacted_path>``
- Sensitive system paths (``/etc``, ``/var``, ``/run``, ``/proc``, ``/sys``,
  ``/tmp``) → ``<redacted_path>``
- API keys / Bearer tokens / ``api_key=…`` patterns → ``<redacted_secret>``
- Thinking blocks / internal scratchpad: not present in ``HookContext``, but
  never forward any field whose name contains "thinking" or "scratchpad".
- ``userId``: excluded from the payload (too sensitive for operator webhooks).
"""
from __future__ import annotations

import re
from typing import Any, Union

from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.manifest import HookManifest

# ---------------------------------------------------------------------------
# Path / secret redaction patterns
# ---------------------------------------------------------------------------

# Absolute paths that contain a username component (POSIX home dirs)
_PATH_RE = re.compile(
    r"(/(?:Users|home)/[^/\s\"']+(?:/[^\s\"']*)?)",
    re.IGNORECASE,
)

# Broader sensitive POSIX system paths (etc, var, run, proc, sys, tmp)
_SENSITIVE_PATH_RE = re.compile(
    r"(/(?:etc|var|run|proc|sys|tmp)/[^\s\"']*)",
)

# Common secret / token patterns
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI / Anthropic style keys: "sk-" or "sk-ant-" followed by alphanum
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    # Bearer tokens
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b", re.IGNORECASE),
    # Generic "api_key = <value>" patterns
    re.compile(r"\b(?:api[_-]?key|apikey|access[_-]?token|secret[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_value(value: str) -> str:
    """Replace sensitive substrings in *value* with safe placeholders."""
    # Redact home-directory paths first
    value = _PATH_RE.sub("<redacted_path>", value)
    # Redact sensitive system paths
    value = _SENSITIVE_PATH_RE.sub("<redacted_path>", value)
    # Redact secret patterns
    for pat in _SECRET_PATTERNS:
        value = pat.sub("<redacted_secret>", value)
    return value


def _sanitize_any(obj: object) -> object:
    """Recursively sanitize *obj* (dict / list / str / other)."""
    if isinstance(obj, str):
        return _sanitize_value(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_any(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_any(item) for item in obj]
    return obj


def _sanitize_any_typed(obj: Union[dict[str, Any], list[Any], str, object]) -> Union[dict[str, Any], list[Any], str, object]:
    """Typed wrapper around ``_sanitize_any`` to avoid type: ignore at call sites."""
    return _sanitize_any(obj)


def _build_sanitized_hook_input(context: HookContext, manifest: HookManifest) -> dict[str, Any]:
    """Build the JSON payload sent to the hook process / HTTP endpoint.

    Deliberately omits:
    - Any field that could carry raw workspace paths (sanitized as a fallback)
    - Thinking blocks / internal reasoning
    - Auth tokens / API keys
    - Full conversation history
    - ``userId`` (sensitive)
    """
    payload: dict[str, Any] = {
        "hookEvent": manifest.point.value,
        "hookName": manifest.name,
        "botId": context.bot_id,
    }

    # Optional safe context fields
    if context.session_id is not None:
        payload["sessionId"] = context.session_id
    if context.turn_id is not None:
        payload["turnId"] = context.turn_id
    if context.channel is not None:
        payload["channel"] = context.channel
    if context.locale is not None:
        payload["locale"] = context.locale
    if context.memory_mode is not None:
        payload["memoryMode"] = context.memory_mode
    if context.agent_model is not None:
        payload["agentModel"] = context.agent_model
    if context.plugin_id is not None:
        payload["pluginId"] = context.plugin_id
    if context.policy_scope is not None:
        payload["policyScope"] = context.policy_scope

    # TODO: add sanitized toolInput here once HookContext gains a tool_input field.
    # When added: payload["toolInput"] = _sanitize_any_typed(context.tool_input)

    # Apply sanitization pass over all string values (defence-in-depth)
    result = _sanitize_any_typed(payload)
    assert isinstance(result, dict)
    return result
