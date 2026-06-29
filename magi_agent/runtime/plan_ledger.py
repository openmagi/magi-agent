"""Durable plan/todo ledger: append-only JSONL content log + WS1-gated index.

Design: WS3 Goal/Completion + Durable Cross-Turn Todo Ledger, PR3a (sections
3.2, 5, 5.1, 5.2).

The canonical store of todo text is an append-only JSONL file, one line per
``TodoWrite`` mutation, full-snapshot semantics (the model sends the whole list
each call, so the last valid line is the current state):

    <workspace_root>/.magi/durable/plan_ledger/<session_id>.jsonl

This content log ships INDEPENDENT of WS1: it creates the ``plan_ledger/``
subdir under the workspace itself when the WS1 substrate is absent, and restore
reads the JSONL directly with no durable-index dependency.

A second, content-free ``DurableRecord`` "index" entry (digests + safe int/ref
metadata only) is upserted into the ``plan_ledger`` collection of
``storage.durable_store`` ONLY when both a durable store is injected (WS1
present) AND a valid sha256 ``policy_digest`` is available. With either absent,
the index upsert is a deliberate no-op and the JSONL half still writes.

This module is import-light on purpose (no ADK / provider imports) so that the
``todo_toolhost`` cold-start path stays clean: it is imported lazily by the CLI
wiring only when ``MAGI_PLAN_LEDGER_DURABLE_ENABLED`` is ON.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.tools.todo_toolhost import _VALID_TODO_STATUSES, _normalize_todos


_LOGGER = logging.getLogger(__name__)

LEDGER_SCHEMA = "openmagi.plan_ledger.v1"

_LOCAL_KEY = "local"

# A subset of durable_store._SAFE_REF_VALUE_RE's value charset that is also
# filesystem-safe (no "/" path traversal, no ":" which is a path separator on
# legacy systems). The session_id is used directly as a JSONL filename AND as a
# durable ref suffix, so it must pass this shape before either is constructed.
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,119}$")


class TodoItem(BaseModel):
    """One normalized todo: ``content`` + a recognized ``status``.

    Frozen so the snapshot tuple is hashable / comparable and can never be
    mutated after the ledger reads it.
    """

    model_config = ConfigDict(frozen=True)

    content: str
    status: Literal["pending", "in_progress", "completed"]


class PlanLedgerEntry(BaseModel):
    """One full-snapshot line of the plan ledger JSONL."""

    model_config = ConfigDict(populate_by_name=True)

    schema_: Literal["openmagi.plan_ledger.v1"] = Field(
        default=LEDGER_SCHEMA, alias="schema"
    )
    session_id: str
    turn_id: str | None = None
    seq: int = Field(ge=0)
    created_at: datetime
    todos: tuple[TodoItem, ...]
    snapshot_digest: str


@runtime_checkable
class _DurableIndexStore(Protocol):
    """Minimal duck-typed surface for the WS1 durable index store."""

    def put(self, record: object) -> object: ...


def _coerce_items(todos: object) -> tuple[TodoItem, ...]:
    """Normalize caller todos through the shared ``_normalize_todos`` so the
    ledger and the in-memory list can never diverge.
    """
    normalized = _normalize_todos(todos)
    return tuple(
        TodoItem(content=str(item["content"]), status=str(item["status"]))  # type: ignore[arg-type]
        for item in normalized
    )


def _snapshot_digest(items: tuple[TodoItem, ...]) -> str:
    payload = json.dumps(
        [{"content": item.content, "status": item.status} for item in items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PlanLedgerStore:
    """Append + restore the durable plan ledger for one workspace.

    ``append`` and ``restore`` NEVER raise on I/O failure: they log once and
    degrade to OFF-equivalent behavior for that session so a turn is never
    aborted by ledger trouble.
    """

    def __init__(
        self,
        workspace_root: str | os.PathLike[str],
        *,
        durable_store: object | None = None,
        policy_digest: str | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._durable_store = durable_store
        self._policy_digest = policy_digest

    # -- paths -------------------------------------------------------------

    def _ledger_dir(self) -> Path:
        return self._workspace_root / ".magi" / "durable" / "plan_ledger"

    def _path_for(self, safe_session_id: str) -> Path:
        return self._ledger_dir() / f"{safe_session_id}.jsonl"

    def _safe_session_id(self, session_id: str | None) -> tuple[str, bool]:
        """Return ``(key, is_original_safe)``.

        Degrades an unsafe session id to the ``local`` key (parity with
        ``todo_toolhost`` ``session_id or "local"``) so a stray ``/`` can never
        traverse the path. The bool reports whether the ORIGINAL id was safe,
        which gates the durable index upsert (an unsafe id skips the index).
        """
        if session_id is None:
            return _LOCAL_KEY, True
        if _SAFE_SESSION_ID_RE.fullmatch(session_id):
            return session_id, True
        _LOGGER.warning(
            "plan_ledger: unsafe session_id rejected; degrading to local key"
        )
        return _LOCAL_KEY, False

    # -- writes ------------------------------------------------------------

    def _next_seq(self, path: Path) -> int:
        if not path.exists():
            return 0
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0

    def _write_line(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Heal a torn previous write: if a prior append was interrupted before its
        # trailing newline (crash mid-write), a bare append would concatenate this
        # JSON onto that fragment and corrupt the line. Prepend a newline when the
        # file does not already end in one, so each record stays on its own line.
        needs_leading_newline = path.exists() and path.stat().st_size > 0
        if needs_leading_newline:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                needs_leading_newline = probe.read(1) != b"\n"
        with open(path, "a", encoding="utf-8") as handle:
            if needs_leading_newline:
                handle.write("\n")
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        todos: object,
    ) -> None:
        try:
            safe_key, original_safe = self._safe_session_id(session_id)
            items = _coerce_items(todos)
            path = self._path_for(safe_key)
            seq = self._next_seq(path)
            entry = PlanLedgerEntry(
                session_id=safe_key,
                turn_id=turn_id,
                seq=seq,
                created_at=datetime.now(UTC),
                todos=items,
                snapshot_digest=_snapshot_digest(items),
            )
            self._write_line(path, entry.model_dump_json(by_alias=True))
            if original_safe:
                self._maybe_upsert_index(safe_key, entry)
        except Exception:  # noqa: BLE001 - ledger trouble must never abort a turn
            _LOGGER.warning("plan_ledger: append degraded to in-memory", exc_info=True)

    def _maybe_upsert_index(self, safe_key: str, entry: PlanLedgerEntry) -> None:
        """Upsert the content-free WS1 index record, if BOTH a store and a valid
        policy digest are available. A ``None`` digest is a deliberate no-op
        (the digest precondition is checked BEFORE the ``DurableRecord`` build),
        not a silent crash-skip of an unconstructable record.
        """
        store = self._durable_store
        if store is None or self._policy_digest is None:
            return
        put = getattr(store, "put", None)
        if not callable(put):
            return
        open_todos = sum(1 for item in entry.todos if item.status != "completed")
        try:
            from magi_agent.storage.durable_store import DurableRecord

            record = DurableRecord(
                collection="plan_ledger",
                recordId=f"plan_ledger:{safe_key}",
                contentDigest=entry.snapshot_digest,
                policySnapshotDigest=self._policy_digest,
                metadata={
                    "openTodos": open_todos,
                    "totalTodos": len(entry.todos),
                    "seq": entry.seq,
                    "ref": f"plan_ledger:{safe_key}",
                },
            )
            put(record)
        except Exception:  # noqa: BLE001 - index is a discovery optimization only
            _LOGGER.warning(
                "plan_ledger: durable index upsert skipped", exc_info=True
            )

    # -- reads -------------------------------------------------------------

    def restore(self, session_id: str | None) -> tuple[TodoItem, ...]:
        """Return the latest valid snapshot from the JSONL, skipping a torn
        trailing line. Returns ``()`` when the session has no ledger or on any
        read error.
        """
        try:
            safe_key, _ = self._safe_session_id(session_id)
            path = self._path_for(safe_key)
            if not path.exists():
                return ()
            lines = path.read_text("utf-8").splitlines()
            for raw in reversed(lines):
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = PlanLedgerEntry.model_validate_json(line)
                except Exception:  # noqa: BLE001 - skip torn/invalid trailing lines
                    continue
                return entry.todos
            return ()
        except Exception:  # noqa: BLE001 - restore trouble degrades to in-memory
            _LOGGER.warning("plan_ledger: restore degraded to empty", exc_info=True)
            return ()


def coerce_todo_items(todos: object) -> tuple[TodoItem, ...]:
    """Public helper: normalize caller todos into the canonical snapshot tuple."""
    return _coerce_items(todos)


def todo_item_to_dict(item: object) -> dict[str, object]:
    """Coerce a restored ledger item back into the in-memory dict shape."""
    content = getattr(item, "content", None)
    status = getattr(item, "status", None)
    if content is None and isinstance(item, dict):
        content = item.get("content")
        status = item.get("status")
    return {
        "content": content if isinstance(content, str) else "",
        "status": status if status in _VALID_TODO_STATUSES else "pending",
    }


__all__ = [
    "LEDGER_SCHEMA",
    "PlanLedgerEntry",
    "PlanLedgerStore",
    "TodoItem",
    "coerce_todo_items",
    "todo_item_to_dict",
]
