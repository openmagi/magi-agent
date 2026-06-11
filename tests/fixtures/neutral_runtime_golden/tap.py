"""Recorder <-> ControlPlanePlugin tap (pure-observe).

``recording_plane(plane, recorder)`` wraps a real :class:`ControlPlane`'s
fan-out methods so every *final* decision (after fan-out ordering:
first-deny-wins, rewrite-mutates, after-tool-first-non-None) is mirrored into
the recorder. ``recording_tool_error(plugin, recorder)`` wraps the plugin-level
``on_tool_error_callback`` (the edit-retry raise path, which is not a
LoopControl hook). Both are strictly observational: the wrapped callables return
the exact same value as the originals, so control behavior is unchanged.
"""

from __future__ import annotations

import hashlib
from typing import Any

from magi_agent.adk_bridge.control_plane import ControlPlane


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", type(tool).__name__))


def _text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]


def _contents_list(llm_request: Any) -> list | None:
    contents = getattr(llm_request, "contents", None)
    if isinstance(contents, list):
        return contents
    if isinstance(llm_request, dict):
        c = llm_request.get("contents")
        if isinstance(c, list):
            return c
    return None


def _tools_len(llm_request: Any) -> int | None:
    config = getattr(llm_request, "config", None)
    tools = getattr(config, "tools", None)
    if isinstance(tools, list):
        return len(tools)
    return None


def _appended_text(item: Any) -> str:
    """Best-effort text extraction of an appended content item (Content or dict)."""
    if isinstance(item, dict):
        return str(item.get("content", item))
    parts = getattr(item, "parts", None)
    if isinstance(parts, (list, tuple)):
        texts = [
            getattr(p, "text", None)
            for p in parts
            if getattr(p, "text", None)
        ]
        if texts:
            return "".join(texts)
    return str(item)


def recording_plane(plane: ControlPlane, recorder: Any) -> ControlPlane:
    """Wrap ``plane``'s fan-out methods in place to mirror decisions into recorder.

    Returns the same plane (mutated) for convenience. Pure-observe: each wrapped
    method returns exactly what the original returned.
    """
    orig_before_tool = plane._before_tool
    orig_after_tool = plane._after_tool
    orig_before_model = plane._before_model
    orig_after_agent = plane._after_agent

    async def before_tool(*, tool, args, tool_context):
        # Snapshot args BEFORE fan-out (a rewrite mutates args in-place).
        snapshot = dict(args) if isinstance(args, dict) else args
        result = await orig_before_tool(tool=tool, args=args, tool_context=tool_context)
        if result is not None:
            decision = {"action": "deny", "reason": result.get("reason")
                        if isinstance(result, dict) else None}
        elif isinstance(args, dict) and args != snapshot:
            decision = {"action": "rewrite", "reason": None}
        else:
            decision = {"action": "allow", "reason": None}
        recorder.record_before_tool(
            tool_name=_tool_name(tool),
            tool_args=snapshot,
            decision=decision,
        )
        return result

    async def after_tool(*, tool, args, tool_context, result):
        override = await orig_after_tool(
            tool=tool, args=args, tool_context=tool_context, result=result
        )
        recorder.record_after_tool(tool_name=_tool_name(tool), override=override)
        return override

    async def before_model(*, callback_context, llm_request):
        contents = _contents_list(llm_request)
        before_len = len(contents) if contents is not None else None
        tools_before = _tools_len(llm_request)

        result = await orig_before_model(
            callback_context=callback_context, llm_request=llm_request
        )

        contents_after = _contents_list(llm_request)
        after_len = len(contents_after) if contents_after is not None else None
        tools_after = _tools_len(llm_request)

        grew = (
            before_len is not None
            and after_len is not None
            and after_len > before_len
        )
        shrank = (
            before_len is not None
            and after_len is not None
            and after_len < before_len
        )
        tools_cleared = (
            tools_before is not None
            and tools_after is not None
            and tools_after == 0
            and tools_before > 0
        )
        mutated = bool(grew or shrank or tools_cleared)
        recorder.record_before_model(mutated=mutated, tools_cleared=tools_cleared)

        if shrank:
            # Compaction trimmed the context to a recent tail.
            recorder.record_compaction(fired=True, kept_tail=after_len)
        if grew and contents_after is not None:
            # A reminder / wrap-up message was appended (reinject seam).
            appended = contents_after[before_len:]
            for item in appended:
                role = (
                    item.get("role", "user")
                    if isinstance(item, dict)
                    else getattr(item, "role", "user")
                )
                source = "max_steps" if tools_cleared else "ga_constraint"
                recorder.record_reinject(
                    role=str(role),
                    text_digest=_text_digest(_appended_text(item)),
                    source=source,
                )
        return result

    async def after_agent(*, agent, callback_context):
        return await orig_after_agent(agent=agent, callback_context=callback_context)

    plane._before_tool = before_tool  # type: ignore[assignment]
    plane._after_tool = after_tool  # type: ignore[assignment]
    plane._before_model = before_model  # type: ignore[assignment]
    plane._after_agent = after_agent  # type: ignore[assignment]
    return plane


def recording_tool_error(plugin: Any, recorder: Any) -> Any:
    """Wrap ``plugin.on_tool_error_callback`` to mirror the edit-retry decision.

    The edit-retry seam fires when a tool *raises* (gate5b FileEdit ValueError),
    which ADK routes to the plugin-level ``on_tool_error_callback`` rather than
    any LoopControl hook. Pure-observe: returns exactly the original value.
    """
    orig = plugin.on_tool_error_callback

    async def on_tool_error_callback(*, tool, tool_args, tool_context, error):
        override = await orig(
            tool=tool, tool_args=tool_args, tool_context=tool_context, error=error
        )
        recorder.record_tool_error(tool_name=_tool_name(tool), override=override)
        return override

    plugin.on_tool_error_callback = on_tool_error_callback  # type: ignore[assignment]
    return plugin
