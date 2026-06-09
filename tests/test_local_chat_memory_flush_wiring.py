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
async def test_seam_inert_under_real_default_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production default (master MAGI_MEMORY_ENABLED unset) => the seam is inert.

    Unlike the flags-on test above, this drives the SSE generator with the REAL
    ``resolve_memory_config()`` (no pinned config, no forced flags). With the
    master switch off, the wiring layer must write NO daily file and run NO
    compaction — proving the live seam is a no-op on the shipped default path.
    """
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    # Belt-and-suspenders: ensure no stray env flips the master default on.
    for env in (
        "MAGI_MEMORY_ENABLED",
        "MAGI_MEMORY_WRITE_ENABLED",
        "MAGI_MEMORY_COMPACTION_ENABLED",
    ):
        monkeypatch.delenv(env, raising=False)

    compaction_calls: list[object] = []

    class _SpyTree:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def run(self, *_a: object, **_k: object):  # noqa: ANN202
            compaction_calls.append(object())
            return None

    monkeypatch.setattr(memory_turn_hook, "CompactionTree", _SpyTree)

    items = [
        _ev("tool_start", name="Bash"),
        _ev("text_delta", delta="A substantial reply that would otherwise persist."),
        EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t"),
    ]
    _install_fake_headless(monkeypatch, items)

    out = await _drain(
        chat_mod._local_adk_chat_sse(
            _runtime(), {"sessionId": "sess-y", "turnId": "turn-y"}, "build the project"
        )
    )
    assert out.rstrip().endswith("data: [DONE]")
    assert not (tmp_path / "memory").exists()
    assert compaction_calls == []


@pytest.mark.asyncio
async def test_incognito_mode_blocks_live_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1 regression: write flags ON but incognito mode => NO daily file written.

    Drives the live SSE seam with write_enabled ON, then binds the per-request
    memory mode to INCOGNITO exactly as the runtime does (gate on +
    ``x-core-agent-memory-mode`` header via ``memory_mode_request_scope``).
    The seam must thread that mode into ``record_turn`` so the flush is
    suppressed. WITHOUT the C1 fix (call site omits ``memory_mode``) this test
    FAILS because the daily file is written.
    """
    from magi_agent.runtime.memory_mode_context import (
        MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV,
        memory_mode_request_scope,
    )
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        memory_turn_hook,
        "resolve_memory_config",
        lambda: MemoryRuntimeConfig(masterEnabled=True, writeEnabled=True),
    )
    # Turn the memory-mode routing gate on so the header is honored.
    monkeypatch.setenv(MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV, "1")

    items = [
        _ev("tool_start", name="Bash"),
        _ev("text_delta", delta="A substantial reply that must NOT persist."),
        EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t"),
    ]
    _install_fake_headless(monkeypatch, items)

    # Bind the per-request mode the same way the serve path does.
    with memory_mode_request_scope({"x-core-agent-memory-mode": "incognito"}):
        out = await _drain(
            chat_mod._local_adk_chat_sse(
                _runtime(), {"sessionId": "sess-z", "turnId": "turn-z"}, "build it"
            )
        )

    assert out.rstrip().endswith("data: [DONE]")
    assert not (tmp_path / "memory" / "daily").exists()


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
