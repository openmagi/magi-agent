import base64
import os

import pytest

from magi_agent.computer.autonomous.cua_backend import CuaCapture, CuaDriverBackend
from magi_agent.computer.autonomous.cua_pure import UIElement

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
async def test_dispatch_translates_and_calls_tool() -> None:
    session = _FakeSession()
    backend = CuaDriverBackend(session=session)
    await backend.dispatch({"action": "click", "element_index": 1}, pid=42, window_id=7)
    assert ("click", {"pid": 42, "window_id": 7, "element_index": 1}) in session.calls
