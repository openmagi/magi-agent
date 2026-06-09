"""PR-B — turn-end memory hook: transcript→daily flush + compaction trigger.

Covers the WIRING that PR-A's CompactionTree intentionally left out:
  1. flush writes a daily entry when write_enabled (non-trivial turn)
  2. trivial turns (no tool + short reply) are skipped
  3. write OFF / incognito / read_only => no daily file (inert)
  4. compaction trigger runs CompactionTree.run once per session when enabled
  5. compaction OFF => CompactionTree.run NOT called
  6. fail-soft: a broken config/IO never raises into the caller
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from magi_agent.memory.config import MemoryRuntimeConfig
from magi_agent.runtime import memory_turn_hook
from magi_agent.runtime.memory_turn_hook import (
    record_turn,
    reset_session_compaction_state,
)


@pytest.fixture(autouse=True)
def _clear_session_state() -> None:
    reset_session_compaction_state()
    yield
    reset_session_compaction_state()


def _cfg(**overrides: object) -> MemoryRuntimeConfig:
    base: dict[str, object] = {
        "masterEnabled": True,
        "writeEnabled": False,
        "compactionEnabled": False,
    }
    base.update(overrides)
    return MemoryRuntimeConfig(**base)


def _daily_files(workspace: Path) -> list[Path]:
    daily = workspace / "memory" / "daily"
    if not daily.is_dir():
        return []
    return sorted(daily.glob("*.md"))


# ---------------------------------------------------------------------------
# 1. flush writes a daily entry on a non-trivial turn
# ---------------------------------------------------------------------------


def test_flush_writes_daily_entry_when_write_enabled(tmp_path: Path) -> None:
    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="please refactor the parser module",
        assistant_text="Done — I refactored parser.py and added two tests.",
        used_tool=True,
        config=_cfg(writeEnabled=True),
        today=date(2026, 6, 8),
    )
    files = _daily_files(tmp_path)
    assert [p.name for p in files] == ["2026-06-08.md"]
    body = files[0].read_text(encoding="utf-8")
    assert "turn t1" in body
    assert "refactor the parser" in body
    assert "[tools used]" in body


# ---------------------------------------------------------------------------
# 2. trivial turn skipped (no tool + short reply)
# ---------------------------------------------------------------------------


def test_trivial_turn_is_skipped(tmp_path: Path) -> None:
    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="hi",
        assistant_text="Hello!",
        used_tool=False,
        config=_cfg(writeEnabled=True),
        today=date(2026, 6, 8),
    )
    assert _daily_files(tmp_path) == []


def test_short_reply_with_tool_use_is_not_trivial(tmp_path: Path) -> None:
    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="ls",
        assistant_text="ok",
        used_tool=True,
        config=_cfg(writeEnabled=True),
        today=date(2026, 6, 8),
    )
    assert len(_daily_files(tmp_path)) == 1


# ---------------------------------------------------------------------------
# 3. write OFF / incognito / read_only => inert
# ---------------------------------------------------------------------------


def test_write_disabled_writes_nothing(tmp_path: Path) -> None:
    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="do something substantial " * 5,
        assistant_text="a substantial assistant reply " * 5,
        used_tool=True,
        config=_cfg(writeEnabled=False),
        today=date(2026, 6, 8),
    )
    assert _daily_files(tmp_path) == []


@pytest.mark.parametrize("mode", ["incognito", "read_only", "read-only"])
def test_non_writing_modes_skip_flush(tmp_path: Path, mode: str) -> None:
    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="meaningful prompt about the build pipeline",
        assistant_text="a long meaningful assistant reply about the build " * 3,
        used_tool=True,
        config=_cfg(writeEnabled=True),
        memory_mode=mode,
        today=date(2026, 6, 8),
    )
    assert _daily_files(tmp_path) == []


# ---------------------------------------------------------------------------
# 4 & 5. compaction trigger
# ---------------------------------------------------------------------------


def test_compaction_runs_once_per_session_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[date] = []

    class _FakeTree:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def run(self, *, today: date, force: bool = False):  # noqa: ANN202
            calls.append(today)
            return None

    monkeypatch.setattr(memory_turn_hook, "CompactionTree", _FakeTree)

    cfg = _cfg(writeEnabled=True, compactionEnabled=True)
    for turn in range(3):
        record_turn(
            workspace_root=tmp_path,
            session_id="sess-A",
            turn_id=f"t{turn}",
            user_text="prompt about the deploy",
            assistant_text="a long assistant reply about the deploy pipeline " * 3,
            used_tool=True,
            config=cfg,
            today=date(2026, 6, 8),
        )
    # once per session despite 3 turns
    assert calls == [date(2026, 6, 8)]

    # a different session triggers again
    record_turn(
        workspace_root=tmp_path,
        session_id="sess-B",
        turn_id="t0",
        user_text="another prompt",
        assistant_text="another long assistant reply about something " * 3,
        used_tool=True,
        config=cfg,
        today=date(2026, 6, 9),
    )
    assert calls == [date(2026, 6, 8), date(2026, 6, 9)]


def test_compaction_not_called_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[date] = []

    class _FakeTree:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def run(self, *, today: date, force: bool = False):  # noqa: ANN202
            calls.append(today)
            return None

    monkeypatch.setattr(memory_turn_hook, "CompactionTree", _FakeTree)

    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="prompt",
        assistant_text="a long assistant reply about something useful here " * 3,
        used_tool=True,
        config=_cfg(writeEnabled=True, compactionEnabled=False),
        today=date(2026, 6, 8),
    )
    assert calls == []


# ---------------------------------------------------------------------------
# 5b. redact BEFORE truncate (I1 regression)
# ---------------------------------------------------------------------------


def test_secret_split_by_truncation_is_still_redacted(tmp_path: Path) -> None:
    """A secret straddling the assistant ~400-char cap must NOT leak a fragment.

    If the entry were truncated BEFORE redaction, the cap would split the token
    mid-string and the redactor (which matches whole secrets) could miss the
    surviving prefix. Redact-before-truncate kills the secret first.
    """
    secret = "sk-live-DEADBEEFCAFEBABE0123456789ABCDEF"
    # Pad so the secret straddles the 400-char assistant cap: a leading run of
    # filler ending a few chars before 400, then the secret crossing the cap.
    filler = "x" * 397
    assistant = f"{filler}{secret} and then more trailing context after it"

    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="here is the deploy token to use",
        assistant_text=assistant,
        used_tool=True,
        config=_cfg(writeEnabled=True),
        today=date(2026, 6, 8),
    )

    files = _daily_files(tmp_path)
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    # The secret (or any fragment of it) must not survive in the daily entry.
    # (The redaction marker itself may be truncated away by the char cap; what
    # matters is that no secret material leaks.)
    assert secret not in body
    assert "DEADBEEF" not in body
    assert "sk-live" not in body


def test_secret_in_user_text_is_redacted(tmp_path: Path) -> None:
    """User-side secrets are redacted before truncation too (~200-char cap)."""
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    user = ("y" * 190) + secret + " trailing user context"

    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text=user,
        assistant_text="a long meaningful assistant reply about the work " * 3,
        used_tool=True,
        config=_cfg(writeEnabled=True),
        today=date(2026, 6, 8),
    )

    files = _daily_files(tmp_path)
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert secret not in body
    assert "ghp_" not in body


# ---------------------------------------------------------------------------
# 6. fail-soft
# ---------------------------------------------------------------------------


def test_record_turn_never_raises_on_broken_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> MemoryRuntimeConfig:
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(memory_turn_hook, "resolve_memory_config", _boom)
    # config=None forces resolution → exception is caught, no raise.
    record_turn(
        workspace_root=tmp_path,
        session_id="s1",
        turn_id="t1",
        user_text="prompt",
        assistant_text="reply " * 30,
        used_tool=True,
    )
    assert _daily_files(tmp_path) == []
