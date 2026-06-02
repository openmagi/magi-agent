from __future__ import annotations

from .content_addressed import ContentAddressedJSONRecord
from .durable_store import (
    ArtifactIndexRecord,
    CorruptionReport,
    DurableRecord,
    DurableStoreReceipt,
    DurableStoreSafetyError,
    DurableStoreSchemaVersion,
)


class InMemoryDurableStore:
    """Test-only metadata store with append-only semantics."""

    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], DurableRecord] = {}
        self._artifacts: dict[str, ArtifactIndexRecord] = {}
        self._content_records: dict[str, ContentAddressedJSONRecord] = {}

    def append(self, record: DurableRecord) -> DurableStoreReceipt:
        record = DurableRecord.model_validate(record.model_dump(by_alias=True, mode="json"))
        key = (record.collection, record.record_id)
        if key in self._records:
            raise DurableStoreSafetyError("durable store is append-only")
        self._records[key] = record
        return DurableStoreReceipt(
            status="stored",
            collection=record.collection,
            recordId=record.record_id,
            recordDigest=record.record_digest,
            reasonCodes=("stored_memory_adapter",),
        )

    def get(self, collection: str, record_id: str) -> DurableRecord | None:
        record = self._records.get((collection, record_id))
        if record is None:
            return None
        return DurableRecord.model_validate(record.model_dump(by_alias=True, mode="json"))

    def put_artifact_index(self, record: ArtifactIndexRecord) -> DurableStoreReceipt:
        record = ArtifactIndexRecord.model_validate(record.model_dump(by_alias=True, mode="json"))
        if record.artifact_id in self._artifacts:
            raise DurableStoreSafetyError("durable store is append-only")
        self._artifacts[record.artifact_id] = record
        return DurableStoreReceipt(
            status="stored",
            collection="artifact_index",
            recordId=record.artifact_id,
            recordDigest=record.artifact_digest,
            reasonCodes=("artifact_index_stored",),
        )

    def put_content_record(self, record: ContentAddressedJSONRecord) -> DurableStoreReceipt:
        record = ContentAddressedJSONRecord.model_validate(record.model_dump(by_alias=True, mode="json"))
        if record.content_digest in self._content_records:
            raise DurableStoreSafetyError("durable store is append-only")
        self._content_records[record.content_digest] = record
        return DurableStoreReceipt(
            status="stored",
            collection="content_records",
            recordId=record.content_digest,
            recordDigest=record.content_digest,
            reasonCodes=("content_record_stored",),
        )

    def get_content_record(self, content_digest: str) -> ContentAddressedJSONRecord | None:
        record = self._content_records.get(content_digest)
        if record is None:
            return None
        return ContentAddressedJSONRecord.model_validate(record.model_dump(by_alias=True, mode="json"))

    def export_records(self) -> dict[str, object]:
        return {
            "schemaVersion": DurableStoreSchemaVersion.CURRENT,
            "records": [
                DurableRecord.model_validate(
                    record.model_dump(by_alias=True, mode="json")
                ).storage_payload()
                for record in sorted(
                    self._records.values(),
                    key=lambda item: (item.collection, item.record_id),
                )
            ],
            "artifacts": [
                ArtifactIndexRecord.model_validate(
                    artifact.model_dump(by_alias=True, mode="json")
                ).storage_payload()
                for artifact in sorted(
                    self._artifacts.values(),
                    key=lambda item: item.artifact_id,
                )
            ],
            "contentRecords": [
                ContentAddressedJSONRecord.model_validate(
                    record.model_dump(by_alias=True, mode="json")
                ).storage_payload()
                for record in sorted(
                    self._content_records.values(),
                    key=lambda item: item.content_digest,
                )
            ],
        }

    def corruption_report(self) -> CorruptionReport:
        return CorruptionReport(ok=True)
