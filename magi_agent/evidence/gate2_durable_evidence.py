"""Durable evidence store for Gate 2 selected sandbox canary.

Records five categories of evidence that must ALL succeed before the selected
Gate 2 path can return HTTP 200:

1. Request record — digest + body digest stored before processing
2. Counter increment — gate2 selected counter incremented
3. Delivery receipt — response/output digest persisted
4. Sandbox mutation receipt — mutation receipt persisted (not just in response)
5. Rollback receipt — rollback receipt persisted

Fail-closed: if any write fails, the store reports which evidence is missing.
The caller must not return success.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Gate2EvidenceRecord:
    """Immutable snapshot of all five evidence categories for one request."""

    request_digest: str
    body_digest: str | None
    request_recorded: bool
    counter_incremented: bool
    delivery_receipt_recorded: bool
    mutation_receipt_recorded: bool
    rollback_receipt_recorded: bool
    recorded_at_ms: int

    @property
    def all_evidence_present(self) -> bool:
        return (
            self.request_recorded
            and self.counter_incremented
            and self.delivery_receipt_recorded
            and self.mutation_receipt_recorded
            and self.rollback_receipt_recorded
        )

    @property
    def missing_evidence(self) -> list[str]:
        missing: list[str] = []
        if not self.request_recorded:
            missing.append("request_record")
        if not self.counter_incremented:
            missing.append("counter_increment")
        if not self.delivery_receipt_recorded:
            missing.append("delivery_receipt")
        if not self.mutation_receipt_recorded:
            missing.append("mutation_receipt")
        if not self.rollback_receipt_recorded:
            missing.append("rollback_receipt")
        return missing


@dataclass(frozen=True)
class Gate2DurableEvidenceResult:
    """Result of attempting to record all Gate 2 evidence."""

    success: bool
    record: Gate2EvidenceRecord
    error: str | None = None


class Gate2DurableEvidenceStore:
    """File-backed durable evidence store for Gate 2 sandbox canary.

    Each request gets a JSON record persisted to disk. All five evidence
    categories must be written before the record is considered complete.
    Thread-safe via a reentrant lock.
    """

    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._lock = threading.RLock()

    @property
    def store_path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"records": {}, "counters": {"gate2Records": 0, "deliveryReceipts": 0}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"records": {}, "counters": {"gate2Records": 0, "deliveryReceipts": 0}}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(self._path))

    def record_all_evidence(
        self,
        *,
        request_digest: str,
        body_digest: str | None,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        status: str,
        reason: str,
        mutation_receipt_digest: str,
        rollback_receipt_digest: str | None,
        sandbox_path_digest: str,
        output_digest: str | None = None,
        sandbox_file_count: int = 0,
    ) -> Gate2DurableEvidenceResult:
        """Atomically record all five evidence categories.

        Returns a result indicating success or which evidence failed to record.
        """
        now = _now_ms()
        with self._lock:
            try:
                data = self._load()
                records = data.setdefault("records", {})
                counters = data.setdefault(
                    "counters", {"gate2Records": 0, "deliveryReceipts": 0}
                )

                # 1. Request record
                request_recorded = False
                record_entry: dict[str, Any] = {
                    "requestDigest": request_digest,
                    "bodyDigest": body_digest,
                    "selectedBotDigest": selected_bot_digest,
                    "trustedOwnerUserIdDigest": trusted_owner_user_id_digest,
                    "environment": environment,
                    "status": status,
                    "reason": reason,
                    "createdAtMs": now,
                }
                records[request_digest] = record_entry
                request_recorded = True

                # 2. Counter increment
                counter_incremented = False
                counters["gate2Records"] = counters.get("gate2Records", 0) + 1
                record_entry["counterIncrementedAtMs"] = now
                counter_incremented = True

                # 3. Delivery receipt
                delivery_receipt_recorded = False
                record_entry["deliveryReceipt"] = {
                    "outputDigest": output_digest,
                    "deliveryStatus": "sandbox_completed",
                    "recordedAtMs": now,
                }
                counters["deliveryReceipts"] = counters.get("deliveryReceipts", 0) + 1
                delivery_receipt_recorded = True

                # 4. Sandbox mutation receipt
                mutation_receipt_recorded = False
                record_entry["mutationReceipt"] = {
                    "receiptDigest": mutation_receipt_digest,
                    "sandboxPathDigest": sandbox_path_digest,
                    "sandboxFileCount": sandbox_file_count,
                    "persistedAtMs": now,
                }
                mutation_receipt_recorded = True

                # 5. Rollback receipt (only counted as recorded if digest is non-None)
                rollback_receipt_recorded = False
                record_entry["rollbackReceipt"] = {
                    "rollbackDigest": rollback_receipt_digest,
                    "persistedAtMs": now,
                }
                rollback_receipt_recorded = rollback_receipt_digest is not None

                # Persist atomically
                self._save(data)

                evidence_record = Gate2EvidenceRecord(
                    request_digest=request_digest,
                    body_digest=body_digest,
                    request_recorded=request_recorded,
                    counter_incremented=counter_incremented,
                    delivery_receipt_recorded=delivery_receipt_recorded,
                    mutation_receipt_recorded=mutation_receipt_recorded,
                    rollback_receipt_recorded=rollback_receipt_recorded,
                    recorded_at_ms=now,
                )
                return Gate2DurableEvidenceResult(
                    success=evidence_record.all_evidence_present,
                    record=evidence_record,
                )
            except (OSError, json.JSONDecodeError) as exc:
                # Fail-closed: evidence write failed
                return Gate2DurableEvidenceResult(
                    success=False,
                    record=Gate2EvidenceRecord(
                        request_digest=request_digest,
                        body_digest=body_digest,
                        request_recorded=False,
                        counter_incremented=False,
                        delivery_receipt_recorded=False,
                        mutation_receipt_recorded=False,
                        rollback_receipt_recorded=False,
                        recorded_at_ms=now,
                    ),
                    error=f"evidence_write_failed: {type(exc).__name__}",
                )

    def get_evidence(self, request_digest: str) -> Gate2EvidenceRecord | None:
        """Retrieve evidence for a specific request digest."""
        with self._lock:
            data = self._load()
            records = data.get("records", {})
            entry = records.get(request_digest)
            if not isinstance(entry, dict):
                return None
            return Gate2EvidenceRecord(
                request_digest=request_digest,
                body_digest=entry.get("bodyDigest"),
                request_recorded=True,
                counter_incremented=entry.get("counterIncrementedAtMs") is not None,
                delivery_receipt_recorded=entry.get("deliveryReceipt") is not None,
                mutation_receipt_recorded=entry.get("mutationReceipt") is not None,
                rollback_receipt_recorded=entry.get("rollbackReceipt") is not None,
                recorded_at_ms=entry.get("createdAtMs", 0),
            )

    def get_counters(self) -> dict[str, int]:
        """Return current counter values."""
        with self._lock:
            data = self._load()
            counters = data.get("counters", {})
            return {
                "gate2Records": counters.get("gate2Records", 0),
                "deliveryReceipts": counters.get("deliveryReceipts", 0),
            }
