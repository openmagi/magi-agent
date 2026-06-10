"""Durable JSONL-backed :class:`ControlRequestStore` (doc 09 PR-4 / A7).

The base :class:`magi_agent.runtime.control.ControlRequestStore` keeps its whole
approval lifecycle in process memory, so a crash or restart drops every pending
approval. That is fine for the synchronous CLI sink race, but it makes
out-of-band / always-on approval (a human approving later via a channel or the
gateway daemon) impossible: there is nowhere to look up the pending request once
the originating process is gone.

``DurableControlRequestStore`` is a drop-in subclass that adds *persistence
only*. It:

* appends one append-only JSONL line per lifecycle mutation (create / resolve /
  cancel / expire), each carrying the full post-mutation
  :class:`ControlRequestRecord` snapshot plus the ``seq`` watermark, and
* on construction, replays the JSONL log to rebuild the in-memory pending /
  terminal / idempotency maps and the ledger before any new mutation runs.

It deliberately does NOT change the original ``ControlRequestStore`` (whose
``durable_writes_enabled: Literal[False]`` is preserved for backward compat).
The in-memory store stays byte-identical; durability is opt-in behind the
``MAGI_CONTROL_STORE_DURABLE`` gate (see
:func:`magi_agent.config.env.control_store_durable_enabled`).

Concurrency note: writes are line-oriented appends. A single-writer assumption
holds for the current CLI gate. A multi-process gateway writer (PR-5 / 03) must
add an external file lock — this PR intentionally keeps the backend dependency
free (no SQLite, no fcntl) per the open-decision in doc 09 §6.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from magi_agent.runtime.control import (
    ControlRequestRecord,
    ControlRequestStore,
    ControlRequestStoreResult,
)

# JSONL schema version for the persisted lifecycle log. Bumped only on a
# breaking change to the on-disk line shape; unknown versions are skipped
# (fail-open) so an old binary never crashes on a newer log.
_LOG_VERSION = 1


class DurableControlRequestStore(ControlRequestStore):
    """In-memory :class:`ControlRequestStore` backed by an append-only JSONL log.

    Persistence is additive: every public mutation runs the parent's in-memory
    logic first (so records, events, idempotency and sequencing are identical to
    the volatile store), then a snapshot line is appended to ``path``.
    """

    def __init__(self, *, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        super().__init__()
        self._replay()

    @property
    def path(self) -> Path:
        return self._path

    # -- mutation overrides: run parent logic, then persist the snapshot ----

    def create_tool_permission_request(
        self, **kwargs: object
    ) -> ControlRequestStoreResult:
        result = super().create_tool_permission_request(**kwargs)  # type: ignore[arg-type]
        self._persist(result)
        return result

    def create_user_question_request(
        self, **kwargs: object
    ) -> ControlRequestStoreResult:
        result = super().create_user_question_request(**kwargs)  # type: ignore[arg-type]
        self._persist(result)
        return result

    def resolve_request(
        self, request_id: str, **kwargs: object
    ) -> ControlRequestStoreResult:
        result = super().resolve_request(request_id, **kwargs)  # type: ignore[arg-type]
        self._persist(result)
        return result

    def expire_request(
        self, request_id: str, **kwargs: object
    ) -> ControlRequestStoreResult | None:
        result = super().expire_request(request_id, **kwargs)  # type: ignore[arg-type]
        if result is not None:
            self._persist(result)
        return result

    def cancel_request(
        self, request_id: str, **kwargs: object
    ) -> ControlRequestStoreResult:
        result = super().cancel_request(request_id, **kwargs)  # type: ignore[arg-type]
        self._persist(result)
        return result

    # -- persistence -------------------------------------------------------

    def _persist(self, result: ControlRequestStoreResult) -> None:
        # A duplicate (idempotency hit) made no state change — nothing new to
        # log. This keeps the JSONL log free of redundant snapshots and keeps
        # replay deterministic.
        if result.duplicate:
            return
        line = json.dumps(
            {
                "v": _LOG_VERSION,
                "seq": self._seq,
                "record": result.record.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _replay(self) -> None:
        if not self._path.exists():
            return
        max_seq = 0
        with self._path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                snapshot = _parse_line(raw)
                if snapshot is None:
                    continue
                record, seq = snapshot
                self._apply_replayed(record)
                if seq > max_seq:
                    max_seq = seq
        # Continue the event sequence past the highest persisted watermark so a
        # new mutation never collides with a replayed ledger seq.
        self._seq = max_seq

    def _apply_replayed(self, record: ControlRequestRecord) -> None:
        request_id = record.request_id
        if record.state == "pending":
            self._pending_by_id[request_id] = record
            self._terminal_by_id.pop(request_id, None)
        else:
            self._terminal_by_id[request_id] = record
            self._pending_by_id.pop(request_id, None)
        if record.idempotency_key:
            self._idempotency_to_request_id[record.idempotency_key] = request_id


def _parse_line(raw: str) -> tuple[ControlRequestRecord, int] | None:
    """Parse one JSONL line into a ``(record, seq)`` pair, or ``None`` to skip.

    Corrupt / blank / unknown-version lines are skipped (fail-open) so a torn
    write at crash time never aborts replay of the surrounding valid records.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != _LOG_VERSION:
        return None
    record_payload = payload.get("record")
    if not isinstance(record_payload, dict):
        return None
    try:
        record = ControlRequestRecord.model_validate(record_payload)
    except Exception:  # noqa: BLE001 — fail-open on a malformed snapshot
        return None
    seq_raw = payload.get("seq", 0)
    seq = seq_raw if isinstance(seq_raw, int) else 0
    return record, seq
