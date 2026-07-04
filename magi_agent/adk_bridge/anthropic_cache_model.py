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
Claude does NOT yet flow through this ADK model path by default. This is NOT
dormant infra: it is wired into the model class that WILL be used the moment a
Claude/anthropic model id is routed through ADK
(:func:`magi_agent.shadow.gate5b4c3_live_runner_boundary` resolves the model),
and both the message-tail and the system-prefix injection are proven by tests.

Two independent caching seams live here:

1. The rolling message tail (``MAGI_MESSAGE_CACHE_ENABLED``, default-ON full
   profile) marks the last ~2 non-system messages.
2. The static system-prompt prefix (``MAGI_PROMPT_CACHE_ENABLED``, profile-aware
   default-ON) marks one block at the boundary between the static prefix and the
   per-turn dynamic tail (see :func:`inject_system_prefix_cache_control`). Its
   response usage is recorded through :func:`get_prompt_cache_metrics` and logged
   as a ``prompt_cache_usage`` INFO record on the non-stream branch.

Breakpoint budget: system prefix 1 + message tail up to 2 = at most 3, within
Anthropic's 4-breakpoint ceiling. The streaming branch cannot record cache
counters because the ADK base streaming helper only surfaces ``input_tokens`` /
``output_tokens`` from usage; system-prefix caching still applies on the wire,
only the metric is a non-stream-only v1 limitation. A custom ``MAGI_LLM_API_BASE``
makes the whole cache-aware model inert (see ``runtime.model_factory``), so both
flags are no-ops behind a gateway.

Design notes
------------
- Anthropic-only. The last ``tail_size`` (default 2) non-system messages get a
  marker on their final content block; never more than
  :data:`MESSAGE_TAIL_MAX_BREAKPOINTS` (2) new breakpoints, so combined with the
  single system-prefix breakpoint the request stays within Anthropic's
  4-breakpoint ceiling.
- Message-tail marking is gated on ``MAGI_MESSAGE_CACHE_ENABLED`` (default-ON
  full profile) via :func:`magi_agent.config.env.is_message_cache_enabled`;
  system-prefix marking is gated on ``MAGI_PROMPT_CACHE_ENABLED``. With both OFF
  the request is byte-identical to default ADK behaviour.
- The ``anthropic`` package is an OPTIONAL extra and is imported lazily by ADK
  itself; this module never imports it directly, so environments without
  ``anthropic`` are unaffected until a Claude model is actually constructed.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import os
from typing import Any, ClassVar

from magi_agent.config.env import is_message_cache_enabled

_LOGGER = logging.getLogger(__name__)

# Anthropic accepts at most 4 cache breakpoints per request. The system prefix
# may already reserve up to 2 (see prompt.injection), so the rolling
# conversation tail may add at most 2 more.
MESSAGE_TAIL_MAX_BREAKPOINTS = 2

_EPHEMERAL_CACHE_CONTROL = {"type": "ephemeral"}

# Literal boundary marker between the static system-prompt prefix and the
# per-turn dynamic tail. It is a copy of
# ``magi_agent.runtime.message_builder.PROMPT_DYNAMIC_BOUNDARY`` kept here to
# avoid a module-level adk_bridge -> runtime import; the two are pinned equal by
# ``tests/test_anthropic_cache_model.py::TestInjectSystemPrefixCacheControl``.
PROMPT_DYNAMIC_BOUNDARY = "__MAGI_PROMPT_DYNAMIC_BOUNDARY__"

# Module-scope singleton PromptCacheMetrics for the live ADK Anthropic path.
# Built lazily so ``prompt.metrics`` is only imported when a Claude request is
# actually served. ``reset_prompt_cache_metrics`` exists for test isolation.
_PROMPT_CACHE_METRICS: Any = None


def get_prompt_cache_metrics() -> Any:
    """Return the process-wide ``PromptCacheMetrics`` singleton (lazy)."""
    global _PROMPT_CACHE_METRICS
    if _PROMPT_CACHE_METRICS is None:
        from magi_agent.prompt.metrics import PromptCacheMetrics  # noqa: PLC0415

        _PROMPT_CACHE_METRICS = PromptCacheMetrics()
    return _PROMPT_CACHE_METRICS


def reset_prompt_cache_metrics() -> None:
    """Drop the singleton so the next accessor rebuilds a fresh instance."""
    global _PROMPT_CACHE_METRICS
    _PROMPT_CACHE_METRICS = None


def inject_system_prefix_cache_control(system: object) -> object:
    """Return *system* rewritten so the static prompt prefix carries a marker.

    The Anthropic Messages API ``system`` parameter accepts either a plain
    ``str`` or an iterable of ``TextBlockParam`` dicts. When *system* is a
    non-empty string this splits it at :data:`PROMPT_DYNAMIC_BOUNDARY`:

    - block 1 is the static prefix (up to and including the boundary literal)
      and carries ``cache_control: {"type": "ephemeral"}``;
    - block 2 is the per-turn dynamic tail and is left unmarked (omitted when
      empty).

    When the boundary is absent the whole string becomes a single marked block.
    Anything that is not a non-empty ``str`` (``None``, an already-built list, a
    non-string) is returned unchanged so callers can no-op safely.

    Invariant: concatenating the ``text`` of the returned blocks reproduces the
    original string byte-for-byte, so the model-visible prompt is unchanged.
    """
    if not isinstance(system, str) or not system:
        return system
    idx = system.find(PROMPT_DYNAMIC_BOUNDARY)
    if idx == -1:
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": dict(_EPHEMERAL_CACHE_CONTROL),
            }
        ]
    cut = idx + len(PROMPT_DYNAMIC_BOUNDARY)
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system[:cut],
            "cache_control": dict(_EPHEMERAL_CACHE_CONTROL),
        }
    ]
    tail = system[cut:]
    if tail:
        blocks.append({"type": "text", "text": tail})
    return blocks

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
        _ThinkingAccumulator,
        _ToolUseAccumulator,
        _build_anthropic_thinking_param,
        function_declaration_to_tool_param,
        message_to_generate_content_response,
    )
    from google.adk.models.llm_response import LlmResponse  # noqa: PLC0415
    from google.genai import types as genai_types  # noqa: PLC0415
    from anthropic import NOT_GIVEN  # noqa: PLC0415
    from anthropic import types as anthropic_types  # noqa: PLC0415

    from magi_agent.adk_bridge.anthropic_part_sanitizer import (  # noqa: PLC0415
        safe_contents_to_message_params,
    )

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

        @staticmethod
        def _thinking_enabled_for_request(llm_request) -> bool:
            """Whether the outgoing request client-enables Anthropic thinking.

            Mirrors the truthiness of ``_build_anthropic_thinking_param``: thinking
            is enabled only when ``config.thinking_config`` is present with a
            positive ``thinking_budget``. Magi never sets ``thinking_config`` on the
            ADK native path, so this is False today; the sanitizer then strips
            thought-bearing parts (whose signatures are already unrecoverable due
            to the streaming ``SignatureDelta`` drop). Derived from the SAME config
            the thinking param reads so the two stay consistent.
            """
            config = getattr(llm_request, "config", None)
            thinking_config = getattr(config, "thinking_config", None) if config else None
            if thinking_config is None:
                return False
            budget = getattr(thinking_config, "thinking_budget", None)
            return isinstance(budget, int) and budget > 0

        def _maybe_mark_system_prefix(self, llm_request):
            """Mark the static system-prompt prefix when prompt caching is ON.

            Gated on ``MAGI_PROMPT_CACHE_ENABLED`` (profile-aware default-ON) via
            :func:`magi_agent.prompt.metrics.load_cache_config`. Returns a
            ``model_copy`` with the rewritten ``system_instruction`` so both the
            non-stream ``create`` call (which reads
            ``llm_request.config.system_instruction`` directly) and the streaming
            branch (whose ADK base helper reads the same field) are covered from
            one place. The original request is never mutated.
            """
            from magi_agent.prompt.metrics import load_cache_config  # noqa: PLC0415

            enabled, provider = load_cache_config()
            if not enabled or provider not in ("auto", "anthropic"):
                return llm_request
            config = getattr(llm_request, "config", None)
            system = (
                getattr(config, "system_instruction", None) if config else None
            )
            marked = inject_system_prefix_cache_control(system)
            if marked is system:
                return llm_request
            return llm_request.model_copy(
                update={
                    "config": config.model_copy(
                        update={"system_instruction": marked}
                    )
                }
            )

        def _record_prompt_cache_usage(self, message: Any) -> None:
            """Record prompt-cache usage from a non-stream Anthropic response.

            Best-effort: never lets a metrics/logging failure abort the request.
            The ADK streaming helper drops the cache counters, so this only fires
            on the non-stream branch (a documented v1 limitation).
            """
            try:
                usage = getattr(message, "usage", None)
                if usage is None:
                    return
                usage_dict = (
                    usage.model_dump()
                    if hasattr(usage, "model_dump")
                    else dict(usage)
                )
                get_prompt_cache_metrics().record_api_usage(usage_dict)
                _LOGGER.info(
                    "prompt_cache_usage cache_read_input_tokens=%s "
                    "cache_creation_input_tokens=%s input_tokens=%s",
                    usage_dict.get("cache_read_input_tokens", 0),
                    usage_dict.get("cache_creation_input_tokens", 0),
                    usage_dict.get("input_tokens", 0),
                )
            except Exception:  # noqa: BLE001 - metrics must never break a turn
                pass

        async def generate_content_async(self, llm_request, stream: bool = False):
            llm_request = self._maybe_mark_system_prefix(llm_request)
            model_to_use = self._resolve_model_name(llm_request.model)
            # Sanitize BEFORE ADK's part_to_message_block: Sonnet 5 adaptive
            # thinking emits signature-only / empty-thinking parts that ADK 1.33
            # raises NotImplementedError on (line 265). safe_contents_... drops
            # those (and, while thinking is disabled, all thought-bearing parts)
            # with a structured warning, and delegates convertible parts to ADK
            # unchanged. One seam covers both the non-stream and stream branches
            # (streaming receives this already-built ``messages`` list) and both
            # the local and hosted surfaces (both construct CacheAwareClaude).
            messages = safe_contents_to_message_params(
                llm_request.contents,
                thinking_enabled=self._thinking_enabled_for_request(llm_request),
            )
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
                self._record_prompt_cache_usage(message)
                yield message_to_generate_content_response(message)
            else:
                async for response in self._generate_content_streaming(
                    llm_request, messages, tools, tool_choice, thinking
                ):
                    yield response

        async def _generate_content_streaming(
            self, llm_request, messages, tools, tool_choice, thinking=NOT_GIVEN
        ):
            """Streaming helper mirroring ADK 1.33.0 with SignatureDelta capture.

            ADK's ``AnthropicLlm._generate_content_streaming`` handles
            ``ThinkingDelta`` / ``TextDelta`` / ``InputJSONDelta`` but SILENTLY
            DROPS ``anthropic.types.SignatureDelta`` (present in anthropic
            0.116.0). A streamed thinking block therefore aggregates to
            ``Part(text=<thinking>, thought=True)`` with NO signature; a
            signature-only interleaved thinking block collapses to the empty-
            thinking shape that ADK's ``part_to_message_block`` raises on next
            turn. This override adds the ``SignatureDelta`` branch (so streamed
            thinking parts round-trip their signature and become convertible)
            plus a warning for unknown ``content_block_start`` types. Everything
            else mirrors ADK exactly, so non-thinking streams are unchanged.

            Version-coupled to ADK 1.33.0. Remove once upstream ADK captures
            SignatureDelta (see google/adk-python, ref adk-python#6195).
            """
            model_to_use = self._resolve_model_name(llm_request.model)
            raw_stream = await self._anthropic_client.messages.create(
                model=model_to_use,
                system=llm_request.config.system_instruction,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=self.max_tokens,
                stream=True,
                thinking=thinking,
            )

            text_blocks: dict[int, str] = {}
            tool_use_blocks: dict[int, _ToolUseAccumulator] = {}
            thinking_blocks: dict[int, _ThinkingAccumulator] = {}
            redacted_thinking_blocks: dict[int, str] = {}
            input_tokens = 0
            output_tokens = 0

            async for event in raw_stream:
                if event.type == "message_start":
                    input_tokens = event.message.usage.input_tokens
                    output_tokens = event.message.usage.output_tokens
                elif event.type == "content_block_start":
                    block = event.content_block
                    if isinstance(block, anthropic_types.ThinkingBlock):
                        thinking_blocks[event.index] = _ThinkingAccumulator(
                            thinking=block.thinking,
                            signature=block.signature,
                        )
                    elif isinstance(
                        block, anthropic_types.RedactedThinkingBlock
                    ):
                        redacted_thinking_blocks[event.index] = block.data
                    elif isinstance(block, anthropic_types.TextBlock):
                        text_blocks[event.index] = block.text
                    elif isinstance(block, anthropic_types.ToolUseBlock):
                        tool_use_blocks[event.index] = _ToolUseAccumulator(
                            id=block.id,
                            name=block.name,
                            args_json="",
                        )
                    else:
                        _LOGGER.warning(
                            "anthropic_stream unknown_content_block type=%s "
                            "index=%s",
                            type(block).__name__,
                            event.index,
                        )
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if isinstance(delta, anthropic_types.ThinkingDelta):
                        thinking_blocks.setdefault(
                            event.index,
                            _ThinkingAccumulator(thinking="", signature=""),
                        )
                        thinking_blocks[event.index].thinking += delta.thinking
                        yield LlmResponse(
                            content=genai_types.Content(
                                role="model",
                                parts=[
                                    genai_types.Part(
                                        text=delta.thinking, thought=True
                                    )
                                ],
                            ),
                            partial=True,
                        )
                    elif isinstance(delta, anthropic_types.SignatureDelta):
                        # The fix: accumulate the thinking-block signature that
                        # ADK 1.33.0 drops, so the final aggregated part carries
                        # thought_signature and is round-trippable / convertible.
                        thinking_blocks.setdefault(
                            event.index,
                            _ThinkingAccumulator(thinking="", signature=""),
                        )
                        thinking_blocks[event.index].signature += delta.signature
                    elif isinstance(delta, anthropic_types.TextDelta):
                        text_blocks.setdefault(event.index, "")
                        text_blocks[event.index] += delta.text
                        yield LlmResponse(
                            content=genai_types.Content(
                                role="model",
                                parts=[
                                    genai_types.Part.from_text(text=delta.text)
                                ],
                            ),
                            partial=True,
                        )
                    elif isinstance(delta, anthropic_types.InputJSONDelta):
                        if event.index in tool_use_blocks:
                            tool_use_blocks[
                                event.index
                            ].args_json += delta.partial_json
                elif event.type == "message_delta":
                    output_tokens = event.usage.output_tokens

            all_parts: list[Any] = []
            all_indices = sorted(
                set(
                    list(thinking_blocks.keys())
                    + list(redacted_thinking_blocks.keys())
                    + list(text_blocks.keys())
                    + list(tool_use_blocks.keys())
                )
            )
            for idx in all_indices:
                if idx in thinking_blocks:
                    acc = thinking_blocks[idx]
                    part = genai_types.Part(text=acc.thinking, thought=True)
                    if acc.signature:
                        part.thought_signature = acc.signature.encode("utf-8")
                    all_parts.append(part)
                if idx in redacted_thinking_blocks:
                    all_parts.append(
                        genai_types.Part(
                            thought=True,
                            thought_signature=redacted_thinking_blocks[
                                idx
                            ].encode("utf-8"),
                        )
                    )
                if idx in text_blocks:
                    all_parts.append(
                        genai_types.Part.from_text(text=text_blocks[idx])
                    )
                if idx in tool_use_blocks:
                    acc = tool_use_blocks[idx]
                    args = json.loads(acc.args_json) if acc.args_json else {}
                    part = genai_types.Part.from_function_call(
                        name=acc.name, args=args
                    )
                    part.function_call.id = acc.id
                    all_parts.append(part)

            yield LlmResponse(
                content=genai_types.Content(role="model", parts=all_parts),
                usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=input_tokens,
                    candidates_token_count=output_tokens,
                    total_token_count=input_tokens + output_tokens,
                ),
                partial=False,
            )

    return _CacheControlMixin


def get_cache_aware_claude_class(model: str = "claude-sonnet-5") -> type:
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
    "PROMPT_DYNAMIC_BOUNDARY",
    "build_cache_aware_claude",
    "get_cache_aware_claude_class",
    "get_prompt_cache_metrics",
    "inject_message_tail_cache_control",
    "inject_system_prefix_cache_control",
    "reset_prompt_cache_metrics",
]
