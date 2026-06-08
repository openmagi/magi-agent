"""Tests for InputHistory (PR1.2) — pure logic + JSONL persistence."""

from __future__ import annotations

from pathlib import Path


def test_history_path_under_magi_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    from magi_agent.cli.tui.history import history_path

    p = history_path("sess-1")
    assert p == tmp_path / "tui" / "history-sess-1.jsonl"


def test_add_then_prev_walks_back_newest_first() -> None:
    from magi_agent.cli.tui.history import InputHistory

    h = InputHistory(session_id="s", path=None)
    h.add("one")
    h.add("two")
    h.add("three")
    assert h.prev("draft") == "three"
    assert h.prev("three") == "two"
    assert h.prev("two") == "one"
    # at the oldest, prev stays put
    assert h.prev("one") == "one"


def test_next_walks_forward_and_restores_draft() -> None:
    from magi_agent.cli.tui.history import InputHistory

    h = InputHistory(session_id="s", path=None)
    h.add("one")
    h.add("two")
    assert h.prev("live draft") == "two"  # stashes "live draft"
    assert h.prev("two") == "one"
    assert h.next() == "two"
    assert h.next() == "live draft"  # back to the stashed draft
    assert h.next() is None  # already at the bottom


def test_add_dedups_consecutive_and_skips_blank() -> None:
    from magi_agent.cli.tui.history import InputHistory

    h = InputHistory(session_id="s", path=None)
    h.add("same")
    h.add("same")  # consecutive dup ignored
    h.add("   ")  # blank ignored
    assert h.prev("x") == "same"
    assert h.prev("same") == "same"  # only one entry


def test_persist_and_reload(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import InputHistory

    p = tmp_path / "tui" / "history-s.jsonl"
    h1 = InputHistory(session_id="s", path=p)
    h1.add("first")
    h1.add("second")
    # a fresh instance reads the same file
    h2 = InputHistory(session_id="s", path=p)
    assert h2.prev("d") == "second"
    assert h2.prev("second") == "first"


def test_ring_caps_at_max(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import InputHistory

    p = tmp_path / "tui" / "history-s.jsonl"
    h = InputHistory(session_id="s", path=p, max_entries=3)
    for text in ("a", "b", "c", "d"):
        h.add(text)
    # oldest ("a") evicted
    assert h.prev("x") == "d"
    assert h.prev("d") == "c"
    assert h.prev("c") == "b"
    assert h.prev("b") == "b"  # "a" gone, clamps at "b"


def test_history_module_import_clean() -> None:
    import subprocess
    import sys

    code = (
        "import magi_agent.cli.tui.history as h;"
        "import sys;"
        "print(any(m=='textual' or m.startswith('textual.') for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False", result.stdout
