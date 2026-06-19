from __future__ import annotations

import base64
import os
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

# cua-driver's own window (display name "Cua Driver") must never be the control
# target — launching the driver raises it in front of the user's apps.
_DRIVER_APP_NAMES = {"cua driver", "cuadriver"}

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


def _window_area(window: Mapping[str, object]) -> float:
    bounds = window.get("bounds")
    if not isinstance(bounds, Mapping):
        return 0.0
    width = bounds.get("width", 0)
    height = bounds.get("height", 0)
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        return float(width) * float(height)
    return 0.0


def _matches_app_hint(window: Mapping[str, object], hint: str) -> bool:
    """Case-insensitive substring match against ``app_name`` or ``title``.

    Substring rather than equality so a user can pass ``"chrome"`` and match
    ``"Google Chrome"``, and so localized app names match (e.g. macOS shows
    TextEdit as ``"텍스트 편집기"`` under a Korean locale — passing the bundle
    id stem ``"textedit"`` still works because cua-driver's underlying app
    metadata exposes it; for AppleScript-style scripting names the substring
    catches partial overlaps).
    """
    needle = hint.casefold().strip()
    if not needle:
        return True
    for key in ("app_name", "title"):
        haystack = str(window.get(key, "") or "").casefold()
        if needle in haystack:
            return True
    return False


def _select_window(
    windows: Sequence[Mapping[str, object]],
    *,
    app_hint: str | None = None,
) -> Mapping[str, object]:
    """Pick the control target: the largest on-screen non-driver window.

    When ``app_hint`` is given, narrow the candidate pool to windows whose
    ``app_name``/``title`` match the hint (case-insensitive substring); also
    consider off-screen matches in that case, since cua-driver launches apps
    backgrounded with ``is_on_screen=False`` and we still want to target them.

    Falls back to the largest of whatever is available. ``windows[0]`` is wrong
    in practice — the driver's own window and tiny accessory strips can outrank
    the main content window in z-order.
    """
    non_driver = [
        w
        for w in windows
        if str(w.get("app_name", "")).casefold() not in _DRIVER_APP_NAMES
    ]
    if app_hint:
        hinted = [w for w in non_driver if _matches_app_hint(w, app_hint)]
        pool = hinted or non_driver
    else:
        pool = [w for w in non_driver if w.get("is_on_screen", True)] or non_driver
    pool = pool or list(windows)
    if not pool:
        return {}
    return max(pool, key=_window_area)


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

    async def capture(self, *, app_hint: str | None = None) -> CuaCapture:
        # When an app_hint is given the target may be backgrounded (cua-driver's
        # launch_app sets self_activation_suppressed → is_on_screen=False); ask
        # for all windows so off-screen hint matches are visible.
        on_screen_only = app_hint is None
        windows = _unwrap(
            await self._session.call_tool(  # type: ignore[attr-defined]
                "list_windows", {"on_screen_only": on_screen_only}
            )
        )
        window_list = windows.get("windows")
        entries = window_list if isinstance(window_list, (list, tuple)) else []
        first = _select_window(entries, app_hint=app_hint)
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
