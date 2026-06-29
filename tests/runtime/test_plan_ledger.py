"""PR3a tests: durable plan/todo ledger (content log + WS1-gated index).

Design: WS3 Goal/Completion + Durable Cross-Turn Todo Ledger, PR3a (section 5,
5.1, 5.2). Covers the JSONL content log, cross-turn ``restore_into`` on the
LIVE per-turn runner-build path, the session_id shape guard, the mandatory
policy_snapshot_digest handling, content-free index entries, and OFF-path
byte-identical behavior.

All filesystem writes go to ``tmp_path`` and every flag is set/cleared via
``monkeypatch`` so the tracked ``memory/`` tree is never touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from magi_agent.config.env import is_plan_ledger_durable_enabled
from magi_agent.runtime.plan_ledger import (
    PlanLedgerEntry,
    PlanLedgerStore,
    TodoItem,
)
from magi_agent.storage.durable_store import (
    DurableRecord,
    RUNTIME_METADATA_COLLECTIONS,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult
from magi_agent.tools.todo_toolhost import (
    TodoWriteHandlerSet,
    bind_todo_write_handler,
    get_todo_write_handler_set,
)


_FLAG = "MAGI_PLAN_LEDGER_DURABLE_ENABLED"
_VALID_DIGEST = "sha256:" + "0" * 64


def _todo(content: str, status: str = "pending") -> dict[str, object]:
    return {"content": content, "status": status}


def _ctx(*, session_id: str | None, workspace_root: str, turn_id: str | None = None) -> ToolContext:
    return ToolContext(
        bot_id="local-cli",
        user_id="cli",
        session_id=session_id,
        turn_id=turn_id,
        workspace_root=workspace_root,
    )


class _FakeDurableStore:
    """Records ``DurableRecord`` puts so tests can inspect the index half."""

    def __init__(self) -> None:
        self.puts: list[DurableRecord] = []

    def put(self, record: DurableRecord) -> None:
        self.puts.append(record)


# ---------------------------------------------------------------------------
# Flag resolver
# ---------------------------------------------------------------------------


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    assert is_plan_ledger_durable_enabled() is False


def test_flag_strict_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    assert is_plan_ledger_durable_enabled() is True
    monkeypatch.setenv(_FLAG, "0")
    assert is_plan_ledger_durable_enabled() is False


# ---------------------------------------------------------------------------
# Store: content log round trip
# ---------------------------------------------------------------------------


def test_append_then_restore_round_trips(tmp_path: Path) -> None:
    store = PlanLedgerStore(str(tmp_path))
    store.append(session_id="s1", turn_id="t1", todos=[_todo("A")])
    store.append(session_id="s1", turn_id="t2", todos=[_todo("A"), _todo("B")])
    store.append(
        session_id="s1",
        turn_id="t3",
        todos=[_todo("A", "completed"), _todo("B", "completed")],
    )

    fresh = PlanLedgerStore(str(tmp_path))
    restored = fresh.restore("s1")
    assert restored == (
        TodoItem(content="A", status="completed"),
        TodoItem(content="B", status="completed"),
    )


def test_monotonic_seq_last_wins(tmp_path: Path) -> None:
    store = PlanLedgerStore(str(tmp_path))
    store.append(session_id="s1", turn_id=None, todos=[_todo("A")])
    store.append(session_id="s1", turn_id=None, todos=[_todo("B")])
    store.append(session_id="s1", turn_id=None, todos=[_todo("C")])

    path = tmp_path / ".magi" / "durable" / "plan_ledger" / "s1.jsonl"
    lines = [ln for ln in path.read_text("utf-8").splitlines() if ln.strip()]
    seqs = [PlanLedgerEntry.model_validate_json(ln).seq for ln in lines]
    assert seqs == [0, 1, 2]
    assert store.restore("s1") == (TodoItem(content="C", status="pending"),)


def test_restore_skips_torn_last_line(tmp_path: Path) -> None:
    store = PlanLedgerStore(str(tmp_path))
    store.append(session_id="s1", turn_id=None, todos=[_todo("A")])
    store.append(session_id="s1", turn_id=None, todos=[_todo("B")])
    path = tmp_path / ".magi" / "durable" / "plan_ledger" / "s1.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"sch')  # torn, no newline

    assert store.restore("s1") == (TodoItem(content="B", status="pending"),)


def test_append_heals_torn_previous_write(tmp_path: Path) -> None:
    """A crash mid-write (a torn fragment with no trailing newline) must not make
    the NEXT append concatenate onto it; the new record stays on its own line and
    restore returns the newest write, not a revert to the older snapshot."""
    store = PlanLedgerStore(str(tmp_path))
    store.append(session_id="s1", turn_id=None, todos=[_todo("A")])
    store.append(session_id="s1", turn_id=None, todos=[_todo("B")])
    path = tmp_path / ".magi" / "durable" / "plan_ledger" / "s1.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"sch')  # torn previous write: no trailing newline

    # New write must heal the torn tail (prepend a newline) so C lands intact.
    store.append(session_id="s1", turn_id=None, todos=[_todo("C")])
    assert store.restore("s1") == (TodoItem(content="C", status="pending"),)


def test_session_none_uses_local_key(tmp_path: Path) -> None:
    store = PlanLedgerStore(str(tmp_path))
    store.append(session_id=None, turn_id=None, todos=[_todo("A")])
    assert (tmp_path / ".magi" / "durable" / "plan_ledger" / "local.jsonl").exists()
    assert store.restore(None) == (TodoItem(content="A", status="pending"),)


def test_restore_missing_session_is_empty(tmp_path: Path) -> None:
    store = PlanLedgerStore(str(tmp_path))
    assert store.restore("never-written") == ()


def test_ledger_write_failure_degrades_not_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler_set = TodoWriteHandlerSet()
    handler_set.bind(_registry_with_todo())
    store = PlanLedgerStore(str(tmp_path))
    handler_set.set_ledger_sink(store)

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    # Force the append write path to fail; the tool must still succeed.
    monkeypatch.setattr(store, "_write_line", _boom)
    result = handler_set._handle(
        {"todos": [_todo("A")]},
        _ctx(session_id="s1", workspace_root=str(tmp_path)),
    )
    assert isinstance(result, ToolResult)
    assert result.status == "ok"
    assert handler_set.todos_for("s1") == [_todo("A")]


def _registry_with_todo() -> ToolRegistry:
    from magi_agent.runtime.openmagi_runtime import _build_core_tool_registry

    return _build_core_tool_registry()


# ---------------------------------------------------------------------------
# Session id shape guard
# ---------------------------------------------------------------------------


def test_unsafe_session_id_degrades_no_crash(tmp_path: Path) -> None:
    store = PlanLedgerStore(
        str(tmp_path), durable_store=_FakeDurableStore(), policy_digest=_VALID_DIGEST
    )
    # Must not raise and must not create a traversal path.
    store.append(session_id="a/b", turn_id=None, todos=[_todo("A")])
    ledger_dir = tmp_path / ".magi" / "durable" / "plan_ledger"
    assert (ledger_dir / "local.jsonl").exists()
    assert not (ledger_dir / "a").exists()
    # Index upsert is skipped for an unsafe session id.
    assert store._durable_store.puts == []  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Index half (WS1-gated, content-free)
# ---------------------------------------------------------------------------


def test_index_noop_without_durable_store(tmp_path: Path) -> None:
    store = PlanLedgerStore(str(tmp_path), durable_store=None, policy_digest=_VALID_DIGEST)
    store.append(session_id="s1", turn_id=None, todos=[_todo("A")])
    # JSONL still written; no index store to write to.
    assert (tmp_path / ".magi" / "durable" / "plan_ledger" / "s1.jsonl").exists()
    assert store.restore("s1") == (TodoItem(content="A", status="pending"),)


def test_index_noop_when_policy_digest_none(tmp_path: Path) -> None:
    fake = _FakeDurableStore()
    store = PlanLedgerStore(str(tmp_path), durable_store=fake, policy_digest=None)
    store.append(session_id="s1", turn_id=None, todos=[_todo("A")])
    assert (tmp_path / ".magi" / "durable" / "plan_ledger" / "s1.jsonl").exists()
    assert fake.puts == []  # deliberate no-op, not a swallowed safety error


def test_index_written_when_store_and_digest_present(tmp_path: Path) -> None:
    fake = _FakeDurableStore()
    store = PlanLedgerStore(str(tmp_path), durable_store=fake, policy_digest=_VALID_DIGEST)
    store.append(
        session_id="s1",
        turn_id=None,
        todos=[_todo("A", "completed"), _todo("B")],
    )
    assert len(fake.puts) == 1
    record = fake.puts[0]
    assert record.collection == "plan_ledger"
    assert record.record_id == "plan_ledger:s1"
    assert record.policy_snapshot_digest == _VALID_DIGEST
    assert record.metadata == {
        "openTodos": 1,
        "totalTodos": 2,
        "seq": 0,
        "ref": "plan_ledger:s1",
    }


def test_index_collection_is_registered() -> None:
    assert "plan_ledger" in RUNTIME_METADATA_COLLECTIONS


def test_index_entry_is_content_free() -> None:
    # A content-free record validates.
    DurableRecord(
        collection="plan_ledger",
        recordId="plan_ledger:s1",
        contentDigest=_VALID_DIGEST,
        policySnapshotDigest=_VALID_DIGEST,
        metadata={"openTodos": 1, "totalTodos": 2, "seq": 0, "ref": "plan_ledger:s1"},
    )
    # Putting freeform todo text into metadata is rejected by the safety contract.
    with pytest.raises(ValidationError):
        DurableRecord(
            collection="plan_ledger",
            recordId="plan_ledger:s1",
            contentDigest=_VALID_DIGEST,
            policySnapshotDigest=_VALID_DIGEST,
            metadata={"note": "write the quarterly report"},
        )


# ---------------------------------------------------------------------------
# Handler set: sink + restore_into + snapshot_for
# ---------------------------------------------------------------------------


def test_restore_survives_fresh_process_handler(tmp_path: Path) -> None:
    registry1 = _registry_with_todo()
    handler1 = get_todo_write_handler_set(registry1)
    assert handler1 is not None
    handler1.set_ledger_sink(PlanLedgerStore(str(tmp_path)))
    handler1._handle(
        {"todos": [_todo("A", "completed"), _todo("B", "completed")]},
        _ctx(session_id="s1", workspace_root=str(tmp_path)),
    )

    # Fresh handler set (simulates a new process / new per-turn build).
    registry2 = _registry_with_todo()
    handler2 = get_todo_write_handler_set(registry2)
    assert handler2 is not None
    handler2.set_ledger_sink(PlanLedgerStore(str(tmp_path)))
    assert handler2.todos_for("s1") == []  # empty before restore
    handler2.restore_into("s1")
    assert handler2.todos_for("s1") == [
        _todo("A", "completed"),
        _todo("B", "completed"),
    ]
    assert handler2.snapshot_for("s1") == (
        TodoItem(content="A", status="completed"),
        TodoItem(content="B", status="completed"),
    )


def test_restore_into_does_not_clobber_same_turn_write(tmp_path: Path) -> None:
    store = PlanLedgerStore(str(tmp_path))
    store.append(session_id="s1", turn_id=None, todos=[_todo("OLD", "completed")])

    handler = TodoWriteHandlerSet()
    handler.bind(_registry_with_todo())
    handler.set_ledger_sink(store)
    # A same-turn write happened before restore_into runs again.
    handler._handle(
        {"todos": [_todo("NEW")]},
        _ctx(session_id="s1", workspace_root=str(tmp_path)),
    )
    handler.restore_into("s1")  # must NOT overwrite the fresher in-memory state
    assert handler.todos_for("s1") == [_todo("NEW")]


def test_bind_todo_write_handler_stashes_accessor() -> None:
    registry = ToolRegistry()
    from magi_agent.tools.catalog import register_core_tool_manifests

    register_core_tool_manifests(registry)
    handler = bind_todo_write_handler(registry)
    assert get_todo_write_handler_set(registry) is handler


# ---------------------------------------------------------------------------
# CliModelRunner attribute channel (PR3a runner-attribute threading)
# ---------------------------------------------------------------------------


def test_cli_model_runner_exposes_plan_ledger_handler_set() -> None:
    from magi_agent.cli.real_runner import CliModelRunner

    handler = TodoWriteHandlerSet()
    runner = CliModelRunner(
        runner=object(),
        agent=object(),
        session_service=object(),
        app_name="x",
        plan_ledger_handler_set=handler,
    )
    assert runner.plan_ledger_handler_set is handler


def test_cli_model_runner_handler_set_defaults_none() -> None:
    from magi_agent.cli.real_runner import CliModelRunner

    runner = CliModelRunner(
        runner=object(),
        agent=object(),
        session_service=object(),
        app_name="x",
    )
    assert runner.plan_ledger_handler_set is None


# ---------------------------------------------------------------------------
# LIVE per-turn path (Critical #1 / #3)
# ---------------------------------------------------------------------------


_FAKE_PROVIDER = "anthropic"
_FAKE_MODEL = "claude-sonnet-4-5"


def _fake_provider_config() -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(
        provider=_FAKE_PROVIDER, model=_FAKE_MODEL, api_key="sk-test-plan-ledger"
    )


class _FakeRunner:
    model_provider = _FAKE_PROVIDER
    model_label = _FAKE_MODEL
    general_automation_receipts: object = None
    runner_policy_assembly: object = None
    local_tool_evidence_collector: object = None

    def __init__(self, **kwargs: object) -> None:
        self._plan_ledger_handler_set = kwargs.get("plan_ledger_handler_set")

    @property
    def plan_ledger_handler_set(self) -> object | None:
        return self._plan_ledger_handler_set


def _patch_real_runner_build(monkeypatch: pytest.MonkeyPatch) -> None:
    import magi_agent.cli.providers as prov
    import magi_agent.cli.real_runner as rr

    def fake_resolve(*, model_override: object = None) -> object:
        return _fake_provider_config()

    def fake_build_runner(config: object, **kwargs: object) -> _FakeRunner:
        return _FakeRunner(**kwargs)

    monkeypatch.setattr(prov, "resolve_provider_config", fake_resolve)
    monkeypatch.setattr(rr, "build_cli_model_runner", fake_build_runner)


def test_live_path_handler_set_has_sink_attached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FLAG, "1")
    _patch_real_runner_build(monkeypatch)
    from magi_agent.cli.wiring import _build_default_runner, _plan_ledger_handler_set

    runner = _build_default_runner(cwd=str(tmp_path), session_id="s1")
    handler = _plan_ledger_handler_set(runner)
    assert handler is not None
    assert handler.ledger_sink is not None


def test_live_path_restores_todos_across_turn_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FLAG, "1")
    _patch_real_runner_build(monkeypatch)
    from magi_agent.cli.wiring import _build_default_runner, _plan_ledger_handler_set

    runner1 = _build_default_runner(cwd=str(tmp_path), session_id="s1")
    handler1 = _plan_ledger_handler_set(runner1)
    assert handler1 is not None
    handler1._handle(
        {"todos": [_todo("A", "completed"), _todo("B", "completed")]},
        _ctx(session_id="s1", workspace_root=str(tmp_path)),
    )

    # Second per-turn build: fresh runner, fresh handler set, empty _todos, NO
    # intervening TodoWrite. The durable snapshot must be restored.
    runner2 = _build_default_runner(cwd=str(tmp_path), session_id="s1")
    handler2 = _plan_ledger_handler_set(runner2)
    assert handler2 is not None
    assert handler2.snapshot_for("s1") == (
        TodoItem(content="A", status="completed"),
        TodoItem(content="B", status="completed"),
    )


def test_off_flag_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    _patch_real_runner_build(monkeypatch)
    from magi_agent.cli.wiring import _build_default_runner, _plan_ledger_handler_set

    runner = _build_default_runner(cwd=str(tmp_path), session_id="s1")
    # No handler set is threaded onto the runner when the flag is OFF.
    assert _plan_ledger_handler_set(runner) is None
    # No JSONL is created.
    assert not (tmp_path / ".magi" / "durable" / "plan_ledger").exists()


def test_stub_runner_yields_no_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FLAG, "1")
    import magi_agent.cli.providers as prov

    monkeypatch.setattr(
        prov, "resolve_provider_config", lambda *, model_override=None: None
    )
    from magi_agent.cli.wiring import _build_default_runner, _plan_ledger_handler_set

    runner = _build_default_runner(cwd=str(tmp_path), session_id="s1")
    assert _plan_ledger_handler_set(runner) is None
