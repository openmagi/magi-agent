from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError


def _boundary_module() -> Any:
    return importlib.import_module("magi_agent.runtime.projection_write_boundary")


def test_projection_write_intents_are_denied_by_default() -> None:
    boundary = _boundary_module()
    for target in ("transcript", "sse", "control_event", "control_request"):
        intent = boundary.ProjectionWriteIntent(
            target=target,
            operation="append",
            session_key="agent:main:app:default",
            idempotency_key=f"idem-{target}",
            payload={"type": target, "fixtureOnly": True},
        )

        result = boundary.evaluate_projection_write_intent(intent)
        result_dump = result.model_dump(by_alias=True)

        assert result.allowed is False
        assert result.durable_write_attempted is False
        assert result.receipt is None
        assert result.denial.target == target
        assert result.denial.reason_code == "projection_writes_disabled"
        assert result_dump["durableWriteAttempted"] is False
        assert result_dump["productionReceiptProduced"] is False
        assert result_dump["receipt"] is None
        assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_projection_authority_flags_remain_false_under_validation_construct_and_copy() -> None:
    boundary = _boundary_module()
    flags = boundary.ProjectionWriteAuthorityFlags.model_validate(
        {
            "transcriptWriteAllowed": True,
            "sseWriteAllowed": True,
            "controlEventWriteAllowed": True,
            "controlRequestWriteAllowed": True,
            "durableWriteAllowed": True,
            "productionReceiptAllowed": True,
            "storageBackendAttached": True,
            "filesystemWriteAllowed": True,
            "databaseWriteAllowed": True,
            "transportWriteAllowed": True,
        }
    )

    assert set(flags.model_dump(by_alias=True).values()) == {False}
    constructed = boundary.ProjectionWriteAuthorityFlags.model_construct()
    copied = flags.model_copy(update={"transcriptWriteAllowed": True})
    assert set(constructed.model_dump(by_alias=True).values()) == {
        False,
    }
    assert set(copied.model_dump(by_alias=True).values()) == {
        False,
    }


def test_future_receipt_schema_requires_storage_backend_id_target_and_operational_fields() -> None:
    boundary = _boundary_module()
    required_fields = {
        "receiptId",
        "storageBackend",
        "target",
        "rollbackSupported",
        "supportReference",
        "retentionPolicy",
        "checksum",
        "timestamp",
    }
    complete_payload = {
        "receiptId": "receipt-fixture-1",
        "storageBackend": "future-durable-store",
        "target": "transcript",
        "rollbackSupported": False,
        "supportReference": "operator-runbook",
        "retentionPolicy": "fixture-only",
        "checksum": "sha256:fixture",
        "timestamp": 1,
    }

    receipt = boundary.ProjectionWriteReceipt.model_validate(complete_payload)

    assert set(receipt.model_dump(by_alias=True).keys()) == required_fields
    for missing_field in required_fields:
        payload = dict(complete_payload)
        payload.pop(missing_field)
        with pytest.raises(ValidationError):
            boundary.ProjectionWriteReceipt.model_validate(payload)


def test_projection_write_boundary_import_is_schema_only_and_runtime_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module(
    "magi_agent.runtime.projection_write_boundary"
)
assert module is not None

forbidden_exact = (
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.events",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.tools.dispatcher",
    "magi_agent.runtime.transcript",
    "magi_agent.runtime.control",
    "magi_agent.transport.sse",
    "fastapi",
    "uvicorn",
)
forbidden_prefixes = (
    "google.adk",
    "magi_agent.tools",
    "magi_agent.memory",
    "magi_agent.database",
    "magi_agent.db",
    "magi_agent.telegram",
    "magi_agent.k8s",
    "magi_agent.transport.chat",
    "magi_agent.transport.routes",
    "magi_agent.chat_proxy",
    "magi_agent.proxy",
    "magi_agent.app",
    "magi_agent.main",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"projection write boundary loaded forbidden modules: {loaded}")
""",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_projection_write_boundary_source_forbids_live_writers_and_transports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "runtime"
        / "projection_write_boundary.py"
    )
    source = module_path.read_text(encoding="utf-8")
    forbidden_fragments = (
        "google.adk",
        "TranscriptStore",
        "InMemorySseWriter",
        "ControlEventLedger",
        "magi_agent.runtime.transcript",
        "magi_agent.runtime.control",
        "magi_agent.transport.sse",
        "magi_agent.transport.chat",
        "magi_agent.tools",
        "magi_agent.memory",
        "magi_agent.database",
        "magi_agent.db",
        "magi_agent.telegram",
        "magi_agent.k8s",
        "supabase",
        "psycopg",
        "asyncpg",
        "kubernetes",
        "fastapi",
        "APIRouter",
        ".append(",
        ".write(",
        "open(",
        "write_text(",
        "Path(",
        "requests",
        "httpx",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source
