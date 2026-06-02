from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.storage.content_addressed import (
    ContentAddressedJSONRecord,
    StoredTurnCheckpoint,
    content_digest_for_payload,
)
from magi_agent.storage.durable_store import (
    DurableRecord,
    DurableStoreSafetyError,
    RuntimeMetadataIndexRecord,
    runtime_metadata_collections,
)
from magi_agent.storage.memory_store import InMemoryDurableStore


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def test_content_addressed_record_computes_canonical_digest_and_converts_to_durable_record() -> None:
    payload = {
        "stateDigest": DIGEST_A,
        "sourceRef": "source:ledger-1",
        "mode": "checkpoint",
    }
    first = ContentAddressedJSONRecord(recordKind="turn_state", payload=payload, policySnapshotDigest=DIGEST_B)
    second = ContentAddressedJSONRecord(
        recordKind="turn_state",
        payload={"mode": "checkpoint", "sourceRef": "source:ledger-1", "stateDigest": DIGEST_A},
        policySnapshotDigest=DIGEST_B,
    )

    durable = first.to_durable_record(collection="checkpoints", record_id="checkpoint-state-1")

    assert first.content_digest == second.content_digest
    assert first.content_digest == content_digest_for_payload("turn_state", payload)
    assert durable.collection == "checkpoints"
    assert durable.content_digest == first.content_digest
    assert durable.policy_snapshot_digest == DIGEST_B
    assert durable.metadata["contentRecordKind"] == "kind:turn_state"


def test_content_addressed_record_rejects_digest_mismatch() -> None:
    with pytest.raises(ValidationError, match="content digest"):
        ContentAddressedJSONRecord(
            recordKind="turn_state",
            payload={"stateDigest": DIGEST_A},
            contentDigest=DIGEST_C,
            policySnapshotDigest=DIGEST_B,
        )


@pytest.mark.parametrize(
    "payload",
    (
        {"raw" + "Prompt": "hello"},
        {"answer": "raw " + "output text"},
        {"authRef": "Bearer unsafe"},
        {"sourceRef": "/Users/example/.ssh/id_rsa"},
        {"blobRef": "data:text/plain;base64,cmF3"},
        {"freeText": "this is not a safe reference"},
    ),
)
def test_content_addressed_record_rejects_raw_or_unbounded_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ContentAddressedJSONRecord(
            recordKind="turn_state",
            payload=payload,
            policySnapshotDigest=DIGEST_B,
        )


@pytest.mark.parametrize(
    "key",
    (
        "authRef",
        "authorizationRef",
        "cookieRef",
        "tokenDigest",
        "rawOutputDigest",
        "sessionKeyDigest",
    ),
)
def test_content_addressed_record_rejects_sensitive_keys_even_with_digest_values(key: str) -> None:
    with pytest.raises(ValidationError, match="raw or sensitive"):
        ContentAddressedJSONRecord(
            recordKind="turn_state",
            payload={key: DIGEST_A},
            policySnapshotDigest=DIGEST_B,
        )


def test_content_addressed_record_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValidationError):
        ContentAddressedJSONRecord(
            recordKind="turn_state",
            payload={"score": float("nan")},
            policySnapshotDigest=DIGEST_B,
        )


def test_content_addressed_storage_payload_revalidates_mutated_payload() -> None:
    record = ContentAddressedJSONRecord(
        recordKind="turn_state",
        payload={"stateDigest": DIGEST_A},
        policySnapshotDigest=DIGEST_B,
    )
    assert isinstance(record.payload, dict)
    record.payload["raw" + "Prompt"] = "unsafe"

    with pytest.raises(ValidationError, match="raw or sensitive"):
        record.storage_payload()


def test_content_addressed_record_model_copy_update_is_disabled() -> None:
    record = ContentAddressedJSONRecord(
        recordKind="turn_state",
        payload={"stateDigest": DIGEST_A},
        policySnapshotDigest=DIGEST_B,
    )

    with pytest.raises(ValueError, match="model_copy update"):
        record.model_copy(update={"contentDigest": DIGEST_C})


def test_runtime_metadata_index_covers_required_runtime_collections_without_raw_payloads() -> None:
    expected = {
        "sessions",
        "checkpoints",
        "replay_fork_lineage",
        "receipts",
        "evidence_ledger_index",
        "job_queue",
        "policy_snapshot_refs",
        "artifact_index",
        "eval_observations",
        "delivery_action_receipts",
        "credential_lease_metadata",
    }
    store = InMemoryDurableStore()

    assert set(runtime_metadata_collections()) == expected
    for collection in sorted(expected):
        index = RuntimeMetadataIndexRecord(
            collection=collection,
            itemId=f"item-{len(store.export_records()['records']) + 1}",
            itemDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"status": "indexed"},
        )
        receipt = store.append(index.to_durable_record())
        assert receipt.status == "stored"

    exported = store.export_records()
    assert len(exported["records"]) == len(expected)
    encoded = json.dumps(exported, sort_keys=True)
    assert "raw prompt" not in encoded.lower()
    assert "connector_token" not in encoded


def test_runtime_metadata_index_rejects_unregistered_collection_or_secret_metadata() -> None:
    with pytest.raises(ValidationError, match="collection"):
        RuntimeMetadataIndexRecord(
            collection="unregistered",
            itemId="item-1",
            itemDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
        )
    with pytest.raises(ValidationError, match="raw or sensitive"):
        RuntimeMetadataIndexRecord(
            collection="credential_lease_metadata",
            itemId="lease-1",
            itemDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"connector" + "Token": "unsafe"},
        )


def test_content_addressed_storage_import_boundary_has_no_live_provider_imports() -> None:
    script = """
import sys
import magi_agent.storage.content_addressed
for name in (
    'google.adk.runners',
    'magi_agent.chat',
    'magi_agent.tools',
    'magi_agent.web_acquisition',
    'supabase',
    'psycopg',
    'kubernetes',
    'httpx',
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


def test_no_generated_or_private_paths_in_content_addressed_public_payloads(tmp_path: Path) -> None:
    record = ContentAddressedJSONRecord(
        recordKind="turn_state",
        payload={"stateDigest": DIGEST_A, "artifactRef": "artifact:state-1"},
        policySnapshotDigest=DIGEST_B,
    )
    output = tmp_path / "export.json"
    output.write_text(json.dumps(record.storage_payload(), sort_keys=True))

    encoded = output.read_text()
    assert "/Users/" not in encoded
    assert "/workspace/" not in encoded
    assert "raw" + "Prompt" not in encoded


def test_content_payload_bytes_are_digest_only_when_stored_in_durable_record() -> None:
    payload = {"stateDigest": DIGEST_A, "sourceRef": "source:state-1"}
    record = ContentAddressedJSONRecord(
        recordKind="turn_state",
        payload=payload,
        policySnapshotDigest=DIGEST_B,
    )
    durable = record.to_durable_record(collection="checkpoints", record_id="state-digest-only")

    encoded = json.dumps(durable.storage_payload(), sort_keys=True)
    assert record.content_digest in encoded
    assert "source:state-1" not in encoded
    assert '"payload"' not in encoded


def test_content_payloads_are_append_only_and_rehydratable_in_memory_store() -> None:
    record = ContentAddressedJSONRecord(
        recordKind="turn_state",
        payload={"stateDigest": DIGEST_A, "sourceRef": "source:state-1"},
        policySnapshotDigest=DIGEST_B,
    )
    store = InMemoryDurableStore()

    receipt = store.put_content_record(record)
    loaded = store.get_content_record(record.content_digest)

    assert receipt.status == "stored"
    assert loaded == record
    assert loaded is not record
    with pytest.raises(DurableStoreSafetyError, match="append-only"):
        store.put_content_record(record)
