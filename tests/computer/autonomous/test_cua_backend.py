import base64
import os

import pytest

from magi_agent.computer.autonomous.cua_backend import (
    CuaCapture,
    CuaDriverBackend,
    _select_window,
)
from magi_agent.computer.autonomous.cua_pure import UIElement


def _win(app: str, wid: int, w: int, h: int, on_screen: bool = True) -> dict:
    return {
        "app_name": app,
        "pid": 1,
        "window_id": wid,
        "is_on_screen": on_screen,
        "bounds": {"width": w, "height": h},
    }


def test_select_window_prefers_largest_non_driver() -> None:
    # Driver window is largest but must be skipped; among the rest pick by area,
    # not list order (the small accessory strip comes first).
    windows = [
        _win("Cua Driver", 100, 1400, 900),
        _win("Code", 101, 1500, 32),
        _win("Code", 102, 1500, 900),
    ]
    assert _select_window(windows)["window_id"] == 102


def test_select_window_falls_back_to_driver_only() -> None:
    windows = [_win("Cua Driver", 100, 100, 100)]
    assert _select_window(windows)["window_id"] == 100


def test_select_window_empty() -> None:
    assert _select_window([]) == {}


def test_select_window_honors_app_hint_substring() -> None:
    windows = [
        _win("Cua Driver", 100, 1400, 900),
        _win("Code", 101, 1500, 900),  # bigger but not the hinted one
        _win("Google Chrome", 102, 1000, 700),
    ]
    assert _select_window(windows, app_hint="chrome")["window_id"] == 102


def test_select_window_app_hint_matches_localized_title() -> None:
    # Korean-locale macOS reports TextEdit as "텍스트 편집기"; we also want a
    # title substring to match.
    windows = [
        _win("Code", 101, 1500, 900),
        {**_win("텍스트 편집기", 102, 500, 500), "title": "Untitled"},
    ]
    assert _select_window(windows, app_hint="텍스트")["window_id"] == 102
    assert _select_window(windows, app_hint="untitled")["window_id"] == 102


def test_select_window_app_hint_accepts_offscreen() -> None:
    # cua-driver launches apps backgrounded (is_on_screen=False); the hint path
    # must still pick them up.
    windows = [
        _win("Code", 101, 1500, 900, on_screen=True),
        _win("TextEdit", 102, 600, 500, on_screen=False),
    ]
    assert _select_window(windows, app_hint="textedit")["window_id"] == 102


def test_select_window_app_hint_no_match_falls_back() -> None:
    windows = [
        _win("Cua Driver", 100, 1400, 900),
        _win("Code", 101, 1500, 900),
    ]
    # Nothing matches "chrome"; fall back to the largest non-driver window.
    assert _select_window(windows, app_hint="chrome")["window_id"] == 101

# Real cua-driver 0.5.7 get_window_state returns `tree_markdown` (not `data`) and
# writes the PNG to `screenshot_out_file` (no inline `screenshot_b64`).
_PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKE"


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        if name == "list_windows":
            return {"windows": [{"pid": 42, "window_id": 7}]}
        if name == "get_window_state":
            out = args.get("screenshot_out_file")
            if out:
                with open(out, "wb") as handle:
                    handle.write(_PNG_BYTES)
            return {
                "pid": 42,
                "window_id": 7,
                "element_count": 1,
                "tree_markdown": '- [1] AXButton "OK" [actions=[press]]',
            }
        return {"ok": True}


@pytest.mark.asyncio
async def test_capture_returns_parsed_state() -> None:
    session = _FakeSession()
    backend = CuaDriverBackend(session=session)
    cap = await backend.capture()
    assert isinstance(cap, CuaCapture)
    assert cap.pid == 42
    assert cap.window_id == 7
    assert cap.ax_tree == '- [1] AXButton "OK" [actions=[press]]'
    assert cap.screenshot_b64 == base64.b64encode(_PNG_BYTES).decode("ascii")
    assert cap.elements == [UIElement(index=1, role="AXButton", label="OK")]


@pytest.mark.asyncio
async def test_capture_does_not_persist_screenshot() -> None:
    session = _FakeSession()
    await CuaDriverBackend(session=session).capture()
    gws_args = next(a for n, a in session.calls if n == "get_window_state")
    assert not os.path.exists(gws_args["screenshot_out_file"])


@pytest.mark.asyncio
async def test_capture_with_app_hint_widens_list_windows_and_selects_match() -> None:
    class _HintSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def call_tool(self, name: str, args: dict) -> dict:
            self.calls.append((name, args))
            if name == "list_windows":
                return {
                    "windows": [
                        {"app_name": "Code", "pid": 1, "window_id": 100,
                         "bounds": {"width": 1500, "height": 900}, "is_on_screen": True},
                        {"app_name": "TextEdit", "pid": 2, "window_id": 200,
                         "bounds": {"width": 500, "height": 500}, "is_on_screen": False},
                    ]
                }
            if name == "get_window_state":
                out = args.get("screenshot_out_file")
                if out:
                    with open(out, "wb") as handle:
                        handle.write(_PNG_BYTES)
                return {
                    "pid": args["pid"], "window_id": args["window_id"],
                    "element_count": 0, "tree_markdown": "",
                }
            return {}

    session = _HintSession()
    cap = await CuaDriverBackend(session=session).capture(app_hint="textedit")
    assert cap.pid == 2 and cap.window_id == 200  # picked the hinted off-screen window
    lw_args = next(a for n, a in session.calls if n == "list_windows")
    assert lw_args == {"on_screen_only": False}  # widened when hint is set


@pytest.mark.asyncio
async def test_dispatch_translates_and_calls_tool() -> None:
    session = _FakeSession()
    backend = CuaDriverBackend(session=session)
    await backend.dispatch({"action": "click", "element_index": 1}, pid=42, window_id=7)
    assert ("click", {"pid": 42, "window_id": 7, "element_index": 1}) in session.calls
