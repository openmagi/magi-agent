"""PR1 — wire ``record_turn`` into the CLI headless turn loop (compaction +
daily-log parity with the hosted transport path).

GAP this pins: the hosted SSE seam (``chat_routes._local_adk_chat_sse``) fires
``record_turn`` at turn-finalize, so the daily memory log + 5-level compaction
tree run on the hosted path. The CLI / headless / local-serve path
(``run_headless``) did NOT, so in CLI mode memory was silently never written.

These tests drive a real ``run_headless`` turn through a fake engine driver
(no model) and assert:

  * memory ON  => a daily entry file appears under ``<cwd>/memory/daily/`` AND
    the once-per-session compaction guard is honored;
  * memory OFF (default, flags unset) => the call is a no-op: NOTHING is written
    under ``<cwd>/memory/``.

Both the ``--output text`` (collect-then-write) branch and the
``--output stream-json`` (live projection) branch are covered, because the two
branches finalize the turn through different code paths.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path

import pytest

from magi_agent.cli.contracts import EngineResult, RuntimeEvent, Terminal
from magi_agent.cli.headless import run_headless
from magi_agent.memory.config import MemoryRuntimeConfig
from magi_agent.runtime import memory_turn_hook
from magi_agent.runtime.memory_turn_hook import reset_session_compaction_state


class _FakeTurnDriver:
    """Fake engine driver: a tool call + assistant text + terminal.

    Mirrors the real engine event shapes the projection helpers read:
    assistant text as ``token`` events with ``payload={"type":"text_delta",
    "delta":...}`` and tool activity as a ``tool`` event with
    ``payload={"type":"tool_start", ...}``.
    """

    def __init__(self, *, deltas: tuple[str, ...], use_tool: bool = True) -> None:
        self._deltas = deltas
        self._use_tool = use_tool

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: asyncio.Event,
        gate: object | None = None,
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        _ = (runtime, turn_input, gate, cancel)
        turn_id = "t1"
        if self._use_tool:
            yield RuntimeEvent(
                type="tool",
                payload={"type": "tool_start", "name": "Bash"},
                turn_id=turn_id,
            )
        for delta in self._deltas:
            yield RuntimeEvent(
                type="token",
                payload={"type": "text_delta", "delta": delta},
                turn_id=turn_id,
            )
        yield EngineResult(  # type: ignore[misc]
            terminal=Terminal.completed,
            usage={"input_tokens": 1, "output_tokens": 2},
            cost_usd=0.0,
            error=None,
        )


def _flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``record_turn`` resolve a write+compaction-enabled config + fixed date."""
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


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    reset_session_compaction_state()
    yield
    reset_session_compaction_state()


@pytest.fixture(autouse=True)
def _cli_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")


def _run(prompt: str, *, output: str, tmp_path: Path, driver: object) -> str:
    """Run a headless turn rooted at ``tmp_path`` (record_turn uses ``os.getcwd()``)."""
    import os

    prev = os.getcwd()
    os.chdir(tmp_path)
    buffer = io.StringIO()
    try:
        asyncio.run(
            run_headless(
                prompt,
                output=output,  # type: ignore[arg-type]
                driver=driver,
                session_id="cli-sess-1",
                stream=buffer,
            )
        )
    finally:
        os.chdir(prev)
    return buffer.getvalue()


def test_text_output_memory_on_writes_daily_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--output text + memory ON => a daily entry file is written under memory/daily/."""
    _flags_on(monkeypatch)

    _run(
        "build the project and run the tests",
        output="text",
        tmp_path=tmp_path,
        driver=_FakeTurnDriver(
            deltas=("I ran the build", " and the tests passed cleanly.")
        ),
    )

    daily = tmp_path / "memory" / "daily" / "2026-06-09.md"
    assert daily.is_file(), "CLI text turn must flush a daily entry when memory is ON"
    body = daily.read_text(encoding="utf-8")
    assert "build the project" in body


def test_stream_json_output_memory_on_writes_daily_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--output stream-json + memory ON => daily entry written (other finalize branch)."""
    _flags_on(monkeypatch)

    _run(
        "wire the webhook signature check",
        output="stream-json",
        tmp_path=tmp_path,
        driver=_FakeTurnDriver(deltas=("Added HMAC verification.",)),
    )

    daily = tmp_path / "memory" / "daily" / "2026-06-09.md"
    assert daily.is_file(), "CLI stream-json turn must flush a daily entry when memory is ON"
    body = daily.read_text(encoding="utf-8")
    assert "webhook signature" in body


def test_memory_on_runs_compaction_once_per_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wired hook fires the once-per-session compaction trigger with the CLI sid."""
    _flags_on(monkeypatch)

    compaction_calls: list[str] = []
    real_maybe = memory_turn_hook._maybe_run_compaction

    def _spy(cfg, *, memory_dir, session_id, today, summarizer):  # noqa: ANN001
        compaction_calls.append(session_id)
        return real_maybe(
            cfg,
            memory_dir=memory_dir,
            session_id=session_id,
            today=today,
            summarizer=summarizer,
        )

    monkeypatch.setattr(memory_turn_hook, "_maybe_run_compaction", _spy)

    _run(
        "do substantial work on the deploy pipeline",
        output="text",
        tmp_path=tmp_path,
        driver=_FakeTurnDriver(deltas=("Shipped the deploy pipeline change.",)),
    )

    assert compaction_calls == ["cli-sess-1"], (
        "compaction must run exactly once, keyed by the CLI session id"
    )


def test_memory_off_default_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Master OFF (the default-install config) => the wired hook is a no-op.

    The hook calls ``record_turn``, which resolves its config from
    ``resolve_memory_config``. We pin that to a master-OFF config (write +
    compaction OFF) so the OFF contract is asserted hermetically — independent of
    any ambient ``MAGI_MEMORY_*`` env the dev shell may export (a clean install
    has ``MAGI_MEMORY_ENABLED`` unset => master OFF).
    """
    monkeypatch.setattr(
        memory_turn_hook,
        "resolve_memory_config",
        lambda: MemoryRuntimeConfig(masterEnabled=False),
    )

    out = _run(
        "this is a substantial prompt about the rollout and pipeline",
        output="text",
        tmp_path=tmp_path,
        driver=_FakeTurnDriver(deltas=("A long substantial reply about it. " * 3,)),
    )

    # The turn itself still produced output...
    assert out.strip() != ""
    # ...but memory was never touched (default-OFF parity with the hosted seam).
    assert not (tmp_path / "memory").exists(), (
        "with memory flags OFF the CLI turn must not write any memory files"
    )
