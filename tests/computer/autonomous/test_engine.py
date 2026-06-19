import pytest

from magi_agent.computer.autonomous.cua_backend import CuaCapture
from magi_agent.computer.autonomous.engine import ComputerEngine, ComputerRunResult


class _FakeBackend:
    def __init__(self) -> None:
        self.dispatched: list[dict] = []

    async def capture(self) -> CuaCapture:
        return CuaCapture(
            pid=1, window_id=2, screenshot_b64="QUJD",
            ax_tree='[element_index 1] AXButton "OK"', elements=[],
        )

    async def dispatch(self, action, *, pid, window_id) -> None:
        self.dispatched.append(dict(action))


def _scripted_chat(replies):
    seq = iter(replies)

    async def _step(_messages):
        return next(seq)

    return _step


async def _allow(_desc: str) -> bool:
    return True


async def _deny(_desc: str) -> bool:
    return False


@pytest.mark.asyncio
async def test_done_returns_ok() -> None:
    backend = _FakeBackend()
    engine = ComputerEngine(
        backend=backend,
        chat_step=_scripted_chat(['{"action": "done", "summary": "all set"}']),
        consent=_allow,
    )
    result = await engine.run(task="do nothing", max_steps=5)
    assert isinstance(result, ComputerRunResult)
    assert result.status == "ok"
    assert result.summary == "all set"


@pytest.mark.asyncio
async def test_benign_click_then_done() -> None:
    backend = _FakeBackend()
    engine = ComputerEngine(
        backend=backend,
        chat_step=_scripted_chat(
            ['{"action": "click", "element_index": 1}', '{"action": "done", "summary": "ok"}']
        ),
        consent=_deny,  # deny must NOT matter for a benign click
    )
    result = await engine.run(task="click ok", max_steps=5)
    assert result.status == "ok"
    assert backend.dispatched == [{"action": "click", "element_index": 1}]


@pytest.mark.asyncio
async def test_sensitive_action_denied_is_skipped_not_dispatched() -> None:
    backend = _FakeBackend()
    engine = ComputerEngine(
        backend=backend,
        chat_step=_scripted_chat(
            [
                '{"action": "type", "text": "hunter2", "target": "password field"}',
                '{"action": "done", "summary": "stopped"}',
            ]
        ),
        consent=_deny,
    )
    result = await engine.run(task="type password", max_steps=5)
    assert result.status == "ok"
    assert backend.dispatched == []  # denied sensitive action never dispatched


@pytest.mark.asyncio
async def test_max_steps_exhausted_is_ok_with_budget_summary() -> None:
    backend = _FakeBackend()
    engine = ComputerEngine(
        backend=backend,
        chat_step=_scripted_chat(['{"action": "scroll", "direction": "down"}'] * 10),
        consent=_allow,
    )
    result = await engine.run(task="scroll forever", max_steps=2)
    assert result.status == "ok"
    assert result.steps_used == 2
    assert "budget" in result.summary.lower()


@pytest.mark.asyncio
async def test_run_failure_returns_error() -> None:
    class _BoomBackend(_FakeBackend):
        async def capture(self):
            raise RuntimeError("cua-driver died")

    engine = ComputerEngine(
        backend=_BoomBackend(), chat_step=_scripted_chat([]), consent=_allow
    )
    result = await engine.run(task="x", max_steps=2)
    assert result.status == "error"
    assert result.error_code == "computer_run_failed"
    assert "cua-driver died" in result.summary
