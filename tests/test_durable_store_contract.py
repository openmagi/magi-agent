from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from magi_agent.storage.durable_store import (
    ArtifactIndexRecord,
    DurableStoreConfig,
    DurableRecord,
    DurableStoreKind,
    DurableStoreReceipt,
    DurableStoreSafetyError,
    DurableStoreSchemaVersion,
    ReplayDecision,
    durable_store_config_from_env,
)
from magi_agent.storage.memory_store import InMemoryDurableStore
from magi_agent.storage.sqlite_store import SQLiteDurableStore


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
ARTIFACT_REF_A = f"artifact://filesystem/{DIGEST_A}"
ARTIFACT_REF_C = f"artifact://filesystem/{DIGEST_C}"


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


def _ledger_root(rows: list[tuple[object, ...]]) -> str:
    encoded = json.dumps(
        {
            "schemaVersion": DurableStoreSchemaVersion.CURRENT,
            "ledgerRows": [list(row) for row in rows],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_durable_store_env_defaults_to_oss_sqlite_and_never_requires_hosted_db() -> None:
    config = durable_store_config_from_env({})

    assert config.kind == "sqlite"
    assert config.sqlite_path == Path("/var/lib/openmagi/runtime/durable.sqlite")
    assert config.artifact_path == Path("/var/lib/openmagi/runtime/artifacts")
    assert config.export_path == Path("/var/lib/openmagi/runtime/exports")
    assert config.artifact_store == "filesystem"
    assert config.hosted_sync_required is False
    assert config.production_writes_enabled is False


def test_durable_store_env_builds_sqlite_config_without_live_supabase_requirement(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime" / "durable.sqlite"
    artifact_path = tmp_path / "artifacts"
    export_path = tmp_path / "exports"

    config = durable_store_config_from_env(
        {
            "OPENMAGI_DURABLE_STORE": "sqlite",
            "OPENMAGI_DURABLE_SQLITE_PATH": str(db_path),
            "OPENMAGI_ARTIFACT_STORE": "filesystem",
            "OPENMAGI_ARTIFACT_PATH": str(artifact_path),
            "OPENMAGI_RUNTIME_EXPORT_PATH": str(export_path),
            "OPENMAGI_DURABLE_SQLITE_WAL": "1",
            "OPENMAGI_DURABLE_SQLITE_BUSY_TIMEOUT_MS": "5000",
        }
    )

    assert config.kind == "sqlite"
    assert config.sqlite_path == db_path
    assert config.sqlite_wal is True
    assert config.sqlite_busy_timeout_ms == 5000
    assert config.artifact_path == artifact_path
    assert config.export_path == export_path
    assert config.postgres_dsn_ref is None
    assert config.hosted_sync_required is False


def test_durable_store_config_cannot_be_forged_to_enable_production_writes(
    tmp_path: Path,
) -> None:
    config = DurableStoreConfig(
        kind="sqlite",
        sqlitePath=tmp_path / "durable.sqlite",
        artifactStore="filesystem",
    )
    forged = DurableStoreConfig.model_construct(
        kind="memory",
        artifactStore="filesystem",
        production_writes_enabled=True,
        postgres_dsn_ref="postgres://user@example.invalid/db",
    )
    copied = config.model_copy(update={"productionWritesEnabled": True})

    assert forged.production_writes_enabled is False
    assert copied.production_writes_enabled is False
    assert forged.postgres_dsn_ref is None
    assert forged.model_dump(by_alias=True)["productionWritesEnabled"] is False
    assert copied.model_dump(by_alias=True)["productionWritesEnabled"] is False
    assert forged.model_dump(by_alias=True)["postgresDsnRef"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("prompt", "raw prompt text"),
        ("rawOutput", "raw model answer"),
        ("authorization", "Bearer unsafe"),
        ("cookie", "sid=unsafe"),
        ("sessionKey", "session-unsafe"),
        ("connectorToken", "token-unsafe"),
        ("secretMaterial", "sk-unsafe"),
    ),
)
def test_durable_record_rejects_raw_sensitive_fields(key: str, value: str) -> None:
    with pytest.raises(ValueError, match="raw or sensitive"):
        DurableRecord(
            collection="sessions",
            recordId="session-1",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={key: value},
        )


@pytest.mark.parametrize(
    "value",
    (
        "password=unsafe",
        "api_key=unsafe",
        "connector_token=unsafe",
        "session_key=unsafe",
        "private_key=unsafe",
        "auth_key=unsafe",
        "secret: unsafe",
        "credential=unsafe",
        "Authorization%3A%20Bearer%20unsafe",
    ),
)
def test_durable_record_rejects_sensitive_assignment_values(value: str) -> None:
    with pytest.raises(ValueError, match="raw or sensitive"):
        DurableRecord(
            collection="sessions",
            recordId="session-sensitive-value",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeLooking": value},
        )


@pytest.mark.parametrize(
    "value",
    (
        "auth:BearerABC123",
        "session:sessionKeyABC123",
        "connector:connectorTokenABC123",
        "credential:privateKeyABC123",
        "token:ABC123",
    ),
)
def test_durable_record_rejects_sensitive_safe_ref_values(value: str) -> None:
    with pytest.raises(ValueError, match="raw or sensitive"):
        DurableRecord(
            collection="sessions",
            recordId="session-sensitive-ref",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeRef": value},
        )


def test_durable_record_rejects_free_form_metadata_strings_under_safe_keys() -> None:
    with pytest.raises(ValueError, match="safe refs or digests"):
        DurableRecord(
            collection="sessions",
            recordId="session-free-form",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeRef": "Please remember that I prefer concise answers."},
        )


@pytest.mark.parametrize(
    "metadata",
    (
        {"safeRef": Path("Authorization: Bearer unsafe")},
        {"safeRef": Path("data:text/plain;base64,cmF3IGJ5dGVz")},
        {"safeRef": Path("/Users/kevin/private")},
    ),
)
def test_durable_record_rejects_pathlike_metadata_values(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="metadata"):
        DurableRecord(
            collection="sessions",
            recordId="session-pathlike",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata=metadata,
        )


def test_durable_record_digest_binds_created_at() -> None:
    first = DurableRecord(
        collection="sessions",
        recordId="session-created-at",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
        createdAt=datetime(2026, 5, 24, 1, 0, tzinfo=UTC),
    )
    second = DurableRecord(
        collection="sessions",
        recordId="session-created-at",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
        createdAt=datetime(2026, 5, 24, 2, 0, tzinfo=UTC),
    )

    assert first.record_digest != second.record_digest


def test_in_memory_durable_store_is_append_only_and_keeps_policy_snapshot() -> None:
    store = InMemoryDurableStore()
    record = DurableRecord(
        collection="checkpoints",
        recordId="checkpoint-1",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "checkpoint:1"},
    )

    receipt = store.append(record)
    loaded = store.get("checkpoints", "checkpoint-1")

    assert receipt.status == "stored"
    assert receipt.record_digest.startswith("sha256:")
    assert loaded == record
    assert loaded.policy_snapshot_digest == DIGEST_B

    with pytest.raises(DurableStoreSafetyError, match="append-only"):
        store.append(record)


def test_in_memory_store_returns_defensive_records_and_revalidates_export() -> None:
    store = InMemoryDurableStore()
    store.append(
        DurableRecord(
            collection="sessions",
            recordId="session-defensive",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeRef": "session:1"},
        )
    )

    loaded = store.get("sessions", "session-defensive")
    assert loaded is not None
    loaded.metadata["prompt"] = "raw prompt text"

    exported = store.export_records()

    assert "raw prompt text" not in str(exported)
    assert exported["records"][0]["metadata"] == {"safeRef": "session:1"}


def test_durable_store_revalidates_forged_records_before_persistence() -> None:
    store = InMemoryDurableStore()
    forged = DurableRecord.model_construct(
        collection="sessions",
        record_id="forged-session",
        content_digest=DIGEST_A,
        policy_snapshot_digest=DIGEST_B,
        metadata={"prompt": "raw prompt text"},
    )

    with pytest.raises(ValueError, match="raw or sensitive"):
        store.append(forged)


def test_durable_record_rejects_sensitive_values_nested_inside_lists() -> None:
    with pytest.raises(ValueError, match="raw or sensitive"):
        DurableRecord(
            collection="sessions",
            recordId="session-nested",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeNested": [["Authorization: Bearer unsafe"]]},
        )


@pytest.mark.parametrize(
    "metadata",
    (
        {"safeRef": "data:text/plain;base64,cmF3IGJ5dGVz"},
        {"safeRef": "data:text/plain,raw-bytes"},
        {"safeRef": "data:text/plain;charset=utf-8;base64,cmF3IGJ5dGVz"},
        {"safeRef": "data%3Atext%2Fplain%3Bbase64%2CcmF3IGJ5dGVz"},
        {"safeRef": "data%3Atext%2Fplain%2Craw-bytes"},
        {"nested": {"safeRef": "inline:cmF3LWJ5dGVzLWJsb2I="}},
        {"nested": [{"safeRef": "blob:cmF3LWJ5dGVzLWJsb2I="}]},
    ),
)
def test_durable_record_rejects_inline_blob_values_under_safe_keys(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="artifact blobs"):
        DurableRecord(
            collection="sessions",
            recordId="session-inline-blob",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    (
        {"/Users/kevin/.ssh/id_rsa": "digest-ref"},
        {"nested": {"/home/kevin/.kube/config": "digest-ref"}},
        {"safeRef": ".ssh/id_rsa"},
        {"safeRef": "~/.ssh/id_rsa"},
        {"safeRef": "project/.kube/config"},
        {"safeRef": "/tmp/.aws/credentials"},
    ),
)
def test_durable_record_rejects_private_path_keys_and_dotdir_values(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="raw or sensitive"):
        DurableRecord(
            collection="sessions",
            recordId="session-private-path-metadata",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata=metadata,
        )


@pytest.mark.parametrize(
    ("field", "kwargs"),
    (
        ("collection", {"collection": "/Users/kevin/private", "recordId": "record-1"}),
        ("recordId", {"collection": "sessions", "recordId": "/Users/kevin/private"}),
    ),
)
def test_durable_record_rejects_private_path_shaped_identifiers(
    field: str,
    kwargs: dict[str, str],
) -> None:
    _ = field
    with pytest.raises(ValueError, match="private path"):
        DurableRecord(
            **kwargs,
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
        )


@pytest.mark.parametrize(
    ("field", "kwargs"),
    (
        ("collection", {"collection": "auth:BearerABC123", "recordId": "record-1"}),
        ("recordId", {"collection": "sessions", "recordId": "auth:BearerABC123"}),
        ("recordId", {"collection": "sessions", "recordId": "session:sessionKeyABC123"}),
    ),
)
def test_durable_record_rejects_sensitive_identifier_refs(
    field: str,
    kwargs: dict[str, str],
) -> None:
    _ = field
    with pytest.raises(ValueError, match="raw or sensitive"):
        DurableRecord(
            **kwargs,
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
        )


def test_artifact_index_revalidates_forged_inline_blob_records(tmp_path: Path) -> None:
    store = SQLiteDurableStore(tmp_path / "durable.sqlite")
    forged = ArtifactIndexRecord.model_construct(
        artifact_id="artifact-forged",
        content_digest=DIGEST_A,
        blob_ref="inline:raw-bytes",
        size_bytes=32,
        render_receipt_digest=DIGEST_B,
        metadata={"blob": "raw bytes"},
    )

    with pytest.raises(ValueError, match="artifact blobs"):
        store.put_artifact_index(forged)


def test_artifact_index_rejects_private_path_shaped_artifact_id() -> None:
    with pytest.raises(ValueError, match="private path"):
        ArtifactIndexRecord(
            artifactId="/Users/kevin/private",
            contentDigest=DIGEST_A,
            blobRef=ARTIFACT_REF_A,
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
        )


@pytest.mark.parametrize(
    "metadata",
    (
        {"nested": {"blob": "raw bytes"}},
        {"nested": [{"content": "raw bytes"}]},
        {"nested": {"artifactContent": "raw bytes"}},
        {"nested": {"payload": "raw bytes"}},
        {"nested": [{"fileData": "raw bytes"}]},
        {"nested": {"body": "raw bytes"}},
        {"nested": {"uri": "data:text/plain;base64,cmF3IGJ5dGVz"}},
    ),
)
def test_artifact_index_rejects_nested_artifact_blob_metadata(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="artifact blobs"):
        ArtifactIndexRecord(
            artifactId="artifact-nested",
            contentDigest=DIGEST_A,
            blobRef=ARTIFACT_REF_A,
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
            metadata=metadata,
        )


def test_artifact_index_rejects_private_path_metadata_keys() -> None:
    with pytest.raises(ValueError, match="raw or sensitive"):
        ArtifactIndexRecord(
            artifactId="artifact-private-metadata",
            contentDigest=DIGEST_A,
            blobRef=ARTIFACT_REF_A,
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
            metadata={"nested": {"/Users/kevin/.ssh/id_rsa": "digest-ref"}},
        )


def test_artifact_index_rejects_pathlike_metadata_values() -> None:
    with pytest.raises(ValueError, match="metadata"):
        ArtifactIndexRecord(
            artifactId="artifact-pathlike-metadata",
            contentDigest=DIGEST_A,
            blobRef=ARTIFACT_REF_A,
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
            metadata={"safeRef": Path("data:text/plain;base64,cmF3IGJ5dGVz")},
        )


def test_replay_decision_never_allows_side_effects_even_when_forged() -> None:
    decision = ReplayDecision.model_validate(
        {"mode": "replay", "allowSideEffects": True, "reasonCodes": ["forged"]}
    )

    assert decision.allow_side_effects is False
    assert decision.public_projection()["allowSideEffects"] is False

    forged = ReplayDecision.model_construct(mode="replay", allow_side_effects=True)
    copied = ReplayDecision(mode="replay").model_copy(update={"allow_side_effects": True})

    assert forged.public_projection()["allowSideEffects"] is False
    assert copied.public_projection()["allowSideEffects"] is False


def test_durable_store_receipt_never_exposes_production_write_when_forged() -> None:
    forged = DurableStoreReceipt.model_construct(
        status="stored",
        collection="receipts",
        record_id="receipt-1",
        record_digest=DIGEST_A,
        production_write=True,
    )
    copied = DurableStoreReceipt(
        status="stored",
        collection="receipts",
        recordId="receipt-1",
        recordDigest=DIGEST_A,
    ).model_copy(update={"productionWrite": True})

    assert forged.production_write is False
    assert copied.production_write is False
    assert forged.model_dump(by_alias=True)["productionWrite"] is False
    assert copied.model_dump(by_alias=True)["productionWrite"] is False


def test_artifact_index_points_to_external_blob_path_and_rejects_inline_blobs() -> None:
    record = ArtifactIndexRecord(
        artifactId="artifact-1",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
    )

    assert record.blob_ref == ARTIFACT_REF_A

    with pytest.raises(ValueError, match="artifact blobs"):
        ArtifactIndexRecord(
            artifactId="artifact-2",
            contentDigest=DIGEST_A,
            blobRef="inline:raw-bytes",
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
            metadata={"blob": "raw bytes"},
        )


def test_artifact_index_rejects_non_content_addressed_or_mismatched_blob_refs() -> None:
    with pytest.raises(ValueError, match="content-addressed"):
        ArtifactIndexRecord(
            artifactId="artifact-mutable-ref",
            contentDigest=DIGEST_A,
            blobRef="artifact://filesystem/latest/report.md",
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
        )

    with pytest.raises(ValueError, match="content digest"):
        ArtifactIndexRecord(
            artifactId="artifact-mismatch",
            contentDigest=DIGEST_A,
            blobRef=ARTIFACT_REF_C,
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
        )


def test_artifact_index_rejects_private_path_shaped_blob_refs() -> None:
    with pytest.raises(ValueError, match="private path"):
        ArtifactIndexRecord(
            artifactId="artifact-private",
            contentDigest=DIGEST_A,
            blobRef="artifact://filesystem//Users/kevin/private.txt",
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
        )


@pytest.mark.parametrize(
    "blob_ref",
    (
        "artifact://object/Authorization: Bearer unsafe",
        "artifact://object/cookie: sid=unsafe",
        "artifact://object/Authorization%3A%20Bearer%20unsafe",
        "artifact://object/password=unsafe",
    ),
)
def test_artifact_index_rejects_sensitive_blob_ref_text(blob_ref: str) -> None:
    with pytest.raises(ValueError, match="raw or sensitive"):
        ArtifactIndexRecord(
            artifactId="artifact-sensitive-ref",
            contentDigest=DIGEST_A,
            blobRef=blob_ref,
            sizeBytes=128,
            renderReceiptDigest=DIGEST_B,
        )


def test_sqlite_store_persists_metadata_and_supports_export_without_blob_storage(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.initialize()
    store.append(
        DurableRecord(
            collection="receipts",
            recordId="receipt-1",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"receiptRef": "receipt:1"},
        )
    )
    artifact = ArtifactIndexRecord(
        artifactId="artifact-1",
        contentDigest=DIGEST_C,
        blobRef=ARTIFACT_REF_C,
        sizeBytes=64,
        renderReceiptDigest=DIGEST_B,
    )
    store.put_artifact_index(artifact)

    loaded = store.get("receipts", "receipt-1")
    exported = store.export_records()

    assert loaded is not None
    assert loaded.metadata == {"receiptRef": "receipt:1"}
    assert exported["schemaVersion"] == DurableStoreSchemaVersion.CURRENT
    assert exported["integrityMode"] == "local_logical_integrity"
    assert exported["externalTamperEvidence"] is False
    assert exported["externalAnchorRequired"] is True
    assert exported["artifacts"][0]["blobRef"] == ARTIFACT_REF_C
    assert "raw bytes" not in str(exported)
    assert b"raw bytes" not in db_path.read_bytes()


def test_sqlite_store_rejects_duplicate_append(tmp_path: Path) -> None:
    store = SQLiteDurableStore(tmp_path / "durable.sqlite")
    record = DurableRecord(
        collection="sessions",
        recordId="session-1",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )

    store.append(record)

    with pytest.raises(DurableStoreSafetyError, match="append-only"):
        store.append(record)


def test_sqlite_store_exposes_explicit_schema_migration_report(tmp_path: Path) -> None:
    store = SQLiteDurableStore(tmp_path / "durable.sqlite")
    report = store.migration_report()

    assert report["schemaVersion"] == DurableStoreSchemaVersion.CURRENT
    assert report["migrationRequired"] is False
    assert report["appliedMigrations"] == []
    assert report["integrityMode"] == "local_logical_integrity"
    assert report["externalTamperEvidence"] is False
    assert report["externalAnchorRequired"] is True


def test_sqlite_export_revalidates_tampered_rows(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.initialize()
    with sqlite3.connect(db_path) as conn:
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
                "sessions",
                "tampered",
                DIGEST_A,
                DIGEST_A,
                DIGEST_B,
                json.dumps(
                    {
                        "collection": "sessions",
                        "recordId": "tampered",
                        "contentDigest": DIGEST_A,
                        "policySnapshotDigest": DIGEST_B,
                        "metadata": {"safeNested": [["Authorization: Bearer unsafe"]]},
                        "createdAt": "2026-05-24T00:00:00Z",
                    }
                ),
                "2026-05-24T00:00:00Z",
            ),
        )

    with pytest.raises(ValueError, match="raw or sensitive"):
        store.export_records()


def test_sqlite_export_verifies_column_integrity_for_safe_tampered_rows(
    tmp_path: Path,
) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.initialize()
    safe_record = DurableRecord(
        collection="sessions",
        recordId="tampered-safe",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    with sqlite3.connect(db_path) as conn:
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
                "sessions",
                "tampered-safe",
                "sha256:" + "9" * 64,
                DIGEST_A,
                DIGEST_B,
                json.dumps(safe_record.storage_payload()),
                safe_record.storage_payload()["createdAt"],
            ),
        )

    with pytest.raises(DurableStoreSafetyError, match="integrity"):
        store.export_records()


def test_sqlite_export_verifies_created_at_column_integrity(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.initialize()
    safe_record = DurableRecord(
        collection="sessions",
        recordId="tampered-created-at",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    with sqlite3.connect(db_path) as conn:
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
                "sessions",
                "tampered-created-at",
                safe_record.record_digest,
                DIGEST_A,
                DIGEST_B,
                json.dumps(safe_record.storage_payload()),
                "2030-01-01T00:00:00Z",
            ),
        )

    with pytest.raises(DurableStoreSafetyError, match="integrity"):
        store.export_records()


def test_sqlite_store_blocks_durable_record_update_and_delete(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = DurableRecord(
        collection="sessions",
        recordId="immutable",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    tampered = DurableRecord(
        collection="sessions",
        recordId="immutable",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:2"},
    )
    store.append(original)

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(
                """
                UPDATE durable_records
                SET record_digest = ?, payload_json = ?
                WHERE collection = ? AND record_id = ?
                """,
                (
                    tampered.record_digest,
                    json.dumps(tampered.storage_payload(), sort_keys=True),
                    "sessions",
                    "immutable",
                ),
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(
                "DELETE FROM durable_records WHERE collection = ? AND record_id = ?",
                ("sessions", "immutable"),
            )


def test_sqlite_export_detects_durable_row_rewrite_after_trigger_removal(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = DurableRecord(
        collection="sessions",
        recordId="ledger-record",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    tampered = DurableRecord(
        collection="sessions",
        recordId="ledger-record",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:2"},
    )
    store.append(original)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER durable_records_no_update")
        conn.execute(
            """
            UPDATE durable_records
            SET record_digest = ?, payload_json = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.record_digest,
                json.dumps(tampered.storage_payload(), sort_keys=True),
                "sessions",
                "ledger-record",
            ),
            )

    with pytest.raises(DurableStoreSafetyError, match="schema"):
        store.export_records()


def test_sqlite_export_detects_durable_row_and_ledger_rewrite_after_trigger_removal(
    tmp_path: Path,
) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = DurableRecord(
        collection="sessions",
        recordId="ledger-record-full",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    tampered = DurableRecord(
        collection="sessions",
        recordId="ledger-record-full",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:2"},
    )
    store.append(original)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER durable_records_no_update")
        conn.execute("DROP TRIGGER append_ledger_no_update")
        conn.execute(
            """
            UPDATE durable_records
            SET record_digest = ?, payload_json = ?, created_at = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.record_digest,
                json.dumps(tampered.storage_payload(), sort_keys=True),
                tampered.storage_payload()["createdAt"],
                "sessions",
                "ledger-record-full",
            ),
        )
        conn.execute(
            """
            UPDATE append_ledger
            SET record_digest = ?, entry_digest = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.record_digest,
                _ledger_entry_digest(
                    "sessions",
                    "ledger-record-full",
                    tampered.record_digest,
                ),
                "sessions",
                "ledger-record-full",
            ),
        )
        _restore_no_update_trigger(conn, "durable_records_no_update", "durable_records")
        _restore_no_update_trigger(conn, "append_ledger_no_update", "append_ledger")

    with pytest.raises(DurableStoreSafetyError, match="ledger"):
        store.export_records()


def test_sqlite_export_detects_durable_row_ledger_and_sidecar_head_rewrite(
    tmp_path: Path,
) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = DurableRecord(
        collection="sessions",
        recordId="ledger-head-record",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    tampered = DurableRecord(
        collection="sessions",
        recordId="ledger-head-record",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:2"},
    )
    store.append(original)

    tampered_ledger_row = (
        "sessions",
        "ledger-head-record",
        tampered.record_digest,
        _ledger_entry_digest("sessions", "ledger-head-record", tampered.record_digest),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER durable_records_no_update")
        conn.execute("DROP TRIGGER append_ledger_no_update")
        conn.execute(
            """
            UPDATE durable_records
            SET record_digest = ?, payload_json = ?, created_at = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.record_digest,
                json.dumps(tampered.storage_payload(), sort_keys=True),
                tampered.storage_payload()["createdAt"],
                "sessions",
                "ledger-head-record",
            ),
        )
        conn.execute(
            """
            UPDATE append_ledger
            SET record_digest = ?, entry_digest = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.record_digest,
                tampered_ledger_row[3],
                "sessions",
                "ledger-head-record",
            ),
        )
        _restore_no_update_trigger(conn, "durable_records_no_update", "durable_records")
        _restore_no_update_trigger(conn, "append_ledger_no_update", "append_ledger")
    Path(f"{db_path}.ledger-head").write_text(_ledger_root([tampered_ledger_row]) + "\n")

    with pytest.raises(DurableStoreSafetyError, match="ledger"):
        store.export_records()


def test_sqlite_full_local_rewrite_does_not_claim_external_tamper_evidence(
    tmp_path: Path,
) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = DurableRecord(
        collection="sessions",
        recordId="full-local-rewrite",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    tampered = DurableRecord(
        collection="sessions",
        recordId="full-local-rewrite",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:2"},
    )
    store.append(original)

    tampered_ledger_row = (
        "sessions",
        "full-local-rewrite",
        tampered.record_digest,
        _ledger_entry_digest("sessions", "full-local-rewrite", tampered.record_digest),
    )
    ledger_root = _ledger_root([tampered_ledger_row])
    event_payload: dict[str, object] = {
        "schemaVersion": DurableStoreSchemaVersion.CURRENT,
        "sequence": 1,
        "previousLedgerRoot": _ledger_root([]),
        "ledgerRoot": ledger_root,
        "collection": "sessions",
        "recordId": "full-local-rewrite",
        "recordDigest": tampered.record_digest,
        "entryDigest": tampered_ledger_row[3],
    }
    event_digest = "sha256:" + hashlib.sha256(
        json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    event_payload["eventDigest"] = event_digest

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER durable_records_no_update")
        conn.execute("DROP TRIGGER append_ledger_no_update")
        conn.execute(
            """
            UPDATE durable_records
            SET record_digest = ?, payload_json = ?, created_at = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.record_digest,
                json.dumps(tampered.storage_payload(), sort_keys=True),
                tampered.storage_payload()["createdAt"],
                "sessions",
                "full-local-rewrite",
            ),
        )
        conn.execute(
            """
            UPDATE append_ledger
            SET record_digest = ?, entry_digest = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.record_digest,
                tampered_ledger_row[3],
                "sessions",
                "full-local-rewrite",
            ),
        )
        _restore_no_update_trigger(conn, "durable_records_no_update", "durable_records")
        _restore_no_update_trigger(conn, "append_ledger_no_update", "append_ledger")
    Path(f"{db_path}.ledger-head").write_text(ledger_root + "\n")
    events_path = Path(f"{db_path}.ledger-events")
    for event_file in events_path.glob("*.json"):
        event_file.unlink()
    (events_path / f"00000000000000000001-{event_digest[7:]}.json").write_text(
        json.dumps(event_payload, sort_keys=True, separators=(",", ":")) + "\n"
    )

    exported = store.export_records()
    report = store.corruption_report()

    assert exported["records"][0]["metadata"] == {"safeRef": "session:2"}
    assert exported["externalTamperEvidence"] is False
    assert exported["externalAnchorRequired"] is True
    assert report.ok is True
    assert report.external_tamper_evidence is False
    assert report.external_anchor_required is True


def test_sqlite_export_detects_durable_row_delete_after_trigger_removal(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.append(
        DurableRecord(
            collection="sessions",
            recordId="ledger-delete",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeRef": "session:1"},
        )
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER durable_records_no_delete")
        conn.execute(
            "DELETE FROM durable_records WHERE collection = ? AND record_id = ?",
            ("sessions", "ledger-delete"),
        )

    with pytest.raises(DurableStoreSafetyError, match="schema"):
        store.export_records()


def test_sqlite_get_detects_deleted_row_still_present_in_ledger(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.append(
        DurableRecord(
            collection="sessions",
            recordId="ledger-get-delete",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeRef": "session:1"},
        )
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER durable_records_no_delete")
        conn.execute(
            "DELETE FROM durable_records WHERE collection = ? AND record_id = ?",
            ("sessions", "ledger-get-delete"),
        )
        _restore_no_delete_trigger(conn, "durable_records_no_delete", "durable_records")

    with pytest.raises(DurableStoreSafetyError, match="ledger"):
        store.get("sessions", "ledger-get-delete")


def test_sqlite_export_detects_missing_schema_metadata_on_existing_db(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.append(
        DurableRecord(
            collection="sessions",
            recordId="schema-marker",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeRef": "session:1"},
        )
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER schema_metadata_no_delete")
        conn.execute(
            "DELETE FROM schema_metadata WHERE key = ?",
            ("schemaVersion",),
        )

    with pytest.raises(DurableStoreSafetyError, match="schema"):
        store.export_records()


def test_sqlite_corruption_report_detects_logical_schema_corruption(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.append(
        DurableRecord(
            collection="sessions",
            recordId="corruption-report",
            contentDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"safeRef": "session:1"},
        )
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER durable_records_no_update")

    report = store.corruption_report()

    assert report.ok is False
    assert report.external_tamper_evidence is False
    assert report.external_anchor_required is True
    assert "durable_logical_integrity_failed" in report.reason_codes


def test_sqlite_get_verifies_payload_integrity(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.initialize()
    safe_record = DurableRecord(
        collection="sessions",
        recordId="tampered-get",
        contentDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
        metadata={"safeRef": "session:1"},
    )
    forged_payload = safe_record.storage_payload()
    forged_payload["contentDigest"] = DIGEST_C
    with sqlite3.connect(db_path) as conn:
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
                "sessions",
                "tampered-get",
                safe_record.record_digest,
                DIGEST_A,
                DIGEST_B,
                json.dumps(forged_payload),
                safe_record.storage_payload()["createdAt"],
            ),
        )

    with pytest.raises(DurableStoreSafetyError, match="integrity"):
        store.get("sessions", "tampered-get")


def test_sqlite_export_verifies_artifact_column_integrity(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.initialize()
    artifact = ArtifactIndexRecord(
        artifactId="artifact-safe",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
    )
    with sqlite3.connect(db_path) as conn:
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
                "artifact-safe",
                DIGEST_A,
                "artifact://filesystem/sha256/tampered",
                128,
                DIGEST_B,
                artifact.artifact_digest,
                json.dumps(artifact.storage_payload()),
            ),
        )

    with pytest.raises(DurableStoreSafetyError, match="integrity"):
        store.export_records()


def test_sqlite_export_verifies_artifact_payload_metadata_integrity(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    store.initialize()
    artifact = ArtifactIndexRecord(
        artifactId="artifact-safe",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:1"},
    )
    payload = artifact.storage_payload()
    payload["metadata"] = {"safeRef": "artifact:tampered"}
    with sqlite3.connect(db_path) as conn:
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
                "artifact-safe",
                DIGEST_A,
                ARTIFACT_REF_A,
                128,
                DIGEST_B,
                artifact.artifact_digest,
                json.dumps(payload),
            ),
        )

    with pytest.raises(DurableStoreSafetyError, match="integrity"):
        store.export_records()


def test_sqlite_store_blocks_artifact_index_update_and_delete(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = ArtifactIndexRecord(
        artifactId="artifact-immutable",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:1"},
    )
    tampered = ArtifactIndexRecord(
        artifactId="artifact-immutable",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:2"},
    )
    store.put_artifact_index(original)

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(
                """
                UPDATE artifact_index
                SET artifact_digest = ?, payload_json = ?
                WHERE artifact_id = ?
                """,
                (
                    tampered.artifact_digest,
                    json.dumps(tampered.storage_payload(), sort_keys=True),
                    "artifact-immutable",
                ),
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(
                "DELETE FROM artifact_index WHERE artifact_id = ?",
                ("artifact-immutable",),
            )


def test_artifact_receipts_bind_full_artifact_index_payload() -> None:
    store = InMemoryDurableStore()
    first = ArtifactIndexRecord(
        artifactId="artifact-receipt-a",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:1"},
    )
    second = ArtifactIndexRecord(
        artifactId="artifact-receipt-b",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:2"},
    )

    first_receipt = store.put_artifact_index(first)
    second_receipt = store.put_artifact_index(second)

    assert first_receipt.record_digest == first.artifact_digest
    assert second_receipt.record_digest == second.artifact_digest
    assert first_receipt.record_digest != second_receipt.record_digest


def test_sqlite_export_detects_artifact_rewrite_after_trigger_removal(tmp_path: Path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = ArtifactIndexRecord(
        artifactId="artifact-ledger",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:1"},
    )
    tampered = ArtifactIndexRecord(
        artifactId="artifact-ledger",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:2"},
    )
    store.put_artifact_index(original)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER artifact_index_no_update")
        conn.execute(
            """
            UPDATE artifact_index
            SET artifact_digest = ?, payload_json = ?
            WHERE artifact_id = ?
            """,
            (
                tampered.artifact_digest,
                json.dumps(tampered.storage_payload(), sort_keys=True),
                "artifact-ledger",
            ),
        )

    with pytest.raises(DurableStoreSafetyError, match="schema"):
        store.export_records()


def test_sqlite_export_detects_artifact_and_ledger_rewrite_after_trigger_removal(
    tmp_path: Path,
) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "durable.sqlite"
    store = SQLiteDurableStore(db_path)
    original = ArtifactIndexRecord(
        artifactId="artifact-ledger-full",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:1"},
    )
    tampered = ArtifactIndexRecord(
        artifactId="artifact-ledger-full",
        contentDigest=DIGEST_A,
        blobRef=ARTIFACT_REF_A,
        sizeBytes=128,
        renderReceiptDigest=DIGEST_B,
        metadata={"safeRef": "artifact:2"},
    )
    store.put_artifact_index(original)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER artifact_index_no_update")
        conn.execute("DROP TRIGGER append_ledger_no_update")
        conn.execute(
            """
            UPDATE artifact_index
            SET artifact_digest = ?, payload_json = ?
            WHERE artifact_id = ?
            """,
            (
                tampered.artifact_digest,
                json.dumps(tampered.storage_payload(), sort_keys=True),
                "artifact-ledger-full",
            ),
        )
        conn.execute(
            """
            UPDATE append_ledger
            SET record_digest = ?, entry_digest = ?
            WHERE collection = ? AND record_id = ?
            """,
            (
                tampered.artifact_digest,
                _ledger_entry_digest(
                    "artifact_index",
                    "artifact-ledger-full",
                    tampered.artifact_digest,
                ),
                "artifact_index",
                "artifact-ledger-full",
            ),
        )
        _restore_no_update_trigger(conn, "artifact_index_no_update", "artifact_index")
        _restore_no_update_trigger(conn, "append_ledger_no_update", "append_ledger")

    with pytest.raises(DurableStoreSafetyError, match="ledger"):
        store.export_records()


def test_sqlite_store_corruption_report_is_explicit(tmp_path: Path) -> None:
    db_path = tmp_path / "durable.sqlite"
    db_path.write_text("not sqlite")
    store = SQLiteDurableStore(db_path)

    report = store.corruption_report()

    assert report.ok is False
    assert report.external_tamper_evidence is False
    assert report.external_anchor_required is True
    assert "sqlite_integrity_check_failed" in report.reason_codes


def test_corruption_report_cannot_be_forged_to_claim_external_tamper_evidence() -> None:
    from magi_agent.storage.durable_store import CorruptionReport

    forged = CorruptionReport.model_construct(
        ok=True,
        external_tamper_evidence=True,
        external_anchor_required=False,
    )
    copied = CorruptionReport(ok=True).model_copy(
        update={
            "externalTamperEvidence": True,
            "externalAnchorRequired": False,
        }
    )

    assert forged.external_tamper_evidence is False
    assert forged.external_anchor_required is True
    assert copied.external_tamper_evidence is False
    assert copied.external_anchor_required is True
    assert forged.model_dump(by_alias=True)["externalTamperEvidence"] is False
    assert copied.model_dump(by_alias=True)["externalAnchorRequired"] is True


def test_durable_store_kind_rejects_live_postgres_without_adapter() -> None:
    with pytest.raises(DurableStoreSafetyError, match="hosted adapter"):
        DurableStoreKind.model_validate("postgres")


def test_durable_store_env_rejects_bad_timeout() -> None:
    with pytest.raises(ValueError):
        durable_store_config_from_env(
            {
                "OPENMAGI_DURABLE_STORE": "sqlite",
                "OPENMAGI_DURABLE_SQLITE_PATH": "/tmp/openmagi.sqlite",
                "OPENMAGI_DURABLE_SQLITE_BUSY_TIMEOUT_MS": "not-a-number",
            }
        )


def test_storage_import_boundary_has_no_live_provider_or_route_imports() -> None:
    script = """
import sys
import magi_agent.storage.durable_store
import magi_agent.storage.memory_store
import magi_agent.storage.sqlite_store
for name in (
    'supabase',
    'psycopg',
    'psycopg2',
    'asyncpg',
    'boto3',
    'kubernetes',
    'httpx',
    'google.adk.runners',
    'magi_agent.chat',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def _restore_no_update_trigger(
    conn,
    trigger_name: str,
    table_name: str,
) -> None:
    conn.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE ON {table_name}
        BEGIN
            SELECT RAISE(ABORT, 'durable store is append-only');
        END;
        """
    )


def _restore_no_delete_trigger(
    conn,
    trigger_name: str,
    table_name: str,
) -> None:
    conn.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE DELETE ON {table_name}
        BEGIN
            SELECT RAISE(ABORT, 'durable store is append-only');
        END;
        """
    )
