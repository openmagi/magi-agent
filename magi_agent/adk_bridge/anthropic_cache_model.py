"""Cache-aware Anthropic (Claude) model for the ADK runner boundary — PR11.

Why this module exists
----------------------
Magi's live model calls flow through Google ADK. ADK's Anthropic integration
(``google.adk.models.anthropic_llm``) converts ``genai`` ``Content``/``Part``
objects into Anthropic ``MessageParam`` blocks and posts them to the Anthropic
Messages API. The conversion path drops any ``cache_control`` marker: a
``genai.types.Part`` has no such field, and ``part_to_message_block`` emits bare
``TextBlockParam`` / ``ToolResultBlockParam`` dicts.

The pure helper :func:`magi_agent.prompt.injection.CacheControlInjector.mark_message_tail`
is therefore correct but never reaches the wire on the live ADK path. This
module closes that gap by subclassing ADK's :class:`google.adk.models.Claude`
and post-processing the *outgoing Anthropic request messages* so the last ~2
non-system messages carry ``cache_control: {type: ephemeral}`` — mirroring
OpenCode's rolling-tail prompt caching.

Status
------
Claude does NOT yet flow through this Python/ADK path in production (the
TypeScript runtime still owns Claude; that removal is tracked separately). This
is NOT dormant infra: it is wired into the model class that WILL be used the
moment a Claude/anthropic model id is routed through ADK
(:func:`magi_agent.shadow.gate5b4c3_live_runner_boundary` resolves the model),
and the request-level injection is proven by tests. The first live smoke is
Gemini, so the marker only materialises once Claude runs via ADK.

Design notes
------------
- Anthropic-only. The last ``tail_size`` (default 2) non-system messages get a
  marker on their final content block; never more than
  :data:`MESSAGE_TAIL_MAX_BREAKPOINTS` (2) new breakpoints, so combined with the
  up-to-2 system-prefix breakpoints the request stays within Anthropic's
  4-breakpoint ceiling.
- Gated on ``MAGI_MESSAGE_CACHE_ENABLED`` (default OFF) via
  :func:`magi_agent.config.env.is_message_cache_enabled`. OFF ⇒ the request is
  byte-identical to default ADK behaviour.
- The ``anthropic`` package is an OPTIONAL extra and is imported lazily by ADK
  itself; this module never imports it directly, so environments without
  ``anthropic`` are unaffected until a Claude model is actually constructed.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any

from magi_agent.config.env import is_message_cache_enabled

# Anthropic accepts at most 4 cache breakpoints per request. The system prefix
# may already reserve up to 2 (see prompt.injection), so the rolling
# conversation tail may add at most 2 more.
MESSAGE_TAIL_MAX_BREAKPOINTS = 2

_EPHEMERAL_CACHE_CONTROL = {"type": "ephemeral"}


def inject_message_tail_cache_control(
    messages: list[Any],
    *,
    tail_size: int = 2,
) -> list[Any]:
    """Return a copy of *messages* with cache markers on the tail.

    Marks the last *tail_size* (capped at :data:`MESSAGE_TAIL_MAX_BREAKPOINTS`)
    non-system messages with ``cache_control: {type: ephemeral}`` on their last
    content block. System messages are never marked. The input is never mutated.

    This is the pure, provider-agnostic-shaped marker logic lifted from
    :meth:`magi_agent.prompt.injection.CacheControlInjector.mark_message_tail`
    so it can be unit-tested deterministically without the ``anthropic``
    package. It operates on Anthropic ``MessageParam``-shaped mappings
    (``{"role": ..., "content": [...] | str}``); each entry may be a plain dict
    or any mapping (e.g. a ``TypedDict``).

    Args:
        messages: Ordered Anthropic message params.
        tail_size: How many trailing non-system messages to mark.

    Returns:
        A new list. Marked messages are deep-copied; untouched messages are
        passed through by reference.
    """
    result: list[Any] = list(messages)
    capped = min(max(tail_size, 0), MESSAGE_TAIL_MAX_BREAKPOINTS)
    if capped == 0:
        return result

    non_system_indices = [
        index
        for index, message in enumerate(result)
        if _role_of(message) != "system"
    ]
    for index in non_system_indices[-capped:]:
        result[index] = _mark_message(result[index])
    return result


def _role_of(message: Any) -> Any:
    if isinstance(message, Mapping):
        return message.get("role")
    return getattr(message, "role", None)


def _mark_message(message: Any) -> Any:
    marked = copy.deepcopy(message)
    content = marked.get("content") if isinstance(marked, Mapping) else None
    if isinstance(content, list):
        for position in range(len(content) - 1, -1, -1):
            block = content[position]
            if isinstance(block, dict):
                block["cache_control"] = dict(_EPHEMERAL_CACHE_CONTROL)
                break
        return marked
    if isinstance(marked, dict):
        text = "" if content is None else str(content)
        marked["content"] = [
            {
                "type": "text",
                "text": text,
                "cache_control": dict(_EPHEMERAL_CACHE_CONTROL),
            }
        ]
    return marked


def _build_cache_aware_claude_class() -> type:
    """Build the ``CacheAwareClaude`` subclass against the installed ADK.

    Importing :class:`google.adk.models.Claude` pulls in the optional
    ``anthropic`` package (ADK gates this with "Claude models require the
    anthropic package"). We defer that import to call time so this module can be
    imported in environments without ``anthropic`` — the cost is only paid when
    a Claude model is actually constructed.
    """
    from google.adk.models.anthropic_llm import (  # noqa: PLC0415
        Claude,
        content_to_message_param,
        message_to_generate_content_response,
    )
    from anthropic import NOT_GIVEN  # noqa: PLC0415
    from anthropic import types as anthropic_types  # noqa: PLC0415

    class CacheAwareClaude(Claude):
        """``google.adk.models.Claude`` that injects rolling-tail cache markers.

        Overrides :meth:`generate_content_async` to post-process the outgoing
        Anthropic request: the last ~2 non-system messages get a
        ``cache_control: {type: ephemeral}`` marker before the messages reach
        ``messages.create(...)``. Gated on ``MAGI_MESSAGE_CACHE_ENABLED``; when
        OFF the request matches the parent's exactly.
        """

        # Public marker so callers can assert the cache-aware path is wired.
        magi_message_cache_aware: bool = True

        def _maybe_inject_cache_control(self, messages: list[Any]) -> list[Any]:
            if not is_message_cache_enabled():
                return messages
            return inject_message_tail_cache_control(messages)

        async def generate_content_async(self, llm_request, stream: bool = False):
            model_to_use = self._resolve_model_name(llm_request.model)
            messages = [
                content_to_message_param(content)
                for content in llm_request.contents or []
            ]
            messages = self._maybe_inject_cache_control(messages)

            tools = NOT_GIVEN
            from google.adk.models.anthropic_llm import (  # noqa: PLC0415
                function_declaration_to_tool_param,
                _build_anthropic_thinking_param,
            )

            if (
                llm_request.config
                and llm_request.config.tools
                and llm_request.config.tools[0].function_declarations
            ):
                tools = [
                    function_declaration_to_tool_param(tool)
                    for tool in llm_request.config.tools[0].function_declarations
                ]
            tool_choice = (
                anthropic_types.ToolChoiceAutoParam(type="auto")
                if llm_request.tools_dict
                else NOT_GIVEN
            )
            thinking = _build_anthropic_thinking_param(llm_request.config)

            if not stream:
                message = await self._anthropic_client.messages.create(
                    model=model_to_use,
                    system=llm_request.config.system_instruction,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=self.max_tokens,
                    thinking=thinking,
                )
                yield message_to_generate_content_response(message)
            else:
                async for response in self._generate_content_streaming(
                    llm_request, messages, tools, tool_choice, thinking
                ):
                    yield response

    return CacheAwareClaude


def get_cache_aware_claude_class() -> type:
    """Return (and memoise) the ``CacheAwareClaude`` subclass.

    Raises ``ImportError`` (from ADK / the ``anthropic`` package) only when the
    class is first requested, matching ADK's own lazy gating.
    """
    global _CACHE_AWARE_CLAUDE_CLASS
    if _CACHE_AWARE_CLAUDE_CLASS is None:
        _CACHE_AWARE_CLAUDE_CLASS = _build_cache_aware_claude_class()
    return _CACHE_AWARE_CLAUDE_CLASS


def build_cache_aware_claude(model: str):
    """Construct a ``CacheAwareClaude`` instance for *model*.

    This is the seam the live runner boundary calls when a Claude/anthropic
    model id is routed through ADK.
    """
    cls = get_cache_aware_claude_class()
    return cls(model=model)


_CACHE_AWARE_CLAUDE_CLASS: type | None = None


__all__ = [
    "MESSAGE_TAIL_MAX_BREAKPOINTS",
    "build_cache_aware_claude",
    "get_cache_aware_claude_class",
    "inject_message_tail_cache_control",
]
