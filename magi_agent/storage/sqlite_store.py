from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .durable_store import (
    ArtifactIndexRecord,
    CorruptionReport,
    DurableRecord,
    DurableStoreReceipt,
    DurableStoreSafetyError,
    DurableStoreSchemaVersion,
)

_REQUIRED_TRIGGERS = frozenset(
    {
        "durable_records_no_update",
        "durable_records_no_delete",
        "artifact_index_no_update",
        "artifact_index_no_delete",
        "append_ledger_no_update",
        "append_ledger_no_delete",
        "schema_metadata_no_update",
        "schema_metadata_no_delete",
    }
)
_MANAGED_TABLES = frozenset(
    {
        "durable_records",
        "artifact_index",
        "append_ledger",
        "schema_metadata",
    }
)


class SQLiteDurableStore:
    """PVC-local SQLite metadata store.

    This adapter stores digest-addressed metadata only. Artifact bytes remain
    outside SQLite behind artifact:// refs.
    """

    openmagi_local_sqlite_adapter = True

    def __init__(
        self,
        path: Path | str,
        *,
        wal: bool = True,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.path = Path(path)
        self.wal = wal
        self.busy_timeout_ms = busy_timeout_ms
        self.ledger_head_path = Path(f"{self.path}.ledger-head")
        self.ledger_events_path = Path(f"{self.path}.ledger-events")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            if _managed_schema_exists(conn):
                _assert_required_schema(conn)
            if self.wal:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS durable_records (
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    record_digest TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    policy_snapshot_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (collection, record_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_index (
                    artifact_id TEXT PRIMARY KEY,
                    content_digest TEXT NOT NULL,
                    blob_ref TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    render_receipt_digest TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS append_ledger (
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    record_digest TEXT NOT NULL,
                    entry_digest TEXT NOT NULL,
                    PRIMARY KEY (collection, record_id)
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_metadata(key, value) VALUES (?, ?)",
                ("schemaVersion", DurableStoreSchemaVersion.CURRENT),
            )
            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS durable_records_no_update
                BEFORE UPDATE ON durable_records
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS durable_records_no_delete
                BEFORE DELETE ON durable_records
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS artifact_index_no_update
                BEFORE UPDATE ON artifact_index
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS artifact_index_no_delete
                BEFORE DELETE ON artifact_index
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS append_ledger_no_update
                BEFORE UPDATE ON append_ledger
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS append_ledger_no_delete
                BEFORE DELETE ON append_ledger
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS schema_metadata_no_update
                BEFORE UPDATE ON schema_metadata
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS schema_metadata_no_delete
                BEFORE DELETE ON schema_metadata
                BEGIN
                    SELECT RAISE(ABORT, 'durable store is append-only');
                END;
                """
            )

    def append(self, record: DurableRecord) -> DurableStoreReceipt:
        record = DurableRecord.model_validate(record.model_dump(by_alias=True, mode="json"))
        self.initialize()
        previous_root = self._current_ledger_root()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO durable_records(
                        collection,
                        record_id,
                        record_digest,
                        content_digest,
                        policy_snapshot_digest,
                        payload_json,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.collection,
                        record.record_id,
                        record.record_digest,
                        record.content_digest,
                        record.policy_snapshot_digest,
                        json.dumps(record.storage_payload(), sort_keys=True),
                        record.storage_payload()["createdAt"],
                    ),
                )
                _append_ledger_entry(
                    conn,
                    collection=record.collection,
                    record_id=record.record_id,
                    record_digest=record.record_digest,
                )
        except sqlite3.IntegrityError as exc:
            raise DurableStoreSafetyError("durable store is append-only") from exc
        current_root = self._current_ledger_root()
        self._append_sidecar_event(
            previous_root=previous_root,
            ledger_root=current_root,
            collection=record.collection,
            record_id=record.record_id,
            record_digest=record.record_digest,
        )
        self._write_ledger_head(current_root)

        return DurableStoreReceipt(
            status="stored",
            collection=record.collection,
            recordId=record.record_id,
            recordDigest=record.record_digest,
            reasonCodes=("stored_sqlite_metadata",),
        )

    def get(self, collection: str, record_id: str) -> DurableRecord | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    collection,
                    record_id,
                    record_digest,
                    content_digest,
                    policy_snapshot_digest,
                    created_at,
                    payload_json
                FROM durable_records
                WHERE collection = ? AND record_id = ?
                """,
                (collection, record_id),
            ).fetchone()
        if row is None:
            self._verify_missing_record_absent_from_ledger(collection, record_id)
            return None
        payload = _validated_record_payload(row)
        self._verify_ledger_entries([(row[0], row[1], row[2])], exact=False)
        return DurableRecord.model_validate(payload)

    def put_artifact_index(self, record: ArtifactIndexRecord) -> DurableStoreReceipt:
        record = ArtifactIndexRecord.model_validate(record.model_dump(by_alias=True, mode="json"))
        self.initialize()
        previous_root = self._current_ledger_root()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO artifact_index(
                        artifact_id,
                        content_digest,
                        blob_ref,
                        size_bytes,
                        render_receipt_digest,
                        artifact_digest,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.artifact_id,
                        record.content_digest,
                        record.blob_ref,
                        record.size_bytes,
                        record.render_receipt_digest,
                        record.artifact_digest,
                        json.dumps(record.storage_payload(), sort_keys=True),
                    ),
                )
                _append_ledger_entry(
                    conn,
                    collection="artifact_index",
                    record_id=record.artifact_id,
                    record_digest=record.artifact_digest,
                )
        except sqlite3.IntegrityError as exc:
            raise DurableStoreSafetyError("durable store is append-only") from exc
        current_root = self._current_ledger_root()
        self._append_sidecar_event(
            previous_root=previous_root,
            ledger_root=current_root,
            collection="artifact_index",
            record_id=record.artifact_id,
            record_digest=record.artifact_digest,
        )
        self._write_ledger_head(current_root)
        return DurableStoreReceipt(
            status="stored",
            collection="artifact_index",
            recordId=record.artifact_id,
            recordDigest=record.artifact_digest,
            reasonCodes=("artifact_index_stored",),
        )

    def export_records(self) -> dict[str, object]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    collection,
                    record_id,
                    record_digest,
                    content_digest,
                    policy_snapshot_digest,
                    created_at,
                    payload_json
                FROM durable_records
                ORDER BY collection, record_id
                """
            ).fetchall()
            artifact_rows = conn.execute(
                """
                SELECT
                    artifact_id,
                    content_digest,
                    blob_ref,
                    size_bytes,
                    render_receipt_digest,
                    artifact_digest,
                    payload_json
                FROM artifact_index
                ORDER BY artifact_id
                """
            ).fetchall()
        record_payloads = [_validated_record_payload(row) for row in rows]
        artifact_payloads = [_validated_artifact_payload(row) for row in artifact_rows]
        ledger_items = [
            (row[0], row[1], row[2])
            for row in rows
        ] + [
            ("artifact_index", row[0], row[5])
            for row in artifact_rows
        ]
        self._verify_ledger_entries(ledger_items, exact=True)
        return {
            "schemaVersion": DurableStoreSchemaVersion.CURRENT,
            "integrityMode": "local_logical_integrity",
            "externalTamperEvidence": False,
            "externalAnchorRequired": True,
            "records": record_payloads,
            "artifacts": artifact_payloads,
        }

    def corruption_report(self) -> CorruptionReport:
        try:
            with self._connect() as conn:
                row = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError:
            return CorruptionReport(ok=False, reasonCodes=("sqlite_integrity_check_failed",))
        ok = row is not None and row[0] == "ok"
        if ok:
            try:
                self.export_records()
            except (DurableStoreSafetyError, ValueError):
                return CorruptionReport(
                    ok=False,
                    reasonCodes=("durable_logical_integrity_failed",),
                )
        return CorruptionReport(
            ok=ok,
            reasonCodes=() if ok else ("sqlite_integrity_check_failed",),
        )

    def migration_report(self) -> dict[str, object]:
        self.initialize()
        return {
            "schemaVersion": DurableStoreSchemaVersion.CURRENT,
            "migrationRequired": False,
            "appliedMigrations": [],
            "integrityMode": "local_logical_integrity",
            "externalTamperEvidence": False,
            "externalAnchorRequired": True,
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        conn.row_factory = _row_factory
        return conn

    def _verify_ledger_entries(
        self,
        expected: list[tuple[object, object, object]],
        *,
        exact: bool,
    ) -> None:
        self.initialize()
        expected_set = {
            (str(collection), str(record_id), str(record_digest))
            for collection, record_id, record_digest in expected
        }
        with self._connect() as conn:
            if exact:
                ledger_rows = conn.execute(
                    """
                    SELECT collection, record_id, record_digest, entry_digest
                    FROM append_ledger
                    ORDER BY collection, record_id
                    """
                ).fetchall()
            else:
                ledger_rows = [
                    conn.execute(
                        """
                        SELECT collection, record_id, record_digest, entry_digest
                        FROM append_ledger
                        WHERE collection = ? AND record_id = ?
                        """,
                        (collection, record_id),
                    ).fetchone()
                    for collection, record_id, _record_digest in expected_set
                ]
                if any(row is None for row in ledger_rows):
                    raise DurableStoreSafetyError("durable ledger integrity check failed")
        ledger_set = set()
        for collection, record_id, record_digest, entry_digest in ledger_rows:
            if entry_digest != _ledger_entry_digest(collection, record_id, record_digest):
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            ledger_set.add((collection, record_id, record_digest))
        if ledger_set != expected_set:
            raise DurableStoreSafetyError("durable ledger integrity check failed")
        self._verify_ledger_head()

    def _verify_missing_record_absent_from_ledger(self, collection: str, record_id: str) -> None:
        self._verify_ledger_head()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM append_ledger
                WHERE collection = ? AND record_id = ?
                """,
                (collection, record_id),
            ).fetchone()
        if row is not None:
            raise DurableStoreSafetyError("durable ledger integrity check failed")

    def _current_ledger_root(self) -> str:
        return _ledger_root(self._current_ledger_rows())

    def _current_ledger_rows(self) -> list[tuple[Any, ...]]:
        with self._connect() as conn:
            ledger_rows = conn.execute(
                """
                SELECT collection, record_id, record_digest, entry_digest
                FROM append_ledger
                ORDER BY collection, record_id
                """
            ).fetchall()
        return list(ledger_rows)

    def _write_ledger_head(self, ledger_root: str) -> None:
        self.ledger_head_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.ledger_head_path.with_name(f"{self.ledger_head_path.name}.tmp")
        temp_path.write_text(ledger_root + "\n")
        temp_path.replace(self.ledger_head_path)

    def _verify_ledger_head(self) -> None:
        ledger_rows = self._current_ledger_rows()
        current = _ledger_root(ledger_rows)
        if (
            current == _ledger_root([])
            and not self.ledger_head_path.exists()
            and not self.ledger_events_path.exists()
        ):
            return
        try:
            recorded = self.ledger_head_path.read_text().strip()
        except OSError as exc:
            raise DurableStoreSafetyError("durable ledger integrity check failed") from exc
        if recorded != current:
            raise DurableStoreSafetyError("durable ledger integrity check failed")
        self._verify_sidecar_events(ledger_rows=ledger_rows, ledger_root=current)

    def _append_sidecar_event(
        self,
        *,
        previous_root: str,
        ledger_root: str,
        collection: str,
        record_id: str,
        record_digest: str,
    ) -> None:
        # Same-PVC sidecars catch accidental/partial corruption. They are not
        # an external tamper-evidence authority; see export/migration reports.
        self.ledger_events_path.mkdir(parents=True, exist_ok=True)
        sequence = len(list(self.ledger_events_path.glob("*.json"))) + 1
        event_payload: dict[str, object] = {
            "schemaVersion": DurableStoreSchemaVersion.CURRENT,
            "sequence": sequence,
            "previousLedgerRoot": previous_root,
            "ledgerRoot": ledger_root,
            "collection": collection,
            "recordId": record_id,
            "recordDigest": record_digest,
            "entryDigest": _ledger_entry_digest(collection, record_id, record_digest),
        }
        event_digest = _digest_json(event_payload)
        event_payload["eventDigest"] = event_digest
        temp_path = self.ledger_events_path / f"{sequence:020d}-{event_digest[7:]}.json.tmp"
        final_path = self.ledger_events_path / f"{sequence:020d}-{event_digest[7:]}.json"
        temp_path.write_text(
            json.dumps(event_payload, sort_keys=True, separators=(",", ":")) + "\n"
        )
        temp_path.replace(final_path)

    def _read_sidecar_events(self) -> list[dict[str, object]]:
        if not self.ledger_events_path.exists():
            return []
        events: list[dict[str, object]] = []
        for event_path in sorted(self.ledger_events_path.glob("*.json")):
            payload = json.loads(event_path.read_text())
            if not isinstance(payload, dict):
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            event_digest = payload.get("eventDigest")
            if not isinstance(event_digest, str):
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            payload_without_digest = dict(payload)
            payload_without_digest.pop("eventDigest", None)
            expected_digest = _digest_json(payload_without_digest)
            if event_digest != expected_digest or event_digest[7:] not in event_path.name:
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            events.append(payload)
        return events

    def _verify_sidecar_events(
        self,
        *,
        ledger_rows: list[tuple[Any, ...]],
        ledger_root: str,
    ) -> None:
        events = self._read_sidecar_events()
        if len(events) != len(ledger_rows):
            raise DurableStoreSafetyError("durable ledger integrity check failed")
        expected_set = {(row[0], row[1], row[2]) for row in ledger_rows}
        event_set = set()
        previous_root = _ledger_root([])
        for expected_sequence, event in enumerate(events, start=1):
            if event.get("schemaVersion") != DurableStoreSchemaVersion.CURRENT:
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            if event.get("sequence") != expected_sequence:
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            collection = event.get("collection")
            record_id = event.get("recordId")
            record_digest = event.get("recordDigest")
            entry_digest = event.get("entryDigest")
            event_root = event.get("ledgerRoot")
            if not all(
                isinstance(value, str)
                for value in (
                    collection,
                    record_id,
                    record_digest,
                    entry_digest,
                    event_root,
                    event.get("previousLedgerRoot"),
                )
            ):
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            if event.get("previousLedgerRoot") != previous_root:
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            if entry_digest != _ledger_entry_digest(collection, record_id, record_digest):
                raise DurableStoreSafetyError("durable ledger integrity check failed")
            event_set.add((collection, record_id, record_digest))
            previous_root = event_root
        if previous_root != ledger_root or event_set != expected_set:
            raise DurableStoreSafetyError("durable ledger integrity check failed")


def _row_factory(_cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
    return row


def _validated_record_payload(row: tuple[Any, ...]) -> dict[str, object]:
    (
        collection,
        record_id,
        record_digest,
        content_digest,
        policy_snapshot_digest,
        created_at,
        payload_json,
    ) = row
    record = DurableRecord.model_validate(json.loads(payload_json))
    if (
        record.collection != collection
        or record.record_id != record_id
        or record.content_digest != content_digest
        or record.policy_snapshot_digest != policy_snapshot_digest
        or record.record_digest != record_digest
        or _canonical_datetime(record.created_at.isoformat()) != _canonical_datetime(str(created_at))
    ):
        raise DurableStoreSafetyError("durable row integrity check failed")
    return record.storage_payload()


def _validated_artifact_payload(row: tuple[Any, ...]) -> dict[str, object]:
    (
        artifact_id,
        content_digest,
        blob_ref,
        size_bytes,
        render_receipt_digest,
        artifact_digest,
        payload_json,
    ) = row
    artifact = ArtifactIndexRecord.model_validate(json.loads(payload_json))
    if (
        artifact.artifact_id != artifact_id
        or artifact.content_digest != content_digest
        or artifact.blob_ref != blob_ref
        or artifact.size_bytes != size_bytes
        or artifact.render_receipt_digest != render_receipt_digest
        or artifact.artifact_digest != artifact_digest
    ):
        raise DurableStoreSafetyError("artifact row integrity check failed")
    return artifact.storage_payload()


def _append_ledger_entry(
    conn: sqlite3.Connection,
    *,
    collection: str,
    record_id: str,
    record_digest: str,
) -> None:
    conn.execute(
        """
        INSERT INTO append_ledger(collection, record_id, record_digest, entry_digest)
        VALUES (?, ?, ?, ?)
        """,
        (
            collection,
            record_id,
            record_digest,
            _ledger_entry_digest(collection, record_id, record_digest),
        ),
    )


def _ledger_entry_digest(collection: str, record_id: str, record_digest: str) -> str:
    encoded = json.dumps(
        {
            "schemaVersion": DurableStoreSchemaVersion.CURRENT,
            "collection": collection,
            "recordId": record_id,
            "recordDigest": record_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _managed_schema_exists(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()
    return any(row[0] in _MANAGED_TABLES for row in rows)


def _assert_required_schema(conn: sqlite3.Connection) -> None:
    table_rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()
    present_tables = {row[0] for row in table_rows}
    if not _MANAGED_TABLES.issubset(present_tables):
        raise DurableStoreSafetyError("durable schema integrity check failed")
    version = conn.execute(
        "SELECT value FROM schema_metadata WHERE key = ?",
        ("schemaVersion",),
    ).fetchone()
    if version is None or version[0] != DurableStoreSchemaVersion.CURRENT:
        raise DurableStoreSafetyError("durable schema integrity check failed")
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'trigger'
        """
    ).fetchall()
    present = {row[0] for row in rows}
    if not _REQUIRED_TRIGGERS.issubset(present):
        raise DurableStoreSafetyError("durable schema integrity check failed")


def _canonical_datetime(value: str) -> str:
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).isoformat()


def _ledger_root(rows: list[tuple[Any, ...]]) -> str:
    encoded = json.dumps(
        {
            "schemaVersion": DurableStoreSchemaVersion.CURRENT,
            "ledgerRows": [list(row) for row in rows],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _digest_json(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
