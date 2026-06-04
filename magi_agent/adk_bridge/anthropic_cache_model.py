"""Cache-aware Anthropic (Claude) model for the ADK runner boundary — PR11.

Why this module exists
----------------------
Magi's live model calls flow through Google ADK. ADK's Anthropic integration
(``google.adk.models.anthropic_llm``) converts ``genai`` ``Content``/``Part``
objects into Anthropic ``MessageParam`` blocks and posts them to the Anthropic
Messages API. The conversion path drops any ``cache_control`` marker: a
``genai.types.Part`` has no such field, and ``part_to_message_block`` emits bare
``TextBlockParam`` / ``ToolResultBlockParam`` dicts.

The shared marker helper :func:`inject_message_tail_cache_control` is therefore
correct but never reaches the wire on the live ADK path. This module closes that
gap by subclassing ADK's Anthropic model class and post-processing the
*outgoing Anthropic request messages* so the last ~2 non-system messages carry
``cache_control: {type: ephemeral}`` — mirroring OpenCode's rolling-tail prompt
caching.

Base-class selection (Vertex vs direct Anthropic)
-------------------------------------------------
ADK ships two Anthropic model classes that share the *same* ``claude-3-.*`` /
``claude-.*-4.*`` ``supported_models()`` regex, and ADK's own ``LLMRegistry``
resolves ``claude-*`` ids to :class:`google.adk.models.Claude` — the **Vertex**
subclass whose ``_anthropic_client`` is ``AsyncAnthropicVertex`` and which
requires ``GOOGLE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_LOCATION``. The direct-API
base :class:`google.adk.models.anthropic_llm.AnthropicLlm` uses
``AsyncAnthropic()`` (``ANTHROPIC_API_KEY``).

Magi's deployment uses the **direct Anthropic API**: the Gate 5B live-smoke
config (``magi_agent.config.env``) mandates ``GOOGLE_GENAI_USE_VERTEXAI=false``.
Blindly subclassing the Vertex ``Claude`` would therefore inherit the wrong
client and fail at first model call with a "GOOGLE_CLOUD_PROJECT must be set"
``ValueError``. So this module does NOT hard-code a base: :func:`build_cache_aware_claude`
picks the base via :func:`_select_anthropic_base` — default ``AnthropicLlm``
(direct API) unless a Vertex signal is present (Vertex ``projects/`` resource id,
``GOOGLE_GENAI_USE_VERTEXAI`` truthy, or both ``GOOGLE_CLOUD_PROJECT`` and
``GOOGLE_CLOUD_LOCATION`` set), in which case the Vertex ``Claude`` is used so the
inherited ``_anthropic_client`` matches the deployment.

The cache-injection logic is shared by both bases via :class:`_CacheControlMixin`,
which overrides ``generate_content_async`` to replicate ADK's
build-messages-then-send path with the rolling-tail marker applied to the
``messages`` list (covering both the non-stream and ``stream=True`` branches,
since the streaming helper receives the already-injected ``messages``).

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
import os
from typing import Any, ClassVar

from magi_agent.config.env import is_message_cache_enabled

# Anthropic accepts at most 4 cache breakpoints per request. The system prefix
# may already reserve up to 2 (see prompt.injection), so the rolling
# conversation tail may add at most 2 more.
MESSAGE_TAIL_MAX_BREAKPOINTS = 2

_EPHEMERAL_CACHE_CONTROL = {"type": "ephemeral"}

# Memoised cache-aware subclasses, keyed by ADK base-class name. We build one
# subclass per base (direct ``AnthropicLlm`` / Vertex ``Claude``) lazily so the
# optional ``anthropic`` import is only paid when a Claude model is constructed.
_CACHE_AWARE_CLASSES: dict[str, type] = {}


def inject_message_tail_cache_control(
    messages: list[Any],
    *,
    tail_size: int = 2,
) -> list[Any]:
    """Return a copy of *messages* with cache markers on the tail.

    Marks the last *tail_size* (capped at :data:`MESSAGE_TAIL_MAX_BREAKPOINTS`)
    non-system messages with ``cache_control: {type: ephemeral}`` on their last
    content block. System messages are never marked. The input is never mutated.

    This is the single source of truth for the rolling-tail marker logic on the
    live ADK path. The provider-aware shim
    :meth:`magi_agent.prompt.injection.CacheControlInjector.mark_message_tail`
    delegates to this helper for the Anthropic case so the marking logic is not
    duplicated. It operates on Anthropic ``MessageParam``-shaped mappings
    (``{"role": ..., "content": [...] | str}``); each entry may be a plain dict
    or any mapping (e.g. a ``TypedDict``).

    Args:
        messages: Ordered Anthropic message params.
        tail_size: How many trailing non-system messages to mark.

    Returns:
        A new list. Marked messages are shallow-copied (with their touched
        content blocks copied) so the input is never mutated; untouched messages
        are passed through by reference.
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
    """Return a copy of *message* with a cache marker on its last content block.

    Uses the shallow ``dict(message)`` + per-block ``dict(block)`` copy pattern
    (matching ``prompt.injection``): ADK builds the message params freshly on
    every call, so a full ``deepcopy`` would be wasteful. We copy only the
    container dict and the single block we mutate, leaving the input untouched.
    """
    if not isinstance(message, Mapping):
        return message
    marked = dict(message)
    content = marked.get("content")
    if isinstance(content, list):
        blocks = [
            dict(block) if isinstance(block, dict) else block for block in content
        ]
        for position in range(len(blocks) - 1, -1, -1):
            block = blocks[position]
            if isinstance(block, dict):
                block["cache_control"] = dict(_EPHEMERAL_CACHE_CONTROL)
                break
        marked["content"] = blocks
        return marked
    text = "" if content is None else str(content)
    marked["content"] = [
        {
            "type": "text",
            "text": text,
            "cache_control": dict(_EPHEMERAL_CACHE_CONTROL),
        }
    ]
    return marked


def _has_vertex_signal(model: str) -> bool:
    """True when the deployment signals Anthropic-on-Vertex for *model*.

    Mirrors the signals ADK's own ``Claude._anthropic_client`` reacts to:

    - a Vertex ``projects/.../locations/.../...`` resource-style model id, or
    - ``GOOGLE_GENAI_USE_VERTEXAI`` set truthy, or
    - both ``GOOGLE_CLOUD_PROJECT`` and ``GOOGLE_CLOUD_LOCATION`` present.

    When none of these hold we assume the direct Anthropic API
    (``ANTHROPIC_API_KEY``), which is magi's deployment posture
    (``GOOGLE_GENAI_USE_VERTEXAI=false`` per the Gate 5B live-smoke config).
    """
    if (model or "").startswith("projects/"):
        return True
    use_vertex = (os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") or "").strip().lower()
    if use_vertex in {"1", "true", "yes", "on"}:
        return True
    project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    location = (os.environ.get("GOOGLE_CLOUD_LOCATION") or "").strip()
    return bool(project and location)


def _select_anthropic_base(model: str) -> type:
    """Pick the ADK Anthropic base class whose client matches the deployment.

    Returns the Vertex :class:`google.adk.models.Claude` when a Vertex signal is
    present (so the inherited ``_anthropic_client`` is ``AsyncAnthropicVertex``),
    otherwise the direct-API :class:`AnthropicLlm` (``AsyncAnthropic``). The
    ``anthropic`` import is paid here, matching ADK's own lazy gating.
    """
    from google.adk.models.anthropic_llm import (  # noqa: PLC0415
        AnthropicLlm,
        Claude,
    )

    return Claude if _has_vertex_signal(model) else AnthropicLlm


def _build_cache_aware_claude_class(base: type) -> type:
    """Build a cache-aware subclass over *base* (``AnthropicLlm`` or ``Claude``).

    The cache-injection logic lives in :class:`_CacheControlMixin`; *base*
    supplies the correct ``_anthropic_client`` (direct vs Vertex). Importing the
    ADK Anthropic module pulls in the optional ``anthropic`` package, so this is
    only called from :func:`build_cache_aware_claude` at construction time.
    """

    class CacheAwareClaude(_build_cache_control_mixin(), base):
        """ADK Anthropic model that injects rolling-tail cache markers.

        Overrides :meth:`generate_content_async` to post-process the outgoing
        Anthropic request: the last ~2 non-system messages get a
        ``cache_control: {type: ephemeral}`` marker before the messages reach
        ``messages.create(...)``. Gated on ``MAGI_MESSAGE_CACHE_ENABLED``; when
        OFF the request matches the parent's exactly. ``_anthropic_client`` is
        inherited from *base*, so the credential path (direct vs Vertex) is
        correct for the deployment.
        """

        # Public marker so callers/tests can assert the cache-aware path is
        # wired. Declared ``ClassVar`` so pydantic (ADK models are pydantic
        # BaseModels) treats it as a plain class attribute, not a model field.
        magi_message_cache_aware: ClassVar[bool] = True

    return CacheAwareClaude


def _build_cache_control_mixin() -> type:
    """Construct the cache-control mixin against the installed ADK module.

    Kept as a builder (not a module-level class) so the ``anthropic``-backed ADK
    import stays lazy: defining the override needs the ADK helper functions in
    scope only when a Claude model is actually built.
    """
    from google.adk.models.anthropic_llm import (  # noqa: PLC0415
        _build_anthropic_thinking_param,
        content_to_message_param,
        function_declaration_to_tool_param,
        message_to_generate_content_response,
    )
    from anthropic import NOT_GIVEN  # noqa: PLC0415
    from anthropic import types as anthropic_types  # noqa: PLC0415

    class _CacheControlMixin:
        """Replicates ADK's build-then-send path with rolling-tail injection.

        ADK's ``generate_content_async`` builds the Anthropic ``messages`` list
        and posts it in a single method, so we cannot wrap ``super()`` to mutate
        the in-flight messages. Instead we mirror the parent method exactly and
        inject the marker into ``messages`` before the create call. The
        streaming branch passes the already-injected ``messages`` into
        ``_generate_content_streaming`` (inherited from the base), so injection
        reaches both branches.
        """

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

    return _CacheControlMixin


def get_cache_aware_claude_class(model: str = "claude-sonnet-4-6") -> type:
    """Return (and memoise) the cache-aware subclass for *model*'s deployment.

    The base class is chosen by :func:`_select_anthropic_base` (direct
    ``AnthropicLlm`` by default; Vertex ``Claude`` when a Vertex signal is
    present) and cached per base so repeat calls are cheap. Raises ``ImportError``
    (from ADK / the ``anthropic`` package) only when the class is first requested,
    matching ADK's own lazy gating.
    """
    base = _select_anthropic_base(model)
    cached = _CACHE_AWARE_CLASSES.get(base.__name__)
    if cached is None:
        cached = _build_cache_aware_claude_class(base)
        _CACHE_AWARE_CLASSES[base.__name__] = cached
    return cached


def build_cache_aware_claude(model: str):
    """Construct a cache-aware ADK Anthropic model instance for *model*.

    This is the seam the live runner boundary calls when a Claude/anthropic
    model id is routed through ADK. The base class (and therefore the
    ``_anthropic_client`` credential path) is selected to match the deployment.
    """
    cls = get_cache_aware_claude_class(model)
    return cls(model=model)


__all__ = [
    "MESSAGE_TAIL_MAX_BREAKPOINTS",
    "build_cache_aware_claude",
    "get_cache_aware_claude_class",
    "inject_message_tail_cache_control",
]
