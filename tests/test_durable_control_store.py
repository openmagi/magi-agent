"""Tests for the durable JSONL-backed ControlRequestStore (doc 09 PR-4 / A7).

The in-memory :class:`ControlRequestStore` loses every pending approval when
the process dies. ``DurableControlRequestStore`` is a drop-in subclass that
appends each lifecycle mutation to an append-only JSONL log and replays that
log on construction so a fresh process recovers the prior pending queue.

These tests assert:
  * write request -> reload from disk (new instance) -> request still pending,
  * resolve/cancel/expire survive a restart as terminal records,
  * idempotency dedupe survives a restart,
  * corrupt JSONL lines are skipped (fail-open) rather than crashing replay,
  * the durable store is byte-identical to the in-memory store for in-process
    behaviour (same public records / ledger semantics) — only persistence is
    added,
  * the env gate is default-OFF.
"""

from __future__ import annotations

from collections.abc import Mapping

from magi_agent.runtime.control import ControlRequestStore
from magi_agent.runtime.durable_control_store import DurableControlRequestStore
from magi_agent.config.env import (
    control_store_durable_enabled,
    control_store_durable_path,
)


def _make_pending(store: ControlRequestStore, idem: str, now: int = 1000):
    return store.create_tool_permission_request(
        session_key="agent:main:app:default",
        turn_id="turn-1",
        channel_name="app",
        source="turn",
        prompt="Allow Bash?",
        proposed_input={"command": "ls"},
        idempotency_key=idem,
        now=now,
        timeout_ms=30_000,
    )


# ---------------------------------------------------------------------------
# Persistence across "restart"
# ---------------------------------------------------------------------------


def test_pending_request_survives_restart(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    first = DurableControlRequestStore(path=path)
    created = _make_pending(first, "idem-1")
    request_id = created.record.request_id
    assert created.record.state == "pending"

    # New process: fresh store instance replaying the same JSONL log.
    second = DurableControlRequestStore(path=path)
    restored = second.get_pending(request_id)
    assert restored is not None
    assert restored.state == "pending"
    assert restored.request_id == request_id
    assert {r.request_id for r in second.pending_requests} == {request_id}


def test_resolved_request_survives_restart(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    first = DurableControlRequestStore(path=path)
    created = _make_pending(first, "idem-resolve")
    request_id = created.record.request_id
    first.resolve_request(request_id, decision="approved", now=2000)

    second = DurableControlRequestStore(path=path)
    assert second.get_pending(request_id) is None
    terminal = second.get_terminal(request_id)
    assert terminal is not None
    assert terminal.state == "approved"
    assert terminal.decision == "approved"


def test_cancelled_request_survives_restart(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    first = DurableControlRequestStore(path=path)
    created = _make_pending(first, "idem-cancel")
    request_id = created.record.request_id
    first.cancel_request(request_id, reason="superseded", now=2000)

    second = DurableControlRequestStore(path=path)
    assert second.get_pending(request_id) is None
    terminal = second.get_terminal(request_id)
    assert terminal is not None
    assert terminal.state == "cancelled"


def test_expired_request_survives_restart(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    first = DurableControlRequestStore(path=path)
    created = _make_pending(first, "idem-expire", now=1000)
    request_id = created.record.request_id
    first.expire_request(request_id, now=1000 + 30_000)

    second = DurableControlRequestStore(path=path)
    assert second.get_pending(request_id) is None
    terminal = second.get_terminal(request_id)
    assert terminal is not None
    assert terminal.state == "timed_out"


def test_idempotency_dedupe_survives_restart(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    first = DurableControlRequestStore(path=path)
    created = _make_pending(first, "idem-dup")
    request_id = created.record.request_id

    second = DurableControlRequestStore(path=path)
    # Re-creating with the same idempotency key after restart must dedupe to
    # the replayed record, not open a second request.
    again = _make_pending(second, "idem-dup")
    assert again.duplicate is True
    assert again.record.request_id == request_id
    assert {r.request_id for r in second.pending_requests} == {request_id}


def test_seq_continues_monotonic_after_restart(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    first = DurableControlRequestStore(path=path)
    _make_pending(first, "idem-seq-a")

    second = DurableControlRequestStore(path=path)
    # A new lifecycle event on the reloaded store must not collide with the
    # replayed event sequence — the ledger append guards monotonicity.
    created = _make_pending(second, "idem-seq-b")
    assert created.record.state == "pending"
    seqs = [e.seq for e in second.ledger.events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_corrupt_lines_are_skipped_on_replay(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    first = DurableControlRequestStore(path=path)
    created = _make_pending(first, "idem-corrupt")
    request_id = created.record.request_id

    # Append a torn / corrupt line (e.g. a partial write at crash time).
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
        handle.write("\n")  # blank line

    second = DurableControlRequestStore(path=path)
    # Corrupt + blank lines are skipped; the valid record is still restored.
    restored = second.get_pending(request_id)
    assert restored is not None
    assert restored.state == "pending"


def test_missing_file_is_empty_store(tmp_path) -> None:
    path = tmp_path / "does-not-exist.jsonl"
    store = DurableControlRequestStore(path=path)
    assert store.pending_requests == ()
    assert store.terminal_requests == ()


# ---------------------------------------------------------------------------
# Parity with in-memory store (only persistence added)
# ---------------------------------------------------------------------------


def test_durable_store_is_a_control_request_store(tmp_path) -> None:
    store = DurableControlRequestStore(path=tmp_path / "control.jsonl")
    assert isinstance(store, ControlRequestStore)


def test_in_memory_record_matches_durable_record(tmp_path) -> None:
    mem = ControlRequestStore()
    durable = DurableControlRequestStore(path=tmp_path / "control.jsonl")

    mem_result = _make_pending(mem, "idem-parity")
    durable_result = _make_pending(durable, "idem-parity")

    # Same idempotency key -> same stable request id + same public record shape.
    assert mem_result.record.request_id == durable_result.record.request_id
    assert (
        mem_result.record.model_dump() == durable_result.record.model_dump()
    )


# ---------------------------------------------------------------------------
# Env gate (default OFF)
# ---------------------------------------------------------------------------


def test_durable_gate_default_off() -> None:
    env: Mapping[str, str] = {}
    assert control_store_durable_enabled(env) is False


def test_durable_gate_truthy_on() -> None:
    assert control_store_durable_enabled({"MAGI_CONTROL_STORE_DURABLE": "1"}) is True
    assert (
        control_store_durable_enabled({"MAGI_CONTROL_STORE_DURABLE": "true"}) is True
    )


def test_durable_gate_falsey() -> None:
    assert control_store_durable_enabled({"MAGI_CONTROL_STORE_DURABLE": "0"}) is False
    assert (
        control_store_durable_enabled({"MAGI_CONTROL_STORE_DURABLE": "no"}) is False
    )


def test_durable_path_default_none() -> None:
    assert control_store_durable_path({}) is None


def test_durable_path_from_env(tmp_path) -> None:
    target = tmp_path / "queue.jsonl"
    resolved = control_store_durable_path(
        {"MAGI_CONTROL_STORE_PATH": str(target)}
    )
    assert resolved == target


# ---------------------------------------------------------------------------
# CLI permission-gate wiring (default-OFF byte-identical)
# ---------------------------------------------------------------------------


def test_gate_default_store_is_in_memory_when_gate_off(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_CONTROL_STORE_DURABLE", raising=False)
    from magi_agent.cli.permissions import RulesPermissionGate

    gate = RulesPermissionGate()
    # OFF default: volatile in-memory store, NOT the durable subclass.
    assert type(gate.store) is ControlRequestStore
    assert not isinstance(gate.store, DurableControlRequestStore)


def test_gate_default_store_is_durable_when_gate_on(monkeypatch, tmp_path) -> None:
    target = tmp_path / "gate-queue.jsonl"
    monkeypatch.setenv("MAGI_CONTROL_STORE_DURABLE", "1")
    monkeypatch.setenv("MAGI_CONTROL_STORE_PATH", str(target))
    from magi_agent.cli.permissions import RulesPermissionGate

    gate = RulesPermissionGate()
    assert isinstance(gate.store, DurableControlRequestStore)
    assert gate.store.path == target


def test_gate_injected_store_always_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CONTROL_STORE_DURABLE", "1")
    monkeypatch.setenv("MAGI_CONTROL_STORE_PATH", str(tmp_path / "ignored.jsonl"))
    from magi_agent.cli.permissions import RulesPermissionGate

    injected = ControlRequestStore()
    gate = RulesPermissionGate(store=injected)
    assert gate.store is injected
