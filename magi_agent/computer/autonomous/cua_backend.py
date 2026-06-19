from __future__ import annotations

import base64
import os
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from magi_agent.computer.autonomous.cua_pure import (
    UIElement,
    action_to_cua_call,
    parse_window_state,
)


@dataclass(frozen=True)
class CuaCapture:
    pid: int
    window_id: int
    screenshot_b64: str
    ax_tree: str
    elements: list[UIElement]


def _as_int(value: object, default: int) -> int:
    """Coerce an untyped tool-result value to int, falling back on default."""
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _unwrap(result: object) -> Mapping[str, object]:
    """Normalize a tool result to a mapping.

    Fakes (and tests) return a plain mapping. The real ``mcp`` ``ClientSession``
    returns a ``CallToolResult`` whose ``structuredContent`` carries cua-driver's
    JSON payload. Verified against cua-driver 0.5.7: ``list_apps`` →
    ``{"apps": ...}``, ``get_window_state`` → ``{"tree_markdown", "pid",
    "window_id", "element_count"}``.
    """
    if isinstance(result, Mapping):
        return result
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, Mapping):
        return structured
    return {}


def _read_png_b64(path: str) -> str:
    """Base64-encode a PNG written by cua-driver, or "" if absent."""
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")


class CuaDriverBackend:
    """Async client over a cua-driver MCP stdio session.

    ``session`` is any object exposing ``async call_tool(name, args)``; in
    production it is an ``mcp`` ``ClientSession``, in tests a fake. Lifecycle is
    owned by the ``session()`` async-context manager, NOT by this class or the
    engine — anyio cancel scopes must be entered and exited in the same frame.
    """

    def __init__(self, *, session: object) -> None:
        self._session = session

    @classmethod
    @asynccontextmanager
    async def session(cls) -> "AsyncIterator[CuaDriverBackend]":  # pragma: no cover - needs the real binary
        """Start ``cua-driver mcp`` over stdio and yield a backend bound to it.

        Both anyio-backed contexts (``stdio_client`` and ``ClientSession``) are
        entered and exited inside THIS frame via ``async with`` — never split
        across ``spawn``/``aclose`` (that trips anyio's cancel-scope identity).
        """
        from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
        from mcp.client.stdio import stdio_client  # noqa: PLC0415

        params = StdioServerParameters(command="cua-driver", args=["mcp"])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as sess:
            await sess.initialize()
            yield cls(session=sess)

    async def capture(self) -> CuaCapture:
        windows = _unwrap(
            await self._session.call_tool("list_windows", {"on_screen_only": True})  # type: ignore[attr-defined]
        )
        window_list = windows.get("windows")
        entries = window_list if isinstance(window_list, (list, tuple)) else []
        first: Mapping[str, object] = entries[0] if entries else {}
        pid = _as_int(first.get("pid", 0), 0)
        window_id = _as_int(first.get("window_id", 0), 0)

        # cua-driver embeds the PNG as base64 by default; with screenshot_out_file
        # it instead writes the file and returns screenshot_file_path. We take the
        # file path (smaller responses) and read+delete it — screenshots are never
        # persisted to disk (visual-secret hygiene).
        shot_fd, shot_path = tempfile.mkstemp(suffix=".png", prefix="magi-cua-")
        os.close(shot_fd)
        screenshot_path = shot_path
        try:
            state = _unwrap(
                await self._session.call_tool(  # type: ignore[attr-defined]
                    "get_window_state",
                    {
                        "pid": pid,
                        "window_id": window_id,
                        "capture_mode": "som",
                        "screenshot_out_file": shot_path,
                    },
                )
            )
            ax_tree = str(state.get("tree_markdown", ""))
            screenshot_path = str(state.get("screenshot_file_path") or shot_path)
            screenshot_b64 = _read_png_b64(screenshot_path)
        finally:
            for leftover in {shot_path, screenshot_path}:
                if leftover and os.path.exists(leftover):
                    os.unlink(leftover)

        return CuaCapture(
            pid=_as_int(state.get("pid", pid), pid),
            window_id=_as_int(state.get("window_id", window_id), window_id),
            screenshot_b64=screenshot_b64,
            ax_tree=ax_tree,
            elements=parse_window_state(ax_tree),
        )

    async def dispatch(self, action: Mapping[str, object], *, pid: int, window_id: int) -> None:
        name, args = action_to_cua_call(action, pid=pid, window_id=window_id)
        await self._session.call_tool(name, args)  # type: ignore[attr-defined]
