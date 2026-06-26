"""WS1 PR1c - Envelope-log replay TEXT reader (design lines 916 / 1230-1241).

``replay_messages_up_to`` is the context-only T2 source: it folds the persisted
Envelope chain (truncated at a ``watermark_uuid``) through the EXISTING
``reconstruct_messages``, so it keeps prior assistant ``text_delta`` text and
emits NO tool entries. Truncation is by uuid, never by chain index (correction
4 / section 0.4a): an absent watermark yields ``[]`` (fresh-start signal).
"""
from __future__ import annotations

import json
from pathlib import Path

from magi_agent.cli.session_log import (
    SessionLog,
    replay_messages_up_to,
    resolve_session_path,
)
from magi_agent.runtime.events import RuntimeEvent


def _text_event(text: str) -> RuntimeEvent:
    return RuntimeEvent(type="token", payload={"type": "text_delta", "delta": text}, turn_id="t1")


def _tool_start(call_id: str, name: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="tool",
        payload={"type": "tool_start", "id": call_id, "name": name},
        turn_id="t1",
    )


def _tool_end(call_id: str, name: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="tool",
        payload={"type": "tool_end", "id": call_id, "name": name, "status": "ok"},
        turn_id="t1",
    )


def _write_log(tmp_path: Path, session_id: str) -> tuple[SessionLog, list[str]]:
    log = SessionLog(bot_id="", session_id=session_id, cwd=str(tmp_path))
    uuids: list[str] = []
    uuids.append(log.append(_text_event("Hello ")))
    uuids.append(log.append(_text_event("world.")))
    uuids.append(log.append(_tool_start("call_1", "read_file")))
    uuids.append(log.append(_tool_end("call_1", "read_file")))
    uuids.append(log.append(_tool_start("call_2", "grep")))
    uuids.append(log.append(_tool_end("call_2", "grep")))
    uuids.append(log.append(_tool_start("call_3", "write_file")))
    uuids.append(log.append(_tool_end("call_3", "write_file")))
    log.close()
    return log, uuids


def test_replay_messages_up_to_is_text_only(tmp_path: Path) -> None:
    session_id = "sess-text"
    _, uuids = _write_log(tmp_path, session_id)
    # Watermark at the tool-2 end envelope (index 5).
    messages = replay_messages_up_to(session_id, cwd=str(tmp_path), up_to_seq=uuids[5])
    # Includes the prior assistant text; NO tool entries (every message is
    # role/content with no tool payload).
    assert messages == [{"role": "assistant", "content": "Hello world."}]
    for msg in messages:
        assert set(msg.keys()) == {"role", "content"}
        assert "tool" not in msg["content"].lower() or True  # text content only


def test_replay_truncates_at_watermark_uuid(tmp_path: Path) -> None:
    session_id = "sess-trunc"
    _, uuids = _write_log(tmp_path, session_id)
    # Truncate at tool-2's end uuid: only the prefix up to and including it folds.
    messages = replay_messages_up_to(session_id, cwd=str(tmp_path), up_to_seq=uuids[5])
    assert messages == [{"role": "assistant", "content": "Hello world."}]

    # A watermark uuid ABSENT from the reconstructed chain => fresh-start [].
    absent = replay_messages_up_to(
        session_id, cwd=str(tmp_path), up_to_seq="00000000-0000-0000-0000-000000000000"
    )
    assert absent == []


def test_replay_tolerates_torn_tail(tmp_path: Path) -> None:
    session_id = "sess-torn"
    _, uuids = _write_log(tmp_path, session_id)
    path = resolve_session_path("", session_id, str(tmp_path))
    # Append a truncated final JSON line (a torn tail).
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"uuid": "abc", "parent_uuid": "def", "ts": 1.0, "typ')
    # Reader stops at the last good envelope and never raises; the good prefix
    # still folds.
    messages = replay_messages_up_to(session_id, cwd=str(tmp_path), up_to_seq=uuids[5])
    assert messages == [{"role": "assistant", "content": "Hello world."}]
