"""PR-B — the local ADK chat SSE seam actually invokes the turn-end memory hook.

Proves the WIRING in ``_local_adk_chat_sse`` (not just the hook in isolation):
drive the live local-chat SSE generator with a fake headless runtime + a fake
engine stream, and assert that with the flags ON a daily file is flushed at the
turn-finalization point — and that an errored turn flushes nothing.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.memory.config import MemoryRuntimeConfig
from magi_agent.runtime import memory_turn_hook
from magi_agent.runtime.memory_turn_hook import reset_session_compaction_state


def _ev(type_: str, **fields: object) -> SimpleNamespace:
    payload = {"type": type_, **fields}
    return SimpleNamespace(payload=payload)


class _FakeEngine:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
        for item in self._items:
            yield item


def _install_fake_headless(monkeypatch: pytest.MonkeyPatch, items: list[object]) -> None:
    import magi_agent.cli.wiring as wiring

    def _fake_build(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(engine=_FakeEngine(items), gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(model="anthropic/claude"))


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    reset_session_compaction_state()
    yield
    reset_session_compaction_state()


async def _drain(gen) -> str:  # noqa: ANN001
    chunks: list[str] = []
    async for chunk in gen:
        chunks.append(chunk)
    return "".join(chunks)


@pytest.mark.asyncio
async def test_local_chat_seam_flushes_daily_when_flags_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    # Pin the resolved config to flags-on so the seam is exercised deterministically.
    monkeypatch.setattr(
        memory_turn_hook,
        "resolve_memory_config",
        lambda: MemoryRuntimeConfig(masterEnabled=True, writeEnabled=True),
    )
    # Pin today for a deterministic daily filename.
    import datetime as _dt

    class _FixedDate(_dt.date):
        @classmethod
        def today(cls) -> _dt.date:  # type: ignore[override]
            return _dt.date(2026, 6, 8)

    monkeypatch.setattr(memory_turn_hook, "date", _FixedDate)

    items = [
        _ev("tool_start", name="Bash"),
        _ev("text_delta", delta="I ran the build and it passed."),
        EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t"),
    ]
    _install_fake_headless(monkeypatch, items)

    payload = {"sessionId": "sess-x", "turnId": "turn-x"}
    out = await _drain(
        chat_mod._local_adk_chat_sse(_runtime(), payload, "build the project")
    )
    assert out.rstrip().endswith("data: [DONE]")

    daily = sorted((tmp_path / "memory" / "daily").glob("*.md"))
    assert [p.name for p in daily] == ["2026-06-08.md"]
    body = daily[0].read_text(encoding="utf-8")
    assert "build the project" in body
    assert "[tools used]" in body


@pytest.mark.asyncio
async def test_errored_turn_flushes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        memory_turn_hook,
        "resolve_memory_config",
        lambda: MemoryRuntimeConfig(masterEnabled=True, writeEnabled=True),
    )

    items = [
        _ev("text_delta", delta="partial..."),
        EngineResult(terminal=Terminal.error, error="boom", session_id="s", turn_id="t"),
    ]
    _install_fake_headless(monkeypatch, items)

    out = await _drain(
        chat_mod._local_adk_chat_sse(_runtime(), {"sessionId": "s", "turnId": "t"}, "do x")
    )
    assert "boom" in out
    assert not (tmp_path / "memory" / "daily").exists()
