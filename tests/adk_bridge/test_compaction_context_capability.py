"""S-D: compaction decision narrowed behind one context capability.

``_CompactionLoopControl`` (and a user pack authoring an equivalent control)
must reach the compaction decision through ``ctx.compaction`` — a narrow
``CompactionCapability`` — rather than the privileged ``ContextLifecycleBoundary``
+ ``WorkspaceSessionService`` plumbing baked into ``MagiContextCompactionPlugin``.

Behavior must be byte-identical to the pre-migration ``before_model_callback``:
over-budget contents are trimmed to the recent tail (with orphan widening), and
the boundary/session services stay encapsulated behind the capability.
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.context_compaction import (
    CompactionCapability,
    build_context_compaction_plugin,
)
from magi_agent.packs.context import ControlPlaneContext


class _Part:
    def __init__(self, text):
        self.text = text
        self.function_response = None
        self.function_call = None


class _Content:
    def __init__(self, text, role="user"):
        self.parts = [_Part(text)]
        self.role = role


class _Req:
    def __init__(self, n):
        self.contents = [_Content(f"msg {i}" * 50) for i in range(n)]


def test_compaction_capability_trims_contents_when_over_budget():
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=1, tail_events=2
    )
    assert plugin is not None
    cap = CompactionCapability(plugin)
    req = _Req(10)
    ctx = ControlPlaneContext.minimal(compaction=cap)
    asyncio.run(plugin.apply_before_model(ctx, llm_request=req))
    assert len(req.contents) <= 4  # trimmed toward the tail (orphan widening allowed)


def test_apply_before_model_falls_back_to_own_capability_when_ctx_has_none():
    # No capability on the context -> the plugin uses its own (the legacy path),
    # so behavior is identical to before_model_callback.
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=1, tail_events=2
    )
    assert plugin is not None
    req = _Req(10)
    ctx = ControlPlaneContext.minimal(compaction=None)
    asyncio.run(plugin.apply_before_model(ctx, llm_request=req))
    assert len(req.contents) <= 4


def test_capability_trim_matches_legacy_before_model_callback():
    # The capability's trim and the legacy ADK callback produce the same result.
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=1, tail_events=2
    )
    assert plugin is not None
    via_callback = _Req(10)
    asyncio.run(
        plugin.before_model_callback(callback_context=None, llm_request=via_callback)
    )
    via_capability = _Req(10)
    asyncio.run(CompactionCapability(plugin).trim(via_capability))
    assert len(via_callback.contents) == len(via_capability.contents)
