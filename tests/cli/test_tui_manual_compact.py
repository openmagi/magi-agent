"""G7: TUI request_compact surface wiring (manual force signal).

Exercises the real ``MagiTuiApp.request_compact`` method body without building a
full Textual ``App``: it only touches ``self.compact_requests`` and
``self.controller.commit_block``, so a minimal stub object bound to the unbound
method drives the exact production code path.
"""

from __future__ import annotations

import pytest

from magi_agent.cli.tui.app import MagiTuiApp
from magi_agent.runtime.manual_compaction_context import (
    MAGI_COMPACTION_MANUAL_ENABLED_ENV,
    consume_manual_compaction,
    reset_manual_compaction,
)


class _StubController:
    def __init__(self) -> None:
        self.committed: list[str] = []

    def commit_block(self, text: str) -> None:
        self.committed.append(text)


class _StubApp:
    def __init__(self) -> None:
        self.compact_requests = 0
        self.controller = _StubController()


@pytest.fixture(autouse=True)
def _isolate_signal():
    reset_manual_compaction()
    yield
    reset_manual_compaction()


def _call_request_compact(app: _StubApp) -> None:
    # Bind the real unbound method to the stub so the production body runs.
    MagiTuiApp.request_compact(app)  # type: ignore[arg-type]


def test_flag_off_commits_stub_and_sets_no_signal(monkeypatch) -> None:
    monkeypatch.delenv(MAGI_COMPACTION_MANUAL_ENABLED_ENV, raising=False)
    app = _StubApp()
    _call_request_compact(app)

    assert app.compact_requests == 1
    assert app.controller.committed == ["[compact requested]"]
    assert consume_manual_compaction() is False


def test_flag_on_sets_signal_and_honest_line(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_COMPACTION_MANUAL_ENABLED_ENV, "1")
    app = _StubApp()
    _call_request_compact(app)

    assert app.compact_requests == 1
    assert app.controller.committed == [
        "[compact] context compaction will run on the next message"
    ]
    assert consume_manual_compaction() is True
    assert consume_manual_compaction() is False
