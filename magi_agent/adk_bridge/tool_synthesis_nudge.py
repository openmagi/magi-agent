"""Per-step tool-synthesis reflection nudge for the live ADK Runner.

Live-SWE-agent's highest-leverage delta is a 2-line reflection nudge appended
to EVERY tool observation ("would a custom tool help the remaining work?").
This module implements that as an ADK ``BasePlugin`` whose
``after_tool_callback`` returns a copy of the tool's function-response dict
with one extra key carrying the static nudge text. As with
``edit_retry_reflection``, a non-``None`` dict returned from this seam
REPLACES the tool response fed to the model on the next LLM call — the nudge
travels on the function_response channel only (model-visible, never
user-visible).

The plugin is registered LAST on the control plane (see
``control_plane.build_default_plane``) so edit-retry / resilience overrides
win the plane's first-non-None-wins after-tool fan-out; the nudge only rides
on results no other control replaced.

Skip rules (all return ``None`` → original result untouched):

- non-mapping results (ADK core tools always return dicts; anything else is
  out of contract — leave it alone),
- results already carrying the nudge key (idempotence),
- synthetic injected responses from other plugins (``response_type`` marker,
  e.g. ``MAGI_EDIT_RETRY_REFLECTION``) — never stack guidance on guidance,
- truncated/oversized observations, mirroring Live-SWE behavior: a boolean
  ``truncated`` flag at the top level or one level deep
  (``output``/``llmOutput``/``transcriptOutput``), the MCP / output-budget
  ``truncation`` projection (``llmPreviewTruncated`` /
  ``transcriptPreviewTruncated``), or the gate5b Bash head/tail elision
  marker embedded in string output.

Activation is owned by the caller: ``build_tool_synthesis_nudge_plugin``
returns ``None`` unless ``enabled=True`` (flag + tier resolution happens in
``magi_agent.runtime.tool_synthesis``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.runtime.tool_synthesis import TOOL_SYNTHESIS_NUDGE_TEXT

TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME = "magi_tool_synthesis_nudge_plugin"

#: Key carrying the nudge on the replacement tool response. camelCase to match
#: the ``ToolResult.model_dump(by_alias=True)`` dict it is merged into.
TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY = "toolSynthesisReflection"

#: Marker other plugins stamp on synthetic injected responses (e.g.
#: ``EDIT_RETRY_REFLECTION_RESPONSE_TYPE``); never nudge those.
_SYNTHETIC_RESPONSE_MARKER_KEY = "response_type"

#: Stable literal inside gate5b's Bash head/tail elision marker
#: (``gate5b_full_toolhost._bounded_head_tail`` / ``_BoundedPipeCapture``).
#: Long slice on purpose: a bare "output truncated" substring appears in
#: ordinary content (logs, this repo's own source) and would suppress nudges.
_BASH_ELISION_MARKER = "bytes elided - output truncated"

#: Result keys whose nested mapping/string payloads are checked for truncation.
_OUTPUT_KEYS = ("output", "llmOutput", "transcriptOutput")

#: camelCase flags inside the MCP / output-budget ``truncation`` projection
#: (``tools/output_budget.py`` ``public_projection``).
_TRUNCATION_PROJECTION_KEYS = ("llmPreviewTruncated", "transcriptPreviewTruncated")


class MagiToolSynthesisNudgePlugin(BasePlugin):
    """ADK plugin appending the static tool-synthesis nudge to tool results.

    Stateless: the nudge text is constant and per-call, so there is no
    per-invocation state to sweep in ``after_run_callback``.
    """

    def __init__(self, *, name: str = TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME) -> None:
        super().__init__(name)

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        _ = (tool, tool_args, tool_context)
        if not isinstance(result, Mapping):
            return None
        if TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY in result:
            return None
        if _SYNTHETIC_RESPONSE_MARKER_KEY in result:
            return None
        if _is_truncated(result):
            return None
        return {
            **result,
            TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY: TOOL_SYNTHESIS_NUDGE_TEXT,
        }


def _is_truncated(result: Mapping[str, Any]) -> bool:
    """Best-effort truncation detection at the after-tool seam.

    Checks the boolean ``truncated`` markers tools emit (top level and one
    level deep under the output keys), the MCP / output-budget ``truncation``
    projection mapping, plus gate5b's inline Bash elision marker. Deeper
    truncation signals are NOT plumbed here — see PR notes.
    """
    if result.get("truncated"):
        return True
    truncation = result.get("truncation")
    if isinstance(truncation, Mapping) and any(
        truncation.get(key) for key in _TRUNCATION_PROJECTION_KEYS
    ):
        return True
    for key in _OUTPUT_KEYS:
        value = result.get(key)
        if isinstance(value, str):
            if _BASH_ELISION_MARKER in value:
                return True
        elif isinstance(value, Mapping):
            if value.get("truncated"):
                return True
            if any(
                isinstance(item, str) and _BASH_ELISION_MARKER in item
                for item in value.values()
            ):
                return True
    return False


def build_tool_synthesis_nudge_plugin(
    *,
    enabled: bool,
) -> MagiToolSynthesisNudgePlugin | None:
    """Return a configured plugin, or ``None`` when the feature is inactive.

    Flag + model-tier resolution is owned by
    ``magi_agent.runtime.tool_synthesis.tool_synthesis_nudge_active``; callers
    pass the resolved decision here so this module stays free of env-parsing
    and tier-registry concerns.
    """
    if not enabled:
        return None
    return MagiToolSynthesisNudgePlugin()


__all__ = [
    "TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME",
    "TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY",
    "MagiToolSynthesisNudgePlugin",
    "build_tool_synthesis_nudge_plugin",
]
