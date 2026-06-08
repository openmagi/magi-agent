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


def test_history_add_graceful_degrade_on_oserror(monkeypatch, tmp_path: Path) -> None:
    # The module's many ``except OSError`` claims must be real: if every disk
    # write path raises OSError, construction + add() must NOT propagate and the
    # in-memory ring must still work (graceful degradation, no silent crash).
    import magi_agent.cli.tui.history as hist_mod
    from magi_agent.cli.tui.history import InputHistory

    p = tmp_path / "tui" / "history-s.jsonl"

    def _boom(*_a, **_k):
        raise OSError("disk write blocked")

    # Block every disk op the persistence path can hit.
    monkeypatch.setattr(hist_mod.os, "open", _boom)
    monkeypatch.setattr(hist_mod.os, "replace", _boom)
    monkeypatch.setattr(hist_mod.Path, "mkdir", _boom)

    # Construction (which may _load) and add() must not raise.
    h = InputHistory(session_id="s", path=p, max_entries=3)
    h.add("first entry")
    h.add("second entry")

    # The in-memory ring still works despite no disk persistence.
    assert h.prev("draft") == "second entry"
    assert h.prev("second entry") == "first entry"
    # And no file was actually written (the writes were all blocked).
    assert not p.exists()


def test_draft_stash_graceful_degrade_on_oserror(monkeypatch, tmp_path: Path) -> None:
    # Same OSError graceful-degradation guarantee for DraftStash.save().
    import magi_agent.cli.tui.history as hist_mod
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"

    def _boom(*_a, **_k):
        raise OSError("disk write blocked")

    monkeypatch.setattr(hist_mod.os, "open", _boom)
    monkeypatch.setattr(hist_mod.os, "replace", _boom)
    monkeypatch.setattr(hist_mod.Path, "mkdir", _boom)

    s = DraftStash(session_id="s", path=p)
    # save() returns True (it was stored in memory) and must not raise despite
    # the disk write being blocked.
    assert s.save("a long enough draft to be stashed in memory") is True
    assert s.recent() == ["a long enough draft to be stashed in memory"]
    assert not p.exists()


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


# ---------------------------------------------------------------------------
# DraftStash — save (>= 20 chars) + recency+count recent() (PR1.3 t7)
# ---------------------------------------------------------------------------
def test_draft_path_under_magi_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    from magi_agent.cli.tui.history import draft_path

    p = draft_path("sess-1")
    assert p == tmp_path / "tui" / "drafts-sess-1.jsonl"


def test_draft_stash_ignores_short_drafts(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    s = DraftStash(session_id="s", path=p)
    s.save("too short")  # < 20 chars -> ignored
    assert s.recent() == []


def test_draft_stash_saves_long_drafts(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    s = DraftStash(session_id="s", path=p)
    s.save("this is a long enough draft to keep around")
    assert s.recent() == ["this is a long enough draft to keep around"]


def test_draft_stash_frecency_count_then_recency(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    s = DraftStash(session_id="s", path=p)
    a = "draft alpha that is quite long indeed"
    b = "draft beta that is also long enough yes"
    s.save(a)
    s.save(b)
    s.save(a)  # alpha saved twice -> higher count, ranks first
    ranked = s.recent(limit=10)
    assert ranked[0] == a
    assert ranked[1] == b


def test_draft_stash_recency_breaks_count_ties(tmp_path: Path) -> None:
    # Equal save counts -> the most-recently-saved draft ranks first.
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    s = DraftStash(session_id="s", path=p)
    older = "an older draft that is long enough to keep"
    newer = "a newer draft that is also long enough yes"
    s.save(older)
    s.save(newer)
    ranked = s.recent(limit=10)
    assert ranked[0] == newer
    assert ranked[1] == older


def test_draft_stash_limit(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    s = DraftStash(session_id="s", path=p)
    for i in range(5):
        s.save(f"draft number {i} padded out to twenty plus chars")
    assert len(s.recent(limit=3)) == 3


def test_draft_stash_persist_and_reload(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    s1 = DraftStash(session_id="s", path=p)
    s1.save("a sufficiently long draft to be persisted")
    s2 = DraftStash(session_id="s", path=p)
    assert s2.recent() == ["a sufficiently long draft to be persisted"]


def test_draft_stash_disk_compaction_bounds_file_growth(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    max_drafts = 3
    s = DraftStash(session_id="s", path=p, max_drafts=max_drafts)
    # Write well past 2*_max DISTINCT long-enough drafts: each save() appends a
    # line, so without compaction the file grows unbounded.
    n = 4 * max_drafts  # 12 distinct drafts
    for i in range(n):
        s.save(f"draft entry number {i} padded out to twenty plus characters")

    # On-disk file must have been compacted: the appended JSONL can never exceed
    # the 2*_max threshold once compaction has fired.
    line_count = sum(
        1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    assert line_count <= 2 * max_drafts, line_count

    # A fresh reload returns drafts in correct ranking (recency, since each
    # distinct draft was saved once -> equal counts). Surviving distinct entries
    # are bounded by the on-disk line cap (2*_max), and the most-recent ones rank
    # first regardless of where the last compaction landed.
    s2 = DraftStash(session_id="s", path=p, max_drafts=max_drafts)
    ranked = s2.recent(limit=10)
    assert len(ranked) <= 2 * max_drafts
    assert ranked[0] == f"draft entry number {n - 1} padded out to twenty plus characters"
    assert ranked[1] == f"draft entry number {n - 2} padded out to twenty plus characters"
    assert ranked[2] == f"draft entry number {n - 3} padded out to twenty plus characters"


def test_draft_stash_in_memory_path_none(tmp_path: Path) -> None:
    # ``path=None`` is in-memory only (mirrors InputHistory): no file written.
    from magi_agent.cli.tui.history import DraftStash

    s = DraftStash(session_id="s", path=None)
    s.save("an in-memory only draft that is long enough")
    assert s.recent() == ["an in-memory only draft that is long enough"]
    assert not (tmp_path / "tui").exists()


def test_draft_stash_tolerates_corrupt_lines(tmp_path: Path) -> None:
    from magi_agent.cli.tui.history import DraftStash

    p = tmp_path / "tui" / "drafts-s.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "not json at all\n"
        '{"text": "a perfectly valid long enough draft entry"}\n'
        "{bad json}\n"
        '{"nottext": "ignored"}\n',
        encoding="utf-8",
    )
    s = DraftStash(session_id="s", path=p)
    assert s.recent() == ["a perfectly valid long enough draft entry"]


# ---------------------------------------------------------------------------
# App wires CHAT_STASH (ctrl+s) to stash / restore drafts (PR1.3 t8)
# ---------------------------------------------------------------------------
def test_ctrl_s_stashes_then_restores_draft(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    from magi_agent.cli.tui.app import MagiTuiApp

    long_draft = "an unfinished thought that is plenty long"

    async def _run() -> None:
        app = MagiTuiApp(
            engine=_Eng(),
            gate=_Gate(),
            commands=_Reg(),
            renderers=_Rend(),
            session_id="stash-sess",
        )
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = long_draft
            app._input.cursor_location = (0, len(long_draft))
            await pilot.press("ctrl+s")  # stash + clear
            await pilot.pause()
            assert app._input.text == ""
            assert app._drafts.recent()[0] == long_draft
            # empty buffer + ctrl+s restores the most recent draft
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert app._input.text == long_draft

    asyncio.run(_run())


def test_ctrl_s_short_draft_not_stashed_and_buffer_kept(monkeypatch, tmp_path) -> None:
    # A non-blank but < 20 char draft: DraftStash drops it as too short, so
    # nothing is stored AND the buffer is left intact (no silent data loss on a
    # deliberate ctrl+s keypress).
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    from magi_agent.cli.tui.app import MagiTuiApp

    async def _run() -> None:
        app = MagiTuiApp(
            engine=_Eng(),
            gate=_Gate(),
            commands=_Reg(),
            renderers=_Rend(),
            session_id="short-sess",
        )
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = "tiny"
            app._input.cursor_location = (0, len("tiny"))
            await pilot.press("ctrl+s")
            await pilot.pause()
            # buffer kept (not lost), and nothing stashed
            assert app._input.text == "tiny"
            assert app._drafts.recent() == []

    asyncio.run(_run())


def test_ctrl_s_drafts_persist_to_session_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    from magi_agent.cli.tui.app import MagiTuiApp
    from magi_agent.cli.tui.history import DraftStash, draft_path

    durable = "a durable draft long enough to survive a reload"

    async def _run() -> None:
        app = MagiTuiApp(
            engine=_Eng(),
            gate=_Gate(),
            commands=_Reg(),
            renderers=_Rend(),
            session_id="draft-persist",
        )
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = durable
            app._input.cursor_location = (0, len(durable))
            await pilot.press("ctrl+s")
            await pilot.pause()

        assert draft_path("draft-persist").exists()
        reloaded = DraftStash(session_id="draft-persist")
        assert reloaded.recent()[0] == durable

    asyncio.run(_run())
