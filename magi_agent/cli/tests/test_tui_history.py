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


def test_disk_compaction_bounds_file_growth(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import InputHistory

    p = tmp_path / "tui" / "history-s.jsonl"
    max_entries = 3
    h = InputHistory(session_id="s", path=p, max_entries=max_entries)
    # Write well past 2*_max DISTINCT entries through the public API. (Distinct
    # values avoid the consecutive-dedup path so every add() hits disk.)
    n = 4 * max_entries  # 12 distinct entries
    for i in range(n):
        h.add(f"entry-{i}")

    # On-disk file must have been compacted down: the appended JSONL can never
    # exceed the 2*_max threshold once compaction has fired (it rewrites to
    # _max, then a few more appends may accumulate before the next trigger).
    line_count = sum(
        1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    assert line_count <= 2 * max_entries, line_count

    # A freshly loaded history still returns the most-recent entries in order.
    h2 = InputHistory(session_id="s", path=p, max_entries=max_entries)
    assert h2.prev("x") == f"entry-{n - 1}"  # newest
    assert h2.prev(f"entry-{n - 1}") == f"entry-{n - 2}"
    assert h2.prev(f"entry-{n - 2}") == f"entry-{n - 3}"
    # only _max entries survived; oldest clamps
    assert h2.prev(f"entry-{n - 3}") == f"entry-{n - 3}"


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


# ---------------------------------------------------------------------------
# App builds a per-session InputHistory and records submissions (PR1.2 t6)
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402


class _Reg:
    def lookup(self, name):
        return None

    def list_for(self, surface):
        return []


class _Eng:
    async def run_turn_stream(self, *a, **k):
        if False:
            yield None
        return


class _Gate:
    async def evaluate(self, *a, **k):
        return None


class _Rend:
    def get(self, name):
        return None


def test_app_records_submitted_prompt_into_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    from magi_agent.cli.tui.app import MagiTuiApp

    async def _run() -> None:
        app = MagiTuiApp(
            engine=_Eng(),
            gate=_Gate(),
            commands=_Reg(),
            renderers=_Rend(),
            session_id="hist-sess",
        )
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = "remember me"
            app._input.cursor_location = (0, len("remember me"))
            await pilot.press("enter")
            await pilot.pause()
            assert app._history.prev("x") == "remember me"

    asyncio.run(_run())


def test_app_history_persists_to_session_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    from magi_agent.cli.tui.app import MagiTuiApp
    from magi_agent.cli.tui.history import InputHistory, history_path

    async def _run() -> None:
        app = MagiTuiApp(
            engine=_Eng(),
            gate=_Gate(),
            commands=_Reg(),
            renderers=_Rend(),
            session_id="persist-sess",
        )
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = "durable prompt"
            app._input.cursor_location = (0, len("durable prompt"))
            await pilot.press("enter")
            await pilot.pause()

        # The submission was flushed to the per-session JSONL under the
        # monkeypatched root, and a fresh history reads it back.
        assert history_path("persist-sess").exists()
        reloaded = InputHistory(session_id="persist-sess")
        assert reloaded.prev("x") == "durable prompt"

    asyncio.run(_run())
