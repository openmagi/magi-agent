"""Pure mapper: Gate5B4C3ShadowGenerationRequest → TurnContext.

This module is strictly additive — it imports from existing types and helpers
with no side effects. It does NOT modify any production code in chat_routes.py
or elsewhere.

PR4 notes:
- ``recipe``: left as ``None``. The request carries a ``recipe_profile``
  (``recipeId``, ``toolsPolicy``, ``sourceAuthority``) but ``TurnContext.recipe``
  expects a plain recipe-name string. The gate5b hosted path doesn't route via
  the OSS recipe-selector; leave as ``None`` and handle recipe selection
  in PR4's chat_routes integration if needed.
- ``memory_mode``: fixed to ``"normal"``. The hosted memory_mode header is
  parsed from the HTTP request in chat_routes (PR4 concern); this mapper
  doesn't see the raw headers.
- ``permission_mode``: fixed to ``"default"`` (least-privilege deny/ask).
  The no-op gate from PR1's ``build_hosted_runtime`` short-circuits interactive
  permission prompts anyway; the value is recorded here for auditability.
- ``permission_cap``: ``None`` — hosted enforcement is via gate1a/tools
  allowlist threaded through ``build_hosted_runtime``, not TurnContext.
- ``depth``: ``0`` — this is always a top-level serve turn.
- ``budget_ms``: ``0`` — gate5b4c3 has its own budget envelope on the request;
  those stay in chat_routes middleware, not TurnContext.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from magi_agent.runtime.turn_context import TurnContext
from magi_agent.shadow.gate5b4c3_live_runner_boundary import _shadow_session_id

if TYPE_CHECKING:
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationRequest,
    )


def hosted_request_to_turn_context(
    generation: "Gate5B4C3ShadowGenerationRequest",
    *,
    include_history: bool = True,
) -> TurnContext:
    """Map a validated Gate5B4C3ShadowGenerationRequest to a TurnContext.

    Field mapping:
    - prompt          ← generation.turn.sanitized_current_turn_text
    - session_id      ← _shadow_session_id(generation)  (canonical derivation)
    - turn_id         ← generation.turn.turn_id
    - provider        ← generation.model_routing.provider_label
    - model           ← generation.model_routing.model_label
    - initial_messages← generation.turn.sanitized_recent_history converted to
                        tuple of {"role": ..., "content": ...} dicts, OR ``()``
                        when ``include_history`` is False (seed-on-empty, U4)
    - recipe          ← None  (see module docstring)
    - memory_mode     ← "normal"  (header parsing is a PR4 chat_routes concern)
    - permission_mode ← "default"  (no-op gate from PR1 short-circuits anyway)
    - permission_cap  ← None  (hosted enforcement is via gate1a tools allowlist)
    - depth           ← 0  (top-level serve turn)
    - budget_ms       ← 0  (gate5b4c3 budgets stay in chat_routes middleware)

    ``include_history`` (U4 / B2 seed-on-empty): when False the sanitized recent
    history is NOT mapped into ``initial_messages`` (left ``()``), so the driver
    renders no resume prefix. The hosted serving seam sets this to False once the
    durable ADK session already holds the prior turns, preventing the #1364
    double-seed (history via persisted session events AND via an inline resume
    prefix). The default is True so any other caller/test stays byte-identical.
    """
    initial_messages: tuple[dict[str, str], ...] = (
        tuple(
            {"role": msg.role, "content": msg.sanitized_text}
            for msg in generation.turn.sanitized_recent_history
        )
        if include_history
        else ()
    )
    # U5 (B1): thread image blocks from the generation into TurnContext so the
    # driver's _build_opening_parts can emit them as ADK Part.from_bytes parts.
    # Shape mirrors the legacy boundary at gate5b4c3:1497-1506: each element is
    # the converter-dict that image_blocks_to_parts expects.
    # ``getattr`` fallback: production Gate5B4C3ShadowGenerationTurn always has
    # ``sanitized_image_blocks`` (Pydantic default ``()``), but lightweight test
    # fakes (SimpleNamespace) may omit it.
    _raw_image_blocks = getattr(generation.turn, "sanitized_image_blocks", ()) or ()
    image_blocks: tuple[dict[str, object], ...] = tuple(
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": b.media_type,
                "data": b.data,
            },
        }
        for b in _raw_image_blocks
    )
    return TurnContext(
        prompt=generation.turn.sanitized_current_turn_text,
        session_id=_shadow_session_id(generation),
        turn_id=generation.turn.turn_id,
        provider=generation.model_routing.provider_label,
        model=generation.model_routing.model_label,
        initial_messages=initial_messages,
        image_blocks=image_blocks,
        recipe=None,
        memory_mode="normal",
        permission_mode="default",
        permission_cap=None,
        depth=0,
        budget_ms=0,
    )


__all__ = ["hosted_request_to_turn_context"]
