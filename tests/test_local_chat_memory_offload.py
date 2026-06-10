"""PR-C — the local ADK chat SSE seam offloads record_turn via asyncio.to_thread.

The turn-end memory hook (``record_turn``) is synchronous and its first-turn
compaction can do ~300ms of file IO. The chat seam must run it OFF the event
loop via ``asyncio.to_thread`` so the SSE loop never blocks. These tests prove:

  * the seam awaits ``asyncio.to_thread(record_turn, ...)`` (offload happened);
  * the offloaded record_turn still flushes the daily file AND runs compaction
    end-to-end (offload did not break behaviour).
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.memory.config import MemoryRuntimeConfig
from magi_agent.runtime import memory_turn_hook
from magi_agent.runtime.memory_turn_hook import reset_session_compaction_state


def _ev(type_: str, **fields: object) -> SimpleNamespace:
    return SimpleNamespace(payload={"type": type_, **fields})


class _FakeEngine:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
        for item in self._items:
            yield item


def _install_fake_headless(monkeypatch: pytest.MonkeyPatch, items: list[object]) -> None:
    import magi_agent.cli.wiring as wiring

    monkeypatch.setattr(
        wiring, "build_headless_runtime", lambda **_k: SimpleNamespace(engine=_FakeEngine(items), gate=None)
    )
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(model="anthropic/claude"))


async def _drain(gen) -> str:  # noqa: ANN001
    chunks: list[str] = []
    async for chunk in gen:
        chunks.append(chunk)
    return "".join(chunks)


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    reset_session_compaction_state()
    yield
    reset_session_compaction_state()


@pytest.mark.asyncio
async def test_seam_offloads_record_turn_via_to_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record_turn call must go through asyncio.to_thread, not run inline."""
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    offloaded: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, /, *args, **kwargs):  # noqa: ANN001
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(chat_mod.asyncio, "to_thread", _spy_to_thread)

    # Flags-off is fine: we only assert the offload happened; record_turn is a
    # fail-soft no-op under the default master-off config.
    items = [
        _ev("text_delta", delta="ok"),
        EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t"),
    ]
    _install_fake_headless(monkeypatch, items)

    payload = {"sessionId": "sess-off", "turnId": "turn-off"}
    out = await _drain(chat_mod._local_adk_chat_sse(_runtime(), payload, "hello"))
    assert out.rstrip().endswith("data: [DONE]")

    assert memory_turn_hook.record_turn in offloaded, (
        "record_turn must be offloaded via asyncio.to_thread, not called inline"
    )


@pytest.mark.asyncio
async def test_offloaded_record_turn_still_flushes_and_compacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: with flags ON, the offloaded record_turn flushes the daily
    file AND runs the once-per-session compaction (compaction did not regress)."""
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        memory_turn_hook,
        "resolve_memory_config",
        lambda: MemoryRuntimeConfig(
            masterEnabled=True, writeEnabled=True, compactionEnabled=True
        ),
    )

    import datetime as _dt

    class _FixedDate(_dt.date):
        @classmethod
        def today(cls) -> _dt.date:  # type: ignore[override]
            return _dt.date(2026, 6, 9)

    monkeypatch.setattr(memory_turn_hook, "date", _FixedDate)

    # Spy on the compaction trigger to prove it ran on the worker thread.
    compaction_calls: list[str] = []
    real_maybe = memory_turn_hook._maybe_run_compaction

    def _spy_maybe(cfg, *, memory_dir, session_id, today, summarizer):  # noqa: ANN001
        compaction_calls.append(session_id)
        return real_maybe(
            cfg,
            memory_dir=memory_dir,
            session_id=session_id,
            today=today,
            summarizer=summarizer,
        )

    monkeypatch.setattr(memory_turn_hook, "_maybe_run_compaction", _spy_maybe)

    items = [
        _ev("tool_start", name="Bash"),
        _ev("text_delta", delta="I ran the build and it passed cleanly."),
        EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t"),
    ]
    _install_fake_headless(monkeypatch, items)

    payload = {"sessionId": "sess-on", "turnId": "turn-on"}
    out = await _drain(chat_mod._local_adk_chat_sse(_runtime(), payload, "build it"))
    assert out.rstrip().endswith("data: [DONE]")

    # Daily flush happened (offload preserved the write).
    daily = tmp_path / "memory" / "daily" / "2026-06-09.md"
    assert daily.is_file()
    assert "build it" in daily.read_text(encoding="utf-8")

    # Compaction ran exactly once for this session on the worker thread.
    assert compaction_calls == ["sess-on"]
