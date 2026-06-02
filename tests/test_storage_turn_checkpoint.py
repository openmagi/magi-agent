from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openmagi_core_agent.storage.content_addressed import StoredTurnCheckpoint
from openmagi_core_agent.storage.durable_store import (
    DurableStoreBackupContract,
    DurableStoreSafetyError,
    HostedDurableStoreAdapterBoundary,
    ReplayDecision,
)
from openmagi_core_agent.storage.memory_store import InMemoryDurableStore
from openmagi_core_agent.storage.sqlite_store import SQLiteDurableStore


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64
DIGEST_E = "sha256:" + "e" * 64


def _checkpoint() -> StoredTurnCheckpoint:
    return StoredTurnCheckpoint(
        runId="run-001",
        turnId="turn-002",
        checkpointId="checkpoint-003",
        stateRecordDigest=DIGEST_A,
        parentLedgerDigest=DIGEST_B,
        policySnapshotDigest=DIGEST_C,
        contextProjectionDigest=DIGEST_D,
        replayObservationDigest=DIGEST_E,
        replayAllowed=True,
        forkAllowed=True,
        sideEffectsAllowed=True,
    )


def test_turn_checkpoint_binds_parent_ledger_policy_context_and_forces_no_side_effects() -> None:
    checkpoint = _checkpoint()
    content_record = checkpoint.content_record()
    decision = checkpoint.replay_decision()
    fork_decision = checkpoint.fork_decision()

    assert checkpoint.side_effects_allowed is False
    assert content_record.policy_snapshot_digest == DIGEST_C
    assert content_record.payload["parentLedgerDigest"] == DIGEST_B
    assert content_record.payload["policySnapshotDigest"] == DIGEST_C
    assert content_record.payload["contextProjectionDigest"] == DIGEST_D
    assert content_record.payload["sideEffectsAllowed"] is False
    assert decision["sideEffectsAllowed"] is False
    assert fork_decision["sideEffectsAllowed"] is False
    assert fork_decision["parentLedgerDigest"] == DIGEST_B
    assert fork_decision["policySnapshotDigest"] == DIGEST_C


def test_old_run_policy_snapshot_digest_is_immutable_in_checkpoint_storage() -> None:
    first = StoredTurnCheckpoint(
        runId="run-001",
        turnId="turn-001",
        checkpointId="checkpoint-001",
        stateRecordDigest=DIGEST_A,
        parentLedgerDigest=DIGEST_B,
        policySnapshotDigest=DIGEST_C,
        contextProjectionDigest=DIGEST_D,
    )
    second = StoredTurnCheckpoint(
        runId="run-001",
        turnId="turn-001",
        checkpointId="checkpoint-001",
        stateRecordDigest=DIGEST_A,
        parentLedgerDigest=DIGEST_B,
        policySnapshotDigest=DIGEST_E,
        contextProjectionDigest=DIGEST_D,
    )

    assert first.checkpoint_digest != second.checkpoint_digest
    assert first.content_record().content_digest != second.content_record().content_digest


def test_turn_checkpoint_persists_through_memory_and_sqlite_metadata_only(tmp_path) -> None:
    checkpoint = _checkpoint()
    content_record = checkpoint.content_record()
    durable = content_record.to_durable_record(
        collection="checkpoints",
        record_id="checkpoint-003",
    )
    memory_store = InMemoryDurableStore()
    sqlite_store = SQLiteDurableStore(tmp_path / "durable.sqlite")

    memory_receipt = memory_store.append(durable)
    sqlite_receipt = sqlite_store.append(durable)
    exported = sqlite_store.export_records()

    assert memory_receipt.record_digest == sqlite_receipt.record_digest
    assert exported["records"][0]["contentDigest"] == content_record.content_digest
    assert exported["records"][0]["policySnapshotDigest"] == DIGEST_C
    encoded = json.dumps(exported, sort_keys=True)
    assert "raw prompt" not in encoded.lower()
    assert "sideEffectsAllowed" not in encoded


def test_turn_checkpoint_rejects_raw_identifiers_and_bad_digests() -> None:
    with pytest.raises(ValidationError, match="safe refs"):
        StoredTurnCheckpoint(
            runId="/Users/example/.ssh/id_rsa",
            turnId="turn-001",
            checkpointId="checkpoint-001",
            stateRecordDigest=DIGEST_A,
            parentLedgerDigest=DIGEST_B,
            policySnapshotDigest=DIGEST_C,
            contextProjectionDigest=DIGEST_D,
        )
    with pytest.raises(ValidationError, match="sha256"):
        StoredTurnCheckpoint(
            runId="run-001",
            turnId="turn-001",
            checkpointId="checkpoint-001",
            stateRecordDigest="raw-state",
            parentLedgerDigest=DIGEST_B,
            policySnapshotDigest=DIGEST_C,
            contextProjectionDigest=DIGEST_D,
        )


def test_replay_decision_and_checkpoint_cannot_be_forged_to_allow_side_effects() -> None:
    checkpoint = StoredTurnCheckpoint.model_construct(
        runId="run-001",
        turnId="turn-001",
        checkpointId="checkpoint-001",
        stateRecordDigest=DIGEST_A,
        parentLedgerDigest=DIGEST_B,
        policySnapshotDigest=DIGEST_C,
        contextProjectionDigest=DIGEST_D,
        sideEffectsAllowed=True,
    )
    replay = ReplayDecision.model_construct(mode="replay", allow_side_effects=True)

    assert checkpoint.side_effects_allowed is False
    assert checkpoint.replay_decision()["sideEffectsAllowed"] is False
    assert replay.allow_side_effects is False
    assert replay.public_projection()["allowSideEffects"] is False


def test_hosted_adapter_boundary_and_backup_contract_are_default_off_and_digest_only() -> None:
    hosted = HostedDurableStoreAdapterBoundary.model_construct(
        kind="postgres",
        enabled=True,
        storesSecretMaterial=True,
    )
    backup = DurableStoreBackupContract.model_construct(
        exportDigest=DIGEST_A,
        destinationRef="export:daily-001",
        includesArtifactBlobs=True,
        includesSecretMaterial=True,
        sideEffectsAllowed=True,
        sqliteMultiWriterAllowed=True,
    )

    assert hosted.enabled is False
    assert hosted.stores_secret_material is False
    assert hosted.requires_separate_approval is True
    assert "requires_secrets_binding" in hosted.activation_blockers
    assert backup.includes_artifact_blobs is False
    assert backup.includes_secret_material is False
    assert backup.side_effects_allowed is False
    assert backup.sqlite_multi_writer_allowed is False


def test_hosted_and_backup_contract_model_copy_cannot_forge_authority() -> None:
    hosted = HostedDurableStoreAdapterBoundary()
    backup = DurableStoreBackupContract(
        exportDigest=DIGEST_A,
        destinationRef="export:daily-001",
    )

    hosted_copy = hosted.model_copy(
        update={
            "enabled": True,
            "storesSecretMaterial": True,
            "requiresSeparateApproval": False,
        }
    )
    backup_copy = backup.model_copy(
        update={
            "includesArtifactBlobs": True,
            "includesSecretMaterial": True,
            "sideEffectsAllowed": True,
            "sqliteMultiWriterAllowed": True,
        }
    )

    assert hosted_copy.enabled is False
    assert hosted_copy.stores_secret_material is False
    assert hosted_copy.requires_separate_approval is True
    assert backup_copy.includes_artifact_blobs is False
    assert backup_copy.includes_secret_material is False
    assert backup_copy.side_effects_allowed is False
    assert backup_copy.sqlite_multi_writer_allowed is False


def test_backup_contract_rejects_private_or_secret_destinations() -> None:
    with pytest.raises(ValidationError):
        DurableStoreBackupContract(
            exportDigest=DIGEST_A,
            destinationRef="/Users/example/export.json",
        )
    with pytest.raises(ValidationError):
        DurableStoreBackupContract(
            exportDigest=DIGEST_A,
            destinationRef="token:unsafe",
        )


def test_sqlite_runtime_metadata_indexes_round_trip_all_required_collections(tmp_path) -> None:
    from openmagi_core_agent.storage.durable_store import RuntimeMetadataIndexRecord, runtime_metadata_collections

    store = SQLiteDurableStore(tmp_path / "durable.sqlite")
    for index, collection in enumerate(runtime_metadata_collections(), start=1):
        item = RuntimeMetadataIndexRecord(
            collection=collection,
            itemId=f"item-{index}",
            itemDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"status": "indexed"},
        )
        store.append(item.to_durable_record())

    exported = store.export_records()

    assert {record["collection"] for record in exported["records"]} == set(runtime_metadata_collections())
    assert all(record["policySnapshotDigest"] == DIGEST_B for record in exported["records"])


def test_durable_config_blocks_sqlite_multi_writer_and_hosted_postgres_from_env(tmp_path) -> None:
    from openmagi_core_agent.storage.durable_store import DurableStoreConfig, durable_store_config_from_env

    config = DurableStoreConfig.model_construct(
        kind="sqlite",
        sqlitePath=tmp_path / "durable.sqlite",
        artifactStore="filesystem",
        sqliteMultiWriterAllowed=True,
    )

    assert config.sqlite_multi_writer_allowed is False
    assert config.model_dump(by_alias=True)["sqliteMultiWriterAllowed"] is False
    with pytest.raises(DurableStoreSafetyError, match="hosted adapter"):
        durable_store_config_from_env({"OPENMAGI_DURABLE_STORE": "postgres"})


def test_runtime_metadata_index_model_construct_and_copy_revalidate_sensitive_metadata() -> None:
    from openmagi_core_agent.storage.durable_store import RuntimeMetadataIndexRecord

    with pytest.raises(ValueError, match="raw or sensitive"):
        RuntimeMetadataIndexRecord.model_construct(
            collection="credential_lease_metadata",
            itemId="item-1",
            itemDigest=DIGEST_A,
            policySnapshotDigest=DIGEST_B,
            metadata={"connector" + "Token": "secret:value"},
        )

    index = RuntimeMetadataIndexRecord(
        collection="receipts",
        itemId="item-2",
        itemDigest=DIGEST_A,
        policySnapshotDigest=DIGEST_B,
    )
    with pytest.raises(ValueError, match="model_copy update"):
        index.model_copy(update={"metadata": {"connector" + "Token": "secret:value"}})
