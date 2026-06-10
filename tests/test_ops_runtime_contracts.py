from __future__ import annotations

import importlib
import json
import sys

import pytest
from pydantic import ValidationError

from magi_agent.ops import (
    RuntimeMetricRecord,
    RuntimeMetricsSnapshot,
    RuntimeOperationEvent,
    RuntimeOpsAttachmentFlags,
    default_runtime_ops_health_metadata,
    project_runtime_operation_event,
    safe_metadata,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _event(**overrides: object) -> RuntimeOperationEvent:
    payload = {
        "eventId": "event-001",
        "traceId": "trace-001",
        "operationId": "operation-001",
        "sequence": 0,
        "eventType": "tool_observed",
        "status": "accepted",
        "policySnapshotDigest": _digest("1"),
        "ledgerHeadDigest": _digest("2"),
        "contextProjectionDigest": _digest("3"),
        "metadata": {
            "toolRef": "FileRead",
            "durationMs": 12,
            "sourceDigest": _digest("4"),
        },
    }
    payload.update(overrides)
    return RuntimeOperationEvent(**payload)


def test_runtime_operation_event_projects_safe_digest_only_public_shape() -> None:
    projected = project_runtime_operation_event(_event())
    encoded = json.dumps(projected, sort_keys=True)

    assert projected["schemaVersion"] == "openmagi.ops.event.public.v1"
    assert projected["eventId"] == "event-001"
    assert projected["eventDigest"].startswith("sha256:")
    assert projected["activationEnabled"] is False
    assert projected["attachmentFlags"]["liveToolExecutionAttached"] is False
    assert projected["attachmentFlags"]["promptPayloadAttached"] is False
    assert projected["attachmentFlags"]["toolOutputPayloadAttached"] is False
    assert projected["attachmentFlags"]["hiddenReasoningAttached"] is False
    assert projected["attachmentFlags"]["credentialAttached"] is False
    assert "rawPrompt" not in encoded
    assert "rawToolOutput" not in encoded
    assert "privatePath" not in encoded


@pytest.mark.parametrize(
    "metadata",
    (
        {"rawPrompt": "sample-ref"},
        {"hiddenReasoning": "reason-ref"},
        {"rawToolOutput": "tool-output-ref"},
        {"authHeaderRef": "header-ref"},
        {"cookieRef": "cookie-ref"},
        {"privatePathRef": "path-ref"},
        {"toolOutputDigest": _digest("5")},
        {"nested": {"arg": "value"}},
    ),
)
def test_runtime_operation_event_rejects_forbidden_metadata(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="runtime operation validation failed"):
        _event(metadata=metadata)


def test_runtime_operation_event_rejects_private_values_without_echoing_them() -> None:
    rejected_values = (
        "/Users/example/.ssh/id_rsa",
        "bearer:abcd1234",
        "raw.prompt.ref",
        "raw.output.ref",
        "hidden.reasoning.ref",
        "private.path.ref",
        "private.ref",
        "private:ref",
        "private.key",
        "private:key",
        "tool.output.ref",
        "credential.ref",
        "token.ref",
        "secret.value.ref",
        "api.key.ref",
        "password.ref",
        "trace:.env",
        "trace:.ssh",
        "op:.kube",
        "operation:.config",
        "sk-" + "a" * 32,
        "ghp_" + "b" * 32,
        "AKIA" + "C" * 16,
    )

    for rejected in rejected_values:
        with pytest.raises(ValidationError) as exc_info:
            _event(metadata={"diagnosticRef": rejected})
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert rejected not in str(exc_info.value)
        assert rejected not in encoded_error


@pytest.mark.parametrize("rejected", ("private.ref", "trace:.env"))
def test_metric_validation_errors_do_not_echo_private_inputs(rejected: str) -> None:
    probes = (
        lambda: RuntimeMetricRecord(
            metricName="ops.event.accepted",
            value=1,
            unit="count",
            traceDigest=_digest("0"),
            policySnapshotDigest=_digest("1"),
            dimensions={"diagnosticRef": rejected},
        ),
        lambda: RuntimeMetricsSnapshot(counts={rejected: 1}),
        lambda: RuntimeMetricsSnapshot(eventTypeCounts={rejected: 1}),
    )

    for probe in probes:
        with pytest.raises(ValidationError) as exc_info:
            probe()
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert rejected not in str(exc_info.value)
        assert rejected not in encoded_error


@pytest.mark.parametrize("rejected", ("private.ref", "private/ref", "trace:.env"))
def test_ops_validation_error_locations_do_not_echo_private_extra_keys(rejected: str) -> None:
    probes = (
        lambda: RuntimeOperationEvent(
            eventId="event-001",
            traceId="trace-001",
            operationId="operation-001",
            sequence=0,
            eventType="tool_observed",
            status="accepted",
            policySnapshotDigest=_digest("1"),
            ledgerHeadDigest=_digest("2"),
            contextProjectionDigest=_digest("3"),
            **{rejected: "x"},
        ),
        lambda: RuntimeOpsAttachmentFlags(**{rejected: "x"}),
        lambda: RuntimeMetricRecord(
            metricName="ops.event.accepted",
            value=1,
            unit="count",
            traceDigest=_digest("4"),
            policySnapshotDigest=_digest("5"),
            **{rejected: "x"},
        ),
        lambda: RuntimeMetricsSnapshot(**{rejected: "x"}),
    )

    for probe in probes:
        with pytest.raises(ValidationError) as exc_info:
            probe()
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert rejected not in str(exc_info.value)
        assert rejected not in encoded_error


@pytest.mark.parametrize("field_name", ("eventId", "traceId", "operationId"))
def test_runtime_operation_event_rejects_private_refs_in_public_identifiers(field_name: str) -> None:
    with pytest.raises(ValidationError, match="runtime operation validation failed"):
        _event(**{field_name: "private.ref"})
    with pytest.raises(ValidationError, match="runtime operation validation failed"):
        _event(**{field_name: "trace:.env"})


def test_safe_metadata_is_canonical_and_digest_or_ref_only() -> None:
    clean = safe_metadata(
        {
            "zRef": "public.ref",
            "aDigest": _digest("6"),
            "attempt": 2,
            "enabled": False,
        }
    )

    assert list(clean) == ["aDigest", "attempt", "enabled", "zRef"]
    assert clean["aDigest"] == _digest("6")


def test_metric_records_and_attachment_flags_cannot_enable_live_authority() -> None:
    forged_flags = RuntimeOpsAttachmentFlags(
        liveToolExecutionAttached=True,
        productionStorageAttached=True,
        rawPromptAttached=True,
    )
    metric = RuntimeMetricRecord(
        metricName="ops.event.accepted",
        value=1,
        unit="count",
        traceDigest=_digest("7"),
        policySnapshotDigest=_digest("8"),
        dimensions={"status": "accepted"},
        attachmentFlags=forged_flags,
    )
    public = metric.public_projection()

    assert public["attachmentFlags"]["liveToolExecutionAttached"] is False
    assert public["attachmentFlags"]["productionStorageAttached"] is False
    assert public["attachmentFlags"]["promptPayloadAttached"] is False
    assert "rawPromptAttached" not in json.dumps(public, sort_keys=True)
    with pytest.raises(ValueError, match="model_copy update"):
        forged_flags.model_copy(update={"liveToolExecutionAttached": True})
    with pytest.raises(ValueError, match="copy update"):
        forged_flags.copy(update={"liveToolExecutionAttached": True})
    with pytest.raises(ValueError, match="model_construct"):
        RuntimeOpsAttachmentFlags.model_construct(liveToolExecutionAttached=True)


def test_metric_models_cannot_be_constructed_or_copied_into_authority() -> None:
    metric = RuntimeMetricRecord(
        metricName="ops.event.accepted",
        value=1,
        unit="count",
        traceDigest=_digest("9"),
        policySnapshotDigest=_digest("a"),
        dimensions={"status": "accepted"},
    )
    snapshot = RuntimeMetricsSnapshot(
        counts={"accepted": 1},
        eventTypeCounts={"tool_observed": 1},
        metricRecords=(metric,),
    )

    with pytest.raises(ValueError, match="model_copy update"):
        metric.model_copy(update={"source": "remote"})
    with pytest.raises(ValueError, match="copy update"):
        metric.copy(update={"source": "remote"})
    with pytest.raises(ValueError, match="model_construct"):
        RuntimeMetricRecord.model_construct(source="remote")
    with pytest.raises(ValueError, match="model_copy update"):
        snapshot.model_copy(update={"runtimeOperationsEnabled": True})
    with pytest.raises(ValueError, match="copy update"):
        snapshot.copy(update={"runtime_operations_enabled": True, "counts": {"private.ref": 1}})
    with pytest.raises(ValueError, match="model_construct"):
        RuntimeMetricsSnapshot.model_construct(source="remote")


def test_runtime_operation_event_cannot_be_constructed_or_copied_into_authority() -> None:
    event = _event()
    with pytest.raises(ValueError, match="model_copy update"):
        event.model_copy(update={"activationEnabled": True})
    with pytest.raises(ValueError, match="copy update"):
        event.copy(update={"event_id": "private.ref"})
    with pytest.raises(ValueError, match="model_construct"):
        RuntimeOperationEvent.model_construct(activationEnabled=True)
    with pytest.raises(ValidationError):
        _event(activationEnabled=True)


def test_runtime_ops_health_metadata_is_default_off() -> None:
    metadata = default_runtime_ops_health_metadata()

    assert metadata["enabled"] is False
    assert metadata["liveToolExecutionAttached"] is False
    assert metadata["productionStorageAttached"] is False
    assert metadata["productionQueueAttached"] is False


def test_ops_import_boundary_does_not_load_live_runtime_paths() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("magi_agent.ops"):
            sys.modules.pop(module_name)

    before = set(sys.modules)
    importlib.import_module("magi_agent.ops.metrics")
    importlib.import_module("magi_agent.ops.runtime_events")
    newly_loaded = set(sys.modules) - before
    forbidden_prefixes = (
        "google.adk.runners",
        "google.adk.models",
        "magi_agent.tools.kernel",
        "magi_agent.memory.adapters",
        "magi_agent.providers",
        "magi_agent.transport.chat",
        "magi_agent.workspace",
        "magi_agent.channels.telegram_adapter",
    )

    assert not any(name.startswith(prefix) for prefix in forbidden_prefixes for name in newly_loaded)
