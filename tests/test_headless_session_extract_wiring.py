"""The CLI/headless session boundary calls the session-end extractor.

Drives the ``_maybe_session_extract`` helper (the headless end-of-run seam)
directly to verify its gating: errored / empty turns are skipped, and a good
turn forwards the {user, assistant} transcript to ``run_session_extract``.
"""
from __future__ import annotations

import asyncio

import pytest

import magi_agent.runtime.session_extract_runtime as sx
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.headless import _maybe_session_extract


def _good() -> EngineResult:
    return EngineResult(terminal=Terminal.completed)


def _errored() -> EngineResult:
    return EngineResult(terminal=Terminal.completed, error="boom")


def _patch_capture(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def _fake(messages, *, workspace_root, model=None):  # noqa: ANN001
        calls.append({"messages": messages, "workspace_root": str(workspace_root)})
        return object()

    monkeypatch.setattr(sx, "run_session_extract", _fake, raising=True)
    return calls


def test_good_turn_forwards_transcript(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls = _patch_capture(monkeypatch)
    asyncio.run(
        _maybe_session_extract(
            workspace_root=str(tmp_path),
            terminal=_good(),
            user_text="I live in Seoul.",
            assistant_text="Noted.",
        )
    )
    assert len(calls) == 1
    assert calls[0]["messages"] == [
        {"role": "user", "content": "I live in Seoul."},
        {"role": "assistant", "content": "Noted."},
    ]


def test_errored_turn_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls = _patch_capture(monkeypatch)
    asyncio.run(
        _maybe_session_extract(
            workspace_root=str(tmp_path),
            terminal=_errored(),
            user_text="hi",
            assistant_text="yo",
        )
    )
    assert calls == []


def test_empty_turn_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls = _patch_capture(monkeypatch)
    asyncio.run(
        _maybe_session_extract(
            workspace_root=str(tmp_path),
            terminal=_good(),
            user_text="   ",
            assistant_text="",
        )
    )
    assert calls == []
