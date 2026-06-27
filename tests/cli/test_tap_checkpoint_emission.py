"""WS1 PR1c - checkpoint emission from the headless tap (section 0.4 / 0.4a).

The checkpoint is emitted from the SessionLog-owning headless tap (NOT from
``cli/engine._drive``), because the Envelope uuid is assigned by
``SessionLog.append`` and only the tap holds it. Emission is gated behind BOTH
``MAGI_DURABLE_CHECKPOINTS_ENABLED`` and ``MAGI_DURABLE_LOCAL_WRITES_ENABLED``;
with either OFF the tap is byte-identical to today (throws the uuid away,
emits nothing). Fail-open: an emit error never breaks the turn.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from magi_agent.cli.headless import (
    CheckpointTapContext,
    _tap_session_log,
)
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.session_log import SessionLog, load, resolve_session_path
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.storage.durable_checkpoint_store import DurableCheckpointStore


def _text(text: str) -> RuntimeEvent:
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


async def _gen(events: list[RuntimeEvent], terminal: EngineResult):
    for ev in events:
        yield ev
    yield terminal  # type: ignore[misc]


async def _drain(gen) -> tuple[list[RuntimeEvent], EngineResult | None]:
    out: list[RuntimeEvent] = []
    terminal: EngineResult | None = None
    async for item in gen:
        if isinstance(item, EngineResult):
            terminal = item
        else:
            out.append(item)
    return out, terminal


def _ctx(tmp_path: Path, session_id: str, store: DurableCheckpointStore) -> CheckpointTapContext:
    return CheckpointTapContext(
        store=store,
        run_id=session_id,
        turn_id="t1",
        session_id=session_id,
        cwd=str(tmp_path),
    )


def test_tap_emits_checkpoint_per_persisted_tool_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_CHECKPOINTS_ENABLED", "1")
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / ".magi" / "evidence"))
    session_id = "sess-tap"
    store = DurableCheckpointStore(db_path=tmp_path / "wq.db")
    log = SessionLog(bot_id="", session_id=session_id, cwd=str(tmp_path))
    events = [
        _text("Working."),
        _tool_start("c1", "read_file"),
        _tool_end("c1", "read_file"),
        _tool_start("c2", "grep"),
        _tool_end("c2", "grep"),
    ]
    terminal = EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    gen = _tap_session_log(_gen(events, terminal), log, checkpoint_ctx=_ctx(tmp_path, session_id, store))
    out, got_terminal = asyncio.run(_drain(gen))
    log.close()

    assert got_terminal is terminal
    # Two tool_end checkpoints + one terminal checkpoint were written.
    conn = store._get_conn()  # type: ignore[attr-defined]
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM durable_checkpoints WHERE run_id=? AND turn_id=?",
        (session_id, "t1"),
    ).fetchone()["n"]
    assert total == 3
    store.close()


def test_checkpoint_watermark_is_appended_uuid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_CHECKPOINTS_ENABLED", "1")
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / ".magi" / "evidence"))
    session_id = "sess-wm"
    store = DurableCheckpointStore(db_path=tmp_path / "wq.db")
    log = SessionLog(bot_id="", session_id=session_id, cwd=str(tmp_path))
    events = [_tool_start("c1", "read_file"), _tool_end("c1", "read_file")]
    terminal = EngineResult(terminal=Terminal.completed)

    gen = _tap_session_log(_gen(events, terminal), log, checkpoint_ctx=_ctx(tmp_path, session_id, store))
    asyncio.run(_drain(gen))
    log.close()

    conn = store._get_conn()  # type: ignore[attr-defined]
    rows = conn.execute(
        "SELECT watermark_uuid, state_digest, ledger_head_digest FROM durable_checkpoints "
        "WHERE run_id=? ORDER BY created_at",
        (session_id,),
    ).fetchall()
    envelopes = load(resolve_session_path("", session_id, str(tmp_path)))
    tool_end_uuid = next(
        e.uuid
        for e in envelopes
        if isinstance(e.payload, dict) and e.payload.get("type") == "tool_end"
    )
    watermarks = [r["watermark_uuid"] for r in rows]
    assert tool_end_uuid in watermarks
    for r in rows:
        assert r["state_digest"].startswith("sha256:")
        assert r["ledger_head_digest"].startswith("sha256:")
    store.close()


def test_tap_off_byte_identical(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_DURABLE_CHECKPOINTS_ENABLED", raising=False)
    session_id = "sess-off"
    store = DurableCheckpointStore(db_path=tmp_path / "wq.db")
    log = SessionLog(bot_id="", session_id=session_id, cwd=str(tmp_path))
    events = [_text("hi"), _tool_start("c1", "read_file"), _tool_end("c1", "read_file")]
    terminal = EngineResult(terminal=Terminal.completed)

    gen = _tap_session_log(_gen(events, terminal), log, checkpoint_ctx=_ctx(tmp_path, session_id, store))
    out, got_terminal = asyncio.run(_drain(gen))
    log.close()

    assert got_terminal is terminal
    assert [e.payload.get("type") for e in out] == ["text_delta", "tool_start", "tool_end"]
    # Store OFF => no sqlite file touched at all.
    assert not (tmp_path / "wq.db").exists()
    # Golden: a run with NO checkpoint_ctx persists the same envelopes.
    session2 = "sess-off2"
    log2 = SessionLog(bot_id="", session_id=session2, cwd=str(tmp_path))
    gen2 = _tap_session_log(_gen(events, EngineResult(terminal=Terminal.completed)), log2)
    asyncio.run(_drain(gen2))
    log2.close()
    a = load(resolve_session_path("", session_id, str(tmp_path)))
    b = load(resolve_session_path("", session2, str(tmp_path)))
    assert [e.payload.get("type") for e in a] == [e.payload.get("type") for e in b]
    store.close()


def test_emission_failopen(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_CHECKPOINTS_ENABLED", "1")
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / ".magi" / "evidence"))
    session_id = "sess-failopen"

    class BoomStore(DurableCheckpointStore):
        def put(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("boom")

    store = BoomStore(db_path=tmp_path / "wq.db")
    log = SessionLog(bot_id="", session_id=session_id, cwd=str(tmp_path))
    events = [_tool_start("c1", "read_file"), _tool_end("c1", "read_file")]
    terminal = EngineResult(terminal=Terminal.completed)

    gen = _tap_session_log(_gen(events, terminal), log, checkpoint_ctx=_ctx(tmp_path, session_id, store))
    out, got_terminal = asyncio.run(_drain(gen))
    log.close()
    # The turn still completes; the terminal passes through.
    assert got_terminal is terminal
    assert [e.payload.get("type") for e in out] == ["tool_start", "tool_end"]


def test_checkpoint_emitted_from_tap_not_engine() -> None:
    import magi_agent.cli.engine as engine_mod

    src = Path(engine_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {"checkpointing", "durable_checkpoint_store", "durable_checkpoint_emitter"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not any(f in node.module for f in forbidden), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name for f in forbidden), alias.name
    assert "checkpoint_digest_provider" not in src
