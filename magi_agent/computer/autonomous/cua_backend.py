from __future__ import annotations

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
    returns a ``CallToolResult``; its ``structuredContent`` carries cua-driver's
    JSON payload. Confirm this shape against the live binary before flag-on.
    """
    if isinstance(result, Mapping):
        return result
    structured = getattr(result, "structuredContent", None)  # pragma: no cover
    if isinstance(structured, Mapping):  # pragma: no cover
        return structured
    return {}  # pragma: no cover


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
        state = _unwrap(
            await self._session.call_tool(  # type: ignore[attr-defined]
                "get_window_state",
                {"pid": pid, "window_id": window_id, "capture_mode": "som"},
            )
        )
        ax_tree = str(state.get("data", ""))
        return CuaCapture(
            pid=_as_int(state.get("pid", pid), pid),
            window_id=_as_int(state.get("window_id", window_id), window_id),
            screenshot_b64=str(state.get("screenshot_b64", "")),
            ax_tree=ax_tree,
            elements=parse_window_state(ax_tree),
        )

    async def dispatch(self, action: Mapping[str, object], *, pid: int, window_id: int) -> None:
        name, args = action_to_cua_call(action, pid=pid, window_id=window_id)
        await self._session.call_tool(name, args)  # type: ignore[attr-defined]
