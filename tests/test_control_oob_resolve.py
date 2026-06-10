"""Tests for out-of-band approval resolve on the durable control queue (doc 09 PR-5 / A7).

PR-4 gave the control queue an append-only JSONL backend
(:class:`magi_agent.runtime.durable_control_store.DurableControlRequestStore`)
so a pending approval survives a process restart. PR-5 adds the *out-of-band*
(OOB) resolve seam: a pending approval written by one process (the in-turn CLI
gate) can be approved / denied by a *separate* process / CLI invocation (a human
later approving via a channel, the gateway daemon, or the dashboard), and the
original process picks up that decision when it next consumes the queue.

These tests assert:
  * OOB resolve from a *second* store instance is durable and the *original*
    store sees the request as resolved after refreshing from the log,
  * deny / answered decisions round-trip the same way,
  * resolving an unknown request id fails loudly (no silent allow),
  * a second OOB resolve to the *same* decision is idempotent (resolve-once),
  * a conflicting OOB re-resolve raises (terminal request cannot flip decision),
  * ``list_pending`` reads the durable queue and can scope by session key,
  * concurrent OOB writers serialize through a minimal file lock (no torn /
    interleaved JSONL lines),
  * the env gate is default-OFF.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from magi_agent.config.env import control_store_oob_resolve_enabled
from magi_agent.runtime.control import ControlRequestStore
from magi_agent.runtime.control_oob import (
    list_pending,
    resolve_pending,
)
from magi_agent.runtime.durable_control_store import DurableControlRequestStore


def _make_pending(
    store: ControlRequestStore,
    idem: str,
    *,
    session_key: str = "agent:main:app:default",
    now: int = 1000,
):
    return store.create_tool_permission_request(
        session_key=session_key,
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
# Core OOB resolve seam
# ---------------------------------------------------------------------------


def test_oob_resolve_from_second_process_is_seen_by_original(tmp_path) -> None:
    path = tmp_path / "control.jsonl"

    # Process A (in-turn gate) writes a pending approval.
    original = DurableControlRequestStore(path=path)
    created = _make_pending(original, "idem-oob")
    request_id = created.record.request_id
    assert original.get_pending(request_id) is not None

    # Process B (channel / gateway / CLI) resolves it out of band.
    result = resolve_pending(path, request_id, decision="approved", now=2000)
    assert result.record.state == "approved"
    assert result.record.decision == "approved"
    assert result.duplicate is False

    # Original process consumes the queue: refresh picks up the OOB resolution.
    original.refresh_from_log()
    assert original.get_pending(request_id) is None
    terminal = original.get_terminal(request_id)
    assert terminal is not None
    assert terminal.state == "approved"
    assert terminal.decision == "approved"


def test_oob_deny_round_trips(tmp_path) -> None:
    path = tmp_path / "control.jsonl"
    original = DurableControlRequestStore(path=path)
    request_id = _make_pending(original, "idem-deny").record.request_id

    resolve_pending(path, request_id, decision="denied", now=2000, feedback="no")

    original.refresh_from_log()
    terminal = original.get_terminal(request_id)
    assert terminal is not None
    assert terminal.state == "denied"
    assert terminal.decision == "denied"


def test_oob_resolve_unknown_request_raises(tmp_path) -> None:
    path = tmp_path / "control.jsonl"
    DurableControlRequestStore(path=path)
    with pytest.raises(KeyError):
        resolve_pending(path, "ctrl_req_missing", decision="approved", now=2000)


def test_oob_resolve_is_idempotent(tmp_path) -> None:
    path = tmp_path / "control.jsonl"
    original = DurableControlRequestStore(path=path)
    request_id = _make_pending(original, "idem-once").record.request_id

    first = resolve_pending(path, request_id, decision="approved", now=2000)
    second = resolve_pending(path, request_id, decision="approved", now=2500)
    assert first.record.request_id == request_id
    assert second.duplicate is True
    assert second.record.request_id == request_id


def test_oob_conflicting_reresolve_raises(tmp_path) -> None:
    path = tmp_path / "control.jsonl"
    original = DurableControlRequestStore(path=path)
    request_id = _make_pending(original, "idem-conflict").record.request_id

    resolve_pending(path, request_id, decision="approved", now=2000)
    with pytest.raises(ValueError):
        resolve_pending(path, request_id, decision="denied", now=2500)


# ---------------------------------------------------------------------------
# list_pending
# ---------------------------------------------------------------------------


def test_list_pending_reads_durable_queue(tmp_path) -> None:
    path = tmp_path / "control.jsonl"
    store = DurableControlRequestStore(path=path)
    a = _make_pending(store, "idem-a", session_key="agent:main:app:s1").record.request_id
    b = _make_pending(store, "idem-b", session_key="agent:main:app:s2").record.request_id

    everything = list_pending(path)
    assert {r.request_id for r in everything} == {a, b}


def test_list_pending_scopes_by_session_key(tmp_path) -> None:
    path = tmp_path / "control.jsonl"
    store = DurableControlRequestStore(path=path)
    a = _make_pending(store, "idem-a", session_key="agent:main:app:s1").record.request_id
    _make_pending(store, "idem-b", session_key="agent:main:app:s2")

    scoped = list_pending(path, session_key="agent:main:app:s1")
    assert {r.request_id for r in scoped} == {a}


def test_list_pending_excludes_resolved(tmp_path) -> None:
    path = tmp_path / "control.jsonl"
    store = DurableControlRequestStore(path=path)
    request_id = _make_pending(store, "idem-x").record.request_id
    resolve_pending(path, request_id, decision="approved", now=2000)

    assert list_pending(path) == ()


# ---------------------------------------------------------------------------
# Concurrency: minimal file lock for the OOB writer
# ---------------------------------------------------------------------------


def test_concurrent_oob_writers_do_not_tear_lines(tmp_path) -> None:
    import threading

    path = tmp_path / "control.jsonl"
    seed = DurableControlRequestStore(path=path)
    request_ids = [
        _make_pending(seed, f"idem-{i}", now=1000 + i).record.request_id
        for i in range(8)
    ]

    errors: list[BaseException] = []

    def _worker(rid: str) -> None:
        try:
            resolve_pending(path, rid, decision="approved", now=3000)
        except BaseException as exc:  # noqa: BLE001 — surface to assertion
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(rid,)) for rid in request_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []

    # No torn / interleaved lines: every non-blank line must parse as JSON.
    import json

    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            text = raw.strip()
            if text:
                json.loads(text)

    # A fresh reader sees every request resolved (all OOB writes landed).
    final = DurableControlRequestStore(path=path)
    assert final.pending_requests == ()
    assert {r.request_id for r in final.terminal_requests} == set(request_ids)


# ---------------------------------------------------------------------------
# Env gate (default OFF)
# ---------------------------------------------------------------------------


def test_oob_gate_default_off() -> None:
    env: Mapping[str, str] = {}
    assert control_store_oob_resolve_enabled(env) is False


def test_oob_gate_truthy_on() -> None:
    assert control_store_oob_resolve_enabled({"MAGI_CONTROL_STORE_OOB_RESOLVE": "1"}) is True
    assert (
        control_store_oob_resolve_enabled({"MAGI_CONTROL_STORE_OOB_RESOLVE": "true"})
        is True
    )


def test_oob_gate_falsey() -> None:
    assert control_store_oob_resolve_enabled({"MAGI_CONTROL_STORE_OOB_RESOLVE": "0"}) is False
    assert (
        control_store_oob_resolve_enabled({"MAGI_CONTROL_STORE_OOB_RESOLVE": "no"}) is False
    )
