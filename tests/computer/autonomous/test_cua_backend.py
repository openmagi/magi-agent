import pytest

from magi_agent.computer.autonomous.cua_backend import CuaCapture, CuaDriverBackend
from magi_agent.computer.autonomous.cua_pure import UIElement


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        if name == "list_windows":
            return {"windows": [{"pid": 42, "window_id": 7}]}
        if name == "get_window_state":
            return {
                "pid": 42,
                "window_id": 7,
                "screenshot_b64": "QUJD",
                "data": '[element_index 1] AXButton "OK"',
            }
        return {"ok": True}


@pytest.mark.asyncio
async def test_capture_returns_parsed_state() -> None:
    backend = CuaDriverBackend(session=_FakeSession())
    cap = await backend.capture()
    assert isinstance(cap, CuaCapture)
    assert cap.pid == 42
    assert cap.window_id == 7
    assert cap.screenshot_b64 == "QUJD"
    assert cap.elements == [UIElement(index=1, role="AXButton", label="OK")]


@pytest.mark.asyncio
async def test_dispatch_translates_and_calls_tool() -> None:
    session = _FakeSession()
    backend = CuaDriverBackend(session=session)
    await backend.dispatch({"action": "click", "element_index": 1}, pid=42, window_id=7)
    assert ("click", {"pid": 42, "window_id": 7, "element_index": 1}) in session.calls
