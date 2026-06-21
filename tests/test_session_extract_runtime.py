"""Tests for the session-end extraction glue (runtime/session_extract_runtime).

Covers the gate, the ADK-event -> transcript adapter, and the fail-soft
end-to-end flush (gated provider write of declarative facts only).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.runtime.session_extract_runtime import (
    messages_from_adk_events,
    run_session_extract,
    session_extract_enabled,
)

_ENABLE = "MAGI_MEMORY_SESSION_EXTRACT_ENABLED"
_WRITE = "MAGI_MEMORY_WRITE_ENABLED"


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (_ENABLE, _WRITE, "MAGI_MEMORY_ENABLED"):
        monkeypatch.delenv(name, raising=False)


# -- gate --------------------------------------------------------------------


def test_gate_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert session_extract_enabled() is False


def test_gate_on_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(_ENABLE, "1")
    assert session_extract_enabled() is True


# -- ADK event adapter -------------------------------------------------------


def _event(author: str, *texts: str) -> SimpleNamespace:
    parts = [SimpleNamespace(text=t) for t in texts]
    return SimpleNamespace(author=author, content=SimpleNamespace(parts=parts))


def test_messages_from_adk_events_maps_roles_and_joins_text() -> None:
    events = [
        _event("user", "I live in ", "Seoul."),
        _event("magi-agent", "Noted."),
        _event("tool"),  # no text -> skipped
    ]
    assert messages_from_adk_events(events) == [
        {"role": "user", "content": "I live in Seoul."},
        {"role": "assistant", "content": "Noted."},
    ]


def test_messages_from_adk_events_empty() -> None:
    assert messages_from_adk_events(None) == []
    assert messages_from_adk_events([_event("user", "   ")]) == []


# -- end-to-end flush --------------------------------------------------------


class _FakeModel:
    """ADK-style model whose generate_content_async yields a facts JSON blob."""

    def __init__(self, facts: list[str]) -> None:
        self._facts = facts

    async def generate_content_async(self, _request, stream=False):  # noqa: ANN001
        import json

        part = SimpleNamespace(text=json.dumps({"facts": self._facts}))
        yield SimpleNamespace(content=SimpleNamespace(parts=[part]))


def _transcript() -> list[dict]:
    return [
        {"role": "user", "content": "I prefer concise answers."},
        {"role": "assistant", "content": "Understood."},
    ]


def test_run_session_extract_disabled_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear(monkeypatch)  # gate OFF
    receipt = asyncio.run(
        run_session_extract(_transcript(), workspace_root=tmp_path, model=_FakeModel(["x"]))
    )
    assert receipt is None
    assert not (tmp_path / "MEMORY.md").exists()


def test_run_session_extract_writes_declarative_fact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(_ENABLE, "1")
    monkeypatch.setenv(_WRITE, "1")
    receipt = asyncio.run(
        run_session_extract(
            _transcript(),
            workspace_root=tmp_path,
            model=_FakeModel(["The user prefers concise answers."]),
        )
    )
    assert receipt is not None
    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "concise" in content
    # Never writes to SOUL.md.
    assert not (tmp_path / "SOUL.md").exists()


def test_run_session_extract_rejects_task_state_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(_ENABLE, "1")
    monkeypatch.setenv(_WRITE, "1")
    # Task-state ("deployed the build...") is transient, not a durable fact, so
    # the declarative filter rejects it before any write.
    receipt = asyncio.run(
        run_session_extract(
            _transcript(),
            workspace_root=tmp_path,
            model=_FakeModel(["deployed the build to production"]),
        )
    )
    assert receipt is not None
    mem = tmp_path / "MEMORY.md"
    assert not mem.exists() or "deployed the build" not in mem.read_text(encoding="utf-8")


def test_run_session_extract_failsoft_on_model_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(_ENABLE, "1")
    monkeypatch.setenv(_WRITE, "1")

    class _Boom:
        async def generate_content_async(self, _request, stream=False):  # noqa: ANN001
            raise RuntimeError("model down")
            yield  # pragma: no cover

    # Must not raise; returns a receipt with no facts written.
    receipt = asyncio.run(
        run_session_extract(_transcript(), workspace_root=tmp_path, model=_Boom())
    )
    assert receipt is not None
    assert not (tmp_path / "MEMORY.md").exists()
