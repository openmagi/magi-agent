"""Thread-safety tests for offloaded readonly handler shared state (PR14).

When ``MAGI_TOOL_CONCURRENCY_ENABLED=1`` the ``ToolDispatcher`` offloads
``readonly`` / ``concurrency_safe`` synchronous handlers to threadpool threads
via ``asyncio.to_thread``. Several registered readonly handlers mutate shared
instance state with no lock:

- ``LocalReadOnlyToolHost.execute_tool`` appends to ``self._call_log`` and
  lazily creates/writes ``self._ledgers`` (get-then-set double-create race), and
  the per-(session,turn) ledger's source records.
- ``ReadLedger.record_read`` appends to ``self._entries`` while
  ``get_latest_read`` iterates ``reversed(self._entries)``.

These tests run many concurrent offloaded reads against ONE shared host /
ledger and assert no lost records and consistent state (the locks added in PR14
make this deterministic; without them the GIL avoids crashes but loses entries
and double-creates the ledger).
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.local_readonly import LocalReadOnlyToolHost
from magi_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    workspace_content_digest,
)


@pytest.fixture()
def workspace(tmp_path):
    files = []
    for i in range(12):
        path = tmp_path / f"file_{i}.txt"
        path.write_text(f"contents of file {i}\nsecond line\n", encoding="utf-8")
        files.append(path)
    return tmp_path, files


def _context(workspace_root) -> ToolContext:
    # Fixed session/turn so every concurrent read shares ONE ledger key — this is
    # exactly the get-then-set double-create race surface.
    return ToolContext(
        botId="thread-safety-test",
        sessionId="s1",
        turnId="t1",
        workspaceRoot=str(workspace_root),
    )


def test_concurrent_file_reads_share_one_ledger_no_lost_records(workspace) -> None:
    """N concurrent offloaded FileReads against ONE shared host produce N source
    records in a SINGLE shared ledger (no double-created ledger, none lost)."""
    root, files = workspace
    host = LocalReadOnlyToolHost(agent_role="general")
    ctx = _context(root)

    def run_read(rel: str) -> None:
        result = host.execute_tool(
            tool_name="FileRead",
            arguments={"path": rel},
            context=ctx,
        )
        assert result.status == "ok", result

    async def drive() -> None:
        # Each read offloaded onto its own thread via to_thread, so they race on
        # the shared host's _call_log / _ledgers / ledger._records.
        await asyncio.gather(
            *(asyncio.to_thread(run_read, f.name) for f in files)
        )

    asyncio.run(drive())

    # Every call recorded exactly once.
    assert len(host.call_log) == len(files)
    assert sorted(host.call_log) == sorted(["FileRead"] * len(files))

    # Exactly one ledger was created for the shared (session, turn) key — no
    # get-then-set double-create.
    assert len(host._ledgers) == 1
    ledger = next(iter(host._ledgers.values()))
    # Every concurrent read appended its source record; none lost.
    assert len(ledger.snapshot()) == len(files)
    # Source IDs are unique (no two reads computed the same next-id).
    source_ids = [record.source_id for record in ledger.snapshot()]
    assert len(set(source_ids)) == len(source_ids)


def test_read_ledger_concurrent_record_and_scan_no_lost_entries() -> None:
    """Concurrent ``record_read`` calls against ONE ledger append every entry and
    a concurrent ``get_latest_read`` always sees a consistent snapshot."""
    ledger = ReadLedger(
        ReadLedgerConfig(enabled=True, localInMemoryEnabled=True)
    )
    workspace_ref = "workspace:abc123"
    session_id = "s1"
    n = 200
    barrier = threading.Barrier(2)

    def writer() -> None:
        barrier.wait()
        for i in range(n):
            ledger.record_read(
                session_id=session_id,
                workspace_ref=workspace_ref,
                path=f"dir/file_{i}.txt",
                digest=workspace_content_digest(f"content-{i}"),
                size_bytes=10,
                mtime_ns=0,
                read_mode="full",
                turn_id="t1",
                tool_use_id="u1",
            )

    def reader() -> None:
        barrier.wait()
        # Iterate-while-append: must never raise (snapshot under lock).
        for i in range(n):
            ledger.get_latest_read(
                session_id=session_id,
                workspace_ref=workspace_ref,
                path=f"dir/file_{i}.txt",
            )

    t_w = threading.Thread(target=writer)
    t_r = threading.Thread(target=reader)
    t_w.start()
    t_r.start()
    t_w.join()
    t_r.join()

    # All writes landed — no append lost to a race.
    assert len(ledger._entries) == n


def test_read_ledger_two_writers_no_lost_entries() -> None:
    """Two concurrent writers against one ledger lose no appends."""
    ledger = ReadLedger(
        ReadLedgerConfig(enabled=True, localInMemoryEnabled=True)
    )
    per_writer = 150
    barrier = threading.Barrier(2)

    def writer(prefix: str) -> None:
        barrier.wait()
        for i in range(per_writer):
            ledger.record_read(
                session_id="s1",
                workspace_ref="workspace:abc123",
                path=f"{prefix}/file_{i}.txt",
                digest=workspace_content_digest(f"{prefix}-{i}"),
                size_bytes=10,
                mtime_ns=0,
                read_mode="full",
                turn_id="t1",
                tool_use_id="u1",
            )

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(ledger._entries) == per_writer * 2
