from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from magi_agent.observability.transcript import (
    SessionTranscriptWriter,
    get_active_transcript_sink,
    register_session_transcript,
    set_active_transcript_sink,
)


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_record_writes_jsonl_line(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path)
    writer.record({"type": "tool_call", "tool_name": "Bash", "args": {"cmd": "ls"}}, "ch-x", "ch-x:turn")

    path = tmp_path / "sessions" / "ch-x.jsonl"
    rows = _read_lines(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "tool_call"
    assert row["tool_name"] == "Bash"
    assert row["args"] == {"cmd": "ls"}
    assert row["session_id"] == "ch-x"
    assert row["turn_id"] == "ch-x:turn"
    assert row["seq"] == 1
    assert isinstance(row["ts"], str) and row["ts"]


def test_seq_monotonic_per_session(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path)
    for i in range(3):
        writer.record({"type": "turn_start", "n": i}, "s1", "t")

    rows = _read_lines(tmp_path / "sessions" / "s1.jsonl")
    assert [r["seq"] for r in rows] == [1, 2, 3]


def test_separate_files_and_independent_seq(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path)
    writer.record({"type": "turn_start"}, "a", "t")
    writer.record({"type": "turn_start"}, "b", "t")
    writer.record({"type": "turn_end"}, "a", "t")

    a_rows = _read_lines(tmp_path / "sessions" / "a.jsonl")
    b_rows = _read_lines(tmp_path / "sessions" / "b.jsonl")
    assert [r["seq"] for r in a_rows] == [1, 2]
    assert [r["seq"] for r in b_rows] == [1]


def test_fail_open_when_base_is_a_file(tmp_path: Path):
    # base_dir points at an existing regular file → sessions/ cannot be created.
    bogus = tmp_path / "not_a_dir"
    bogus.write_text("x")
    writer = SessionTranscriptWriter(bogus)
    # Must not raise.
    writer.record({"type": "tool_call"}, "s", "t")


def test_session_id_path_traversal_is_contained(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path)
    writer.record({"type": "turn_start"}, "../evil", "t")

    sessions_dir = tmp_path / "sessions"
    # The escaping path tmp_path/evil.jsonl must NOT exist.
    assert not (tmp_path / "evil.jsonl").exists()
    # Exactly one file, and it lives under sessions/.
    written = list(sessions_dir.glob("*.jsonl"))
    assert len(written) == 1
    assert written[0].parent == sessions_dir


def test_text_delta_events_are_skipped(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path)
    writer.record({"type": "text_delta", "delta": "po"}, "s", "t")
    writer.record({"type": "text_delta", "delta": "ng"}, "s", "t")
    writer.record({"type": "message", "role": "assistant", "content": "pong"}, "s", "t")

    rows = _read_lines(tmp_path / "sessions" / "s.jsonl")
    # Only the assembled message is persisted; streaming deltas are noise.
    assert [r["type"] for r in rows] == ["message"]
    # seq still starts at 1 for the first *written* record.
    assert rows[0]["seq"] == 1


def test_prune_removes_over_age_files(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path)
    writer.record({"type": "x"}, "old", "t")
    writer.record({"type": "x"}, "fresh", "t")

    old = tmp_path / "sessions" / "old.jsonl"
    past = 1000.0  # far in the past
    os.utime(old, (past, past))

    writer.prune(retention_days=14, max_files=500)
    assert not old.exists()
    assert (tmp_path / "sessions" / "fresh.jsonl").exists()


def test_prune_enforces_max_files_keeping_newest(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path)
    base = time.time() - 100  # recent, so retention never triggers
    for i in range(5):
        writer.record({"type": "x"}, f"s{i}", "t")
        p = tmp_path / "sessions" / f"s{i}.jsonl"
        os.utime(p, (base + i, base + i))

    writer.prune(retention_days=3650, max_files=2)
    remaining = {p.stem for p in (tmp_path / "sessions").glob("*.jsonl")}
    assert remaining == {"s3", "s4"}


def test_prune_is_fail_open_when_no_sessions_dir(tmp_path: Path):
    writer = SessionTranscriptWriter(tmp_path / "nope")
    # Must not raise when the sessions dir does not exist yet.
    writer.prune(retention_days=14, max_files=500)


def test_register_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MAGI_SESSION_TRANSCRIPT_ENABLED", raising=False)
    set_active_transcript_sink(None)
    app = SimpleNamespace(state=SimpleNamespace())
    result = register_session_transcript(app, object())
    assert result is None
    assert get_active_transcript_sink() is None


def test_register_installs_sink_when_enabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MAGI_SESSION_TRANSCRIPT_ENABLED", "1")
    monkeypatch.setenv("MAGI_OBS_HOME", str(tmp_path))
    set_active_transcript_sink(None)
    try:
        app = SimpleNamespace(state=SimpleNamespace())
        writer = register_session_transcript(app, object())
        assert writer is not None
        sink = get_active_transcript_sink()
        assert sink is not None
        sink({"type": "turn_start"}, "s1", "t1")
        assert (tmp_path / "sessions" / "s1.jsonl").exists()
    finally:
        set_active_transcript_sink(None)


def test_transcript_sink_registry_roundtrip():
    set_active_transcript_sink(None)
    assert get_active_transcript_sink() is None

    def sink(event, session_id, turn_id):
        return None

    set_active_transcript_sink(sink)
    assert get_active_transcript_sink() is sink
    set_active_transcript_sink(None)
    assert get_active_transcript_sink() is None
